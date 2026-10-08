"""Codex 브리지(docker-codeserver/codex-bridge) HTTP 클라이언트.

브리지는 `codex exec` 를 감싼 동기 API 라 한 번에 20초~수 분 걸린다. 호출부(main.py fast-path)는
_stream_staged_job 으로 keepalive 를 흘리며 기다린다.

외부로 나가는 모든 호출은 _post 에서 external_gateway 를 거친다(고객사명·지역·제품·인명 가명, 금액·연락처 제거,
가린 본문만 external_calls 에 기록). 가림 단계가 실패하면 보내지 않는다.
"""
from __future__ import annotations

import base64
from typing import Any

import httpx

from . import external_gateway
from .config import settings

# 브리지 JOB_TIMEOUT_SEC(기본 300초) + 대기열 여유.
_TIMEOUT = httpx.Timeout(connect=5.0, read=420.0, write=10.0, pool=5.0)


class CodexError(Exception):
    """브리지 호출 실패. 메시지는 사용자에게 그대로 보여도 되는 한국어 문구."""


def is_configured() -> bool:
    return bool(settings.codex_bridge_url and settings.codex_bridge_token)


def _plain(msg: Any) -> str:
    """브리지 오류 문구의 내부 이름(Codex·codex-bridge)을 사용자 말로 바꾼다 — 화면에 그대로 나간다."""
    return (str(msg or "").replace("codex-bridge", "AI 연결 서버").replace("Codex 가 ", "외부 AI 가 ")
            .replace("Codex ", "외부 AI ").replace("Codex", "외부 AI"))


async def _post(path: str, payload: dict[str, Any], user_id: int | None = None) -> dict[str, Any]:
    if not is_configured():
        raise CodexError("외부 AI 연결이 설정되지 않았습니다(관리자: CODEX_BRIDGE_URL/CODEX_BRIDGE_TOKEN).")
    try:
        payload, restore = await external_gateway.outbound(payload, endpoint=path, user_id=user_id)
    except external_gateway.GatewayError as e:
        raise CodexError(str(e)) from e
    headers = {"Authorization": f"Bearer {settings.codex_bridge_token}"}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(f"{settings.codex_bridge_url}{path}", json=payload, headers=headers)
    except httpx.HTTPError as e:
        raise CodexError(f"외부 AI 에 연결할 수 없습니다: {e.__class__.__name__}") from e
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.status_code != 200:
        raise CodexError(_plain(data.get("error")) or f"외부 AI 오류 (HTTP {resp.status_code})")
    return restore(data)


async def search(query: str, *, user_id: int | None = None) -> dict[str, Any]:
    """웹 검색. tavily_client.search 와 같은 {answer, results[{title,url,content}]} 형태."""
    data = await _post("/v1/search", {"query": query}, user_id)
    return {"answer": data.get("answer") or "", "results": data.get("results") or []}


async def ask(question: str, context: str = "", *, user_id: int | None = None) -> str:
    """심층 분석 위임. context 는 사내 자료 발췌(선택). user_id 는 전송 기록(external_calls)의 요청자."""
    data = await _post("/v1/ask", {"question": question, "context": context}, user_id)
    return data.get("answer") or ""


async def generate_image(prompt: str, *, user_id: int | None = None) -> tuple[bytes, str, str]:
    """이미지 생성 → (PNG bytes, mime, 한 줄 설명)."""
    image, mime, description, _used = await generate_image_with_refs(prompt, [], user_id=user_id)
    return image, mime, description


async def generate_image_with_refs(
    prompt: str, refs: list[tuple[bytes, str]], *, user_id: int | None = None,
) -> tuple[bytes, str, str, int]:
    """참고 이미지(외형 참조)를 함께 보내는 이미지 생성 → (PNG, mime, 설명, 브리지가 반영한 참고 이미지 수).

    브리지(d6)는 모르는 필드를 무시하므로, 응답의 reference_images_used 가 없으면 반영 0장으로 본다.
    본문 한도(413)로 거부되면 참고 이미지 없이 한 번 더 보낸다 — 이때도 반영 0장.
    """
    payload: dict[str, Any] = {"prompt": prompt}
    if refs:
        payload["reference_images"] = [{"image_base64": base64.b64encode(b).decode(), "mime": m} for b, m in refs]
    try:
        data = await _post("/v1/image", payload, user_id)
    except CodexError:
        if not refs:
            raise
        data = await _post("/v1/image", {"prompt": prompt}, user_id)
    try:
        image = base64.b64decode(data["image_base64"], validate=True)
    except (KeyError, ValueError) as e:
        raise CodexError("외부 AI 가 올바른 이미지를 돌려주지 않았습니다.") from e
    used = data.get("reference_images_used")
    return (image, data.get("mime") or "image/png", data.get("description") or "",
            int(used) if isinstance(used, int) else 0)
