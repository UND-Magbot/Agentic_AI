"""Codex 위임 fast-path — gemma 가 못 하거나 약한 작업(웹 검색·이미지 생성·심층 분석)을
Codex 브리지로 넘긴다. main.py 가 의도를 감지하면 _stream_staged_job 으로 실행한다.

트리거
  명령형: '@이미지 …', '@검색 …', '@codex …' (/ 접두도 허용) — 가장 확실한 방법.
  '@본문' : 명령어 없이 '@'(또는 '/')로 시작하면 Codex 위임. 본문이 개념도·공정도 요청이면
          Codex 가 설계(ask) 후 그린다(image) — 사내 gemma 개념도로 보내지 않는다. 그 밖은 ask.
  자연어: '이미지/로고 … 그려줘·만들어줘', '웹에서 … 검색해줘', '오늘 환율/뉴스 …',
          'codex(코덱스)한테 …'. 첨부가 있는 요청은 기존 첨부 전용 흐름에 양보한다.
"""
from __future__ import annotations

import io
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from . import codex_client, web_search
from .config import settings

_log = logging.getLogger("codex_delegate")

Kind = Literal["search", "image", "ask"]

PROGRESS_STEPS: dict[Kind, tuple[tuple[str, str], ...]] = {
    "search": (("search", "웹 검색 (외부 AI)"), ("compose", "결과 정리")),
    "image": (("generate", "이미지 생성 (외부 AI)"), ("save", "파일 저장")),
    "ask": (("context", "사내 자료 검색"), ("codex", "외부 AI 분석")),
}
FAIL_LABELS: dict[Kind, str] = {"search": "웹 검색", "image": "이미지 생성", "ask": "외부 AI 분석"}

_COMMAND_RE = re.compile(
    r"^\s*[@/](?P<cmd>이미지|그림|image|웹검색|검색|search|codex|코덱스)\b\s*[:：]?\s*",
    re.IGNORECASE,
)
_COMMAND_KIND: dict[str, Kind] = {
    "이미지": "image", "그림": "image", "image": "image",
    "웹검색": "search", "검색": "search", "search": "search",
    "codex": "ask", "코덱스": "ask",
}
_IMAGE_NOUN_RE = re.compile(r"(이미지|그림|일러스트|삽화|포스터|로고|아이콘|썸네일|배너|캐릭터)")
_IMAGE_ACTION_RE = re.compile(r"(생성해|만들어|그려)")
# 개념도·도표류는 전용 흐름, '어떻게/방법'은 정보 질문 — 이미지 생성으로 보내지 않는다.
_IMAGE_EXCLUDE_RE = re.compile(r"(개념도|컨셉도|도면|다이어그램|차트|그래프|엑셀|제안서|어떻게|방법)")
_WEB_EXPLICIT_RE = re.compile(r"(웹|인터넷|구글|네이버|온라인)\s*(에서|으로|에)\s.*?(검색|찾아|알아봐|조회)")
_FRESH_RE = re.compile(r"(오늘|현재|지금|최신|최근|실시간|요즘|이번\s*(주|달))")
_VOLATILE_RE = re.compile(r"(뉴스|환율|주가|시세|날씨|금리|유가|동향|속보)")
_INTERNAL_RE = re.compile(r"(사칙|사내|규정|우리\s*회사|유엔디)")
_ASK_RE = re.compile(r"(codex|코덱스)\s*(에게|한테|로|으로)", re.IGNORECASE)

# '@' / '/' 로 시작하는 자유 요청 — 알려진 명령어가 없을 때.
_BARE_PREFIX_RE = re.compile(r"^\s*[@/]\s*")
_CONCEPT_RE = re.compile(r"(개념도|컨셉도|공정도|공정\s*흐름도|배치도|레이아웃|도면)")

