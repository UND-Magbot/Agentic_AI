# -*- coding: utf-8 -*-
"""자금계획_원화 / 자금계획_외화 → 해당 일자 블록의 수입·지출 라인 추출.

소스: `[사내양식]자금계획…xlsx` 의 `자금계획_원화`(KRW)·`자금계획_외화`(USD) 시트.
두 시트는 열 구조가 동일하다(F=금액, N=지출금액). 외화 시트는 단위가 USD($)일 뿐
원화로 환산하지 않는다 — 원화는 원화끼리, 외화는 외화끼리 따로 대조한다.
구조: 일자 블록 반복. B열 = 일자 마커('5/29' 등). 블록 종료 = B열 '계' 행.
좌측 수입: C 내역, D 거래처, E 발생계좌, F 금액, G 비고.  ('계' 행 G = 수입소계)
"""
from __future__ import annotations

import datetime
import re
from pathlib import Path

from .models import CurrencyPlan, DaybookLine, PlanLine

SHEET_NAME = "자금계획_원화"        # 원화(KRW) 계획 시트
SHEET_NAME_FX = "자금계획_외화"     # 외화(USD) 계획 시트 — 동일 열 구조, 단위만 USD

# 1-indexed 열 — 수입(좌측)
_C_DATE = 2     # B 일자 마커 / '계'
_C_DETAIL = 3   # C 내역
_C_VENDOR = 4   # D 거래처
_C_ACCOUNT = 5  # E 발생계좌
_C_AMOUNT = 6   # F 금액
_C_NOTE = 7     # G 비고 / 수입소계('계' 행)
# 지출(우측) — H 항목, I 적요, J 납부방법, K 발생계좌, L 거래처, M 회계처리, N 금액, O 비고
_C_EXP_ITEM = 8     # H 항목
_C_EXP_DESC = 9     # I 적요
_C_EXP_VENDOR = 12  # L 거래처
_C_EXP_AMOUNT = 14  # N 금액
_END_MARK = "계"


def date_to_marker(date: str) -> str:
    """'2026-05-29' → '5/29' (선행 0 제거, 자금계획 B열 표기)."""
    _, mm, dd = date.split("-")
    return f"{int(mm)}/{int(dd)}"


_DATE_RE = re.compile(r"^\s*(\d{1,2})[/-](\d{1,2})\s*$")


def _marker_md(b) -> tuple[int, int, int | None] | None:
    """B열 값 → (월, 일, 명시연도|None). 날짜 마커가 아니면 None.

    datetime(2026-05-29)은 연도 명시, 문자열 '5/29'·'5-29'는 연도 생략.
    """
    if isinstance(b, (datetime.date, datetime.datetime)):
        return (b.month, b.day, b.year)
    if b is None:
        return None
    s = str(b).strip()
    if s == _END_MARK:
        return None
    m = _DATE_RE.match(s)
    if m:
        return (int(m.group(1)), int(m.group(2)), None)
    return None


def _resolve_block_start(rows, date: str) -> int | None:
    """대상 일자(YYYY-MM-DD) 블록의 시작 행 인덱스. 연도 생략 누적 시트 보정.

    자금계획 B열 날짜 마커는 연도가 생략('5/29')되거나 datetime(2026-05-29)으로
    섞여 있고, 시트는 여러 해(2024-12-30~)에 걸쳐 누적된다. 따라서 같은 '5/29'가
    2025·2026 두 번 나타난다. datetime 마커의 명시 연도를 앵커로, 월 롤오버(전월보다
    작아지면 연도 +1)로 각 마커의 연도를 해석해 정확한 연-월-일 블록을 찾는다.
    """
    ty, tm, td = (int(x) for x in date.split("-"))
    markers: list[list] = []  # [row_index, month, day, explicit_year|None]
    for i, row in enumerate(rows):
        md = _marker_md(_cell(row, _C_DATE))
        if md is not None:
            markers.append([i, md[0], md[1], md[2]])
    if not markers:
        return None
    n = len(markers)
    year: list[int | None] = [None] * n
    # 1) 앞으로 전파 — 명시 연도 앵커 + 월 롤오버(전월보다 작아지면 +1).
    cur: int | None = None
    prev_m: int | None = None
    for k in range(n):
        m, ey = markers[k][1], markers[k][3]
        if ey is not None:
            cur = ey
        elif cur is not None and prev_m is not None and m < prev_m:
            cur += 1
        year[k] = cur
        prev_m = m
    # 2) 첫 앵커 이전 구간 — 뒤로 전파(역 롤오버: 앞 마커의 월이 더 크면 전년).
    fk = next((k for k in range(n) if year[k] is not None), None)
    if fk is not None:
        for k in range(fk - 1, -1, -1):
            year[k] = year[k + 1] - (1 if markers[k][1] > markers[k + 1][1] else 0)
    # 3) 대상 연-월-일 매칭.
    for k in range(n):
        if year[k] == ty and markers[k][1] == tm and markers[k][2] == td:
            return markers[k][0]
    return None


def _num(v) -> float:
    if v is None or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return 0.0


