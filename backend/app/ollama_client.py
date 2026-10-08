"""Ollama LLM 클라이언트 — vllm_client 와 **동일한 공개 인터페이스**.

`stream_chat` / `stream_chat_with_tools` 시그니처·yield 계약을 vllm_client 와 1:1로 맞춰,
호출부(main.py·eval·meeting_summarizer)는 변경 없이 `llm.py` facade 로 갈아끼우면 된다
(req.md 원칙1 — 원본 동작 보존).

Qwen(vLLM) 대비 차이:
  - 중국어 드리프트 방어막(logit_bias CJK / guided_regex / 중국어 stop 마커) **제거**.
    Gemma 3 는 한국어 드리프트가 없어 불필요 → 코드 단순화.
  - Ollama 네이티브 `/api/chat` 사용(NDJSON 스트림). tool_calls 의 arguments 는 이미 dict.
  - tool_choice 강제(named function)는 Ollama API 가 미지원 → forced 케이스는 main.py 의
    결정론적 fast-path(expense·meeting, 그리고 권장: 연차메일)로 처리한다. 여기서는 auto 만.

엔드포인트: {OLLAMA_BASE_URL}/api/chat  (기본 http://localhost:11434)
모델: settings.ollama_model (기본 gemma4:12b)
"""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable

import httpx

from .config import settings

_log = logging.getLogger("ollama_client")

MAX_TOOL_ITERATIONS = 4

# 연차 메일 도구 사용 규칙 — vllm_client._MAIL_GUARD 와 동일 취지(behavior 보존).
# Ollama 는 tool_choice 강제가 없어 프롬프트 가드의 역할이 더 크다.
_MAIL_GUARD = (
    "\n\n[연차 메일 도구 사용 규칙 — 반드시 준수]\n"
    "1) 사용자가 연차/휴가 메일을 '작성/만들/초안/양식' 으로 요청하면 자연어로 본문을 "
    "직접 쓰지 말고 즉시 compose_leave_email 도구를 호출한다.\n"
    "2) 사용자가 '발송/보내/전송/쏴' 등 발송 의도를 표현하면 즉시 send_leave_email 도구를 호출한다.\n"
    "3) 도구 호출 없이 '메일을 발송했습니다' 같은 종결 문장을 생성하는 것은 거짓 보고이므로 금지.\n"
    "4) send_leave_email 인자는 직전 compose_leave_email 과 동일하게 채운다(subject/body 제외 — 서버가 재생성).\n"
    "5) 도구 결과의 short_message 를 그대로 출력하고 추가 종결 문장·안내를 덧붙이지 않는다.\n"
)


def _chat_url() -> str:
    return f"{settings.ollama_base_url}/api/chat"


# 사고(thinking) 기능이 없는 모델 — think 필드를 보내면 Ollama 가 거부한다.
_NO_THINK_MODELS = ("gemma3",)


def no_think(payload: dict) -> dict:
    """공용 채팅은 사고 모드를 끈다. gemma4 는 think 를 생략하면 사고 모드가 켜져
    답이 느려지고 생성 한도를 사고에 다 쓰기도 한다(2026-09-28 공용 모델 gemma4:12b 전환)."""
    if not str(payload.get("model", "")).startswith(_NO_THINK_MODELS):
        payload["think"] = False
    return payload


def _options(temperature: float | None, top_p: float | None, seed: int | None) -> dict:
    """Ollama sampling options. None 이면 키 생략(모델/서버 기본값 사용).

    num_ctx 는 항상 settings.ollama_num_ctx — 제안서·OCR 과 같아야 모델 재적재가 없다.
    """
    opts: dict = {"num_ctx": settings.ollama_num_ctx}
    if temperature is not None:
        opts["temperature"] = temperature
    if top_p is not None:
        opts["top_p"] = top_p
    if seed is not None:
        opts["seed"] = seed
    return opts


