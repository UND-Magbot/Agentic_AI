"""대화/메시지 라우터 — `/v1/conversations/*`.

대화 영속화 1차 구현. 인증된 사용자 본인 대화만 조회/수정 가능.
- GET    /v1/conversations           : 사이드바/검색용 요약 리스트(최근순).
- GET    /v1/conversations/{id}      : 메시지 포함 상세 + 권한 검사.
- POST   /v1/conversations           : 새 대화 생성(초기 메시지 옵션).
- POST   /v1/conversations/{id}/messages : 메시지 1건 추가 + updated_at 자동 갱신.
- PATCH  /v1/conversations/{id}      : title/domain 수정.
- DELETE /v1/conversations/{id}      : 삭제(연쇄로 messages 도 함께 삭제됨).
"""
import re
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .api_auth import get_current_user
from .database import get_db
from .models import Conversation, Message, User, UserDomain
from .schemas_chat import (
    ConversationCreateRequest,
    ConversationDetail,
    ConversationSummary,
    MessageCreate,
    MessageOut,
    db_domain_to_ui,
    ui_domain_to_db,
)

router = APIRouter(prefix="/v1/conversations", tags=["Conversations"])


# ---------- 직렬화 헬퍼 ----------------------------------------------------

def _to_summary(c: Conversation, *, preview: str | None = None) -> ConversationSummary:
    return ConversationSummary(
        id=c.id,
        title=c.title,
        domain=c.domain.value,  # type: ignore[arg-type]
        domain_key=db_domain_to_ui(c.domain.value),
        starred=c.starred,
        started_at=c.started_at,
        updated_at=c.updated_at,
        preview=preview,
    )


# 검색어 매치 위치 ±N 글자 발췌. 매치 못 찾으면 None.
# 표시 정리 규칙:
#  - 진짜 newline/tab 은 공백으로.
#  - literal "\n", "\t" 두 글자(SQL 시드의 standard_conforming_strings=on 으로 인해
#    backslash escape 가 미해석되어 그대로 저장된 케이스)도 공백으로.
#  - 마크다운 강조(`**`) 는 미리보기에선 노이즈라 제거.
_PREVIEW_LITERAL_ESCAPE = re.compile(r"\\[ntr]")
_PREVIEW_MD_BOLD = re.compile(r"\*\*+")


def _normalize_preview(text: str) -> str:
    text = _PREVIEW_LITERAL_ESCAPE.sub(" ", text)
    text = _PREVIEW_MD_BOLD.sub("", text)
    text = " ".join(text.split())
    return text


def _make_preview(content: str, query: str, ctx: int = 50) -> str | None:
    if not query:
        return None
    # ASCII case-insensitive 매치 위치 찾기. 한글은 case 무관(원본 그대로).
    lower = content.lower()
    q = query.lower()
    idx = lower.find(q)
    if idx < 0:
        return None
    start = max(0, idx - ctx)
    end = min(len(content), idx + len(query) + ctx)
    snippet = _normalize_preview(content[start:end])
    if not snippet:
        return None
    if start > 0:
        snippet = "…" + snippet
    if end < len(content):
        snippet = snippet + "…"
    return snippet


def _to_detail(c: Conversation) -> ConversationDetail:
    return ConversationDetail(
        id=c.id,
        title=c.title,
        domain=c.domain.value,  # type: ignore[arg-type]
        domain_key=db_domain_to_ui(c.domain.value),
        starred=c.starred,
        started_at=c.started_at,
        updated_at=c.updated_at,
        messages=[
            MessageOut(
                id=m.id,
                role=m.role,
                content=m.content,
                domain=(m.domain.value if m.domain is not None else None),  # type: ignore[arg-type]
                model=m.model,
                created_at=m.created_at,
            )
            for m in c.messages
        ],
    )


async def _load_owned(
    db: AsyncSession, conv_id: int, user: User, *, with_messages: bool
) -> Conversation:
    """본인 소유 대화 1건 로드. 없거나 타인 소유면 404."""
    stmt = select(Conversation).where(Conversation.id == conv_id)
    if with_messages:
        stmt = stmt.options(selectinload(Conversation.messages))
    res = await db.execute(stmt)
    conv = res.scalar_one_or_none()
    if conv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="대화를 찾을 수 없습니다.")
    # superadmin 은 모든 대화 접근 허용. 그 외는 owner 만.
    if user.role.value != "superadmin" and conv.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="대화를 찾을 수 없습니다.")
    return conv


# ---------- 라우트 ----------------------------------------------------------

