"""주간 회의록 보고 생성 서비스 — STT/요약/xlsx/MinIO 통합 계층.

`meeting_transcriber`(STT) · `meeting_summarizer`(요약) · `meeting_builder`(xlsx) 의
순수 로직을 묶어 다음을 수행한다:
- 회의 녹음 첨부 ID → MinIO 에서 오디오 바이트 fetch
- faster-whisper 로 전사 → vLLM 으로 2개 섹션 요약
- 결과 xlsx → MinIO 저장 + Attachment row 생성
- 호출자에게 다운로드 메타 반환

녹음 첨부의 소유자 user_id 를 결과물 owner 로 재활용한다(녹음 올린 사람이 보고
작성자라는 합리적 추정). expense_service 와 동일한 패턴.

진행 단계 콜백(on_progress): 호출자가 각 단계 전이(active/done/error)를 받아
SSE 마커로 흘려보낼 수 있도록 노출한다. 콜백이 None 이면 무시 — 기존 호출부 무영향.
"""
from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass
from typing import Callable

from sqlalchemy import select

from .database import SessionLocal
from .meeting_builder import MeetingReport, build_filename, build_meeting_xlsx
from .meeting_summarizer import summarize_transcript
from .meeting_transcriber import transcribe
from .models import Attachment, User, UserRole
from .storage import get_object_stream, make_object_key, put_object

logger = logging.getLogger("meeting_service")

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# 회의 녹음으로 허용할 오디오 MIME — api_attachments._ALLOWED_MIME 의 audio/* 와 동기.
_AUDIO_MIMES = {
    "audio/mp4", "audio/x-m4a", "audio/m4a", "audio/aac",
    "audio/mpeg", "audio/mp3",
    "audio/wav", "audio/x-wav", "audio/wave",
    "audio/webm", "audio/ogg", "audio/flac",
}


@dataclass
class MeetingBuildResult:
    """compose_meeting_report 호출 결과."""

    attachment_id: int
    filename: str
    download_url: str
    size_bytes: int
    meeting_date: str
    audio_duration_sec: float
    transcript_chars: int


# ── 오디오 첨부 fetch ───────────────────────────────────────────────────────
async def _fetch_audio(
    db, attachment_ids: list[int]
) -> tuple[bytes, str, int]:
    """첨부 ID 목록에서 첫 오디오 파일의 바이트 + 파일명 + 소유자 user_id 를 가져온다.

    Raises:
        ValueError: 오디오 첨부가 하나도 없는 경우.
        RuntimeError: MinIO fetch 실패.
    """
    if not attachment_ids:
        raise ValueError("첨부된 파일이 없습니다. 회의 녹음 파일을 첨부해 주세요.")

    res = await db.execute(
        select(Attachment).where(Attachment.id.in_(attachment_ids))
    )
    by_id = {a.id: a for a in res.scalars().all()}

    # LLM/프론트가 넘긴 순서를 보존하며 첫 오디오 첨부를 선택.
    for att_id in attachment_ids:
        att = by_id.get(att_id)
        if att is None:
            logger.warning("[meeting] 첨부 id=%s 존재하지 않음 — skip", att_id)
            continue
        if att.mime not in _AUDIO_MIMES:
            logger.warning(
                "[meeting] 첨부 id=%s mime=%s 오디오 아님 — skip", att_id, att.mime
            )
            continue
        try:
            response = get_object_stream(att.object_key)
            try:
                chunks: list[bytes] = []
                for chunk in response.stream(amt=256 * 1024):
                    chunks.append(chunk)
                data = b"".join(chunks)
            finally:
                response.close()
                response.release_conn()
        except Exception as e:
            raise RuntimeError(
                f"녹음 파일 다운로드 실패 (id={att_id}): {e}"
            ) from e
        return data, att.original_filename, att.user_id

    raise ValueError(
        "첨부 파일 중 오디오 파일(m4a/mp3/wav 등)이 없습니다. 회의 녹음을 첨부해 주세요."
    )


# ── 결과 owner 결정 ─────────────────────────────────────────────────────────
async def _resolve_owner_user_id(db, fallback: int | None) -> int:
    """결과 xlsx 의 Attachment.user_id 결정.

    우선순위: 녹음 첨부 owner → 첫 superadmin/domain_admin → 첫 user.
    (expense_service._resolve_owner_user_id 와 동일 규칙.)
    """
    if fallback is not None:
        return fallback
    res = await db.execute(
        select(User.id)
        .where(User.role.in_([UserRole.superadmin, UserRole.domain_admin]))
        .order_by(User.id.asc())
        .limit(1)
    )
    admin_id = res.scalar_one_or_none()
    if admin_id is not None:
        return admin_id
    res = await db.execute(select(User.id).order_by(User.id.asc()).limit(1))
    any_id = res.scalar_one_or_none()
    if any_id is None:
        raise RuntimeError("저장 가능한 사용자가 없습니다 — users 테이블이 비어 있습니다.")
    return any_id


