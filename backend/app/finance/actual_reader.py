# -*- coding: utf-8 -*-
"""자금실적 `MM-DD` 시트 읽기 — 계좌 List 메타·전일잔액(확정값)·기존값(정답).

원본은 read-only(data_only=True)로만 읽는다. 여기서 얻은 전일잔액 M 은 불변 기준값.
원본에 이미 채워진 당일잔액 O 는 (A) 재현검증의 '정답'으로만 대조한다.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import layout as L
from .models import DaybookLine
from .tx_reader import account_key


@dataclass
class ActualAccountRow:
    """05-29 시트 계좌 List 의 한 계좌 행(읽기 결과)."""

    row: int
    bank: str
    account_raw: str
    account_key: str             # 정규화 키(숫자만) — 거래내역 매칭용
    alias: str
    prev_balance: float          # M 전일잔액(확정)
    original_delta: float | None  # N 증감액(원본 기존값, 검증용)
    original_today: float | None  # O 당일잔액(원본 기존값, 정답)
    section: str                  # common | project | foreign
    is_foreign: bool


@dataclass
class DaybookActuals:
    """자금일계표 합계행 실측(읽기)."""

    opening: float | None       # B45 기초잔액
    income_total: float | None  # D45 수입계
    expense_total: float | None # H45 지출계
    closing: float | None       # I45 기말잔액
    delta_today: float | None   # D6 금일 증감액(원화)
    delta_today_fx: float | None = None  # F6 금일 증감액(외화 USD, 환산 안 함)


def _f(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def date_to_sheet(date: str) -> str:
    """'2026-05-29' → '05-29' (시트명)."""
    parts = date.split("-")
    return f"{parts[1]}-{parts[2]}"


def _read_section(ws, rows, section: str, is_foreign: bool) -> list[ActualAccountRow]:
    out: list[ActualAccountRow] = []
    last_bank = ""
    for r in rows:
        bank = ws.cell(r, L.COL_BANK).value
        acc = ws.cell(r, L.COL_ACCOUNT).value
        if bank:
            last_bank = str(bank)
        if not acc:
            continue  # 계좌 없는 행은 건너뜀
        out.append(ActualAccountRow(
            row=r,
            bank=last_bank,
            account_raw=str(acc),
            account_key=account_key(acc),
            alias=str(ws.cell(r, L.COL_ALIAS).value or ""),
            prev_balance=_f(ws.cell(r, L.COL_PREV).value) or 0.0,
            original_delta=_f(ws.cell(r, L.COL_DELTA).value),
            original_today=_f(ws.cell(r, L.COL_TODAY).value),
            section=section,
            is_foreign=is_foreign,
        ))
    return out


def read_account_rows(ws) -> list[ActualAccountRow]:
    """계좌 List 전체(보통+과제+외화)를 읽는다.

    Note: read_only 워크북은 ws.cell 랜덤접근이 느리므로, 일반(비 read_only)
    워크북에서 호출하는 것을 권장. run_daily 는 원본을 일반 모드(data_only=True)로 연다.
    """
    rows: list[ActualAccountRow] = []
    rows += _read_section(ws, L.COMMON_ROWS, "common", False)
    rows += _read_section(ws, L.PROJECT_ROWS, "project", False)
    rows += _read_section(ws, L.FOREIGN_ROWS, "foreign", True)
    return rows


def read_daybook_actuals(ws) -> DaybookActuals:
    """자금일계표 합계행(B45/D45/H45/I45/D6) 실측값을 읽는다."""
    return DaybookActuals(
        opening=_f(ws[L.CELL_OPENING].value),
        income_total=_f(ws[L.CELL_INCOME_TOTAL].value),
        expense_total=_f(ws[L.CELL_EXPENSE_TOTAL].value),
        closing=_f(ws[L.CELL_CLOSING].value),
        delta_today=_f(ws[L.CELL_DELTA_TODAY].value),
        delta_today_fx=_f(ws[L.CELL_DELTA_TODAY_FX].value),
    )


def read_daybook_lines(ws) -> tuple[list[DaybookLine], list[DaybookLine]]:
    """자금일계표 입력행(9~31)의 (수입 라인, 지출 라인)을 읽는다.

    수입(좌): C 내역, D 금액. 지출(우): E 항목, F 내역, G 거래처, H 금액.
    금액이 있는 라인만 채택. 빈 시트(신규 일자)면 빈 리스트.
    """
    income: list[DaybookLine] = []
    expense: list[DaybookLine] = []
    for r in L.DAYBOOK_ROWS:
        inc_amt = _f(ws.cell(r, L.DB_COL_INCOME_AMOUNT).value)
        inc_detail = ws.cell(r, L.DB_COL_INCOME_DETAIL).value
        if inc_amt and inc_detail:
            income.append(DaybookLine(
                side="income", detail=str(inc_detail), vendor="",
                amount=inc_amt, row=r,
            ))
        exp_amt = _f(ws.cell(r, L.DB_COL_EXP_AMOUNT).value)
        exp_item = ws.cell(r, L.DB_COL_EXP_ITEM).value
        if exp_amt and exp_item:
            detail = ws.cell(r, L.DB_COL_EXP_DETAIL).value or exp_item
            expense.append(DaybookLine(
                side="expense", detail=str(detail),
                vendor=str(ws.cell(r, L.DB_COL_EXP_VENDOR).value or ""),
                amount=exp_amt, row=r,
            ))
    return income, expense
