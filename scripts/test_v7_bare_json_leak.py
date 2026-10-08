"""expense_error_v7 (bare-JSON leak — 도구 이름 없는 인자 JSON) 차단 검증.

v6 와의 차이: v6 는 `.compose_expense_report({...})` 형태(이름 + JSON)였다면,
v7 은 `{"author":...,"lines":[...]}` 처럼 JSON 만 본문에 흘림. _TOOL_META_RE 가
이름을 찾을 수 없어 mode 가 passthrough 로 떨어지던 사고를 두 갈래로 차단:

  1) tool_choice 가 함수로 강제됐다면 첫 content 부터 무조건 tool_buffering.
  2) detect 모드에서 `{` 시작 + 시그니처 키 ≥ 2 → tool_buffering 으로 승격.
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


def _mk_content_stream(text: str, chunk_size: int = 64):
    async def gen(**_kwargs):
        for i in range(0, len(text), chunk_size):
            yield ("content", text[i:i+chunk_size])
        yield ("finish", "stop")
    return gen


async def _collect(stream) -> list[str]:
    out: list[str] = []
    async for d in stream:
        out.append(d)
    return out


# ─── 1. _looks_like_tool_args ─────────────────────────────────────────
def test_looks_like_tool_args() -> None:
    print("\n[test_looks_like_tool_args]")
    cases = [
        # 시그니처 키 ≥ 2 → True
        ('{"author":"X","year":2026}', True),
        ('{"author":"X","lines":[]}', True),
        ('{"year":2026,"month":2,"lines":[]}', True),
        ('{"date":"2026-02-09","reason":"개인"}', True),
        ('{"to":["a@b"],"cc":["c@d"]}', True),
        # 1개만 — 일반 답변과 혼동 가능, False
        ('{"author":"X"}', False),
        ('{"query":"hello"}', False),
        # `{` 로 시작 안 함
        ('자연어 답변', False),
        ('hello {"author":1}', False),
        # 빈 / null
        ('', False),
        ('{', False),
    ]
    for raw, want in cases:
        got = vllm_client._looks_like_tool_args(raw)
        expect(f"sig:{raw[:30]!r}->{want}", got == want, f"got={got}")


# ─── 2. v7 실제 leak — 도구 이름 없는 bare JSON (전체 형태) ───────
def test_v7_real_leak_full_json_no_force() -> None:
    print("\n[test_v7_real_leak_full_json_no_force] 도구 이름 없는 JSON, force 없음")
    # vllm 가 tool_calls 안 쓰고 인자 JSON 만 흘림. force 없는 케이스.
    leak = (
        '{"author":"배재병","year":2026,"month":2,'
        '"lines":['
        '{"source":"개인카드","category":"해당없음","date":"2026-02-07","purpose":"안전화","amount":72000,"vendor":"워크업"},'
        '{"source":"개인카드","category":"여비교통비","date":"2026-02-09","purpose":"SRT","amount":29500,"vendor":"에스알"},'
        '{"source":"개인카드","category":"여비교통비","date":"2026-02-10","purpose":"택시","amount":30000,"vendor":"미래 대리우전"}'
        '],"receipt_attachment_ids":[]}'
    )

    async def fake_dispatch(name, args):
        return json.dumps({
            "ok": True,
            "short_message": f"✓ 양식 생성. author={args['author']}, lines={len(args['lines'])}",
        })

    with patch.object(vllm_client, "_stream_once", _mk_content_stream(leak, chunk_size=80)):
        with patch.object(vllm_client, "_truncate_to_budget",
                          new=AsyncMock(side_effect=lambda msgs, *a, **k: msgs)):
            out = asyncio.run(_collect(vllm_client.stream_chat_with_tools(
                system_prompt="sys", messages=[{"role": "user", "content": "expense"}],
                tools=[{"type": "function", "function": {"name": "compose_expense_report"}}],
                dispatch=fake_dispatch,
                # force 없음 — 시그니처 키 매칭으로만 detect
            )))
    joined = "".join(out)
    expect("v7_noforce.no_json_leak",
           '"author"' not in joined and '"receipt_attachment_ids"' not in joined,
           f"leak: {joined[:200]!r}")
    expect("v7_noforce.success_msg", "✓ 양식 생성" in joined and "배재병" in joined)


# ─── 3. v7 실제 leak — 도구 이름 없는 JSON + tool_choice 강제 ───────
def test_v7_real_leak_full_json_with_force() -> None:
    print("\n[test_v7_real_leak_full_json_with_force] forced tool_choice 시 첫 글자부터 buffer")
    leak = (
        '{"author":"배재병","year":2026,"month":2,'
        '"lines":['
        '{"source":"개인카드","category":"해당없음","date":"2026-02-07","purpose":"안전화","amount":72000,"vendor":"워크업"}'
        '],"receipt_attachment_ids":[]}'
    )

    async def fake_dispatch(name, args):
        return json.dumps({
            "ok": True,
            "short_message": f"✓ forced ok. name={name}, author={args.get('author')}",
        })

    with patch.object(vllm_client, "_stream_once", _mk_content_stream(leak, chunk_size=24)):
        with patch.object(vllm_client, "_truncate_to_budget",
                          new=AsyncMock(side_effect=lambda msgs, *a, **k: msgs)):
            out = asyncio.run(_collect(vllm_client.stream_chat_with_tools(
                system_prompt="sys", messages=[{"role": "user", "content": "expense"}],
                tools=[{"type": "function", "function": {"name": "compose_expense_report"}}],
                dispatch=fake_dispatch,
                tool_choice={"type": "function",
                             "function": {"name": "compose_expense_report"}},
            )))
    joined = "".join(out)
    expect("v7_force.no_leak_first_char",
           "{" not in joined.split("Expense 양식 생성 중")[0] if "Expense" in joined else True,
           f"got={joined[:200]!r}")
    expect("v7_force.dispatched", "forced ok" in joined and "compose_expense_report" in joined)
    expect("v7_force.author_recovered", "배재병" in joined)


# ─── 4. v7 head-truncated tail (사용자가 본 실제 화면) ──────────────
def test_v7_head_truncated_tail() -> None:
    """v7 화면의 실제 텍스트 — JSON 의 꼬리만 보이는 패턴.

    이 경우는 안타깝게도 시그니처 키 1개(`receipt_attachment_ids`)만 있고 `{` 시작도 아니라
    detect 가 못 잡는다. 다만 force 가 활성화돼 있으면 처음부터 buffer 라 사용자에겐 안 보임.
    여기서는 force 가 켜진 케이스만 검증.
    """
    print("\n[test_v7_head_truncated_tail] forced + tail-only JSON")
    tail = (
        '"vendor":"주식회사 에스알"}, {"source":"개인카드","category":"여비교통비",'
        '"date":"2026-02-10","purpose":"콜택시","amount":30000,"vendor":"미래"}],'
        '"receipt_attachment_ids":[]}'
    )

    async def fake_dispatch(name, args):
        raise AssertionError("dispatch should not be called for unparseable tail")

    with patch.object(vllm_client, "_stream_once", _mk_content_stream(tail)):
        with patch.object(vllm_client, "_truncate_to_budget",
                          new=AsyncMock(side_effect=lambda msgs, *a, **k: msgs)):
            out = asyncio.run(_collect(vllm_client.stream_chat_with_tools(
                system_prompt="sys", messages=[{"role": "user", "content": "expense"}],
                tools=[{"type": "function", "function": {"name": "compose_expense_report"}}],
                dispatch=fake_dispatch,
                tool_choice={"type": "function",
                             "function": {"name": "compose_expense_report"}},
            )))
    joined = "".join(out)
    # force 켜져 있으니 처음부터 buffer → JSON 꼬리 절대 노출 X
    expect("v7_tail_force.no_leak", "}]}" not in joined and "vendor" not in joined,
           f"leak: {joined[:200]!r}")
    # 인자 파싱 실패 → generic 안내만
    expect("v7_tail_force.generic_msg",
           "도구 호출 형식 인식 실패" in joined,
           f"got: {joined!r}")


# ─── 5. 자연어 응답은 force 가 없으면 그대로 흘러야 ──────────────
def test_natural_unaffected_no_force() -> None:
    print("\n[test_natural_unaffected_no_force]")
    nat = "안녕하세요. 무엇을 도와드릴까요?"

    async def fake_dispatch(name, args):
        raise AssertionError("should not be called")

    with patch.object(vllm_client, "_stream_once", _mk_content_stream(nat)):
        with patch.object(vllm_client, "_truncate_to_budget",
                          new=AsyncMock(side_effect=lambda msgs, *a, **k: msgs)):
            out = asyncio.run(_collect(vllm_client.stream_chat_with_tools(
                system_prompt="sys", messages=[{"role": "user", "content": "안녕"}],
                tools=[], dispatch=fake_dispatch,
            )))
    joined = "".join(out)
    expect("natural.full_text", "안녕하세요" in joined and "도와드릴까요" in joined)


# ─── 6. force 있어도 진짜 자연어 응답이 오면 generic 안내 ────────
def test_force_with_natural_response() -> None:
    """force 했는데 LLM 이 미스해서 자연어로 답한 경우 — buffer 만 차고 generic 안내."""
    print("\n[test_force_with_natural_response]")
    nat = "죄송합니다. 작업을 수행할 수 없습니다."

    async def fake_dispatch(name, args):
        raise AssertionError("should not be called for non-JSON content")

    with patch.object(vllm_client, "_stream_once", _mk_content_stream(nat)):
        with patch.object(vllm_client, "_truncate_to_budget",
                          new=AsyncMock(side_effect=lambda msgs, *a, **k: msgs)):
            out = asyncio.run(_collect(vllm_client.stream_chat_with_tools(
                system_prompt="sys", messages=[{"role": "user", "content": "expense"}],
                tools=[{"type": "function", "function": {"name": "compose_expense_report"}}],
                dispatch=fake_dispatch,
                tool_choice={"type": "function",
                             "function": {"name": "compose_expense_report"}},
            )))
    joined = "".join(out)
    # 자연어가 사용자에게 노출되지 않음 + generic 안내
    expect("force_natural.no_natural_exposed", "죄송합니다" not in joined, f"got: {joined!r}")
    expect("force_natural.generic_msg", "도구 호출 형식 인식 실패" in joined)


# ─── 7. brace-only JSON 시그니처 키 1개만 — passthrough ────────
def test_single_signature_key_passthrough() -> None:
    print("\n[test_single_signature_key_passthrough] 시그니처 키 1개면 자연어로 간주")
    text = '{"author":"이름만 들어간 객체 — 시그니처 키 1개"}'

    async def fake_dispatch(name, args):
        raise AssertionError("should not be called")

    with patch.object(vllm_client, "_stream_once", _mk_content_stream(text)):
        with patch.object(vllm_client, "_truncate_to_budget",
                          new=AsyncMock(side_effect=lambda msgs, *a, **k: msgs)):
            out = asyncio.run(_collect(vllm_client.stream_chat_with_tools(
                system_prompt="sys", messages=[{"role": "user", "content": "x"}],
                tools=[], dispatch=fake_dispatch,
            )))
    joined = "".join(out)
    expect("single_key.passthrough", '"author"' in joined)


if __name__ == "__main__":
    cases = [
        test_looks_like_tool_args,
        test_v7_real_leak_full_json_no_force,
        test_v7_real_leak_full_json_with_force,
        test_v7_head_truncated_tail,
        test_natural_unaffected_no_force,
        test_force_with_natural_response,
        test_single_signature_key_passthrough,
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
