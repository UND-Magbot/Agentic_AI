"""expense_error_v11 — 도구 도움말 prose / 인자 dump leak 차단 검증.

v11 시나리오: LLM 이 슬래시 구분자/대괄호헤더 입력을 받고 parser fast-path 가 거부 →
LLM fallback → tool_call 못 만들고 prose 응답:

  1차) "Expense 양식 생성 중...\n작성자를 백동주로, 현재 날짜의 연도 2026년으로,
        지출 라인을 입력해주세요. 지출 라인은 각각 '계정과목, 날짜, 사유, 금액,
        공급자' 순서로 작성해야 합니다."

  2차) "-Line 1:\n - 계정과목: 복리후생비\n - 날짜: 2026-02-11\n …
        각 라인을 순서대로 입력해주세요."

기존 `_KOREAN_PARAM_DUMP_RE` 가 '- 계정과목:' 을 잡기 전에 첫 줄의 '- Line 1:' 또는
다른 prose 가 \n 으로 passthrough 를 발동시켜 누수가 일어남.

대응: `_TOOL_HELP_LEAK_RE` 추가 — "- Line N:", "지출 라인을 입력해 주세요",
"계정과목, 날짜, 사유, 금액, 공급자" 등 도구 도움말/시그니처 prose 를 빠르게 감지.
"""
from __future__ import annotations

import asyncio
import io
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
            yield ("content", text[i:i + chunk_size])
        yield ("finish", "stop")
    return gen


async def _collect(stream) -> list[str]:
    out: list[str] = []
    async for d in stream:
        out.append(d)
    return out


# ─── 1. 정규식 단독 검증 ──────────────────────────────────────────
def test_regex_matches_expected_patterns() -> None:
    print("\n[test_regex_matches_expected_patterns]")
    # v11 1차: "지출 라인을 입력해주세요" / "계정과목, 날짜, 사유, 금액, 공급자"
    samples_match = [
        "지출 라인을 입력해주세요.",
        "지출 라인을 각각 다음과 같이 입력",
        "지출 라인을 순서대로",
        "각 라인을 순서대로 입력해주세요",
        "'계정과목, 날짜, 사유, 금액, 공급자' 순서로",
        "계정과목,날짜,사유,금액,공급자",
        "순서로 작성해야 합니다",
        "순서로 작성 해 야 합니다",
        "순서로 입력하세요",
        # v11 2차: 도구 인자 dump 헤더
        "- Line 1:",
        "-Line 2:",
        "- Line n:",
        "- Line m:",
    ]
    for s in samples_match:
        expect(f"v11.match {s!r}",
               bool(vllm_client._TOOL_HELP_LEAK_RE.search(s)),
               f"want match for {s!r}")

    # 정상 prose 는 미매치 — 일반 답변/안내 응답 회귀 방지
    samples_nomatch = [
        "안녕하세요. 무엇을 도와드릴까요?",
        "어떤 expense 양식을 작성해드릴까요?",
        "오늘 일정은 다음과 같습니다",
        "회의는 5월 15일에 진행됩니다",
        "라인 차트가 필요하신가요?",  # 도구와 무관한 'Line' 한국어
        "지출 내역을 정리해드리겠습니다",  # '지출 라인' 이 아닌 '지출 내역'
        "Line 1 is the first line of the song",  # 'Line N:' 형태 아님
    ]
    for s in samples_nomatch:
        expect(f"v11.no_match {s!r}",
               not vllm_client._TOOL_HELP_LEAK_RE.search(s),
               f"unexpected match for {s!r}")


# ─── 2. v11 1차 응답 — 도구 도움말 prose ─────────────────────────
def test_v11_first_response_help_prose() -> None:
    """LLM 이 사용자에게 입력 양식을 prose 로 안내하는 leak (force 없음)."""
    print("\n[test_v11_first_response_help_prose]")
    leak = (
        "Expense 양식 생성 중...\n"
        "작성자를 백동주로, 현재 날짜의 연도 2026년으로, 지출 라인을 입력해주세요. "
        "지출 라인은 각각 '계정과목, 날짜, 사유, 금액, 공급자' 순서로 작성해야 합니다."
    )

    async def fake_dispatch(name, args):
        raise AssertionError("should not dispatch unparseable prose")

    with patch.object(vllm_client, "_stream_once",
                      _mk_content_stream(leak, chunk_size=24)):
        with patch.object(vllm_client, "_truncate_to_budget",
                          new=AsyncMock(side_effect=lambda msgs, *a, **k: msgs)):
            out = asyncio.run(_collect(vllm_client.stream_chat_with_tools(
                system_prompt="sys",
                messages=[{"role": "user", "content": "expense 작성"}],
                tools=[{"type": "function",
                        "function": {"name": "compose_expense_report"}}],
                dispatch=fake_dispatch,
            )))
    joined = "".join(out)
    expect("v11.first.no_help_prose_leak",
           "지출 라인을 입력해주세요" not in joined
           and "계정과목, 날짜, 사유, 금액, 공급자" not in joined
           and "순서로 작성해야" not in joined,
           f"leak: {joined!r}")
    expect("v11.first.generic_msg_shown",
           "도구 호출 형식 인식 실패" in joined,
           f"got: {joined!r}")


