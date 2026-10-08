# -*- coding: utf-8 -*-
"""자금실적 `MM-DD` 시트의 셀 좌표 상수 + 헤더 앵커 검증.

매직넘버 제거. 양식 행 위치가 바뀌면 `verify_layout()` 이 헤더 텍스트 불일치를
시끄럽게 보고하므로(조용한 오기입 방지), 잘못된 좌표로 쓰는 사고를 차단한다.

좌표 근거: docs/design/fund_daily_reconcile_plan.md §1-1 + 원본 셀 단위 실측.
열 번호는 1-indexed (openpyxl).
"""
from __future__ import annotations

import openpyxl

# ── (원화) 계좌 List 영역 ────────────────────────────────────────────────────
COL_BANK = 11      # K 은행
COL_ACCOUNT = 12   # L 계좌번호
COL_PREV = 13      # M 전일잔액
COL_DELTA = 14     # N 증감액 (수식 =O-M, 보존)
COL_TODAY = 15     # O 당일잔액 (★ 입력 셀)
COL_PRODUCT = 16   # P 상품명/과제명
COL_ALIAS = 17     # Q 별칭

COMMON_ROWS = range(9, 22)    # 보통예금 K9:Q21
COMMON_SUBTOTAL_ROW = 22
PROJECT_ROWS = range(25, 38)  # 과제 K25:Q37
PROJECT_SUBTOTAL_ROW = 38
TOTAL_KRW_ROW = 45            # Total (원화) M45/N45/O45

FOREIGN_ROWS = range(50, 54)  # 외화 K50:Q53
FOREIGN_SUBTOTAL_ROW = 54
TOTAL_FX_ROW = 56             # Total (외화)

# ── 1. 자금일계표(실적) 영역 ─────────────────────────────────────────────────
DAYBOOK_TITLE_CELL = "B6"      # '1. 자금일계표(실적)'
CELL_DELTA_TODAY = "D6"        # 금일 증감액 원화 (=D45-H45)
CELL_DELTA_TODAY_FX = "F6"     # 금일 증감액 외화(USD) — 원화로 환산하지 않은 USD 순증감
DAYBOOK_ROWS = range(9, 45)    # 수입/지출 입력행 9~44 (합계 SUM(D9:D44)/SUM(H9:H44) 범위와 일치)
DB_COL_INCOME_DETAIL = 3       # C 내역(수입)
DB_COL_INCOME_AMOUNT = 4       # D 금액(수입)
DB_COL_EXP_ITEM = 5            # E 항목(지출)
DB_COL_EXP_DETAIL = 6          # F 내역(지출)
DB_COL_EXP_VENDOR = 7          # G 거래처(지출)
DB_COL_EXP_AMOUNT = 8          # H 금액(지출)
CELL_OPENING = "B45"           # 기초잔액 (=M45)
CELL_INCOME_TOTAL = "D45"      # 수입계 (=SUM(D9:D44))
CELL_EXPENSE_TOTAL = "H45"     # 지출계 (=SUM(H9:H44))
CELL_CLOSING = "I45"           # 기말잔액 (=B45+D45-H45)

VERIFY_SHEET_NAME = "_검증"    # 복사본에 추가하는 신규 검증 시트(원본엔 없음)


def _norm(s) -> str:
    return "".join(str(s or "").split())


class LayoutError(RuntimeError):
    """양식 레이아웃이 기대 좌표와 불일치할 때 발생."""


def verify_layout(ws) -> None:
    """대상 시트가 기대 좌표·헤더를 갖는지 검증. 불일치 시 LayoutError.

    조용한 오기입을 막기 위해, 쓰기 전에 반드시 호출한다.
    """
    cell = ws.cell
    expect = {
        (8, COL_PREV): "전일잔액",
        (8, COL_DELTA): "증감액",
        (8, COL_TODAY): "당일잔액",
        (8, COL_ALIAS): "별칭",
    }
    problems = []
    for (r, c), text in expect.items():
        got = _norm(cell(r, c).value)
        if text not in got:
            col = openpyxl.utils.get_column_letter(c)
            problems.append(f"{col}{r} 기대 '{text}' 실제 '{got}'")
    # 자금일계표 헤더(8행 C/E)
    if "내역" not in _norm(cell(8, DB_COL_INCOME_DETAIL).value):
        problems.append("C8 '내역' 헤더 불일치")
    if "항목" not in _norm(cell(8, DB_COL_EXP_ITEM).value):
        problems.append("E8 '항목' 헤더 불일치")
    if "자금일계표" not in _norm(ws[DAYBOOK_TITLE_CELL].value):
        problems.append("B6 '자금일계표' 타이틀 불일치")
    if problems:
        raise LayoutError("양식 레이아웃 불일치: " + "; ".join(problems))
