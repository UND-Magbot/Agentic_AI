"""expense_error_v8 — LLM 의 한국어 reasoning prose leak 차단 검증.

v8 시나리오: LLM 이 tool_call 안 하고
  ```
  .compose_expense_report 도구를 호출하겠습니다.

  - 작성자: 배재병
  - 년도: 2026
  - 월: 2
  - 지출 라인:
    1. ...
  ```
같이 인자를 한국어로 나열만 하고 끝남. 사용자에게 이 prose 가 그대로 노출되던 사고.

대응:
  1) _TOOL_META_RE 가 'compose_X 한글' 까지 매칭 → 도구 이름 + 한글 prose leak 감지
  2) _KOREAN_PARAM_DUMP_RE 신설 → '- 작성자: X' '- 지출 라인:' 같은 불릿 덤프 leak 감지
  3) initial_msgs_len 도입 → 사용자의 이전 턴 tool history 가 force 를 누르지 않게.
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


# ─── 1. 정규식 단독 검증 ──────────────────────────────────────────
def test_regexes() -> None:
    print("\n[test_regexes]")
    # _TOOL_META_RE — 'compose_X 한글' 매칭
    expect("meta:dot+name+korean",
           bool(vllm_client._TOOL_META_RE.search('.compose_expense_report 도구를')))
    expect("meta:name+korean_no_dot",
           bool(vllm_client._TOOL_META_RE.search('compose_expense_report 함수를')))
    expect("meta:still_matches_paren",
           bool(vllm_client._TOOL_META_RE.search('.compose_expense_report(')))
    expect("meta:still_matches_brace",
           bool(vllm_client._TOOL_META_RE.search('.compose_expense_report{')))
    # 단순 영문 prose 는 미매칭 (한글이 와야 leak)
    expect("meta:english_no_match",
           not vllm_client._TOOL_META_RE.search('compose_expense_report tool will'))

    # _KOREAN_PARAM_DUMP_RE — 불릿 덤프 매칭
    samples_match = [
        '- 작성자: 배재병',
        '- 년도: 2026',
        '- 월: 2',
        '- 지출 라인:',
        '- 계정과목: 여비교통비',
        '- 날짜: 2/9 월',
        '- 목적: 평택지제역',
        '- 금액: 29500원',
        '- 공급자: 주식회사 에스알',
        '- 영수증 첨부 ID:',
    ]
    for s in samples_match:
        expect(f"dump:match {s!r}", bool(vllm_client._KOREAN_PARAM_DUMP_RE.search(s)),
               f"want match for {s!r}")
    # 일반 불릿/문장은 매칭 안 됨
    samples_nomatch = [
        '- 안녕하세요',
        '- 다음 단계',
        '안녕하세요, 무엇을 도와드릴까요?',
        '오늘 일정은 다음과 같습니다',
    ]
    for s in samples_nomatch:
        expect(f"dump:no_match {s!r}",
               not vllm_client._KOREAN_PARAM_DUMP_RE.search(s),
               f"unexpected match for {s!r}")

    # _THINKING_MARKER_RE
    expect("thinking:open",
           bool(vllm_client._THINKING_MARKER_RE.search('<thinking>I will...')))
    expect("thinking:reasoning",
           bool(vllm_client._THINKING_MARKER_RE.search('text <Reasoning> stuff')))


# ─── 2. v8 실제 leak 텍스트 e2e — force 없음 ────────────────────
def test_v8_real_leak_no_force() -> None:
    print("\n[test_v8_real_leak_no_force] v8 prose leak — force 없이도 차단")
    v8_leak = (
        ".compose_expense_report 도구를 호출하겠습니다.\n\n"
        "- 작성자: 배재병\n"
        "- 년도: 2026\n"
        "- 월: 2\n"
        "- 지출 라인:\n"
        "  1. 개인카드 지출\n"
        "    - 계정과목: 여비교통비\n"
        "    - 날짜: 2/9 월\n"
        "    - 목적: 평택지제역 → 동대구역 SRT 승차권 구매\n"
        "    - 금액: 29500원\n"
        "    - 공급자: 주식회사 에스알\n"
        "\n영수증 첨부 ID 목록이 필요합니다. 첨부된 파일 ID [17, 18, 19]을 확인하였습니다."
    )

    async def fake_dispatch(name, args):
        raise AssertionError("should not dispatch unparseable prose")

    with patch.object(vllm_client, "_stream_once", _mk_content_stream(v8_leak, chunk_size=80)):
        with patch.object(vllm_client, "_truncate_to_budget",
                          new=AsyncMock(side_effect=lambda msgs, *a, **k: msgs)):
            out = asyncio.run(_collect(vllm_client.stream_chat_with_tools(
                system_prompt="sys", messages=[{"role": "user", "content": "expense"}],
                tools=[{"type": "function", "function": {"name": "compose_expense_report"}}],
                dispatch=fake_dispatch,
                # force 없음 — meta/dump 패턴만으로 차단
            )))
    joined = "".join(out)
    expect("v8_noforce.no_prose_leak",
           "도구를 호출하겠습니다" not in joined
           and "작성자: 배재병" not in joined
           and "계정과목: 여비교통비" not in joined,
           f"leak: {joined[:200]!r}")
    expect("v8_noforce.generic_msg", "도구 호출 형식 인식 실패" in joined)


# ─── 3. v8 실제 leak — force 있음 ─────────────────────────────────
def test_v8_real_leak_with_force() -> None:
    print("\n[test_v8_real_leak_with_force] force 있으면 첫 글자부터 buffer")
    v8_leak = (
        ".compose_expense_report 도구를 호출하겠습니다.\n\n"
        "- 작성자: 배재병\n- 년도: 2026\n- 월: 2"
    )

    async def fake_dispatch(name, args):
        raise AssertionError("no JSON to dispatch")

    with patch.object(vllm_client, "_stream_once", _mk_content_stream(v8_leak)):
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
    expect("v8_force.no_prose_leak",
           "도구를 호출" not in joined and "작성자" not in joined,
           f"leak: {joined[:200]!r}")
    expect("v8_force.generic_msg", "도구 호출 형식 인식 실패" in joined)


# ─── 4. 파라미터 덤프만 (도구 이름 없음) — 차단 ────────────────
def test_bare_param_dump_no_tool_name() -> None:
    print("\n[test_bare_param_dump_no_tool_name] 도구 이름 없이 불릿만")
    dump = (
        "- 작성자: 배재병\n"
        "- 년도: 2026\n"
        "- 월: 2\n"
        "- 지출 라인:\n"
        "  1. 개인카드 지출\n"
    )

    async def fake_dispatch(name, args):
        raise AssertionError("no JSON to dispatch")

    with patch.object(vllm_client, "_stream_once", _mk_content_stream(dump)):
        with patch.object(vllm_client, "_truncate_to_budget",
                          new=AsyncMock(side_effect=lambda msgs, *a, **k: msgs)):
            out = asyncio.run(_collect(vllm_client.stream_chat_with_tools(
                system_prompt="sys", messages=[{"role": "user", "content": "expense"}],
                tools=[{"type": "function", "function": {"name": "compose_expense_report"}}],
                dispatch=fake_dispatch,
            )))
    joined = "".join(out)
    expect("dump.no_leak", "작성자" not in joined and "지출 라인" not in joined,
           f"leak: {joined!r}")
    expect("dump.generic_msg", "도구 호출 형식 인식 실패" in joined)


# ─── 5. <thinking> 마커 차단 ─────────────────────────────────────
def test_thinking_marker_blocked() -> None:
    print("\n[test_thinking_marker_blocked]")
    text = "<thinking>Let me figure out the args first...</thinking>"

    async def fake_dispatch(name, args):
        raise AssertionError("no dispatch")

    with patch.object(vllm_client, "_stream_once", _mk_content_stream(text)):
        with patch.object(vllm_client, "_truncate_to_budget",
                          new=AsyncMock(side_effect=lambda msgs, *a, **k: msgs)):
            out = asyncio.run(_collect(vllm_client.stream_chat_with_tools(
                system_prompt="sys", messages=[{"role": "user", "content": "x"}],
                tools=[{"type": "function", "function": {"name": "compose_expense_report"}}],
                dispatch=fake_dispatch,
            )))
    joined = "".join(out)
    expect("thinking.no_leak", "thinking" not in joined.lower(),
           f"leak: {joined!r}")


# ─── 6. 자연어 회귀 (불릿 답변 등) — 영향 없어야 ───────────────
def test_natural_replies_unaffected() -> None:
    print("\n[test_natural_replies_unaffected] 일반 답변/리스트는 보여야")
    nat = (
        "안녕하세요. 다음 단계를 안내드립니다.\n"
        "- 첫번째로 로그인하세요.\n"
        "- 그 다음 메뉴를 클릭하세요.\n"
        "- 마지막으로 확인 버튼을 누르세요."
    )

    async def fake_dispatch(name, args):
        raise AssertionError("no dispatch")

    with patch.object(vllm_client, "_stream_once", _mk_content_stream(nat)):
        with patch.object(vllm_client, "_truncate_to_budget",
                          new=AsyncMock(side_effect=lambda msgs, *a, **k: msgs)):
            out = asyncio.run(_collect(vllm_client.stream_chat_with_tools(
                system_prompt="sys", messages=[{"role": "user", "content": "안내"}],
                tools=[], dispatch=fake_dispatch,
            )))
    joined = "".join(out)
    expect("natural.full_text",
           "로그인하세요" in joined and "확인 버튼" in joined,
           f"got={joined!r}")


# ─── 7. v8 leak 이 hermes XML 형태로 오는 경우 ─────────────────
def test_v8_with_hermes_xml() -> None:
    """LLM 이 reasoning prose 직후 hermes XML 로 실제 호출 — 정상 케이스."""
    print("\n[test_v8_with_hermes_xml] reasoning 후 hermes XML")
    mixed = (
        '.compose_expense_report 도구를 호출하겠습니다.\n'
        '<tool_call>{"name":"compose_expense_report","arguments":{"author":"배재병","year":2026,"month":2,"lines":[{"source":"개인카드","category":"여비교통비","date":"2026-02-09","purpose":"SRT","amount":29500,"vendor":"에스알"}]}}</tool_call>'
    )

    async def fake_dispatch(name, args):
        return json.dumps({
            "ok": True,
            "short_message": f"✓ 정상 dispatch — author={args['author']} lines={len(args['lines'])}",
        })

    with patch.object(vllm_client, "_stream_once", _mk_content_stream(mixed)):
        with patch.object(vllm_client, "_truncate_to_budget",
                          new=AsyncMock(side_effect=lambda msgs, *a, **k: msgs)):
            out = asyncio.run(_collect(vllm_client.stream_chat_with_tools(
                system_prompt="sys", messages=[{"role": "user", "content": "expense"}],
                tools=[{"type": "function", "function": {"name": "compose_expense_report"}}],
                dispatch=fake_dispatch,
            )))
    joined = "".join(out)
    expect("hermes.no_prose_leak", "도구를 호출하겠습니다" not in joined)
    expect("hermes.dispatched_ok", "정상 dispatch" in joined and "배재병" in joined)


# ─── 8. msgs_has_tool_messages — within-loop scoping ──────────
def test_within_loop_scoping() -> None:
    """사용자 이전 턴 tool history 가 force 를 누르지 않아야."""
    print("\n[test_within_loop_scoping]")

    call_count = {"n": 0}

    async def fake_dispatch(name, args):
        call_count["n"] += 1
        return json.dumps({"ok": True, "short_message": f"✓ called (n={call_count['n']})"})

    def make_tool_call_stream(*a, **k):
        async def gen():
            yield ("tool_call", {
                "index": 0, "id": "c1",
                "function": {"name": "compose_expense_report",
                             "arguments": '{"author":"X","year":2026,"month":2,"lines":[{"source":"개인카드","category":"여비교통비","date":"2026-02-09","purpose":"p","amount":1,"vendor":"v"}]}'},
            })
            yield ("finish", "tool_calls")
        return gen()

    # 사용자가 history 에 이전 turn 의 tool result 를 가지고 있는 케이스
    history_with_tool = [
        {"role": "user", "content": "지난번 expense"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "old", "type": "function",
                         "function": {"name": "compose_expense_report",
                                      "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "old", "content": '{"ok":true}'},
        {"role": "assistant", "content": "이전 expense 양식이 생성되었습니다."},
        # 이번 새 요청
        {"role": "user", "content": "이번 달 expense 다시 작성해줘"},
    ]

    with patch.object(vllm_client, "_stream_once", make_tool_call_stream):
        with patch.object(vllm_client, "_truncate_to_budget",
                          new=AsyncMock(side_effect=lambda msgs, *a, **k: msgs)):
            out = asyncio.run(_collect(vllm_client.stream_chat_with_tools(
                system_prompt="sys", messages=history_with_tool,
                tools=[{"type": "function", "function": {"name": "compose_expense_report"}}],
                dispatch=fake_dispatch,
                tool_choice={"type": "function",
                             "function": {"name": "compose_expense_report"}},
            )))
    # iter 1 에서 dispatch 정상 호출되었음 → within-loop scoping 작동
    expect("scoping.dispatched_once_in_iter1",
           call_count["n"] == 1,
           f"got {call_count['n']} dispatches")


if __name__ == "__main__":
    cases = [
        test_regexes,
        test_v8_real_leak_no_force,
        test_v8_real_leak_with_force,
        test_bare_param_dump_no_tool_name,
        test_thinking_marker_blocked,
        test_natural_replies_unaffected,
        test_v8_with_hermes_xml,
        test_within_loop_scoping,
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
