"""vllm_client 의 compose_expense_report 재시도 루프 검증.

LLM 이 검증 실패 응답(ok=false, retryable=true)을 받으면 short_circuit 하지 않고
다음 iteration 으로 진입해 재호출할 수 있어야 한다.
"""
from __future__ import annotations

import asyncio
import io
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

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


def _make_tool_call_stream(name: str, args: dict, finish: str = "tool_calls"):
    """모의 vLLM stream — tool_call 1건과 finish 신호."""
    async def gen(**_kwargs):
        yield ("tool_call", {
            "index": 0,
            "id": f"call_{name}",
            "function": {"name": name, "arguments": json.dumps(args)},
        })
        yield ("finish", finish)
    return gen


def _make_content_stream(text: str):
    async def gen(**_kwargs):
        yield ("content", text)
        yield ("finish", "stop")
    return gen


async def _collect_stream(stream) -> list[str]:
    out: list[str] = []
    async for delta in stream:
        out.append(delta)
    return out


# ─── 1. ok=true 면 short_circuit ──────────────────────────────────────
def test_short_circuit_on_success() -> None:
    print("\n[test_short_circuit_on_success] ok=true 결과 → 한 단락만 yield + return")

    async def fake_dispatch(name, args):
        return json.dumps({
            "ok": True,
            "short_message": "✓ Expense 양식이 생성되었습니다.\n파일명: X.xlsx",
            "filename": "X.xlsx",
            "download_url": "/x",
        })

    iter_count = {"n": 0}
    def stream_once_factory(*a, **k):
        iter_count["n"] += 1
        return _make_tool_call_stream("compose_expense_report", {
            "author": "X", "year": 2026, "month": 2,
            "lines": [{"source": "개인카드"}]
        })()

    with patch.object(vllm_client, "_stream_once", stream_once_factory):
        with patch.object(vllm_client, "_truncate_to_budget",
                          new=AsyncMock(side_effect=lambda msgs, *a, **k: msgs)):
            out = asyncio.run(_collect_stream(vllm_client.stream_chat_with_tools(
                system_prompt="sys", messages=[{"role": "user", "content": "expense"}],
                tools=[{"type": "function", "function": {"name": "compose_expense_report"}}],
                dispatch=fake_dispatch,
            )))
    expect("success.has_short_message", any("✓ Expense 양식" in s for s in out))
    expect("success.exactly_one_iteration", iter_count["n"] == 1, f"got {iter_count['n']}")


# ─── 2. ok=false, retryable=true 면 다음 iteration ─────────────────
def test_retry_on_validation_failure() -> None:
    print("\n[test_retry_on_validation_failure] retryable → 재호출 루프 → 두 번째에 성공")

    call_history: list[dict] = []

    async def fake_dispatch(name, args):
        call_history.append(args)
        if len(call_history) == 1:
            # 첫 호출 — 검증 실패
            return json.dumps({
                "ok": False,
                "retryable": True,
                "errors": ["line[0].date 형식 오류"],
                "error": "line[0].date 형식 오류",
                "short_message": "⚠ 검증 실패",
            })
        else:
            return json.dumps({
                "ok": True,
                "short_message": "✓ Expense 양식이 생성되었습니다 (재호출 성공)",
            })

    iter_count = {"n": 0}
    def stream_once_factory(*a, **k):
        iter_count["n"] += 1
        # 매 iteration 마다 같은 tool_call.
        return _make_tool_call_stream("compose_expense_report", {
            "author": "X", "year": 2026, "month": 2,
            "lines": [{"source": "개인카드"}]
        })()

    with patch.object(vllm_client, "_stream_once", stream_once_factory):
        with patch.object(vllm_client, "_truncate_to_budget",
                          new=AsyncMock(side_effect=lambda msgs, *a, **k: msgs)):
            out = asyncio.run(_collect_stream(vllm_client.stream_chat_with_tools(
                system_prompt="sys", messages=[{"role": "user", "content": "expense"}],
                tools=[{"type": "function", "function": {"name": "compose_expense_report"}}],
                dispatch=fake_dispatch,
            )))

    expect("retry.called_twice", len(call_history) == 2, f"got {len(call_history)} calls")
    expect("retry.iterations_2", iter_count["n"] == 2, f"got {iter_count['n']}")
    expect("retry.final_success_yielded",
           any("재호출 성공" in s for s in out))
    # 첫 호출의 실패 short_message 는 사용자에게 노출 안 됨 (자동 재시도).
    expect("retry.failure_not_exposed",
           not any("⚠ 검증 실패" in s for s in out),
           f"out={out}")


# ─── 3. ok=false, retryable=false → 단발 노출 ─────────────────────
def test_no_retry_when_not_retryable() -> None:
    print("\n[test_no_retry_when_not_retryable] retryable=false → 즉시 yield + return")

    call_count = {"n": 0}

    async def fake_dispatch(name, args):
        call_count["n"] += 1
        return json.dumps({
            "ok": False,
            "retryable": False,
            "error": "치명적 오류 - DB 연결 실패",
            "short_message": "⚠ 시스템 오류",
        })

    iter_count = {"n": 0}
    def stream_once_factory(*a, **k):
        iter_count["n"] += 1
        return _make_tool_call_stream("compose_expense_report", {"author": "X"})()

    with patch.object(vllm_client, "_stream_once", stream_once_factory):
        with patch.object(vllm_client, "_truncate_to_budget",
                          new=AsyncMock(side_effect=lambda msgs, *a, **k: msgs)):
            out = asyncio.run(_collect_stream(vllm_client.stream_chat_with_tools(
                system_prompt="sys", messages=[{"role": "user", "content": "expense"}],
                tools=[{"type": "function", "function": {"name": "compose_expense_report"}}],
                dispatch=fake_dispatch,
            )))
    expect("no_retry.exactly_one_call", call_count["n"] == 1, f"got {call_count['n']}")
    expect("no_retry.message_yielded", any("시스템 오류" in s for s in out))


# ─── 4. 무한 재시도 방지 — MAX_TOOL_ITERATIONS ───────────────────
def test_max_iterations_bound() -> None:
    print("\n[test_max_iterations_bound] 영구 실패해도 MAX_TOOL_ITERATIONS 에 멈춤")

    call_count = {"n": 0}

    async def fake_dispatch(name, args):
        call_count["n"] += 1
        return json.dumps({
            "ok": False, "retryable": True,
            "errors": ["계속 실패"], "error": "계속 실패",
            "short_message": "⚠ 계속 실패",
        })

    def stream_once_factory(*a, **k):
        return _make_tool_call_stream("compose_expense_report", {"author": "X"})()

    with patch.object(vllm_client, "_stream_once", stream_once_factory):
        with patch.object(vllm_client, "_truncate_to_budget",
                          new=AsyncMock(side_effect=lambda msgs, *a, **k: msgs)):
            out = asyncio.run(_collect_stream(vllm_client.stream_chat_with_tools(
                system_prompt="sys", messages=[{"role": "user", "content": "expense"}],
                tools=[{"type": "function", "function": {"name": "compose_expense_report"}}],
                dispatch=fake_dispatch,
            )))
    expect("max.calls_bounded",
           call_count["n"] == vllm_client.MAX_TOOL_ITERATIONS,
           f"got {call_count['n']}, max={vllm_client.MAX_TOOL_ITERATIONS}")
    expect("max.limit_message_yielded",
           any("도구 호출 한도 초과" in s for s in out))


if __name__ == "__main__":
    import traceback
    cases = [
        test_short_circuit_on_success,
        test_retry_on_validation_failure,
        test_no_retry_when_not_retryable,
        test_max_iterations_bound,
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
