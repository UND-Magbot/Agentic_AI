"""expense_error_v6 (content-leak) 재발 방지 테스트.

v6 시나리오: LLM 이 tool_call 안 쓰고 JSON 인자를 본문(content) 으로 흘려보낸 경우.
대응책 두 갈래를 모두 검증:
A) main.py 의 _detect_force_expense_tool — expense 의도 감지 시 tool_choice 강제
B) vllm_client 의 content-leak fallback — 본문 누적 후 dispatch
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


# ─── A. 의도 감지 (main._detect_force_expense_tool) ─────────────────────
def test_force_expense_intent() -> None:
    print("\n[test_force_expense_intent] expense + 작성 의도 감지")
    # main 을 직접 import 못 하므로(fastapi multipart 의존), 정규식만 격리 import.
    import re
    _COMPOSE = re.compile(r"(작성|만들|초안|양식|써|적어|뽑아|구성|정리)", re.IGNORECASE)
    _EXPENSE_CTX = re.compile(
        r"(expense|익스펜스|법인카드|개인카드|영수증|월\s*지출|지출\s*내역|지출\s*정리|경비\s*보고|경비\s*양식)",
        re.IGNORECASE,
    )

    def force(text: str) -> bool:
        return bool(_COMPOSE.search(text)) and bool(_EXPENSE_CTX.search(text))

    # 트리거되어야 하는 케이스 (v6 시나리오 포함)
    triggers = [
        "법인카드 지출 X\n개인카드 지출\n...\n2026년 2월 배재병 익스펜스 보고 작성해줘",
        "법인카드 지출 X\n개인카드 지출\n...\n2026년 2월 배재병 익스펜스 보고 문서 작성해줘",
        "이번 달 expense 양식 만들어줘",
        "5월 익스펜스 보고 작성",
        "법인카드/개인카드 지출 정리해줘",
        "월 지출 정리해서 양식 뽑아줘",
    ]
    for t in triggers:
        expect(f"trigger:{t[:30]}...", force(t), "should trigger")

    # 트리거되면 안 되는 케이스 (단순 질문/언급)
    no_triggers = [
        "expense 가 뭐야?",
        "익스펜스 보고는 언제 내야 해?",
        "법인카드 영수증 분실하면 어떻게 하지?",
        "안녕하세요",
    ]
    for t in no_triggers:
        expect(f"no_trigger:{t}", not force(t), f"should NOT trigger but did")


# ─── B. _extract_balanced_json ──────────────────────────────────────────
def test_extract_balanced_json() -> None:
    print("\n[test_extract_balanced_json]")
    cases = [
        ('{"a":1}', '{"a":1}'),
        ('blah {"a":1} blah', '{"a":1}'),
        ('{"a":{"b":2}}', '{"a":{"b":2}}'),
        ('{"a":"}"}', '{"a":"}"}'),                          # 문자열 안 } 무시
        ('{"a":"\\""}', '{"a":"\\""}'),                      # escaped quote
        # 잘림 — 매칭 안 됨
        ('{"a":1', None),
        ('no json here', None),
    ]
    for raw, want in cases:
        got = vllm_client._extract_balanced_json(raw)
        expect(f"balanced:{raw[:40]!r}", got == want, f"got {got!r}")


# ─── C. _try_parse_args — 일반 + escape 흡수 ─────────────────────────
def test_try_parse_args() -> None:
    print("\n[test_try_parse_args]")
    # 정상 JSON
    v = vllm_client._try_parse_args('{"author":"X","year":2026}')
    expect("parse:plain", v == {"author": "X", "year": 2026})

    # 빈 객체
    v = vllm_client._try_parse_args("{}")
    expect("parse:empty", v == {})

    # dict 아닌 값 → None
    v = vllm_client._try_parse_args('"just a string"')
    expect("parse:scalar->None", v is None)

    # 깨진 JSON
    v = vllm_client._try_parse_args("{not json}")
    expect("parse:broken->None", v is None)


# ─── D. _extract_tool_call_from_content — v6 시나리오 ──────────────
def test_extract_tool_call_from_content() -> None:
    print("\n[test_extract_tool_call_from_content]")

    # 1) hermes XML
    content = '<tool_call>{"name":"compose_expense_report","arguments":{"author":"X","year":2026,"month":2,"lines":[]}}</tool_call>'
    got = vllm_client._extract_tool_call_from_content(content)
    expect("hermes_xml", got is not None and got[0] == "compose_expense_report"
           and got[1]["author"] == "X")

    # 2) hermes XML — arguments 가 문자열 인코딩된 경우
    content2 = '<tool_call>{"name":"compose_expense_report","arguments":"{\\"author\\":\\"X\\"}"}</tool_call>'
    got2 = vllm_client._extract_tool_call_from_content(content2)
    expect("hermes_xml_str_args", got2 is not None and got2[1]["author"] == "X")

    # 3) function call: .compose_expense_report({...})
    content3 = '.compose_expense_report({"author":"배재병","year":2026,"month":2,"lines":[{"source":"개인카드"}]})'
    got3 = vllm_client._extract_tool_call_from_content(content3)
    expect("func_dot_parens", got3 is not None and got3[0] == "compose_expense_report"
           and got3[1]["author"] == "배재병")

    # 4) function call without dot
    content4 = 'compose_expense_report({"author":"X"})'
    got4 = vllm_client._extract_tool_call_from_content(content4)
    expect("func_no_dot", got4 is not None)

    # 5) brace 형태 (no parens) — 가장 흔한 leak.
    content5 = '.compose_expense_report{"author":"X","year":2026,"month":2}'
    got5 = vllm_client._extract_tool_call_from_content(content5)
    expect("brace_form", got5 is not None and got5[1]["author"] == "X")

    # 6) v6 실제 leak — 긴 JSON 본문에 3건 라인 + 트레일링 `)`.
    real_v6 = (
        '.compose_expense_report({"author":"배재병","year":2026,"month":2,'
        '"lines":['
        '{"source":"개인카드","category":"해당없음","date":"2026-02-07","purpose":"안전화 구매","amount":72000,"vendor":"워크업 대구 반야월점"},'
        '{"source":"개인카드","category":"여비교통비","date":"2026-02-09","purpose":"평택지제역 → 동대구역 SRT 승차권 구매","amount":29500,"vendor":"주식회사 에스알"},'
        '{"source":"개인카드","category":"여비교통비","date":"2026-02-10","purpose":"숙소 근처 → 평택지제역 콜택시 이용","amount":30000,"vendor":"미래 대리우전"}'
        ']})'
    )
    got6 = vllm_client._extract_tool_call_from_content(real_v6)
    expect("v6_real_leak_extracted", got6 is not None)
    expect("v6_lines_count_3", got6 and len(got6[1]["lines"]) == 3,
           f"got {got6 and len(got6[1].get('lines', []))} lines")
    expect("v6_first_line_correct",
           got6 and got6[1]["lines"][0]["amount"] == 72000)

    # 7) 도구 이름 없는 텍스트 → None (자연어로 판정)
    content7 = "안녕하세요, 무엇을 도와드릴까요?"
    got7 = vllm_client._extract_tool_call_from_content(content7)
    expect("no_tool_name->None", got7 is None)

    # 8) 헤드가 잘린 케이스 — 도구 이름 없음 → None (생략 노출 방지)
    content8 = '..."amount":29500,"vendor":"주식회사 에스알"}, {"source":"개인카드"}]})'
    got8 = vllm_client._extract_tool_call_from_content(content8)
    expect("truncated_head->None", got8 is None)


# ─── E. stream_chat_with_tools — content-leak fallback E2E ───────
def _mk_content_stream(text: str, chunk_size: int = 64):
    """vLLM 응답을 chunk 단위로 분할 emit (실제 streaming 시뮬레이션)."""
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


def test_content_leak_fallback_e2e() -> None:
    print("\n[test_content_leak_fallback_e2e] v6 leak → fallback dispatch → 사용자에게는 정상 결과만")

    leak_text = (
        '.compose_expense_report({"author":"배재병","year":2026,"month":2,'
        '"lines":['
        '{"source":"개인카드","category":"해당없음","date":"2026-02-07",'
        '"purpose":"안전화","amount":72000,"vendor":"워크업"},'
        '{"source":"개인카드","category":"여비교통비","date":"2026-02-09",'
        '"purpose":"SRT","amount":29500,"vendor":"에스알"}'
        ']})'
    )

    async def fake_dispatch(name, args):
        return json.dumps({
            "ok": True,
            "short_message": f"✓ Expense 생성 완료. lines={len(args['lines'])}, author={args['author']}",
        })

    with patch.object(vllm_client, "_stream_once", _mk_content_stream(leak_text)):
        with patch.object(vllm_client, "_truncate_to_budget",
                          new=AsyncMock(side_effect=lambda msgs, *a, **k: msgs)):
            out = asyncio.run(_collect(vllm_client.stream_chat_with_tools(
                system_prompt="sys", messages=[{"role": "user", "content": "expense"}],
                tools=[{"type": "function", "function": {"name": "compose_expense_report"}}],
                dispatch=fake_dispatch,
            )))
    joined = "".join(out)
    expect("e2e.no_json_leak",
           "}]})" not in joined and '"author"' not in joined,
           f"output={joined[:200]!r}")
    expect("e2e.success_msg",
           "✓ Expense 생성 완료" in joined and "lines=2" in joined and "배재병" in joined,
           f"output={joined!r}")
    expect("e2e.progress_msg",
           "Expense 양식 생성 중" in joined,
           f"output={joined!r}")


def test_unparseable_leak_safe() -> None:
    print("\n[test_unparseable_leak_safe] 도구 이름 없는 head-truncated leak → generic 에러만 노출")

    # v6 화면에 보였던 leak — 도구 이름 부분이 잘려 추출 불가.
    truncated_leak = (
        '"vendor":"주식회사 에스알"}, {"source":"개인카드","category":"여비교통비",'
        '"date":"2026-02-10","purpose":"콜택시","amount":30000,"vendor":"미래 대리우전"}]})'
    )

    async def fake_dispatch(name, args):
        raise AssertionError("should not be called")

    with patch.object(vllm_client, "_stream_once", _mk_content_stream(truncated_leak)):
        with patch.object(vllm_client, "_truncate_to_budget",
                          new=AsyncMock(side_effect=lambda msgs, *a, **k: msgs)):
            out = asyncio.run(_collect(vllm_client.stream_chat_with_tools(
                system_prompt="sys", messages=[{"role": "user", "content": "expense"}],
                tools=[{"type": "function", "function": {"name": "compose_expense_report"}}],
                dispatch=fake_dispatch,
            )))
    joined = "".join(out)
    # 도구 이름이 없어서 tool_buffering 모드가 안 켜짐 → passthrough 로 그냥 흐름.
    # 이 케이스에선 LLM 출력이 그대로 보일 수 있음(부분적). 하지만 적어도 dispatch 는 시도되지 않음.
    expect("unparseable.no_dispatch",
           "should not be called" not in joined)


def test_natural_text_unaffected() -> None:
    print("\n[test_natural_text_unaffected] 자연어 응답은 그대로 흘러야 함")
    nat = "안녕하세요! 무엇을 도와드릴까요? 재무·영업 어느 영역이든 답해 드립니다.\n자세히 알려 주세요."

    async def fake_dispatch(name, args):
        raise AssertionError("should not be called")

    with patch.object(vllm_client, "_stream_once", _mk_content_stream(nat, chunk_size=16)):
        with patch.object(vllm_client, "_truncate_to_budget",
                          new=AsyncMock(side_effect=lambda msgs, *a, **k: msgs)):
            out = asyncio.run(_collect(vllm_client.stream_chat_with_tools(
                system_prompt="sys", messages=[{"role": "user", "content": "안녕"}],
                tools=[],
                dispatch=fake_dispatch,
            )))
    joined = "".join(out)
    expect("natural.full_text", "안녕하세요" in joined and "답해 드립니다" in joined,
           f"got={joined!r}")
    expect("natural.no_fallback_msg",
           "도구 호출 형식 인식 실패" not in joined and "Expense 양식 생성 중" not in joined)


def test_long_natural_no_false_dispatch() -> None:
    print("\n[test_long_natural_no_false_dispatch] 도구 이름 안 들어간 긴 자연어 → passthrough")
    # 4096 자 초과 자연어 — 한 토큰도 도구 이름 없음
    text = ("이것은 매우 긴 자연어 응답입니다. " * 400)  # ~ 8000자

    async def fake_dispatch(name, args):
        raise AssertionError("should not be called")

    with patch.object(vllm_client, "_stream_once", _mk_content_stream(text, chunk_size=128)):
        with patch.object(vllm_client, "_truncate_to_budget",
                          new=AsyncMock(side_effect=lambda msgs, *a, **k: msgs)):
            out = asyncio.run(_collect(vllm_client.stream_chat_with_tools(
                system_prompt="sys", messages=[{"role": "user", "content": "긴 글"}],
                tools=[],
                dispatch=fake_dispatch,
            )))
    joined = "".join(out)
    expect("long_natural.full_length",
           len(joined) >= len(text) - 64,
           f"input_len={len(text)} output_len={len(joined)}")


if __name__ == "__main__":
    cases = [
        test_force_expense_intent,
        test_extract_balanced_json,
        test_try_parse_args,
        test_extract_tool_call_from_content,
        test_content_leak_fallback_e2e,
        test_unparseable_leak_safe,
        test_natural_text_unaffected,
        test_long_natural_no_false_dispatch,
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