async def stream_chat(
    *,
    system_prompt: str,
    messages: list[dict],
    temperature: float | None = None,
    top_p: float | None = None,
    seed: int | None = None,
) -> AsyncGenerator[str, None]:
    """도구 없이 단순 스트리밍. vllm_client.stream_chat 와 동일 계약(content delta yield)."""
    payload = no_think({
        "model": settings.ollama_model,
        "messages": [{"role": "system", "content": system_prompt}, *messages],
        "stream": True,
        "options": _options(temperature, top_p, seed),
    })
    # 긴 생성에 read timeout 없음(요약 등 수십초 가능). connect 만 짧게.
    timeout = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", _chat_url(), json=payload) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode("utf-8", "replace")[:300]
                raise RuntimeError(f"사내 AI 서버 오류 {resp.status_code}: {body}")
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                delta = (obj.get("message") or {}).get("content") or ""
                if delta:
                    yield delta
                if obj.get("done"):
                    break


async def _chat_collect(
    msgs: list[dict],
    tools: list[dict] | None,
    *,
    temperature: float | None,
    top_p: float | None,
    seed: int | None,
) -> AsyncGenerator[tuple[str, object], None]:
    """한 번의 /api/chat 스트림을 소비.

    yield 형식:
      ("content", str)     — 본문 delta(실시간)
      ("tool_calls", list) — 스트림 종료 시 누적된 tool_calls(있을 때만, 1회)
    Ollama tool_calls 의 arguments 는 이미 dict.
    """
    payload: dict = no_think({
        "model": settings.ollama_model,
        "messages": msgs,
        "stream": True,
        "options": _options(temperature, top_p, seed),
    })
    if tools:
        payload["tools"] = tools
    timeout = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)
    collected_calls: list[dict] = []
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", _chat_url(), json=payload) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode("utf-8", "replace")[:300]
                raise RuntimeError(f"사내 AI 서버 오류 {resp.status_code}: {body}")
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = obj.get("message") or {}
                content = msg.get("content") or ""
                if content:
                    yield ("content", content)
                tcs = msg.get("tool_calls") or []
                if tcs:
                    collected_calls.extend(tcs)
                if obj.get("done"):
                    break
    if collected_calls:
        yield ("tool_calls", collected_calls)


def _short_message_from(result: str) -> str:
    try:
        parsed = json.loads(result)
    except json.JSONDecodeError:
        return result
    return parsed.get("short_message") or parsed.get("detail") or parsed.get("error") or ""


async def stream_chat_with_tools(
    *,
    system_prompt: str,
    messages: list[dict],
    tools: list[dict],
    dispatch: Callable[[str, dict], Awaitable[str]],
    temperature: float | None = None,
    top_p: float | None = None,
    seed: int | None = None,
    tool_choice: str | dict | None = None,
) -> AsyncGenerator[str, None]:
    """Ollama/Gemma3 는 function calling(tools)을 지원하지 않는다
    (HTTP 400: "<model> does not support tools"). 이 앱에서 도구를 쓰던 흐름 중
    expense·연차메일은 이미 main.py 의 결정론 fast-path 가 LLM 을 우회해 처리하므로,
    여기서는 tools 를 Ollama 에 전달하지 않고 본문만 스트리밍한다.

    영향: LLM 자동호출이 필요했던 search_web(외부 실시간 검색)만 동작하지 않는다(설계 결정).
    사내 자료 질의는 RAG(임베딩)로 system_prompt 에 이미 주입되므로 영향 없다.

    tools/dispatch/tool_choice 인자는 vllm_client 와의 시그니처 호환을 위해 받기만 한다.
    도구 루프 헬퍼(_chat_collect/_MAIL_GUARD/_short_message_from)는 추후 tools 를
    지원하는 Ollama 모델로 교체할 때 되살릴 수 있도록 보존한다.
    """
    if tool_choice is not None:
        _log.info(
            "[ollama] tool_choice=%s 무시 — gemma3 tools 미지원. 강제 도구 흐름은 main.py fast-path 가 처리.",
            tool_choice,
        )
    if tools:
        _log.info(
            "[ollama] tool 정의 %d개를 Ollama 에 전달하지 않음(gemma3 미지원). "
            "search_web 자동호출 불가 — 외부 실시간 검색 비활성.",
            len(tools),
        )

    # gemma3 는 tools 를 거부하므로 일반 스트리밍과 동일하게 본문만 생성한다.
    async for delta in stream_chat(
        system_prompt=system_prompt,
        messages=messages,
        temperature=temperature,
        top_p=top_p,
        seed=seed,
    ):
        yield delta