# 개념도는 2단계다(2026-09-28): 원문을 바로 그리면 요청 나열에 그치고, ChatGPT 구독 화면처럼
# "먼저 설계 → 그 설계로 그리기" 를 해야 비전 구성안·제어 구성도·검토 포인트가 나온다.
#   1) /v1/ask  : 요청 → 개념도 설계서(원문에 없는 판단은 '(권장)'·'(검토안)' 표시 강제)
#   2) /v1/image: 설계서 → 그림. prompt 는 최대 2000자(codex-bridge LIMITS.prompt).
# 설계 단계가 실패하면 원문을 바로 그린다(concept_prompt).
_CONCEPT_HEADER = "[제조 공정 개념도]"
_PROMPT_MAX = 2000
_ASK_MAX = 4000            # codex-bridge LIMITS.question
_TRUNC_MARK = "\n(원문 일부 생략)"
_STYLE = (
    "- 16:9 가로, 흰 배경, 제안서용 산업 인포그래픽(로봇·설비 일러스트 + 번호 붙은 공정 카드).\n"
    "- 모든 글자는 한국어(장비·브랜드명은 원문 표기). 설계서의 '(권장)'·'(검토안)' 표시는 그림에도 그대로 둔다.\n"
)
# 지시문에는 내부 표시(_CONCEPT_HEADER)를 넣지 않는다 — 모델이 그 글자를 그림 제목으로 썼다(실측).
_CONCEPT_GUIDE = (
    "아래 고객 요청을 바탕으로 제안서용 공정 개념도 1장을 그려라. 제목은 공정 내용을 드러내게 짓는다.\n" + _STYLE +
    "- 공정 순서를 왼쪽→오른쪽 흐름 화살표로, 설비와 비전 카메라 위치를 표시.\n"
    "- 수치는 요청 원문에 있는 값만 쓰고 없는 값은 '확인 필요'로 적는다.\n"
    "- 요청에 있는 검토·확인 사항은 하단 '검토 사항' 박스에 모은다.\n"
    "고객 요청:\n"
)
_DRAW_GUIDE = (
    "아래 설계서대로 제안서용 공정 개념도 1장을 그려라. 그림 제목·부제는 설계서의 제목·부제를 그대로 쓰고, "
    "설계서에 없는 내용은 더하지 않는다.\n"
    + _STYLE + "설계서:\n"
)
_PLAN_QUESTION = """다음 고객 요청으로 제안서에 넣을 공정 개념도 한 장을 설계하라. 그림은 그리지 말고,
이미지 생성 모델에게 그대로 넘길 설계서만 한국어 평문으로 1400자 이내로 써라(마크다운 기호 없이).

설계서 구성(이 순서, 각 항목 짧게):
1. 제목 / 부제 — 제목은 공정 내용을 드러내게 구체적으로(예: '비전 기반 보틀 오픈·주입·재캡핑 공정 개념도').
   '제조 공정 개념도' 같은 일반 제목은 쓰지 않는다. 부제는 핵심 장비·제어 방식
2. 레이아웃: 상단 = 공정 단계 카드(좌→우 화살표), 하단 좌 = 제어 구성도, 하단 우 = 검토 포인트
3. 공정 단계(번호·단계명·그림에 그릴 설비·해당 비전·핵심 동작 2~3개). 되돌아가는 흐름·불량 처리도 표시
4. 비전 구성안(각 비전의 위치·형식·역할, 필수/옵션)
5. 제어 구성(비전·IPC·PLC·로봇·설비 사이 신호)
6. 검토 포인트(번호 목록)

규칙:
- 요청 원문의 수치·장비명·일정은 그대로 쓴다.
- 원문에 없는 판단·수량·사양(비전 대수, 형식, 설치 위치 등)은 반드시 끝에 '(권장)' 또는 '(검토안)'을 붙인다.
- 계산한 값은 식을 함께 쓴다(예: 1.5kg + 3.7kg = 5.2kg).
- 원문에서 확인·검토를 요구한 사항은 모두 검토 포인트에 넣는다.

고객 요청:
"""


def concept_prompt(request: str) -> str:
    """(설계 단계 실패 시) 개념도 지시 + 요청 원문. 넘치면 뒤를 자르고 표시한다."""
    return _fit(_CONCEPT_GUIDE, request)