@router.get("", response_model=list[ConversationSummary])
async def list_conversations(
    limit: int = 50,
    q: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """최근 대화 N건 또는 검색 결과. updated_at 내림차순.

    - superadmin: 전체 대화 (시연/관제 목적).
    - 그 외 사용자: 본인 소유 대화만.
    - q (검색어) 가 주어지면 conversation.title 또는 message.content 에 부분일치(ILIKE)
      되는 대화를 반환한다. 인덱스가 없는 1차 구현 — 데이터 규모가 커지면 pg_trgm 또는
      to_tsvector 기반 전문 검색으로 승격할 수 있다.
    """
    limit = max(1, min(limit, 200))
    # 즐겨찾기(starred=true) 를 항상 위로, 그 안에서는 최근 활동 순.
    stmt = (
        select(Conversation)
        .order_by(Conversation.starred.desc(), Conversation.updated_at.desc())
        .limit(limit)
    )
    if current_user.role.value != "superadmin":
        stmt = stmt.where(Conversation.user_id == current_user.id)

    qs = (q or "").strip()
    if qs:
        like = f"%{qs}%"
        # title 매치 OR (해당 conversation 의 messages 중 content 매치 존재)
        msg_subq = select(Message.conversation_id).where(Message.content.ilike(like))
        stmt = stmt.where(
            or_(
                Conversation.title.ilike(like),
                Conversation.id.in_(msg_subq),
            )
        )

    res = await db.execute(stmt)
    rows = res.scalars().all()

    if not qs:
        return [_to_summary(c) for c in rows]

    # 검색어가 있으면 매치된 첫 메시지의 본문에서 preview 발췌.
    # title 만 매치된 케이스는 preview None 으로 둔다(중복 노출 방지).
    like = f"%{qs}%"
    conv_ids = [c.id for c in rows]
    preview_by_id: dict[int, str] = {}
    if conv_ids:
        # 한 번의 쿼리로 모든 매치된 메시지 본문 가져오기 → conversation 별 가장 이른 매치 1건 선택.
        mres = await db.execute(
            select(Message.conversation_id, Message.content, Message.created_at)
            .where(
                Message.conversation_id.in_(conv_ids),
                Message.content.ilike(like),
            )
            .order_by(Message.conversation_id, Message.created_at)
        )
        for conv_id, content, _created in mres.all():
            if conv_id in preview_by_id:
                continue  # 첫 매치만 사용 (created_at 오름차순 정렬)
            snippet = _make_preview(content, qs)
            if snippet:
                preview_by_id[conv_id] = snippet

    return [_to_summary(c, preview=preview_by_id.get(c.id)) for c in rows]


@router.get("/{conv_id}", response_model=ConversationDetail)
async def get_conversation(
    conv_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conv = await _load_owned(db, conv_id, current_user, with_messages=True)
    return _to_detail(conv)


@router.post("", response_model=ConversationDetail, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    req: ConversationCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """새 대화 생성. messages 가 있으면 함께 INSERT."""
    conv = Conversation(
        user_id=current_user.id,
        title=req.title,
        domain=UserDomain(req.domain),
    )
    db.add(conv)
    await db.flush()  # conv.id 확보

    for m in req.messages:
        db.add(
            Message(
                conversation_id=conv.id,
                role=m.role,
                content=m.content,
                domain=UserDomain(m.domain) if m.domain else None,
                model=m.model,
            )
        )
    await db.commit()
    # 메시지를 포함해 다시 로드.
    res = await db.execute(
        select(Conversation)
        .options(selectinload(Conversation.messages))
        .where(Conversation.id == conv.id)
    )
    return _to_detail(res.scalar_one())


@router.post(
    "/{conv_id}/messages",
    response_model=MessageOut,
    status_code=status.HTTP_201_CREATED,
)
async def append_message(
    conv_id: int,
    req: MessageCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """메시지 1건 추가. updated_at 은 명시적으로 NOW() 로 갱신해 사이드바 정렬을 보장한다."""
    conv = await _load_owned(db, conv_id, current_user, with_messages=False)
    msg = Message(
        conversation_id=conv.id,
        role=req.role,
        content=req.content,
        domain=UserDomain(req.domain) if req.domain else None,
        model=req.model,
    )
    db.add(msg)
    conv.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(msg)
    return MessageOut(
        id=msg.id,
        role=msg.role,
        content=msg.content,
        domain=(msg.domain.value if msg.domain is not None else None),  # type: ignore[arg-type]
        model=msg.model,
        created_at=msg.created_at,
    )


class ConversationPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    domain: Literal["all", "finance", "sales", "design", "develop"] | None = None
    starred: bool | None = None


@router.patch("/{conv_id}", response_model=ConversationSummary)
async def patch_conversation(
    conv_id: int,
    req: ConversationPatch,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conv = await _load_owned(db, conv_id, current_user, with_messages=False)
    if req.title is not None:
        conv.title = req.title
    if req.domain is not None:
        conv.domain = UserDomain(req.domain)
    if req.starred is not None:
        conv.starred = req.starred
    await db.commit()
    await db.refresh(conv)
    return _to_summary(conv)


@router.delete("/{conv_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conv_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conv = await _load_owned(db, conv_id, current_user, with_messages=False)
    await db.delete(conv)
    await db.commit()


# ---------- 클라이언트 편의: UI 도메인키 매핑 endpoint ---------------------

class UiDomainBody(BaseModel):
    """프론트 5종 키('finance'|'sales'|'dev'|'design'|'normal')를 받기 위한 보조 모델."""

    title: str = Field(min_length=1, max_length=200)
    domain_key: Literal["finance", "sales", "dev", "design", "normal"] = "normal"
    messages: list[MessageCreate] = Field(default_factory=list)


@router.post(
    "/from-ui",
    response_model=ConversationDetail,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation_from_ui(
    req: UiDomainBody,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """UI 도메인 키 그대로 받아 대화를 생성하는 편의 엔드포인트.

    프론트의 mock-recent.ts 항목(`dev`/`other` 등)을 그대로 보낼 수 있어,
    매번 매핑을 신경쓰지 않아도 된다.
    """
    payload = ConversationCreateRequest(
        title=req.title,
        domain=ui_domain_to_db(req.domain_key),
        messages=req.messages,
    )
    return await create_conversation(payload, current_user=current_user, db=db)
