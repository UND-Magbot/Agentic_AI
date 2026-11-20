# -*- coding: utf-8 -*-
"""매출 내역 엑셀(사용자 2026-11-20 — 재무 관리와 연계) — 매출일 = 잔금(마지막 회차) 세금계산서 발행일(flow.revenue_date).

범위: 연도별(그해 전부) · 분기별 · 월별. 시트 1 '매출 내역' — 월마다 묶어서, 한 건 = 한 덩어리:
  세금계산서 발행일(잔금) · 고객사 · 견적서 번호 | 품목(여러 줄) · 수량 · 금액(공급가액) | 건 공급가액 · 부가세 · 합계(부가세 포함)
  건마다 앞쪽 칸과 합계 칸은 품목 줄 수만큼 세로로 합친다. 월마다 소계, 맨 아래 총계. 시트 2 '요약' — 월별 건수·수량·금액.
금액은 원 미만 절삭(money.py). 건 공급가액 = 영업 건 공급가액(flow.supply_total), 부가세 = 세금계산서 회차마다의 부가세 합.
품목 금액 = 견적 품목 수량 × 단가(부가세 포함 견적이면 공급가로) — NEGO·물류비 줄도 그대로 보여 건 공급가액과 맞춰 볼 수 있게.
드랍 건·매출일이 없는 건(세금계산서 회차를 끝까지 확인하지 않은 건)은 넣지 않는다.
"""
from __future__ import annotations

import io
from typing import Any

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from .. import money
from . import flow

UNITS = ("year", "quarter", "month")
HEAD = ["세금계산서 발행일(잔금)", "고객사", "견적서 번호", "품목", "수량", "금액(공급가액)", "건 공급가액", "부가세", "합계(부가세 포함)"]
WIDTH = [16, 26, 20, 38, 8, 15, 15, 13, 16]
MERGED = (0, 1, 2, 6, 7, 8)                      # 건마다 세로로 합치는 칸(발행일·고객사·번호 · 건 합계 셋)
WON = "#,##0"
THIN = Side(style="thin", color="D0D5DD")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEAD_FILL = PatternFill("solid", fgColor="E8EEF7")
SUB_FILL = PatternFill("solid", fgColor="F3F4F6")
TOTAL_FILL = PatternFill("solid", fgColor="DCE6F5")


class RevenueError(Exception):
    pass


def period(year: int, unit: str, value: int | None) -> tuple[list[int], str]:
    """(포함할 달 목록, 제목 글)."""
    if unit not in UNITS:
        raise RevenueError("단위는 연도별·분기별·월별 중에서 골라 주세요.")
    if not 2000 <= year <= 2100:
        raise RevenueError("연도를 확인해 주세요.")
    if unit == "year":
        return list(range(1, 13)), f"{year}년"
    if unit == "quarter":
        if value not in (1, 2, 3, 4):
            raise RevenueError("분기(1~4)를 골라 주세요.")
        return [3 * (value - 1) + k for k in (1, 2, 3)], f"{year}년 {value}분기"
    if not value or not 1 <= value <= 12:
        raise RevenueError("월(1~12)을 골라 주세요.")
    return [value], f"{year}년 {value}월"


def _items(d: dict) -> list[tuple[str, float, int]]:
    """견적 품목 → [(품목, 수량, 금액(공급가액))]. 품목이 없는 예전 건은 빈 목록."""
    q = d.get("quote") or {}
    vat = bool(q.get("vat_included"))
    out = []
    for it in q.get("items") or []:
        name = str(it.get("name") or it.get("commodity") or "").strip() or "-"
        qty = float(it.get("qty") or 0)
        up = it.get("unit_price")
        if not isinstance(up, (int, float)):
            out.append((name, qty, 0))
            continue
        unit = money.supply_of(up) if vat else up
        out.append((name, qty, money.won(unit * qty)))
    return out


def collect(rows: list[dict], year: int, months: list[int]) -> list[dict]:
    """기간 안의 매출 건 — 발행일 순."""
    out = []
    for d in rows:
        if d.get("dropped"):
            continue
        day = flow.revenue_date(d)
        if not day or int(day[:4]) != year or int(day[5:7]) not in months:
            continue
        tax = flow.tax_invoices(d)
        supply = money.won(flow.supply_total(d) or 0)
        vat = sum(r["vat"] for r in tax["rows"])
        out.append({"date": day, "customer": d.get("customer") or "", "no": d.get("display_no") or d.get("deal_no") or "",
                    "items": _items(d), "supply": supply, "vat": vat, "total": supply + vat})
    out.sort(key=lambda x: (x["date"], x["customer"]))
    return out


def _clean(v: Any) -> Any:
    return ILLEGAL_CHARACTERS_RE.sub("", v) if isinstance(v, str) else v


