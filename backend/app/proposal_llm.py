"""제안서 기능 전용 LLM 호출.

사내 공용 모델(settings.ollama_model)과 분리해 제안서만 상위 모델(settings.proposal_model)을 쓴다.
제안서는 속도보다 품질이 우선이므로:
  - 사고(thinking) 모드를 켜고, 응답 대기 시간에 상한을 두지 않는다.
  - GPU 메모리 부족·모델 교체 중·연결 끊김 같은 일시 오류는 사용자에게 올리지 않고
    간격을 늘려 가며 재시도한다. 재시도 사실은 로그에만 남긴다.
  - 없는 모델(404)·잘못된 요청(4xx)은 재시도해도 소용없으므로 바로 알린다.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

from .config import settings

_log = logging.getLogger("proposal_llm")

# 응답 생성(read)에는 상한을 두지 않는다. 사고 모드 + 긴 제안서는 수 분이 걸릴 수 있다.
_TIMEOUT = httpx.Timeout(connect=10.0, read=None, write=30.0, pool=None)
# 일시 오류 재시도 간격(초). 합계 약 4분 — 다른 직원의 요청이 GPU 를 쓰고 있어도 기다린다.
RETRY_DELAYS: tuple[float, ...] = (5, 15, 30, 60, 120)
# 사고 모드로 컨텍스트가 모자랄 때 넓힐 상한. 26b 는 32K 에서 일부가 CPU 로 넘어가 느려지지만 동작한다.
MAX_CTX = 32768


class ProposalLLMError(RuntimeError):
    """재시도로 해결되지 않는 오류(모델 없음, 잘못된 요청, 재시도 한도 초과)."""


def _is_transient(status: int) -> bool:
    return status >= 500 or status == 429


async def chat(
    messages: list[dict[str, Any]],
    *,
    fmt: str | dict | None = "json",
    think: bool | None = None,
    num_ctx: int | None = None,
    num_predict: int = 12000,
    temperature: float = 0.3,
    model: str | None = None,
) -> str:
    """제안서 모델로 대화 1회를 보내고 본문(content)을 돌려준다.

    Args:
        messages: Ollama chat 형식 메시지. 이미지는 각 메시지의 "images"(base64) 로 넣는다.
        fmt: "json" 또는 JSON 스키마. None 이면 자유 텍스트.
        think: 사고 모드. None 이면 settings.proposal_think 를 따른다.
        num_ctx: 무시한다 — 컨텍스트는 settings.ollama_num_ctx 하나로 통일한다(요청마다 다르면
            Ollama 가 모델을 다시 올린다). 호출부 호환을 위해 인자만 받는다.
        model: 이 호출만 다른 사내 모델로(없으면 settings.proposal_model).

    Raises:
        ProposalLLMError: 재시도로 해결되지 않는 오류.
    """
    use_think = settings.proposal_think if think is None else think
    payload: dict[str, Any] = {
        "model": model or settings.proposal_model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature, "num_ctx": settings.ollama_num_ctx,
                    "num_predict": num_predict},
    }
    if fmt is not None:
        payload["format"] = fmt
    # 항상 명시한다 — gemma4 는 think 필드가 없으면 사고 모드를 기본으로 켠다(실측).
    payload["think"] = bool(use_think)
    url = f"{settings.ollama_base_url.rstrip('/')}/api/chat"

    last = ""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for attempt in range(len(RETRY_DELAYS) + 1):
            try:
                res = await client.post(url, json=payload)
            except httpx.TransportError as e:            # 서버 재시작·네트워크 순단
                last = f"연결 오류: {e!r}"
            else:
                if res.status_code == 200:
                    j = res.json()
                    content = (j.get("message") or {}).get("content", "")
                    opts = payload["options"]
                    if not content.strip() and j.get("done_reason") == "length":
                        predict_used = (j.get("eval_count") or 0) >= opts["num_predict"] * 0.95
                        if not predict_used and opts["num_ctx"] < MAX_CTX:
                            # 사고가 길어져 답을 쓰기 전에 컨텍스트가 찼다 — 넓혀서 다시 생성한다.
                            opts["num_ctx"] = min(opts["num_ctx"] * 2, MAX_CTX)
                            _log.warning("proposal_llm 빈 응답(컨텍스트 한도) → num_ctx %d 로 재시도", opts["num_ctx"])
                            continue
                        if payload.get("think"):
                            # 생각이 끝나지 않는다(생성 한도 소진) — 공간을 늘려도 소용없으니 답만 받는다.
                            payload["think"] = False
                            _log.warning("proposal_llm 사고가 끝나지 않음 → 사고 모드 끄고 재시도")
                            continue
                    if attempt:
                        _log.info("proposal_llm 재시도 %d회 만에 성공", attempt)
                    return content
                body = res.text[:300]
                if "does not support thinking" in body and payload.get("think"):
                    # 사고 모드 미지원 모델로 바꿔 둔 경우 — 끄고 즉시 다시 보낸다.
                    _log.warning("proposal_llm: %s 는 사고 모드 미지원 → 끄고 진행", payload["model"])
                    payload.pop("think")
                    continue
                if not _is_transient(res.status_code):
                    raise ProposalLLMError(f"제안서 모델 호출 실패({res.status_code}): {body}")
                last = f"{res.status_code}: {body}"

            if attempt == len(RETRY_DELAYS):
                break
            delay = RETRY_DELAYS[attempt]
            _log.warning("proposal_llm 일시 오류 → %ss 후 재시도 (%d/%d): %s",
                         delay, attempt + 1, len(RETRY_DELAYS), last)
            await asyncio.sleep(delay)

    raise ProposalLLMError(f"제안서 모델이 계속 응답하지 않습니다. 마지막 오류: {last}")


async def chat_stream(
    messages: list[dict[str, Any]],
    *,
    num_predict: int = 1200,
    temperature: float = 0.3,
    model: str | None = None,
):
    """대화 1회를 스트리밍으로 — 본문(content) 조각을 차례로 돌려준다(화면에 글자 단위로 흘려보내기용).
    사고 모드는 끈다(gemma 사고 글은 영어로 나와서, 보여 줄 생각 과정은 본문에 한국어로 쓰게 한다 — atc_agent).
    첫 조각 전의 일시 오류(연결·5xx)만 재시도하고, 흘러나오기 시작한 뒤의 오류는 그대로 올린다.

    Raises:
        ProposalLLMError: 재시도로 해결되지 않는 오류.
    """
    payload: dict[str, Any] = {
        "model": model or settings.proposal_model, "messages": messages, "stream": True, "think": False,
        "options": {"temperature": temperature, "num_ctx": settings.ollama_num_ctx, "num_predict": num_predict},
    }
    url = f"{settings.ollama_base_url.rstrip('/')}/api/chat"
    last = ""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for attempt in range(len(RETRY_DELAYS) + 1):
            try:
                async with client.stream("POST", url, json=payload) as res:
                    if res.status_code == 200:
                        async for line in res.aiter_lines():
                            if not line.strip():
                                continue
                            j = json.loads(line)
                            piece = (j.get("message") or {}).get("content") or ""
                            if piece:
                                yield piece
                            if j.get("done"):
                                return
                        return
                    body = (await res.aread()).decode("utf-8", "replace")[:300]
                    if not _is_transient(res.status_code):
                        raise ProposalLLMError(f"사내 모델 호출 실패({res.status_code}): {body}")
                    last = f"{res.status_code}: {body}"
            except httpx.TransportError as e:
                last = f"연결 오류: {e!r}"
            if attempt == len(RETRY_DELAYS):
                break
            delay = RETRY_DELAYS[attempt]
            _log.warning("proposal_llm.chat_stream 일시 오류 → %ss 후 재시도 (%d/%d): %s", delay, attempt + 1, len(RETRY_DELAYS), last)
            await asyncio.sleep(delay)
    raise ProposalLLMError(f"사내 모델이 계속 응답하지 않습니다. 마지막 오류: {last}")