# ─── 3. v11 2차 응답 — '-Line N:' 인자 dump ───────────────────────
def test_v11_second_response_line_dump() -> None:
    """LLM 이 도구 인자를 '-Line 1:\\n - 계정과목: ...' 자연어 dump 로 흘리는 leak."""
    print("\n[test_v11_second_response_line_dump]")
    leak = (
        "-Line 1:\n"
        " - 계정과목: 복리후생비\n"
        " - 날짜: 2026-02-11\n"
        " - 사유: 간식비\n"
        " - 금액: 19,800원\n"
        " - 공급자: 탐앤탐스\n"
        "\n"
        "-Line 2:\n"
        " - 계정과목: 복리후생비\n"
        " - 날짜: 2026-02-11\n"
        " - 사유: 점심식대(출장)\n"
        " - 금액: 45,000원\n"
        " - 공급자: 왕두꺼비부대찌개\n"
        "\n"
        "-Line 3:\n...\n"
        "\n"
        "각 라인을 순서대로 입력해주세요."
    )

    async def fake_dispatch(name, args):
        raise AssertionError("should not dispatch")

    with patch.object(vllm_client, "_stream_once",
                      _mk_content_stream(leak, chunk_size=16)):
        with patch.object(vllm_client, "_truncate_to_budget",
                          new=AsyncMock(side_effect=lambda msgs, *a, **k: msgs)):
            out = asyncio.run(_collect(vllm_client.stream_chat_with_tools(
                system_prompt="sys",
                messages=[{"role": "user", "content": "그래 그 순으로 작성햇잖아"}],
                tools=[{"type": "function",
                        "function": {"name": "compose_expense_report"}}],
                dispatch=fake_dispatch,
            )))
    joined = "".join(out)
    expect("v11.second.no_line_dump_leak",
           "Line 1:" not in joined
           and "계정과목: 복리후생비" not in joined
           and "왕두꺼비부대찌개" not in joined
           and "각 라인을 순서대로" not in joined,
           f"leak: {joined[:300]!r}")
    expect("v11.second.generic_msg_shown",
           "도구 호출 형식 인식 실패" in joined,
           f"got: {joined!r}")


# ─── 4. force 강제 시에도 동일 prose 차단 ─────────────────────────
def test_v11_with_forced_tool_choice() -> None:
    print("\n[test_v11_with_forced_tool_choice]")
    leak = (
        "Expense 양식 생성 중...\n"
        "지출 라인을 입력해주세요. 계정과목, 날짜, 사유, 금액, 공급자 순서로 작성해야 합니다."
    )

    async def fake_dispatch(name, args):
        raise AssertionError("should not dispatch")

    with patch.object(vllm_client, "_stream_once",
                      _mk_content_stream(leak, chunk_size=32)):
        with patch.object(vllm_client, "_truncate_to_budget",
                          new=AsyncMock(side_effect=lambda msgs, *a, **k: msgs)):
            out = asyncio.run(_collect(vllm_client.stream_chat_with_tools(
                system_prompt="sys",
                messages=[{"role": "user", "content": "expense"}],
                tools=[{"type": "function",
                        "function": {"name": "compose_expense_report"}}],
                dispatch=fake_dispatch,
                tool_choice={"type": "function",
                             "function": {"name": "compose_expense_report"}},
            )))
    joined = "".join(out)
    expect("v11.force.no_leak",
           "지출 라인을 입력" not in joined
           and "계정과목, 날짜" not in joined,
           f"leak: {joined!r}")
    expect("v11.force.generic_msg", "도구 호출 형식 인식 실패" in joined)


# ─── 5. 정상 자연어 회귀 — 영향 없어야 ──────────────────────────
def test_natural_replies_unaffected() -> None:
    """일반 답변/안내가 우연히 'Line' 또는 '지출' 키워드를 가져도 통과해야."""
    print("\n[test_natural_replies_unaffected]")
    nat = (
        "안녕하세요. 어떤 expense 양식을 작성해드릴까요?\n"
        "지출 내역을 정리해드리겠습니다.\n"
        "Line 1 is the first item.\n"
        "라인 차트가 필요하시면 말씀해주세요."
    )

    async def fake_dispatch(name, args):
        raise AssertionError("no dispatch expected")

    with patch.object(vllm_client, "_stream_once",
                      _mk_content_stream(nat, chunk_size=32)):
        with patch.object(vllm_client, "_truncate_to_budget",
                          new=AsyncMock(side_effect=lambda msgs, *a, **k: msgs)):
            out = asyncio.run(_collect(vllm_client.stream_chat_with_tools(
                system_prompt="sys",
                messages=[{"role": "user", "content": "안내"}],
                tools=[],
                dispatch=fake_dispatch,
            )))
    joined = "".join(out)
    expect("natural.passthrough",
           "어떤 expense 양식을" in joined and "라인 차트" in joined,
           f"got: {joined!r}")


if __name__ == "__main__":
    cases = [
        test_regex_matches_expected_patterns,
        test_v11_first_response_help_prose,
        test_v11_second_response_line_dump,
        test_v11_with_forced_tool_choice,
        test_natural_replies_unaffected,
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
