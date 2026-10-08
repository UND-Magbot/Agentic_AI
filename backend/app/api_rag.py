"""RAG 검색 API — `/v1/rag/*`.

프론트엔드는 사용자 질의 시 이 엔드포인트로 먼저 검색해 chunk + 출처 메타데이터를
받아 system prompt 에 부착한 뒤 vLLM 호출. 응답에 같은 sources 를 함께 반환해 UI 가
출처 칩으로 렌더할 수 있게 한다.
"""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from .api_auth import get_current_user
from .database import get_db
from .models import User
from .rag import RetrievedChunk, build_context_block, search

router = APIRouter(prefix="/v1/rag", tags=["RAG"])


class RagSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=5, ge=1, le=20)
    domain: str | None = Field(
        default=None,
        description="user_domain 필터 (all/finance/sales/design/develop). None=전체.",
    )
    min_score: float = Field(default=0.3, ge=0.0, le=1.0)


class RagSource(BaseModel):
    id: int
    source_path: str
    source_label: str
    score: float
    snippet: str
    metadata: dict[str, Any]


class RagSearchResponse(BaseModel):
    """검색 결과. context_block 은 system prompt 에 그대로 붙일 수 있는 텍스트.
    sources 는 UI 출처 칩 렌더용 메타데이터 배열.
    """

    sources: list[RagSource]
    context_block: str


def _to_source(c: RetrievedChunk, *, snippet_limit: int = 240) -> RagSource:
    snippet = c.content.strip().replace("\n", " ")
    if len(snippet) > snippet_limit:
        snippet = snippet[:snippet_limit].rstrip() + "…"
    return RagSource(
        id=c.id,
        source_path=c.source_path,
        source_label=c.source_label,
        score=round(c.score, 4),
        snippet=snippet,
        metadata=c.metadata,
    )


@router.post("/search", response_model=RagSearchResponse)
async def rag_search(
    req: RagSearchRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """본인 인증된 사용자만 호출 가능. 도메인 ACL 은 1차에선 미적용 — 사칙은 전사 공통."""
    try:
        chunks = await search(
            db,
            req.query,
            top_k=req.top_k,
            domain=req.domain,
            min_score=req.min_score,
        )
    except Exception as e:  # 임베딩 서비스 미가용 등.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"검색 서비스를 사용할 수 없습니다: {e}",
        )
    return RagSearchResponse(
        sources=[_to_source(c) for c in chunks],
        context_block=build_context_block(chunks, query=req.query),
    )
