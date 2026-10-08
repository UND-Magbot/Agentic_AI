import httpx

from .config import settings


TAVILY_ENDPOINT = "https://api.tavily.com/search"


CONTENT_CHAR_CAP = 800  # 결과 1건당 본문 길이 상한 — 컨텍스트 부담과 정확도의 절충


async def search(query: str, max_results: int = 5) -> dict:
    """Tavily Search 호출. 결과는 LLM 친화 형태({answer, results[]})로 반환."""
    if not settings.tavily_api_key:
        return {
            "error": "TAVILY_API_KEY 미설정. .env 에 키를 넣고 백엔드를 재시작하세요.",
            "results": [],
        }

    payload = {
        "api_key": settings.tavily_api_key,
        "query": query,
        "search_depth": "advanced",
        "include_answer": True,
        "max_results": max_results,
    }

    timeout = httpx.Timeout(connect=5.0, read=20.0, write=5.0, pool=5.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(TAVILY_ENDPOINT, json=payload)
        if resp.status_code != 200:
            return {
                "error": f"웹 검색 오류 {resp.status_code}: {resp.text[:300]}",
                "results": [],
            }
        data = resp.json()
        return {
            "answer": (data.get("answer") or "")[:CONTENT_CHAR_CAP],
            "results": [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "content": (r.get("content", "") or "")[:CONTENT_CHAR_CAP],
                }
                for r in (data.get("results") or [])
            ],
        }
    except httpx.HTTPError as e:
        return {"error": f"웹 검색 호출 실패: {e}", "results": []}