def build_xlsx(rows: list[dict], year: int, unit: str, value: int | None) -> tuple[str, bytes]:
    """(파일 이름에 쓸 기간 글, 엑셀 바이트)."""
    months, title = period(year, unit, value)
    deals = collect(rows, year, months)
    wb = Workbook()
    ws = wb.active
    ws.title = "매출 내역"
    ws.append([f"{title} 매출 내역 — 공급가액 · 잔금 세금계산서 발행일 기준 (드랍·세금계산서 미완료 건 제외)"])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append([])
    ws.append(HEAD)
    for j, w in enumerate(WIDTH, start=1):
        c = ws.cell(row=3, column=j)
        c.font, c.fill, c.border = Font(bold=True), HEAD_FILL, BOX
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.column_dimensions[c.column_letter].width = w
    ws.freeze_panes = "A4"
    r = 4
    grand = {"n": 0, "qty": 0.0, "supply": 0, "vat": 0, "total": 0}
    summary = []
    for m in months:
        mine = [x for x in deals if int(x["date"][5:7]) == m]
        if not mine:
            summary.append((m, 0, 0.0, 0, 0, 0))
            continue
        sub = {"n": len(mine), "qty": 0.0, "supply": 0, "vat": 0, "total": 0}
        for x in mine:
            items = x["items"] or [("-", 0.0, x["supply"])]
            top = r
            for k, (name, qty, amt) in enumerate(items):
                vals = [x["date"], x["customer"], x["no"], name, qty if qty else None, amt,
                        x["supply"], x["vat"], x["total"]] if k == 0 else [None, None, None, name, qty if qty else None, amt, None, None, None]
                for j, v in enumerate(vals, start=1):
                    c = ws.cell(row=r, column=j, value=_clean(v))
                    c.border = BOX
                    c.alignment = Alignment(vertical="top", wrap_text=j == 4)
                    if j in (6, 7, 8, 9):
                        c.number_format = WON
                    if j == 5:
                        c.number_format = "#,##0.##"
                sub["qty"] += qty
                r += 1
            if r - 1 > top:                           # 건 하나 = 한 덩어리 — 앞쪽·합계 칸 세로로 합침
                for j in MERGED:
                    ws.merge_cells(start_row=top, start_column=j + 1, end_row=r - 1, end_column=j + 1)
            for k in ("supply", "vat", "total"):
                sub[k] += x[k]
        vals = [f"{year}년 {m}월 소계", f"{sub['n']}건", None, None, sub["qty"], None, sub["supply"], sub["vat"], sub["total"]]
        for j, v in enumerate(vals, start=1):
            c = ws.cell(row=r, column=j, value=v)
            c.font, c.fill, c.border = Font(bold=True), SUB_FILL, BOX
            if j in (7, 8, 9):
                c.number_format = WON
            if j == 5:
                c.number_format = "#,##0.##"
        r += 1
        summary.append((m, sub["n"], sub["qty"], sub["supply"], sub["vat"], sub["total"]))
        for k in grand:
            grand[k] += sub[k]
    vals = [f"{title} 총계", f"{grand['n']}건", None, None, grand["qty"], None, grand["supply"], grand["vat"], grand["total"]]
    for j, v in enumerate(vals, start=1):
        c = ws.cell(row=r, column=j, value=v)
        c.font, c.fill, c.border = Font(bold=True), TOTAL_FILL, BOX
        if j in (7, 8, 9):
            c.number_format = WON
        if j == 5:
            c.number_format = "#,##0.##"
    if not deals:
        ws.cell(row=r + 2, column=1, value="이 기간에 잔금 세금계산서까지 확인한 건이 없습니다.")
    # 요약 — 월별
    sm = wb.create_sheet("요약")
    sm.append([f"{title} 매출 요약 — 공급가액 · 잔금 세금계산서 발행일 기준"])
    sm["A1"].font = Font(bold=True, size=13)
    sm.append([])
    sm.append(["월", "건수", "수량 합계", "공급가액", "부가세", "합계(부가세 포함)"])
    for j, w in enumerate([10, 8, 12, 16, 14, 18], start=1):
        c = sm.cell(row=3, column=j)
        c.font, c.fill, c.border = Font(bold=True), HEAD_FILL, BOX
        sm.column_dimensions[c.column_letter].width = w
    for m, n, qty, sup, vat, tot in summary:
        sm.append([f"{m}월", n, qty, sup, vat, tot])
    sm.append([title, grand["n"], grand["qty"], grand["supply"], grand["vat"], grand["total"]])
    for row in sm.iter_rows(min_row=4, max_row=sm.max_row):
        for c in row:
            c.border = BOX
            if c.column >= 4:
                c.number_format = WON
            if c.column == 3:
                c.number_format = "#,##0.##"
        if row[0].row == sm.max_row:
            for c in row:
                c.font, c.fill = Font(bold=True), TOTAL_FILL
    buf = io.BytesIO()
    wb.save(buf)
    return title, buf.getvalue()