def read_income_block(path: Path, date: str, sheet: str = SHEET_NAME) -> list[PlanLine]:
    """대상 일자의 수입 블록 라인들을 반환. 일자 블록을 못 찾으면 빈 리스트.

    블록은 B열 일자 마커 행에서 시작(그 행도 데이터 포함)하여 다음 'B=계' 행 직전까지.
    금액(F)이 있는 행만 수입 라인으로 채택(빈 행·구분 행 제외).
    """
    import openpyxl

    wb = openpyxl.load_workbook(Path(path), data_only=True, read_only=True)
    if sheet not in wb.sheetnames:
        raise ValueError(f"'{sheet}' 시트가 없습니다: {wb.sheetnames}")
    ws = wb[sheet]

    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    start = _resolve_block_start(rows, date)
    if start is None:
        return []

    lines: list[PlanLine] = []
    for i in range(start, len(rows)):
        row = rows[i]
        b = row[_C_DATE - 1] if len(row) >= _C_DATE else None
        if i > start and b is not None and str(b).strip() == _END_MARK:
            break
        amount = _num(row[_C_AMOUNT - 1] if len(row) >= _C_AMOUNT else None)
        detail = row[_C_DETAIL - 1] if len(row) >= _C_DETAIL else None
        if amount <= 0 and not detail:
            continue
        lines.append(PlanLine(
            detail=str(detail or ""),
            vendor=str(row[_C_VENDOR - 1] or "") if len(row) >= _C_VENDOR else "",
            account=str(row[_C_ACCOUNT - 1] or "") if len(row) >= _C_ACCOUNT else "",
            amount=amount,
            note=str(row[_C_NOTE - 1] or "") if len(row) >= _C_NOTE else "",
        ))
    return lines


def _cell(row, idx):
    return row[idx - 1] if len(row) >= idx else None


def read_blocks(
    path: Path, date: str, sheet: str = SHEET_NAME, *, include_text_only: bool = False
) -> tuple[list[DaybookLine], list[DaybookLine]]:
    """대상 일자 블록에서 (수입 라인, 지출 라인) 을 DaybookLine 으로 반환.

    수입(좌측): C 내역, D 거래처, F 금액. 지출(우측): H 항목, I 적요, L 거래처, N 금액.
    지출 라인은 H 항목을 DaybookLine.item 에 함께 담는다(자금일계표 E열 기입용).
    sheet 로 `자금계획_원화`(KRW)·`자금계획_외화`(USD) 를 선택한다(열 구조 동일).

    include_text_only=False(기본): 금액(F/N)이 있는 라인만 — 계획↔실적 대조·합계용.
    include_text_only=True: 금액이 없어도 내역/항목/적요 텍스트가 있으면 포함 — 자금일계표
        기입 시 수작업 양식처럼 메모성 줄(예: 금액 없는 '퇴직연금계좌')도 그대로 남기기 위함.
    """
    import openpyxl

    wb = openpyxl.load_workbook(Path(path), data_only=True, read_only=True)
    if sheet not in wb.sheetnames:
        raise ValueError(f"'{sheet}' 시트가 없습니다: {wb.sheetnames}")
    ws = wb[sheet]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    start = _resolve_block_start(rows, date)
    if start is None:
        return [], []

    income: list[DaybookLine] = []
    expense: list[DaybookLine] = []
    for i in range(start, len(rows)):
        row = rows[i]
        b = _cell(row, _C_DATE)
        if i > start and b is not None and str(b).strip() == _END_MARK:
            break
        inc_amt = _num(_cell(row, _C_AMOUNT))
        inc_detail = _cell(row, _C_DETAIL)
        if inc_detail and (inc_amt > 0 or include_text_only):
            income.append(DaybookLine(
                side="income", detail=str(inc_detail or ""),
                vendor=str(_cell(row, _C_VENDOR) or ""), amount=inc_amt, row=i + 1,
            ))
        exp_amt = _num(_cell(row, _C_EXP_AMOUNT))
        exp_item = _cell(row, _C_EXP_ITEM)
        exp_desc = _cell(row, _C_EXP_DESC)
        if (exp_item or exp_desc) and (exp_amt > 0 or include_text_only):
            expense.append(DaybookLine(
                side="expense", detail=str(exp_desc or exp_item or ""),
                vendor=str(_cell(row, _C_EXP_VENDOR) or ""), amount=exp_amt, row=i + 1,
                item=str(exp_item or ""),
            ))
    return income, expense


def block_start_row(path: Path, date: str, sheet: str = SHEET_NAME) -> int | None:
    """대상 일자 블록의 시작 행(1-based). 자금일계표 행을 자금계획 행 구조에 맞춰
    배치(일계표행 = 9 + (자금계획행 − 블록시작행))하는 데 쓴다. 못 찾으면 None.
    """
    import openpyxl

    wb = openpyxl.load_workbook(Path(path), data_only=True, read_only=True)
    if sheet not in wb.sheetnames:
        wb.close()
        return None
    rows = list(wb[sheet].iter_rows(values_only=True))
    wb.close()
    start = _resolve_block_start(rows, date)
    return None if start is None else start + 1


def read_blocks_both(path: Path, date: str) -> "CurrencyPlan":
    """원화·외화 계획을 각각 통화 단위로 읽어 분리 보관(환산하지 않음).

    `자금계획_외화` 시트가 없는 양식이면 외화 블록은 빈 리스트로 둔다.
    """
    krw_income, krw_expense = read_blocks(path, date, SHEET_NAME)
    try:
        usd_income, usd_expense = read_blocks(path, date, SHEET_NAME_FX)
    except ValueError:
        usd_income, usd_expense = [], []
    return CurrencyPlan(
        krw_income=krw_income, krw_expense=krw_expense,
        usd_income=usd_income, usd_expense=usd_expense,
    )
