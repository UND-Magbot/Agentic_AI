"""외부 웹 검색 진입점 — SEARCH_PROVIDER(codex|tavily)에 따라 제공자를 고른다.

Codex 가 실패(인증 만료·브리지 다운 등)하면 Tavily 키가 있을 때만 Tavily 로 폴백한다.
반환 형태는 제공자와 무관하게 {answer, results[{title,url,content}]} (실패 시 error 포함).
"""
from __future__ import annotations

import logging
from typing import Any

from . import codex_client, tavily_client
from .config import settings

_log = logging.getLogger("web_search")


async def search(query: str, *, user_id: int | None = None) -> dict[str, Any]:
    """user_id 는 외부 전송 기록(external_calls)의 요청자."""
    if settings.search_provider == "codex" and codex_client.is_configured():
        try:
            return {**await codex_client.search(query, user_id=user_id), "provider": "codex"}
        except codex_client.CodexError as e:
            _log.warning("[web_search] codex 실패: %s", e)
            if not settings.tavily_api_key:
                return {"error": str(e), "results": [], "provider": "codex"}
    return {**await tavily_client.search(query), "provider": "tavily"}
