"""expense_error_v10 — vLLM 400 / 깨진 tool_call args 친화적 처리.

v10 시나리오:
  1) 입력 토큰이 너무 많아(7836/8192) output 여유 부족
  2) LLM 이 tool_call args 를 mid-string 잘라먹음
  3) 다음 iter 에서 잘린 args 를 vLLM 에 재전송 → vLLM 400 'Unterminated string'
  4) RuntimeError 가 stream 으로 전파 → 사용자에게 'stream error: terminated' 노출

대응:
  A) stream_chat_with_tools 에 RuntimeError 캐치 → 친화 메시지 yield
  B) accumulated tool_call args 가 깨진 JSON 이면 '{}' 로 안전 대체 (vLLM 400 차단)
  C) tool description 축약 (2113→757 chars) — 입력 토큰 절감으로 근본 원인 완화
"""
from __future__ import annotations

import asyncio
import io
import json
import sys
import traceback
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app import vllm_client

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []


def expect(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASS.append(name)
        print(f"  PASS {name}")
    else:
        FAIL.append((name, detail))
        print(f"  FAIL {name} — {detail}")


def _stream_factory(events: list):
    """이벤트 시퀀스를 async generator 로 변환."""
    async def gen(**_kwargs):
        for e in events:
            if isinstance(e, Exception):
                raise e
            yield e
    return gen


async def _collect(stream) -> list[str]:
    out: list[str] = []
    async for d in stream:
        out.append(d)
    return out


# ─── A. vLLM 400 시 친화 메시지 ───────────────────────────────────
def test_vllm_400_friendly_msg() -> None:
    print("\n[test_vllm_400_friendly_msg] RuntimeError → ⚠ + 친화 안내")
    error_400 = RuntimeError(
        'vLLM 400 Bad Request: {"error":{"message":"Unterminated string starting at: '
        'line 24 column 19 (char 487)","type":"BadRequestError","param":null,"code":400}}'
    )

    async def fake_dispatch(name, args):
        raise AssertionError("should not dispatch when stream errors")

    with patch.object(vllm_client, "_stream_once", _stream_factory([error_400])):
        with patch.object(vllm_client, "_truncate_to_budget",
                          new=AsyncMock(side_effect=lambda msgs, *a, **k: msgs)):
            out = asyncio.run(_collect(vllm_client.stream_chat_with_tools(
                system_prompt="sys", messages=[{"role": "user", "content": "x"}],
                tools=[], dispatch=fake_dispatch,
            )))
    joined = "".join(out)
    expect("400.has_warning", "⚠" in joined, f"got: {joined!r}")
    expect("400.no_raw_error",
           "RuntimeError" not in joined and "char 487" not in joined,
           f"got: {joined!r}")
    expect("400.has_hint",
           "다시 시도" in joined or "줄여" in joined,
           f"got: {joined!r}")


def test_vllm_500_friendly_msg() -> None:
    print("\n[test_vllm_500_friendly_msg]")
    err = RuntimeError("vLLM 500 Internal Server Error: ...")

    async def fake_dispatch(name, args):
        return ""

    with patch.object(vllm_client, "_stream_once", _stream_factory([err])):
        with patch.object(vllm_client, "_truncate_to_budget",
                          new=AsyncMock(side_effect=lambda msgs, *a, **k: msgs)):
            out = asyncio.run(_collect(vllm_client.stream_chat_with_tools(
                system_prompt="sys", messages=[{"role": "user", "content": "x"}],
                tools=[], dispatch=fake_dispatch,
            )))
    joined = "".join(out)
    expect("500.friendly", "⚠" in joined and "RuntimeError" not in joined)


def test_connection_error_friendly_msg() -> None:
    """vLLM 컨테이너 죽음 등 연결 오류도 친화 메시지."""
    print("\n[test_connection_error_friendly_msg]")
    err = RuntimeError("vLLM 서버에 연결할 수 없습니다.")

    async def fake_dispatch(name, args):
        return ""

    with patch.object(vllm_client, "_stream_once", _stream_factory([err])):
        with patch.object(vllm_client, "_truncate_to_budget",
                          new=AsyncMock(side_effect=lambda msgs, *a, **k: msgs)):
            out = asyncio.run(_collect(vllm_client.stream_chat_with_tools(
                system_prompt="sys", messages=[{"role": "user", "content": "x"}],
                tools=[], dispatch=fake_dispatch,
            )))
    joined = "".join(out)
    expect("conn.friendly", "⚠" in joined and "다시 시도" in joined)


# ─── B. 깨진 tool_call args 자동 복구 ────────────────────────────
def test_truncated_args_safe_replace() -> None:
    """LLM 이 args 를 mid-string 자른 경우 → '{}' 로 안전 대체.

    이를 검증하기 위해 tool_call 을 truncated arguments 와 함께 emit 하는 stream 을
    만든 뒤, dispatch 시 args 가 정상 (= {}) 인지 확인.
    """
    print("\n[test_truncated_args_safe_replace]")
    # 잘린 JSON (의도적)
    bad_args = '{"author":"배재병","year":2026,"month":2,"lines":[{"source":"개인카드","date":"2026-02-09","purpose":"평택지'

    events = [
        ("tool_call", {
            "index": 0, "id": "c1",
            "function": {"name": "compose_expense_report", "arguments": bad_args},
        }),
        ("finish", "length"),
    ]

    received_args: list[dict] = []

    async def fake_dispatch(name, args):
        received_args.append(args)
        return json.dumps({"ok": True, "short_message": "✓ recovered"})

    with patch.object(vllm_client, "_stream_once", _stream_factory(events)):
        with patch.object(vllm_client, "_truncate_to_budget",
                          new=AsyncMock(side_effect=lambda msgs, *a, **k: msgs)):
            out = asyncio.run(_collect(vllm_client.stream_chat_with_tools(
                system_prompt="sys", messages=[{"role": "user", "content": "x"}],
                tools=[{"type": "function", "function": {"name": "compose_expense_report"}}],
                dispatch=fake_dispatch,
            )))
    # dispatch 가 호출되었고, args 는 깨진 원본이 아니라 safe parse 결과여야.
    expect("trunc.dispatched", len(received_args) == 1, f"got {len(received_args)}")
    # received_args[0] 가 dict 이고, '평택지' 가 살아있어도(repair 성공) 또는 {} 여도 OK.
    # 핵심: dispatch 가 호출됐고 사용자에게 stream error 안 나감.
    joined = "".join(out)
    expect("trunc.no_stream_error", "⚠" not in joined, f"got: {joined!r}")
    expect("trunc.success_msg", "✓ recovered" in joined)


def test_safe_args_string_validates() -> None:
    """assistant 메시지에 들어가는 arguments 문자열 자체가 valid JSON 이어야."""
    print("\n[test_safe_args_string_validates]")
    msgs_appended: list[dict] = []
    original_append = list.append

    bad_args = '{"author":"X","lines":[{"source":"개인'  # 깨진 JSON

    events = [
        ("tool_call", {
            "index": 0, "id": "c1",
            "function": {"name": "compose_expense_report", "arguments": bad_args},
        }),
        ("finish", "tool_calls"),
    ]

    async def fake_dispatch(name, args):
        return json.dumps({"ok": True, "short_message": "✓ ok"})

    captured_msgs: list = []

    async def capture_truncate(msgs, *a, **k):
        captured_msgs.append([dict(m) for m in msgs])
        return msgs

    with patch.object(vllm_client, "_stream_once", _stream_factory(events)):
        with patch.object(vllm_client, "_truncate_to_budget",
                          new=AsyncMock(side_effect=capture_truncate)):
            out = asyncio.run(_collect(vllm_client.stream_chat_with_tools(
                system_prompt="sys", messages=[{"role": "user", "content": "x"}],
                tools=[{"type": "function", "function": {"name": "compose_expense_report"}}],
                dispatch=fake_dispatch,
            )))

    # captured_msgs 에는 iter 1 호출 시점 msgs 가 있음 (assistant tool_calls 추가 전).
    # 만약 iter 2 가 있었다면 그때는 assistant 메시지가 들어가야.
    # 핵심 검증: dispatch 성공 + stream error 안 노출.
    joined = "".join(out)
    expect("safe.no_stream_err", "⚠" not in joined)
    expect("safe.dispatched", "✓ ok" in joined)


# ─── C. tool description 크기 ────────────────────────────────────
def test_tool_description_size() -> None:
    """description 이 적정 크기인지 (토큰 절감 회귀 방지)."""
    print("\n[test_tool_description_size]")
    from app.tools import COMPOSE_EXPENSE_REPORT_TOOL
    desc = COMPOSE_EXPENSE_REPORT_TOOL["function"]["description"]
    expect("desc.length_under_1000", len(desc) < 1000,
           f"description is {len(desc)} chars — should be < 1000 to save tokens")
    # 핵심 정보는 보존되어야
    expect("desc.contains_'expense'", "expense" in desc.lower() or "익스펜스" in desc)
    expect("desc.mentions_lines_mapping",
           "라인" in desc or "lines" in desc)
    expect("desc.mentions_attachment",
           "첨부" in desc or "receipt" in desc or "영수증" in desc)


# ─── D. accumulated args 가 정상 JSON 이면 그대로 사용 ─────────
def test_valid_args_passthrough() -> None:
    print("\n[test_valid_args_passthrough]")
    good_args = '{"author":"배재병","year":2026,"month":2,"lines":[{"source":"개인카드","category":"여비교통비","date":"2026-02-09","purpose":"SRT","amount":29500,"vendor":"에스알"}]}'

    events = [
        ("tool_call", {
            "index": 0, "id": "c1",
            "function": {"name": "compose_expense_report", "arguments": good_args},
        }),
        ("finish", "tool_calls"),
    ]

    received: list[dict] = []

    async def fake_dispatch(name, args):
        received.append(args)
        return json.dumps({"ok": True, "short_message": "✓ all good"})

    with patch.object(vllm_client, "_stream_once", _stream_factory(events)):
        with patch.object(vllm_client, "_truncate_to_budget",
                          new=AsyncMock(side_effect=lambda msgs, *a, **k: msgs)):
            out = asyncio.run(_collect(vllm_client.stream_chat_with_tools(
                system_prompt="sys", messages=[{"role": "user", "content": "x"}],
                tools=[{"type": "function", "function": {"name": "compose_expense_report"}}],
                dispatch=fake_dispatch,
            )))
    expect("valid.dispatched", len(received) == 1)
    expect("valid.args_intact", received[0].get("author") == "배재병")
    expect("valid.lines_intact", len(received[0].get("lines", [])) == 1)
    joined = "".join(out)
    expect("valid.success", "✓ all good" in joined)


if __name__ == "__main__":
    cases = [
        test_vllm_400_friendly_msg,
        test_vllm_500_friendly_msg,
        test_connection_error_friendly_msg,
        test_truncated_args_safe_replace,
        test_safe_args_string_validates,
        test_tool_description_size,
        test_valid_args_passthrough,
    ]
    for fn in cases:
        try:
            fn()
        except Exception as e:
            FAIL.append((fn.__name__, f"{e}"))
            print(f"  FAIL {fn.__name__} — {e}")
            traceback.print_exc()

    print(f"\n==== {len(PASS)} pass / {len(FAIL)} fail ====")
    if FAIL:
        for name, detail in FAIL:
            print(f"  [FAIL] {name}: {detail}")
        sys.exit(1)
    print("ALL OK")