def draw_prompt(plan: str) -> str:
    """설계서 → 그리기 지시(브리지 한도 안)."""
    return _fit(_DRAW_GUIDE, plan)


def _fit(guide: str, body: str) -> str:
    room = _PROMPT_MAX - len(guide)
    if len(body) <= room:
        return guide + body
    return guide + body[: room - len(_TRUNC_MARK)] + _TRUNC_MARK


async def _concept_plan(request: str, user_id: int | None = None) -> str:
    """Codex 로 개념도 설계서를 받는다. 실패하면 빈 문자열(원문 바로 그리기로 대체)."""
    question = _PLAN_QUESTION + request[: _ASK_MAX - len(_PLAN_QUESTION)]
    try:
        return _plain_text(await codex_client.ask(question, user_id=user_id)).strip()
    except codex_client.CodexError as e:
        _log.warning("[codex_delegate] 개념도 설계 실패 → 원문 바로 그리기: %s", e)
        return ""


_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_CODE_RE = re.compile(r"`([^`\n]+)`")

_RAG_TOP_K = 8
_RAG_MIN_SCORE = 0.45


@dataclass
class CodexIntent:
    kind: Kind
    text: str  # 명령 접두를 떼어 낸 요청 본문


def detect_intent(messages: list[dict], *, has_attachments: bool) -> CodexIntent | None:
    """마지막 user 발화에서 Codex 위임 의도를 감지. 브리지 미설정이면 항상 None."""
    if not codex_client.is_configured():
        return None
    if not messages or messages[-1].get("role") != "user":
        return None
    text = (messages[-1].get("content") or "").strip()

    m = _COMMAND_RE.match(text)
    if m:
        rest = text[m.end():].strip()
        return CodexIntent(_COMMAND_KIND[m.group("cmd").lower()], rest) if rest else None

    # 명령어 없이 '@본문' / '/본문' — 사용자가 Codex 에 맡기겠다고 표시한 것(2026-09-28 사용자 요구).
    # 개념도·공정도 요청이면 사내 개념도(gemma)가 아니라 Codex 이미지로 만든다.
    m = _BARE_PREFIX_RE.match(text)
    if m and not has_attachments:
        rest = text[m.end():].strip()
        if rest:
            if _CONCEPT_RE.search(rest):
                # 원문을 그대로 넘기고 run_image 가 설계 → 그리기 2단계로 처리한다.
                return CodexIntent("image", _CONCEPT_HEADER + rest)
            return CodexIntent("ask", rest)

    if has_attachments:
        return None
    if _IMAGE_NOUN_RE.search(text) and _IMAGE_ACTION_RE.search(text) and not _IMAGE_EXCLUDE_RE.search(text):
        return CodexIntent("image", text)
    if _ASK_RE.search(text):
        return CodexIntent("ask", text)
    is_web = _WEB_EXPLICIT_RE.search(text) or (_FRESH_RE.search(text) and _VOLATILE_RE.search(text))
    if is_web and not _INTERNAL_RE.search(text):
        return CodexIntent("search", text)
    return None


# ── 실행 ────────────────────────────────────────────────────────────────────
async def run_search(query: str, on_stage: Callable[[str], None], user_id: int | None = None) -> str:
    on_stage("search")
    result = await web_search.search(query, user_id=user_id)
    if result.get("error"):
        raise ValueError(result["error"])
    on_stage("compose")
    lines = [result.get("answer") or "검색 결과에서 답을 찾지 못했습니다."]
    sources = [r for r in result.get("results") or [] if r.get("url")]
    if sources:
        lines += ["", "출처"]
        lines += [f"{i}. {r.get('title') or r['url']} — {r['url']}" for i, r in enumerate(sources, 1)]
    if result.get("provider") == "tavily" and settings.search_provider == "codex":
        lines += ["", "※ 외부 AI 를 쓸 수 없어 기본 웹 검색 결과로 대신 답했습니다."]
    return "\n".join(lines)


