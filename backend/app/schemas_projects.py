"""프로젝트(대화 그룹화) API 용 Pydantic 스키마.

`schemas_chat.py` (대화/메시지) 와 분리해 책임을 명확히 한다.
"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .schemas_chat import (
    ConversationSummary,
    DomainKey,
    db_domain_to_ui,
)

UiDomainKey = Literal["finance", "sales", "dev", "design", "normal"]


class ProjectSummary(BaseModel):
    """목록(카드 그리드)용 요약 스키마."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None = None
    domain: DomainKey | None = None
    domain_key: UiDomainKey | None = Field(
        default=None,
        description="DB user_domain → UI 5종 키 매핑. domain 이 NULL 이면 None.",
    )
    starred: bool = False
    conversation_count: int = 0
    created_at: datetime
    updated_at: datetime


class ProjectDetail(ProjectSummary):
    """상세 페이지용 — system_prompt + 소속 대화 목록 포함."""

    system_prompt: str | None = None
    conversations: list[ConversationSummary] = Field(default_factory=list)


class ProjectCreateRequest(BaseModel):
    """새 프로젝트 생성."""

    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    system_prompt: str | None = None
    domain_key: UiDomainKey | None = None


class ProjectPatchRequest(BaseModel):
    """수정 — 일부 필드만 갱신. None 은 변경 없음."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    system_prompt: str | None = None
    domain_key: UiDomainKey | None = None
    starred: bool | None = None


class ProjectAttachConversationRequest(BaseModel):
    """대화를 프로젝트에 연결."""

    conversation_id: int


def db_domain_to_ui_or_none(value: str | None) -> UiDomainKey | None:
    if value is None:
        return None
    return db_domain_to_ui(value)
