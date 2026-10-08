"""Expense 양식 생성 서비스 — DB/MinIO 통합 계층.

`expense_builder.py` 는 순수 함수(I/O 없음)로 xlsx 바이트만 만들고, 본 모듈이 다음을 묶는다:
- 영수증 첨부 ID → MinIO 에서 이미지 바이트 fetch
- 결과 xlsx → MinIO 저장 + Attachment row 생성
- 호출자에게 다운로드 메타(attachment_id/url/filename) 반환

Phase 1: 호출자 인증 컨텍스트 미연결. 영수증 첨부의 소유자 user_id 를 결과물 owner 로
재활용한다(영수증 올린 사람이 expense 작성자라는 합리적 추정). 영수증이 없으면 첫 번째
admin/superadmin user 로 fallback — 추후 인증된 chat endpoint 연결 시 contextvar 로 교체.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from .database import SessionLocal
from .expense_builder import (
    ExpenseLine,
    ExpenseReport,
    ReceiptImage,
    build_expense_xlsx,
    build_filename,
)
from .models import Attachment, User, UserRole
from .storage import get_object_stream, make_object_key, put_object

logger = logging.getLogger("expense_service")

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# 영수증으로 허용할 MIME — 이미지만. PDF/Hipass 별도 첨부는 1차 범위 외.
_RECEIPT_IMAGE_MIMES = {
    "image/png", "image/jpeg", "image/jpg", "image/webp", "image/bmp", "image/gif",
}


@dataclass
class ExpenseBuildResult:
    """compose_expense_report 도구의 호출 결과."""

    attachment_id: int
    filename: str
    download_url: str
    size_bytes: int
    lines_count: int
    receipts_count: int


# ── 영수증 fetch ────────────────────────────────────────────────────────────
async def _fetch_receipts(
    db, attachment_ids: list[int]
) -> tuple[list[ReceiptImage], int | None]:
    """첨부 ID 들에서 이미지 바이트 + 소유자 user_id 를 끌어온다.

    반환: (영수증 리스트, 첫 첨부의 owner_user_id).
    이미지가 아닌 첨부(예: PDF)는 자동 skip 하고 경고 로그.
    """
    if not attachment_ids:
        return [], None

    res = await db.execute(
        select(Attachment).where(Attachment.id.in_(attachment_ids))
    )
    rows: list[Attachment] = list(res.scalars().all())
    # LLM 이 넘긴 순서를 보존하기 위해 dict 로 인덱싱.
    by_id = {a.id: a for a in rows}

    receipts: list[ReceiptImage] = []
    owner: int | None = None
    for idx, att_id in enumerate(attachment_ids):
        att = by_id.get(att_id)
        if att is None:
            logger.warning("[expense] 첨부 id=%s 존재하지 않음 — skip", att_id)
            continue
        if att.mime not in _RECEIPT_IMAGE_MIMES:
            logger.warning(
                "[expense] 첨부 id=%s mime=%s 이미지 아님 — skip", att_id, att.mime
            )
            continue
        if owner is None:
            owner = att.user_id

        # MinIO 에서 바이트 수신.
        try:
            response = get_object_stream(att.object_key)
            try:
                chunks: list[bytes] = []
                for chunk in response.stream(amt=64 * 1024):
                    chunks.append(chunk)
                data = b"".join(chunks)
            finally:
                response.close()
                response.release_conn()
        except Exception as e:
            logger.warning(
                "[expense] MinIO fetch 실패 att_id=%s key=%s: %s",
                att_id, att.object_key, e,
            )
            continue

        receipts.append(
            ReceiptImage(
                filename=att.original_filename, data=data, order_index=idx
            )
        )

    return receipts, owner


# ── 결과 owner 결정 ─────────────────────────────────────────────────────────
async def _resolve_owner_user_id(db, fallback: int | None) -> int:
    """결과 xlsx 의 Attachment.user_id 를 결정.

    우선순위: 영수증 owner → 첫 번째 superadmin/domain_admin → 첫 번째 user.
    (이전엔 `UserRole.admin` 을 참조했으나 실제 enum 값은 `domain_admin` —
     AttributeError 로 expense 생성이 통째로 실패하던 버그 수정.)
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
async def compose_expense_report(
    *,
    author: str,
    year: int,
    month: int,
    lines: list[dict[str, Any]],
    receipt_attachment_ids: list[int] | None = None,
) -> ExpenseBuildResult:
    """expense 양식 생성 → MinIO 저장 → Attachment row 생성."""
    attachment_ids = list(receipt_attachment_ids or [])
    parsed_lines = [_parse_line(raw) for raw in lines]

    async with SessionLocal() as db:
        receipts, recipient_owner = await _fetch_receipts(db, attachment_ids)

        report = ExpenseReport(
            author=author.strip(),
            year=int(year),
            month=int(month),
            lines=parsed_lines,
            receipts=receipts,
        )
        xlsx_bytes = build_expense_xlsx(report)
        filename = build_filename(report)

        owner_id = await _resolve_owner_user_id(db, recipient_owner)
        key = make_object_key(owner_id, filename)
        try:
            import io as _io
            put_object(
                key=key,
                data=_io.BytesIO(xlsx_bytes),
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

        return ExpenseBuildResult(
            attachment_id=att.id,
            filename=filename,
            # 브라우저가 직접 클릭할 수 있도록 frontend proxy 경로(/api/...)를 반환.
            # 같은 origin(localhost:3000) 이라 쿠키 인증이 자동으로 따라가 backend 의
            # /v1/attachments/{id}/download 가 Content-Disposition:attachment 헤더로
            # 브라우저 다운로드를 트리거한다. backend 경로를 직접 노출하면 8000 포트
            # 도달 + Bearer 토큰 부재로 401.
            download_url=f"/api/attachments/{att.id}/download",
            size_bytes=len(xlsx_bytes),
            lines_count=len(parsed_lines),
            receipts_count=len(receipts),
        )


# ── 헬퍼 ────────────────────────────────────────────────────────────────────
def _parse_line(raw: dict[str, Any]) -> ExpenseLine:
    """LLM JSON args 의 한 line dict 를 ExpenseLine 으로 정규화.

    엄격 검증보다 관대 — source/category 가 비어 있어도 dataclass 가 받고 빈 셀로 출력.
    amount 는 숫자 강제. date 는 문자열 유지(엑셀이 텍스트 셀로 처리).
    """
    source = (raw.get("source") or "").strip() or "개인카드"
    category = (raw.get("category") or "").strip() or "해당없음"
    date = (raw.get("date") or "").strip()
    purpose = (raw.get("purpose") or "").strip()
    vendor = (raw.get("vendor") or "").strip()
    user = (raw.get("user") or "").strip()
    try:
        amount = float(raw.get("amount") or 0)
    except (TypeError, ValueError):
        amount = 0.0

    if source not in ("법인카드", "개인카드"):
        # LLM 이 다른 표현을 쓰면 가장 흔한 영문도 흡수.
        s_lower = source.lower()
        if "corp" in s_lower or "law" in s_lower:
            source = "법인카드"
        else:
            source = "개인카드"

    return ExpenseLine(
        source=source,  # type: ignore[arg-type]
        category=category,
        date=date,
        purpose=purpose,
        amount=amount,
        vendor=vendor,
        user=user,
    )


def _xlsx_bucket() -> str:
    """현재 MinIO 설정의 bucket 명. storage 모듈과 동기."""
    from .config import settings
    return settings.minio_bucket


__all__ = ["ExpenseBuildResult", "compose_expense_report"]
