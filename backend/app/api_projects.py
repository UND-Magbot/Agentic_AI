"""프로젝트(대화 그룹화) 라우터 — `/v1/projects/*`.

feature_checklist §B "프로젝트 (대화 그룹화)" 도입.
- GET    /v1/projects                       : 목록(검색·정렬 옵션). 본인 소유만.
- POST   /v1/projects                       : 새 프로젝트.
- GET    /v1/projects/{id}                  : 상세(+소속 대화 요약).
- PATCH  /v1/projects/{id}                  : 이름/설명/instructions/domain/starred 수정.
- DELETE /v1/projects/{id}                  : 삭제(연쇄로 매핑도 정리).
- POST   /v1/projects/{id}/conversations    : 대화 1건을 이 프로젝트에 연결.
- DELETE /v1/projects/{id}/conversations/{conv_id} : 매핑 해제.
"""
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .api_auth import get_current_user
from .api_chat import _to_summary as _conv_to_summary
from .database import get_db
from .models import Conversation, Project, ProjectConversation, User, UserDomain
from .schemas_chat import ConversationSummary, ui_domain_to_db
from .schemas_projects import (
    ProjectAttachConversationRequest,
    ProjectCreateRequest,
    ProjectDetail,
    ProjectPatchRequest,
    ProjectSummary,
    db_domain_to_ui_or_none,
)

router = APIRouter(prefix="/v1/projects", tags=["Projects"])


SortBy = Literal["activity", "name", "created"]


# ---------- 직렬화 헬퍼 ----------------------------------------------------


def _to_summary(p: Project, *, conversation_count: int = 0) -> ProjectSummary:
    domain_value = p.domain.value if p.domain is not None else None
    return ProjectSummary(
        id=p.id,
        name=p.name,
        description=p.description,
        domain=domain_value,  # type: ignore[arg-type]
        domain_key=db_domain_to_ui_or_none(domain_value),
        starred=p.starred,
        conversation_count=conversation_count,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


def _to_detail(
    p: Project,
    *,
    conversations: list[ConversationSummary],
) -> ProjectDetail:
    domain_value = p.domain.value if p.domain is not None else None
    return ProjectDetail(
        id=p.id,
        name=p.name,
        description=p.description,
        domain=domain_value,  # type: ignore[arg-type]
        domain_key=db_domain_to_ui_or_none(domain_value),
        starred=p.starred,
        conversation_count=len(conversations),
        created_at=p.created_at,
        updated_at=p.updated_at,
        system_prompt=p.system_prompt,
        conversations=conversations,
    )


async def _load_owned(db: AsyncSession, project_id: int, user: User) -> Project:
    """본인 소유 프로젝트 1건 로드. 없거나 타인 소유면 404."""
    res = await db.execute(select(Project).where(Project.id == project_id))
    proj = res.scalar_one_or_none()
    if proj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="프로젝트를 찾을 수 없습니다.")
    if user.role.value != "superadmin" and proj.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="프로젝트를 찾을 수 없습니다.")
    return proj


async def _conversation_counts(
    db: AsyncSession, project_ids: list[int]
) -> dict[int, int]:
    """프로젝트별 대화 개수 집계 — N+1 회피용 단일 쿼리."""
    if not project_ids:
        return {}
    res = await db.execute(
        select(
            ProjectConversation.project_id,
            func.count(ProjectConversation.conversation_id),
        )
        .where(ProjectConversation.project_id.in_(project_ids))
        .group_by(ProjectConversation.project_id)
    )
    return {pid: cnt for pid, cnt in res.all()}


# ---------- 라우트 ----------------------------------------------------------


