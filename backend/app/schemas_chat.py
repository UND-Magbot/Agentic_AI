"""대화/메시지 API 용 Pydantic 스키마.

`schemas.py` (LLM 호출용) 와 분리해 책임을 명확히 한다.
"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# DB 측 user_domain ENUM 의 값과 일치해야 한다.
DomainKey = Literal["all", "finance", "sales", "design", "develop"]


class MessageOut(BaseModel):
    """단일 메시지 응답."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    content: str
    domain: DomainKey | None = None
    model: str | None = None
    created_at: datetime


class ConversationSummary(BaseModel):
    """사이드바/검색 등 목록 표시용 요약 스키마.

    프론트는 도메인을 finance/sales/dev/design/other 5종으로 사용한다.
    DB 의 user_domain ENUM 은 finance/sales/develop/design/all 이므로
    `domain_key` 필드로 변환된 값을 동시에 노출한다.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    domain: DomainKey
    domain_key: Literal["finance", "sales", "dev", "design", "normal"] = Field(
        description="프론트에서 쓰는 5종 도메인 키 (DB enum → UI enum 매핑)."
    )
    starred: bool = False
    started_at: datetime
    updated_at: datetime
    preview: str | None = Field(
        default=None,
        description=(
            "검색 결과에서만 채워짐. 메시지 본문 내 매치 위치 ±50자 발췌."
            " title 만 매치된 경우 또는 검색어가 없으면 None."
        ),
    )


class ConversationDetail(ConversationSummary):
    """단건 상세 — 메시지 포함."""

    messages: list[MessageOut]


class ConversationCreateRequest(BaseModel):
    """새 대화 생성 — title/domain/(옵션)초기 메시지."""

    title: str = Field(min_length=1, max_length=200)
    domain: DomainKey = "all"
    messages: list["MessageCreate"] = Field(default_factory=list)


class MessageCreate(BaseModel):
    """메시지 추가 페이로드."""

    role: Literal["user", "assistant", "system"]
    content: str = Field(min_length=1)
    domain: DomainKey | None = None
    model: str | None = None


# forward ref 해결.
ConversationCreateRequest.model_rebuild()


def db_domain_to_ui(value: str) -> Literal["finance", "sales", "dev", "design", "normal"]:
    """DB user_domain ENUM 을 UI 5종 키로 매핑한다.

    매핑: develop → dev, all → normal. 그 외는 동일.
    """
    if value == "develop":
        return "dev"
    if value == "all":
        return "normal"
    if value in ("finance", "sales", "design"):
        return value  # type: ignore[return-value]
    return "normal"


def ui_domain_to_db(value: str) -> Literal["all", "finance", "sales", "design", "develop"]:
    """UI 5종 키 → DB user_domain ENUM. dev → develop, normal → all."""
    if value == "dev":
        return "develop"
    if value == "normal":
        return "all"
    if value in ("finance", "sales", "design"):
        return value  # type: ignore[return-value]
    return "all"
