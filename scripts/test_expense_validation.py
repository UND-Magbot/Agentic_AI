"""compose_expense_report 인자 검증/정규화 단위 테스트.

DB 가 필요한 _do_compose 호출 직전까지의 모든 분기를 커버.
관측된 LLM 실패 패턴(v5) 과 예상 실패 패턴 전부 케이스화.
"""
from __future__ import annotations

import asyncio
import io
import json
import sys
import traceback
from pathlib import Path
from typing import Any
from unittest.mock import patch

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.tools import (
    _CATEGORY_ALIASES,
    _compose_expense_report,
    _error_payload,
    _normalize_amount,
    _normalize_category,
    _normalize_date,
    _normalize_lines,
    _normalize_source,
)

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []


def expect(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASS.append(name)
        print(f"  PASS {name}")
    else:
        FAIL.append((name, detail))
        print(f"  FAIL {name} — {detail}")


# ─── 1. _normalize_amount ────────────────────────────────────────────────
def test_normalize_amount() -> None:
    print("\n[test_normalize_amount]")
    cases = [
        # (입력, 기대값, 기대에러여부)
        (29500, 29500.0, False),
        (29500.0, 29500.0, False),
        ("29500", 29500.0, False),
        ("29,500", 29500.0, False),
        ("29,500원", 29500.0, False),
        ("₩29500", 29500.0, False),
        ("$1000", 1000.0, False),
        ("1,234,567", 1234567.0, False),
        ("  72,000원  ", 72000.0, False),
        # 실패
        (0, None, True),
        (-100, None, True),
        ("0", None, True),
        ("", None, True),
        (None, None, True),
        ("abc", None, True),
        ("원", None, True),
    ]
    for raw, want_val, want_err in cases:
        val, err = _normalize_amount(raw)
        if want_err:
            expect(f"amount:reject {raw!r}", err is not None and val is None, f"got val={val} err={err}")
        else:
            expect(f"amount:accept {raw!r}->{want_val}", val == want_val and err is None,
                   f"got val={val} err={err}")


# ─── 2. _normalize_date ──────────────────────────────────────────────────
def test_normalize_date() -> None:
    print("\n[test_normalize_date]")
    cases = [
        ("2026-02-09", "2026-02-09", False),
        ("2026-2-9", "2026-02-09", False),
        ("2026/02/09", "2026-02-09", False),
        ("2026.02.09", "2026-02-09", False),
        ("2026-02-09 월", "2026-02-09", False),
        ("2026년 02월 09일", "2026-02-09", False),
        # 실패 — 연도 누락
        ("2/9", None, True),
        ("2/9 월", None, True),
        ("어제", None, True),
        ("", None, True),
        (None, None, True),
        # 실패 — 잘못된 범위
        ("2026-13-09", None, True),
        ("2026-02-32", None, True),
    ]
    for raw, want, want_err in cases:
        v, err = _normalize_date(raw)
        if want_err:
            expect(f"date:reject {raw!r}", err is not None and v is None, f"got v={v} err={err}")
        else:
            expect(f"date:accept {raw!r}->{want}", v == want and err is None, f"got v={v} err={err}")


# ─── 3. _normalize_category ──────────────────────────────────────────────
def test_normalize_category() -> None:
    print("\n[test_normalize_category]")
    enum_valid = ["여비교통비", "복리후생비", "접대비", "소모품비", "차량유지비",
                  "지급수수료", "도서인쇄비", "해당없음"]
    for cat in enum_valid:
        v, err = _normalize_category(cat)
        expect(f"category:passthrough {cat}", v == cat and err is None)

    # alias
    alias_cases = [
        ("교통비", "여비교통비"),
        ("택시비", "여비교통비"),
        ("SRT", "여비교통비"),
        ("점심", "복리후생비"),
        ("회식", "복리후생비"),
        ("접대", "접대비"),
        ("사무용품", "소모품비"),
        ("주유비", "차량유지비"),
        ("은행수수료", "지급수수료"),
        ("도서", "도서인쇄비"),
        ("잡비", "해당없음"),
        ("기타", "해당없음"),
    ]
    for raw, want in alias_cases:
        v, err = _normalize_category(raw)
        expect(f"category:alias {raw}->{want}", v == want, f"got v={v}")

    # 거부 케이스
    for raw in ("", None, "헛소리", "랜덤"):
        v, err = _normalize_category(raw)
        expect(f"category:reject {raw!r}", v is None and err is not None)


# ─── 4. _normalize_source ────────────────────────────────────────────────
def test_normalize_source() -> None:
    print("\n[test_normalize_source]")
    for s in ("법인카드", "개인카드"):
        v, err = _normalize_source(s)
        expect(f"source:passthrough {s}", v == s and err is None)
    expect("source:reject empty", _normalize_source("")[0] is None)
    expect("source:reject garbage", _normalize_source("기타카드")[0] is None)


# ─── 5. _normalize_lines: 사용자 시나리오 (v5) ──────────────────────────
def test_lines_user_scenario() -> None:
    print("\n[test_lines_user_scenario] 사용자 메시지 3건 → 모두 정규화 통과")
    raw = [
        {
            "source": "개인카드", "category": "해당없음", "date": "2026-02-07",
            "purpose": "현대 글로비스 공장 출입 위한 안전화 구매",
            "amount": 72000, "vendor": "워크업 대구 반야월점",
        },
        {
            "source": "개인카드", "category": "여비교통비", "date": "2026-02-09",
            "purpose": "평택지제역 -> 동대구역 SRT 승차권 구매",
            "amount": 29500, "vendor": "주식회사 에스알",
        },
        {
            "source": "개인카드", "category": "여비교통비", "date": "2026-02-10",
            "purpose": "숙소 근처 -> 평택지제역 콜택시 이용",
            "amount": 30000, "vendor": "미래 대리우전",
        },
    ]
    norm, errs = _normalize_lines(raw)
    expect("user_scenario.no_errors", errs == [], f"errs={errs}")
    expect("user_scenario.count=3", len(norm) == 3)
    expect("user_scenario.line0_date", norm[0]["date"] == "2026-02-07")
    expect("user_scenario.line1_date", norm[1]["date"] == "2026-02-09")
    expect("user_scenario.line2_date", norm[2]["date"] == "2026-02-10")


# ─── 6. _normalize_lines: LLM 흔한 실수들 자동 보정 ───────────────────
def test_lines_llm_quirks_corrected() -> None:
    print("\n[test_lines_llm_quirks_corrected] amount 콤마, date 요일, category alias")
    raw = [
        {
            "source": "개인카드", "category": "교통비",          # ← alias
            "date": "2026-02-09 월",                              # ← 요일 글자 포함
            "purpose": "SRT", "amount": "29,500원",              # ← 콤마+단위
            "vendor": "주식회사 에스알",
        },
    ]
    norm, errs = _normalize_lines(raw)
    expect("quirks.no_errors", errs == [], f"errs={errs}")
    expect("quirks.category_mapped", norm[0]["category"] == "여비교통비")
    expect("quirks.date_cleaned", norm[0]["date"] == "2026-02-09")
    expect("quirks.amount_cleaned", norm[0]["amount"] == 29500.0)


# ─── 7. _normalize_lines: 거부되어야 할 케이스 ─────────────────────────
def test_lines_rejected() -> None:
    print("\n[test_lines_rejected] 비어있는 필드, 잘못된 날짜")
    raw = [
        {  # 0: 정상
            "source": "개인카드", "category": "여비교통비",
            "date": "2026-02-09", "purpose": "ok",
            "amount": 1000, "vendor": "X",
        },
        {  # 1: amount 0
            "source": "개인카드", "category": "여비교통비",
            "date": "2026-02-09", "purpose": "no amount",
            "amount": 0, "vendor": "X",
        },
        {  # 2: date '2/9' (연도 누락)
            "source": "개인카드", "category": "여비교통비",
            "date": "2/9", "purpose": "no year",
            "amount": 1000, "vendor": "X",
        },
        {  # 3: purpose 비어있음
            "source": "개인카드", "category": "여비교통비",
            "date": "2026-02-09", "purpose": "",
            "amount": 1000, "vendor": "X",
        },
        {  # 4: vendor 비어있음
            "source": "개인카드", "category": "여비교통비",
            "date": "2026-02-09", "purpose": "ok",
            "amount": 1000, "vendor": "",
        },
    ]
    norm, errs = _normalize_lines(raw)
    expect("rejected.normalized=1", len(norm) == 1, f"got {len(norm)}")
    expect("rejected.errs_count=4", len(errs) == 4, f"errs={errs}")
    # 각 에러 메시지에 인덱스 명시되어야 LLM 이 어느 라인을 고칠지 알 수 있음.
    expect("rejected.err_includes_index_1", any("line[1]" in e for e in errs))
    expect("rejected.err_includes_index_2", any("line[2]" in e for e in errs))
    expect("rejected.err_includes_index_3", any("line[3]" in e for e in errs))
    expect("rejected.err_includes_index_4", any("line[4]" in e for e in errs))


# ─── 8. _compose_expense_report dispatch — error payloads ─────────────
def test_dispatch_error_payloads() -> None:
    """builder 호출 전 단계의 모든 거부 분기. _do_compose 는 호출 안 되도록 mock."""
    print("\n[test_dispatch_error_payloads]")

    async def fake_compose(**kwargs):
        raise AssertionError("should not reach _do_compose")

    with patch("app.expense_service.compose_expense_report", fake_compose):
        # author 비어 있음
        result = asyncio.run(_compose_expense_report({
            "author": "", "year": 2026, "month": 2,
            "lines": [{"source": "개인카드", "category": "여비교통비",
                       "date": "2026-02-09", "purpose": "X", "amount": 1000, "vendor": "V"}],
        }))
        parsed = json.loads(result)
        expect("dispatch.author_empty", parsed["ok"] is False and parsed.get("retryable") is True)
        expect("dispatch.author_err_mentioned",
               any("author" in e for e in parsed.get("errors", [])))

        # month 범위 초과
        result = asyncio.run(_compose_expense_report({
            "author": "X", "year": 2026, "month": 13,
            "lines": [{"source": "개인카드", "category": "여비교통비",
                       "date": "2026-02-09", "purpose": "X", "amount": 1000, "vendor": "V"}],
        }))
        parsed = json.loads(result)
        expect("dispatch.month_out_of_range", parsed["ok"] is False)

        # lines 비어 있음
        result = asyncio.run(_compose_expense_report({
            "author": "X", "year": 2026, "month": 2, "lines": [],
        }))
        parsed = json.loads(result)
        expect("dispatch.lines_empty", parsed["ok"] is False)

        # line 검증 실패
        result = asyncio.run(_compose_expense_report({
            "author": "X", "year": 2026, "month": 2,
            "lines": [{"source": "개인카드", "category": "여비교통비",
                       "date": "2/9", "purpose": "X", "amount": "abc", "vendor": ""}],
        }))
        parsed = json.loads(result)
        expect("dispatch.line_invalid", parsed["ok"] is False)
        expect("dispatch.line_errors_listed", len(parsed.get("errors", [])) >= 1)
        # short_message 가 LLM-readable
        expect("dispatch.short_message_present", bool(parsed.get("short_message")))

        # personal 라인 17건 → 슬롯 한도 초과
        result = asyncio.run(_compose_expense_report({
            "author": "X", "year": 2026, "month": 2,
            "lines": [
                {"source": "개인카드", "category": "여비교통비",
                 "date": f"2026-02-{(i%28)+1:02d}", "purpose": f"X{i}",
                 "amount": 100, "vendor": "V"} for i in range(17)
            ],
        }))
        parsed = json.loads(result)
        expect("dispatch.over_slot_limit", parsed["ok"] is False)
        expect("dispatch.over_slot_err_msg", any("16" in e for e in parsed.get("errors", [])))


# ─── 9. _compose_expense_report dispatch — success path ─────────────
def test_dispatch_success() -> None:
    """검증 통과 후 builder mock 호출 성공 시 응답 구조."""
    print("\n[test_dispatch_success]")

    class FakeResult:
        attachment_id = 99
        filename = "(주)유엔디_expense02월_배재병.xlsx"
        download_url = "/api/attachments/99/download"
        size_bytes = 20000
        lines_count = 3
        receipts_count = 3

    async def fake_compose(**kwargs):
        return FakeResult()

    with patch("app.expense_service.compose_expense_report", fake_compose):
        result = asyncio.run(_compose_expense_report({
            "author": "배재병", "year": 2026, "month": 2,
            "lines": [
                {"source": "개인카드", "category": "해당없음",
                 "date": "2026-02-07", "purpose": "안전화 구매",
                 "amount": 72000, "vendor": "워크업"},
                {"source": "개인카드", "category": "여비교통비",
                 "date": "2026-02-09", "purpose": "SRT",
                 "amount": "29,500원", "vendor": "에스알"},
                {"source": "개인카드", "category": "여비교통비",
                 "date": "2026-02-10", "purpose": "택시",
                 "amount": 30000, "vendor": "대리운전"},
            ],
            "receipt_attachment_ids": [1, 2, 3],
        }))
        parsed = json.loads(result)
        expect("success.ok_true", parsed["ok"] is True)
        expect("success.has_short_message", "✓" in parsed.get("short_message", ""))
        expect("success.has_download_url", "/api/attachments/99/download" in parsed["short_message"])
        expect("success.filename_in_msg", "(주)유엔디_expense02월_배재병.xlsx" in parsed["short_message"])


# ─── 10. _compose_expense_report — 영수증<라인 불일치 soft warning ───
def test_dispatch_receipt_mismatch_warning() -> None:
    """personal=3 인데 영수증=2 → 성공이지만 warning."""
    print("\n[test_dispatch_receipt_mismatch_warning]")

    class FakeResult:
        attachment_id = 100
        filename = "X.xlsx"
        download_url = "/x"
        size_bytes = 1000
        lines_count = 3
        receipts_count = 2

    async def fake_compose(**kwargs):
        return FakeResult()

    with patch("app.expense_service.compose_expense_report", fake_compose):
        result = asyncio.run(_compose_expense_report({
            "author": "X", "year": 2026, "month": 2,
            "lines": [
                {"source": "개인카드", "category": "해당없음",
                 "date": "2026-02-07", "purpose": "p",
                 "amount": 100, "vendor": "v"} for _ in range(3)
            ],
            "receipt_attachment_ids": [1, 2],
        }))
        parsed = json.loads(result)
        expect("mismatch.ok_true", parsed["ok"] is True)
        expect("mismatch.warning_present",
               any("불일치" in w for w in parsed.get("warnings", [])))


# ─── 11. year/month 정합성 경고 ─────────────────────────────────────────
def test_dispatch_ym_consistency_warning() -> None:
    """report.month=3 인데 라인 모두 2월 날짜 → warning."""
    print("\n[test_dispatch_ym_consistency_warning]")

    class FakeResult:
        attachment_id = 101
        filename = "X.xlsx"
        download_url = "/x"
        size_bytes = 1000
        lines_count = 1
        receipts_count = 0

    async def fake_compose(**kwargs):
        return FakeResult()

    with patch("app.expense_service.compose_expense_report", fake_compose):
        result = asyncio.run(_compose_expense_report({
            "author": "X", "year": 2026, "month": 3,  # 3월
            "lines": [
                {"source": "개인카드", "category": "해당없음",
                 "date": "2026-02-07", "purpose": "p",  # 2월
                 "amount": 100, "vendor": "v"}
            ],
        }))
        parsed = json.loads(result)
        expect("ym.ok_true", parsed["ok"] is True)
        expect("ym.warning_present",
               any("2026-03" in w and "일치하지" in w for w in parsed.get("warnings", [])))


# ─── 12. retryable=true 의미 — LLM 이 보고 인자를 어떻게 고칠지 ───
def test_error_payload_structure() -> None:
    """_error_payload 가 LLM-friendly 한지 검증."""
    print("\n[test_error_payload_structure]")
    payload = json.loads(_error_payload(["line[0]: 사유"], "라인을 다시 매핑하세요."))
    expect("err.ok_false", payload["ok"] is False)
    expect("err.retryable_true", payload["retryable"] is True)
    expect("err.errors_array", isinstance(payload["errors"], list))
    expect("err.error_string", isinstance(payload["error"], str))
    expect("err.short_message", "⚠" in payload["short_message"])
    expect("err.instruction_present",
           "compose_expense_report" in payload["instruction_to_assistant"])


# ─── main ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    cases = [
        test_normalize_amount,
        test_normalize_date,
        test_normalize_category,
        test_normalize_source,
        test_lines_user_scenario,
        test_lines_llm_quirks_corrected,
        test_lines_rejected,
        test_dispatch_error_payloads,
        test_dispatch_success,
        test_dispatch_receipt_mismatch_warning,
        test_dispatch_ym_consistency_warning,
        test_error_payload_structure,
    ]
    for fn in cases:
        try:
            fn()
        except Exception as e:
            FAIL.append((fn.__name__, f"unexpected exception: {e}"))
            print(f"  FAIL {fn.__name__} — {e}")
            traceback.print_exc()

    print(f"\n==== {len(PASS)} pass / {len(FAIL)} fail ====")
    if FAIL:
        for name, detail in FAIL:
            print(f"  [FAIL] {name}: {detail}")
        sys.exit(1)
    print("ALL OK")