async def run_image(prompt: str, user_id: int | None, on_stage: Callable[[str], None]) -> str:
    on_stage("generate")
    is_concept = prompt.startswith(_CONCEPT_HEADER)
    notes: list[str] = []
    if is_concept:
        request = prompt[len(_CONCEPT_HEADER):]
        plan = await _concept_plan(request, user_id)
        prompt = draw_prompt(plan) if plan else concept_prompt(request)
        notes.append("AI 가 요청을 먼저 설계한 뒤 그린 공정 개념도입니다. '(권장)'·'(검토안)' 표시는 "
                     "요청에 없던 제안이니 검토해 주세요." if plan else
                     "설계 단계가 실패해 요청 원문을 바로 그렸습니다.")
        notes.append("수정 가능한 draw.io 원본은 없고, 이미지 속 한글·수치는 틀릴 수 있으니 확인해 주세요.")
        if prompt.endswith(_TRUNC_MARK):
            notes.append("내용이 길어 일부만 이미지 생성에 전달했습니다.")
    try:
        image, mime, description = await codex_client.generate_image(prompt, user_id=user_id)
    except codex_client.CodexError as e:
        raise ValueError(str(e)) from e
    on_stage("save")
    stem = "codex_개념도" if is_concept else "codex_image"
    filename = f"{stem}_{datetime.now():%Y%m%d_%H%M%S}.png"
    att_id = await _save_attachment(image, mime, filename, user_id)
    head = "\n".join([description or "이미지를 생성했습니다.", *(f"- {n}" for n in notes)])
    return f"{head}\n\n[{filename}](/api/attachments/{att_id}/download)"


async def run_ask(
    question: str,
    on_stage: Callable[[str], None],
    fallback: Callable[[], Awaitable[str]],
    user_id: int | None = None,
) -> str:
    """사내 자료(허용 시) + 질문을 Codex 에 위임. 실패하면 gemma 답변으로 대체한다."""
    on_stage("context")
    context = await _rag_context(question) if settings.codex_allow_internal_data else ""
    on_stage("codex")
    try:
        return _plain_text(await codex_client.ask(question, context, user_id=user_id))
    except codex_client.CodexError as e:
        _log.warning("[codex_delegate] ask 실패 → gemma 폴백: %s", e)
        answer = await fallback()
        return f"⚠ 외부 AI 를 사용할 수 없어 기본 모델로 답합니다. ({e})\n\n{answer}"


def _plain_text(text: str) -> str:
    """채팅 화면은 마크다운을 렌더링하지 않는다 — 지시해도 섞여 나오는 **굵게**·`코드`·# 제목 기호를 제거."""
    text = _BOLD_RE.sub(r"\1", text)
    text = _CODE_RE.sub(r"\1", text)
    return _HEADING_RE.sub("", text)


async def _rag_context(question: str) -> str:
    from .database import SessionLocal
    from .rag import build_context_block, search

    try:
        async with SessionLocal() as db:
            chunks = await search(db, question, top_k=_RAG_TOP_K, min_score=_RAG_MIN_SCORE)
    except Exception as e:  # 임베딩/DB 미가용 — 자료 없이 진행.
        _log.warning("[codex_delegate] RAG 검색 실패: %s", e)
        return ""
    return build_context_block(chunks, query=question) if chunks else ""


async def _save_attachment(data: bytes, mime: str, filename: str, user_id: int | None) -> int:
    """MinIO 저장 + Attachment 행 생성. 다운로드는 소유자만 가능하므로 요청자 소유로 만든다."""
    from .database import SessionLocal
    from .expense_service import _resolve_owner_user_id
    from .models import Attachment
    from .storage import make_object_key, put_object

    async with SessionLocal() as db:
        owner_id = await _resolve_owner_user_id(db, user_id)
        key = make_object_key(owner_id, filename)
        put_object(key=key, data=io.BytesIO(data), length=len(data), mime=mime)
        att = Attachment(
            user_id=owner_id,
            bucket=settings.minio_bucket,
            object_key=key,
            original_filename=filename,
            mime=mime,
            size_bytes=len(data),
        )
        db.add(att)
        await db.commit()
        await db.refresh(att)
        return att.id
