"""expense_error_v9 — bare hermes JSON (XML 태그 없는) leak 차단 + JSON 깨짐 복구.

v9 시나리오: LLM 이 prose + 끝에 bare hermes 형태
  `{"name": "compose_expense_report", "arguments": {..., receipt_attachment_ids[]}}`
같이 흘림. XML 태그 없어 기존 _HERMES_TOOL_CALL_RE 미매칭 + JSON 끝부분 깨짐.

대응:
  1) _BARE_HERMES_RE — XML 태그 없이 `{"name":"X","arguments":...}` 패턴 매칭
  2) _try_parse_args — unquoted key (`receipt_attachment_ids[]`) 깨짐 1회 복구
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


# ─── 1. bare hermes 패턴 추출 ─────────────────────────────────
def test_bare_hermes_extraction() -> None:
    print("\n[test_bare_hermes_extraction]")
    cases = [
        ('{"name":"compose_expense_report","arguments":{"author":"배재병","year":2026,"month":2,"lines":[]}}',
         True),
        # 공백/포매팅 변형
        ('  {  "name" : "compose_expense_report" , "arguments" : { "author" : "X" } }  ',
         True),
        # leave_email
        ('{"name":"compose_leave_email","arguments":{"date":"2026-02-09"}}', True),
        # arguments 가 string 으로 인코딩된 경우
        ('{"name":"compose_expense_report","arguments":"{\\"author\\":\\"X\\"}"}',
         True),
        # 모르는 함수 이름 → None
        ('{"name":"unknown_tool","arguments":{}}', False),
        # arguments 누락 → 매치는 되지만 추출 실패
        ('{"name":"compose_expense_report"}', False),
    ]
    for raw, want in cases:
        res = vllm_client._extract_tool_call_from_content(raw)
        if want:
            expect(f"hermes:{raw[:50]!r}", res is not None and res[0].startswith("compose"),
                   f"got {res}")
        else:
            expect(f"hermes:None:{raw[:50]!r}", res is None, f"got {res}")


# ─── 2. JSON 깨짐 1회 복구 ─────────────────────────────────────
def test_json_repair() -> None:
    print("\n[test_json_repair]")
    cases = [
        # v9 실제 깨짐
        ('{"author":"X","lines":[], receipt_attachment_ids[]}',
         {"author": "X", "lines": [], "receipt_attachment_ids": []}),
        # 중첩 객체에서도
        ('{"a":1, foo[]}', {"a": 1, "foo": []}),
        # 객체 시작 직후
        ('{foo[1,2]}', {"foo": [1, 2]}),
        # 정상 JSON 은 변경 없이 통과
        ('{"a":1,"b":[2,3]}', {"a": 1, "b": [2, 3]}),
    ]
    for raw, want in cases:
        got = vllm_client._try_parse_args(raw)
        expect(f"repair:{raw[:40]!r}", got == want, f"got {got}")


# ─── 3. v9 실제 leak e2e — prose + bare hermes (깨진 JSON 포함) ─────
def test_v9_real_leak_e2e() -> None:
    print("\n[test_v9_real_leak_e2e]")
    v9_leak = (
        ".compose_expense_report 도구를 호출하겠습니다.\n\n"
        "- 작성자: 배재병\n"
        "- 년도: 2026\n"
        "- 월: 2\n"
        "- 지출 라인:\n"
        "  - 개인카드 지출:\n"
        "    - 계정과목: 여비교통비\n"
        "    - 날짜: 2/9\n"
        "    - 금액: 29500원\n"
        "  - 개인카드 지출:\n"
        "    - 계정과목: 여비교통비\n"
        "    - 날짜: 2/10\n"
        "    - 금액: 30000원\n"
        "\n영수증 첨부 ID 목록이 제공되지 않았습니다. 따라서 영수증 시트는 빈 상태로 출력됩니다.\n\n"
        # bare hermes — 약간 깨진 JSON (receipt_attachment_ids[] unquoted)
        '{"name": "compose_expense_report", "arguments": {"author": "배재병", "year": 2026, "month": 2, '
        '"lines": [{"source": "개인카드", "category": "여비교통비", "date": "2026-02-09", '
        '"purpose": "SRT", "amount": 29500, "vendor": "에스알"}], receipt_attachment_ids[]}}'
    )

    async def fake_dispatch(name, args):
        return json.dumps({
            "ok": True,
            "short_message": f"✓ recovered. name={name}, lines={len(args.get('lines',[]))}, ids={args.get('receipt_attachment_ids')}",
        })

    with patch.object(vllm_client, "_stream_once", _mk_content_stream(v9_leak, chunk_size=80)):
        with patch.object(vllm_client, "_truncate_to_budget",
                          new=AsyncMock(side_effect=lambda msgs, *a, **k: msgs)):
            out = asyncio.run(_collect(vllm_client.stream_chat_with_tools(
                system_prompt="sys", messages=[{"role": "user", "content": "expense"}],
                tools=[{"type": "function", "function": {"name": "compose_expense_report"}}],
                dispatch=fake_dispatch,
            )))
    joined = "".join(out)
    # prose 한 글자도 노출 안 됨
    expect("v9.no_prose_leak",
           "도구를 호출하겠습니다" not in joined and "작성자: 배재병" not in joined,
           f"leak: {joined[:300]!r}")
    expect("v9.no_json_leak",
           '"name"' not in joined and "receipt_attachment_ids" not in joined,
           f"leak: {joined[:300]!r}")
    expect("v9.dispatched_success",
           "✓ recovered" in joined and "compose_expense_report" in joined,
           f"got: {joined!r}")
    expect("v9.lines_recovered", "lines=1" in joined, f"got: {joined!r}")
    expect("v9.ids_recovered", "ids=[]" in joined, f"got: {joined!r}")


# ─── 4. 정상 hermes XML 회귀 ────────────────────────────────────
def test_hermes_xml_still_works() -> None:
    print("\n[test_hermes_xml_still_works]")
    text = ('<tool_call>{"name":"compose_expense_report",'
            '"arguments":{"author":"X","year":2026,"month":2,"lines":[]}}</tool_call>')

    async def fake_dispatch(name, args):
        return json.dumps({"ok": True, "short_message": "✓ xml ok"})

    with patch.object(vllm_client, "_stream_once", _mk_content_stream(text)):
        with patch.object(vllm_client, "_truncate_to_budget",
                          new=AsyncMock(side_effect=lambda msgs, *a, **k: msgs)):
            out = asyncio.run(_collect(vllm_client.stream_chat_with_tools(
                system_prompt="sys", messages=[{"role": "user", "content": "x"}],
                tools=[{"type": "function", "function": {"name": "compose_expense_report"}}],
                dispatch=fake_dispatch,
            )))
    joined = "".join(out)
    expect("xml.dispatched", "xml ok" in joined)


# ─── 5. 정상 JSON 은 복구로 변형되면 안 됨 ─────────────────────
def test_valid_json_unchanged() -> None:
    print("\n[test_valid_json_unchanged]")
    valid_cases = [
        '{"author":"X","lines":[{"source":"개인카드","amount":1000}]}',
        '{"a":1,"b":2,"c":[1,2,3]}',
        '{}',
        '{"nested":{"deep":{"deeper":[]}}}',
    ]
    for s in valid_cases:
        want = json.loads(s)
        got = vllm_client._try_parse_args(s)
        expect(f"valid_unchanged:{s[:40]!r}", got == want, f"got {got}")


if __name__ == "__main__":
    cases = [
        test_bare_hermes_extraction,
        test_json_repair,
        test_v9_real_leak_e2e,
        test_hermes_xml_still_works,
        test_valid_json_unchanged,
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