@router.get("", response_model=list[ProjectSummary])
async def list_projects(
    q: str | None = None,
    sort_by: SortBy = "activity",
    limit: int = 100,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """프로젝트 목록.

    - sort_by="activity": updated_at DESC (기본 — 캡처 v1 의 Activity 정렬과 동일)
    - sort_by="name":     name ASC
    - sort_by="created":  created_at DESC
    - q (검색어) 가 있으면 name/description 부분일치(ILIKE).
    - superadmin: 전체 / 그 외: 본인 소유만.
    """
    limit = max(1, min(limit, 500))

    stmt = select(Project)
    if current_user.role.value != "superadmin":
        stmt = stmt.where(Project.user_id == current_user.id)

    qs = (q or "").strip()
    if qs:
        like = f"%{qs}%"
        stmt = stmt.where(
            or_(Project.name.ilike(like), Project.description.ilike(like))
        )

    if sort_by == "name":
        stmt = stmt.order_by(Project.name.asc())
    elif sort_by == "created":
        stmt = stmt.order_by(Project.created_at.desc())
    else:
        # 즐겨찾기를 항상 위로, 그 안에서 최근 활동 순.
        stmt = stmt.order_by(Project.starred.desc(), Project.updated_at.desc())

    stmt = stmt.limit(limit)
    res = await db.execute(stmt)
    rows = res.scalars().all()
    counts = await _conversation_counts(db, [p.id for p in rows])
    return [_to_summary(p, conversation_count=counts.get(p.id, 0)) for p in rows]


@router.post("", response_model=ProjectDetail, status_code=status.HTTP_201_CREATED)
async def create_project(
    req: ProjectCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """새 프로젝트 생성."""
    domain_enum: UserDomain | None = None
    if req.domain_key:
        domain_enum = UserDomain(ui_domain_to_db(req.domain_key))
    proj = Project(
        user_id=current_user.id,
        name=req.name.strip(),
        description=(req.description.strip() if req.description else None) or None,
        system_prompt=(req.system_prompt or None),
        domain=domain_enum,
    )
    db.add(proj)
    await db.commit()
    await db.refresh(proj)
    return _to_detail(proj, conversations=[])


@router.get("/{project_id}", response_model=ProjectDetail)
async def get_project(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    proj = await _load_owned(db, project_id, current_user)
    # 매핑된 대화 요약을 updated_at 내림차순으로.
    res = await db.execute(
        select(Conversation)
        .join(
            ProjectConversation,
            ProjectConversation.conversation_id == Conversation.id,
        )
        .where(ProjectConversation.project_id == proj.id)
        .order_by(Conversation.updated_at.desc())
    )
    convs = res.scalars().all()
    summaries = [_conv_to_summary(c) for c in convs]
    return _to_detail(proj, conversations=summaries)


@router.patch("/{project_id}", response_model=ProjectSummary)
async def patch_project(
    project_id: int,
    req: ProjectPatchRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    proj = await _load_owned(db, project_id, current_user)
    if req.name is not None:
        proj.name = req.name.strip()
    if req.description is not None:
        proj.description = req.description.strip() or None
    if req.system_prompt is not None:
        # 빈 문자열이면 NULL 로 정규화 — Instructions 비우기 동작.
        proj.system_prompt = req.system_prompt.strip() or None
    if req.domain_key is not None:
        # 명시적으로 비우려면 다른 엔드포인트(또는 ""로 받지 않고 별도 처리) 필요 — 1차는 항상 set.
        proj.domain = UserDomain(ui_domain_to_db(req.domain_key))
    if req.starred is not None:
        proj.starred = req.starred
    proj.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(proj)
    counts = await _conversation_counts(db, [proj.id])
    return _to_summary(proj, conversation_count=counts.get(proj.id, 0))


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    proj = await _load_owned(db, project_id, current_user)
    await db.delete(proj)
    await db.commit()


# ---------- 대화 ↔ 프로젝트 매핑 ------------------------------------------


@router.post(
    "/{project_id}/conversations",
    status_code=status.HTTP_201_CREATED,
    response_model=ProjectDetail,
)
async def attach_conversation(
    project_id: int,
    req: ProjectAttachConversationRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """대화 1건을 프로젝트에 연결.

    - 대화/프로젝트 모두 본인 소유여야 한다(superadmin 예외).
    - 한 대화당 한 프로젝트 정책: 다른 프로젝트에 이미 매핑되어 있으면 그 매핑은 제거하고 새로 연결.
    """
    proj = await _load_owned(db, project_id, current_user)

    # 대화 권한 확인.
    cres = await db.execute(select(Conversation).where(Conversation.id == req.conversation_id))
    conv = cres.scalar_one_or_none()
    if conv is None or (
        current_user.role.value != "superadmin" and conv.user_id != current_user.id
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="대화를 찾을 수 없습니다.")

    # 기존 매핑 정리(한 대화당 한 프로젝트 정책).
    await db.execute(
        delete(ProjectConversation).where(
            ProjectConversation.conversation_id == conv.id
        )
    )
    db.add(ProjectConversation(project_id=proj.id, conversation_id=conv.id))
    proj.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(proj)

    # 갱신된 detail 반환 — 클라이언트가 즉시 화면을 갱신할 수 있도록.
    res = await db.execute(
        select(Conversation)
        .join(
            ProjectConversation,
            ProjectConversation.conversation_id == Conversation.id,
        )
        .where(ProjectConversation.project_id == proj.id)
        .order_by(Conversation.updated_at.desc())
    )
    convs = res.scalars().all()
    return _to_detail(proj, conversations=[_conv_to_summary(c) for c in convs])


@router.delete(
    "/{project_id}/conversations/{conv_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def detach_conversation(
    project_id: int,
    conv_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    proj = await _load_owned(db, project_id, current_user)
    res = await db.execute(
        delete(ProjectConversation).where(
            ProjectConversation.project_id == proj.id,
            ProjectConversation.conversation_id == conv_id,
        )
    )
    if res.rowcount == 0:  # type: ignore[attr-defined]
        # 이미 떨어져 있던 매핑이거나 다른 프로젝트의 매핑 — 클라이언트에게는 동일하게 처리.
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="매핑을 찾을 수 없습니다.")
    proj.updated_at = datetime.now(timezone.utc)
    await db.commit()
