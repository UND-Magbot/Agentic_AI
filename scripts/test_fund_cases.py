# -*- coding: utf-8 -*-
"""추가 케이스(5/28·27·26·22·21) 테스트 — 정답 시트 기반 완전 검증.

소스: `자금실적_FY26_260529.xlsx`(MM-DD 정답 시트 보유). 05-29 와 동일 방법으로
객관적 검증(재현검증·교차검증·회계항등식)과 계획↔실적 대조(통화 분리)를 검증한다.
정답 시트가 있으므로 거래내역 기반 근사가 아니라 실제 자금일계표(D45/H45)로 대조한다.

핵심 사실(가공 점수 없음):
  - 5/28·27·22·21: 재현검증·교차검증·항등식 통과 + 원화 수입/지출 총액 계획=실적 일치.
  - 5/26: 검증(재현·교차·항등식)은 통과하되, 원화 지출은 계획 631,553,501 ≠ 실적
    49,351,617 로 **불일치를 정확히 탐지**(계획 4건 미반영). 자금일계표는 자계좌이체·
    환전 등을 제외하므로 거래내역 출금합과 다르다. → 이 '탐지'가 정상 동작.
  - 외화: 자금계획_외화 수입계/지출계 == 거래내역 USD 입금합/출금합(환산 없이).

실행: python scripts/test_fund_cases.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "backend"))
sys.stdout = __import__("io").TextIOWrapper(
    sys.stdout.buffer, encoding="utf-8", errors="replace"
)

import render_fund_cases as C
from app.finance import plan_reader, tx_reader

_passed = 0
_failed = 0
_EPS = 0.5

# 계획↔실적이 불일치인 날 + 기대 누락(계획에만, 실적 미반영) 건수.
# 계획대로 집행되지 않은 실제 차이 — 시스템이 '정확히 탐지'하는지 검증한다(통과로 간주).
EXPENSE_MISMATCH = {"2026-05-26": 4, "2026-05-12": 1, "2026-05-06": 3}
INCOME_MISMATCH = {"2026-05-06": 6}
# 재현·교차검증이 불일치인 날(원본 시트 전일잔액이 거래내역과 불연속) + 기대 불일치 계좌 수.
REPRO_MISMATCH = {"2026-05-20": 3}


def check(name: str, cond: bool, detail: str = "") -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ✓ {name}")
    else:
        _failed += 1
        print(f"  ✗ {name}  {detail}")


def main() -> int:
    print("=== 추가 케이스 테스트 (정답 시트 기반 완전 검증) ===\n")
    all_tx = tx_reader.read_tx_lines(C.TX)

    for date in C.DATES:
        c = C.load_case(date, all_tx)
        pa = c["pa"]
        print(f"[{date}] 시트 '{c['sheet']}' · 재현 {c['repro_ok']}/{c['repro_n']}"
              f" · 교차 {c['xcheck_ok']}/{c['xcheck_n']} · 항등식 {'통과' if c['identity_ok'] else '실패'}")

        # 1) 객관적 검증 — 재현검증·교차검증·회계항등식.
        if date in REPRO_MISMATCH:
            # 원본 시트 전일잔액이 거래내역과 불연속 → 시스템이 정확히 탐지해야 한다.
            nfail = c["repro_n"] - c["repro_ok"]
            check(f"재현검증 불일치 정확히 탐지({REPRO_MISMATCH[date]}계좌)",
                  nfail == REPRO_MISMATCH[date], f"불일치 {nfail}계좌")
            check("교차검증도 동일 계좌 탐지",
                  (c["xcheck_n"] - c["xcheck_ok"]) == REPRO_MISMATCH[date],
                  f"{c['xcheck_ok']}/{c['xcheck_n']}")
        else:
            check("당일잔액 재현검증 전건 일치",
                  c["repro_ok"] == c["repro_n"] and c["repro_n"] > 0,
                  f"{c['repro_ok']}/{c['repro_n']}")
            check("거래후잔액 교차검증 전건 일치(체인 도출)",
                  c["xcheck_ok"] == c["xcheck_n"],
                  f"{c['xcheck_ok']}/{c['xcheck_n']}")
        check("회계 항등식 통과", c["identity_ok"])

        # 2) 연도 해석 — 2026 블록 선택(2025 동일자와 구분).
        y2025 = date.replace("2026", "2025")
        ki25, ke25 = plan_reader.read_blocks(C.PLAN, y2025)
        s25 = (sum(l.amount for l in ki25), sum(l.amount for l in ke25))
        s26 = (c["plan"].krw_income_total, c["plan"].krw_expense_total)
        check("연도 해석: 2026 블록 선택(2025와 구분)", s25 != s26, f"2025={s25} 2026={s26}")

        # 3) 원화 계획↔실적(자금일계표) — 일치하는 날은 총액 일치, 불일치하는 날은
        #    계획대로 미집행한 차이를 '정확히 탐지'(누락 건수)하는지 검증.
        if date in INCOME_MISMATCH:
            miss = pa.counts("income")["missing"]
            check(f"원화 수입 불일치 정확히 탐지(누락 {INCOME_MISMATCH[date]}건)",
                  (not pa.income_total_ok) and miss == INCOME_MISMATCH[date],
                  f"total_ok={pa.income_total_ok} missing={miss}")
        else:
            check("원화 수입 총액 계획=실적", pa.income_total_ok,
                  f"계획 {pa.plan_income_total:,.0f} vs 실적 {pa.actual_income_total:,.0f}")
        if date in EXPENSE_MISMATCH:
            miss = pa.counts("expense")["missing"]
            check(f"원화 지출 불일치 정확히 탐지(누락 {EXPENSE_MISMATCH[date]}건)",
                  (not pa.expense_total_ok) and miss == EXPENSE_MISMATCH[date],
                  f"total_ok={pa.expense_total_ok} missing={miss}")
        else:
            check("원화 지출 총액 계획=실적", pa.expense_total_ok,
                  f"계획 {pa.plan_expense_total:,.0f} vs 실적 {pa.actual_expense_total:,.0f}")

        # 4) 외화 — 자금계획_외화 == 거래내역 USD(환산 없이), 순증감 = F6.
        check("외화 계획 수입계 == 거래 USD 입금합",
              abs(c["plan"].usd_income_total - c["usd_in"]) < _EPS,
              f"계획 ${c['plan'].usd_income_total:,.2f} vs 거래 ${c['usd_in']:,.2f}")
        check("외화 계획 지출계 == 거래 USD 출금합",
              abs(c["plan"].usd_expense_total - c["usd_out"]) < _EPS,
              f"계획 ${c['plan'].usd_expense_total:,.2f} vs 거래 ${c['usd_out']:,.2f}")
        check("외화 순증감(USD) = 시트 금일증감 외화(F6)",
              abs((c["usd_in"] - c["usd_out"]) - c["f6"]) < _EPS,
              f"거래 {c['usd_in'] - c['usd_out']:,.2f} vs F6 {c['f6']:,.2f}")
        print()

    print("=" * 60)
    print(f"결과: {_passed} 통과, {_failed} 실패")
    print("=" * 60)
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