# ── 핵심 entry ──────────────────────────────────────────────────────────────
# 진행 단계 ID — frontend ProgressCard 와 동일 식별자. 변경 시 함께 갱신.
PROGRESS_STEPS: tuple[tuple[str, str], ...] = (
    ("audio_fetch", "녹음 파일 준비"),
    ("transcribe", "음성 인식"),
    ("summarize", "회의 내용 요약"),
    ("build_xlsx", "회의록 양식 생성"),
    ("upload", "파일 저장"),
)


async def compose_meeting_report(
    *,
    author: str,
    meeting_date: str,
    attachment_ids: list[int],
    on_progress: Callable[[str, str], None] | None = None,
) -> MeetingBuildResult:
    """회의 녹음 → 주간 회의록 xlsx 생성 → MinIO 저장 → Attachment row 생성.

    Args:
        author: 작성자 한글 이름(파일명용).
        meeting_date: 회의 날짜 YYYY-MM-DD.
        attachment_ids: 업로드 첨부 ID 목록. 이 중 첫 오디오 파일을 녹음으로 사용.
        on_progress: 단계 전이 콜백 ``(step_id, state)``.
            state ∈ {"active", "done", "error"}. 동기 함수 — 호출자는 보통 큐에
            put 하는 짧은 처리만 한다. None 이면 진행 알림 비활성.
    """

    def _emit(step_id: str, state: str) -> None:
        if on_progress is None:
            return
        try:
            on_progress(step_id, state)
        except Exception as exc:  # 진행 알림 실패가 본 작업을 중단시키지 않게 한다.
            logger.warning("[meeting] on_progress(%s,%s) raised: %s", step_id, state, exc)

    try:
        # 1) 짧은 세션 — 오디오 첨부 fetch (DB 조회 + MinIO read).
        _emit("audio_fetch", "active")
        async with SessionLocal() as db:
            audio_bytes, audio_name, owner_fallback = await _fetch_audio(
                db, attachment_ids
            )
        logger.warning(
            "[meeting] audio fetched — name=%s bytes=%d", audio_name, len(audio_bytes)
        )
        _emit("audio_fetch", "done")

        # 2) STT — CPU 바운드 블로킹 작업이라 executor 로 분리(이벤트 루프 보호).
        # DB 세션 밖에서 수행 — 수 분 걸리는 작업 동안 커넥션을 유휴 점유하지 않는다
        # (원격 PostgreSQL idle-in-transaction 으로 인한 커넥션 단절 위험 차단).
        _emit("transcribe", "active")
        loop = asyncio.get_event_loop()
        transcript = await loop.run_in_executor(
            None, lambda: transcribe(audio_bytes, language="ko")
        )
        if not transcript.text.strip():
            raise RuntimeError(
                "녹음에서 음성을 인식하지 못했습니다. 녹음 파일 상태를 확인해 주세요."
            )
        _emit("transcribe", "done")

        # 3) 요약 — vLLM 으로 2개 섹션 생성. (역시 DB 세션 밖.)
        _emit("summarize", "active")
        summary = await summarize_transcript(transcript.text)
        _emit("summarize", "done")

        # 4) xlsx 생성 — 순수 함수, I/O 없음.
        _emit("build_xlsx", "active")
        report = MeetingReport(
            meeting_date=meeting_date,
            main_content=summary.main_content,
            directives=summary.directives,
            author=author.strip(),
        )
        xlsx_bytes = build_meeting_xlsx(report)
        filename = build_filename(report)
        _emit("build_xlsx", "done")

        # 5) 짧은 세션 — owner 결정 + MinIO 저장 + Attachment row.
        _emit("upload", "active")
        async with SessionLocal() as db:
            owner_id = await _resolve_owner_user_id(db, owner_fallback)
            key = make_object_key(owner_id, filename)
            try:
                put_object(
                    key=key,
                    data=io.BytesIO(xlsx_bytes),
                    length=len(xlsx_bytes),
                    mime=_XLSX_MIME,
                )
            except Exception as e:
                raise RuntimeError(f"xlsx MinIO 저장 실패: {e}") from e

            att = Attachment(
                user_id=owner_id,
                bucket=_xlsx_bucket(),
                object_key=key,
                original_filename=filename,
                mime=_XLSX_MIME,
                size_bytes=len(xlsx_bytes),
            )
            db.add(att)
            await db.commit()
            await db.refresh(att)
            att_id = att.id
        _emit("upload", "done")
    except Exception:
        # 어느 단계에서 터졌는지는 _emit 직전 마지막 active 상태로 식별 가능 —
        # 호출자(_stream_meeting)가 active 단계만 error 로 마킹하면 된다.
        raise

    return MeetingBuildResult(
        attachment_id=att_id,
        filename=filename,
        # expense 와 동일 — 프론트 프록시 경로(쿠키 인증 자동 전파).
        download_url=f"/api/attachments/{att_id}/download",
        size_bytes=len(xlsx_bytes),
        meeting_date=meeting_date,
        audio_duration_sec=transcript.duration_sec,
        transcript_chars=len(transcript.text),
    )


def _xlsx_bucket() -> str:
    from .config import settings
    return settings.minio_bucket


__all__ = ["MeetingBuildResult", "compose_meeting_report", "PROGRESS_STEPS"]
