# -*- coding: utf-8 -*-
"""월마감 자료(예: `5월마감_…HQ`) → 자금계획에 배치할 입금예정(수입)·결제예정(지출).

마감자료 시트 구조(본사/HQ 양식):
  E3 = 마감 기준일(datetime). 섹션은 A열 앵커로 구분한다.
    1-1]원화 매출 / 1-2]외화 매출      ← 확정매출(정보성, 입금일정은 섹션2)
    2-1]원화 입금 예정 / 2-2]외화 입금 예정 ← 자금계획 '수입'으로 배치
    3-1]원화 결제 예정 / 3-2]외화 결제 예정 ← 자금계획 '지출'로 배치
  각 섹션: '소계' 행 → '거래처' 헤더 행 → 데이터 행들(거래처 A열이 비면 종료).

날짜 해석(사용자 확정):
  '계산서 발행 후 N일' = 마감 기준일 이후 N일. 단 마감자료는 이미 입금일정/결제예정일2
  컬럼에 구체 날짜(6/30·7/31 등)를 적어두므로 **명시 날짜를 우선**하고, 명시 날짜가 없고
  '…후 N일' 패턴만 있으면 마감기준일 + N일로 계산한다. 둘 다 없으면 None(검토필요)로 두고
  호출측이 대상 월 첫 영업일에 배치한다.

확정금액(합계금액 D열, 외화는 #VALUE! 면 공급가액+부가세)이 0 이하인 행은 미확정으로 보아
제외한다 — 마감 시점에 금액이 잡힌 건만 계획에 반영한다.
"""
from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from pathlib import Path

# 마감자료 HQ 시트의 열(1-based) — 입금/결제 공통 선두 4열은 동일.
_A_VENDOR = 1   # A 거래처
_A_SUPPLY = 2   # B 공급가액
_A_VAT = 3      # C 부가세
_A_TOTAL = 4    # D 합계금액
_A_INC_SCHED = 5  # E 입금 일정(섹션2)
# 섹션3 결제 예정(원화): H 결제조건, J 결제예정일2
_A_PAY_COND = 8   # H 결제조건
_A_PAY_DATE = 10  # J 결제예정일2(원화) / 비고2(외화)

_SHEET_HINT = "HQ"  # 본사 마감 시트명에 포함되는 토큰(우선 선택)

# 섹션 앵커 — '2-1]', '2-2]', '3-1]', '3-2]'.
_ANCHOR_INC_KRW = re.compile(r"2\s*-\s*1\]")
_ANCHOR_INC_FX = re.compile(r"2\s*-\s*2\]")
_ANCHOR_PAY_KRW = re.compile(r"3\s*-\s*1\]")
_ANCHOR_PAY_FX = re.compile(r"3\s*-\s*2\]")
_HEADER_VENDOR = "거래처"

# 날짜 토큰: '6/30', '7/31일', '6/20일 결재' 등에서 M/D.
_MD_RE = re.compile(r"(?<!\d)(\d{1,2})\s*/\s*(\d{1,2})")
# '계산서 발행 후 30일', '마감 후 60일', '마감후 30일' 등.
_AFTER_N_RE = re.compile(r"(?:계산서\s*발행\s*후|마감\s*후|마감후)\s*(\d{1,3})\s*일")


@dataclass
class ClosingLine:
    """마감자료의 한 줄(입금 또는 결제). 자금계획 한 라인으로 배치된다."""

    vendor: str
    amount: float
    date: datetime.date | None          # 해석된 결제/입금 예정일(없으면 None=검토필요)
    schedule_text: str                  # 원본 일정/조건 텍스트(비고로 보존)
    currency: str                       # 'KRW' | 'USD'
    side: str                           # 'income' | 'expense'
    bank: str = ""                      # 결제 섹션 은행(있으면)
    account: str = ""                   # 결제 섹션 계좌번호(있으면)


@dataclass
class ClosingData:
    """마감자료 1건에서 추출한 입금·결제 예정 묶음."""

    base_date: datetime.date
    krw_income: list[ClosingLine] = field(default_factory=list)
    krw_expense: list[ClosingLine] = field(default_factory=list)
    usd_income: list[ClosingLine] = field(default_factory=list)
    usd_expense: list[ClosingLine] = field(default_factory=list)
    skipped_zero: int = 0               # 금액 0(미확정)으로 제외한 행 수
    undated: int = 0                    # 날짜 미해석 행 수(검토필요)

    @property
    def all_lines(self) -> list[ClosingLine]:
        return self.krw_income + self.krw_expense + self.usd_income + self.usd_expense


def _num(v) -> float:
    if v is None or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def _resolve_date(text: str, base: datetime.date) -> datetime.date | None:
    """일정/조건 텍스트 → 예정일. 명시 M/D 우선, 없으면 '…후 N일' = 마감기준일+N.

    M/D 의 연도는 마감 기준연도를 기본으로 하되, 월이 마감월보다 작으면 +1년(연말 롤오버).
    """
    if isinstance(text, (datetime.date, datetime.datetime)):
        d = text.date() if isinstance(text, datetime.datetime) else text
        return d
    s = str(text or "").strip()
    if not s:
        return None
    m = _MD_RE.search(s)
    if m:
        mm, dd = int(m.group(1)), int(m.group(2))
        if 1 <= mm <= 12 and 1 <= dd <= 31:
            year = base.year + (1 if mm < base.month else 0)
            try:
                return datetime.date(year, mm, dd)
            except ValueError:
                return None
    a = _AFTER_N_RE.search(s)
    if a:
        return base + datetime.timedelta(days=int(a.group(1)))
    return None


def _cell(ws, r: int, c: int):
    return ws.cell(r, c).value


def _find_anchor(ws, pat: re.Pattern) -> int | None:
    for r in range(1, ws.max_row + 1):
        a = _cell(ws, r, _A_VENDOR)
        if a and pat.search(str(a)):
            return r
    return None


def _header_row_after(ws, anchor: int) -> int | None:
    """앵커 다음의 '거래처' 헤더 행을 찾는다(보통 앵커+2)."""
    for r in range(anchor + 1, min(anchor + 6, ws.max_row + 1)):
        if str(_cell(ws, r, _A_VENDOR) or "").strip() == _HEADER_VENDOR:
            return r
    return None


def _read_section(
    ws, anchor_pat: re.Pattern, *, side: str, currency: str, base: datetime.date,
    next_anchors: list[re.Pattern], date_from: str,
) -> tuple[list[ClosingLine], int, int]:
    """한 섹션의 데이터 행을 ClosingLine 리스트로. (lines, skipped_zero, undated).

    date_from='income': E열 입금일정에서 날짜. date_from='expense': J열(원화 결제예정일2)
    우선, 없으면 H열 결제조건에서.
    """
    anchor = _find_anchor(ws, anchor_pat)
    if anchor is None:
        return [], 0, 0
    header = _header_row_after(ws, anchor)
    if header is None:
        return [], 0, 0
    # 종료 경계: 다음 섹션 앵커 행(가장 가까운 것) 또는 시트 끝.
    stop = ws.max_row + 1
    for pat in next_anchors:
        a2 = _find_anchor(ws, pat)
        if a2 is not None and header < a2 < stop:
            stop = a2

    lines: list[ClosingLine] = []
    skipped = 0
    undated = 0
    for r in range(header + 1, stop):
        vendor = _cell(ws, r, _A_VENDOR)
        if vendor is None or str(vendor).strip() == "":
            continue
        vs = str(vendor).strip()
        if vs in ("소계", _HEADER_VENDOR):
            continue
        total = _num(_cell(ws, r, _A_TOTAL))
        if total <= 0:  # 외화는 D열이 #VALUE! 일 수 있음 → 공급가액+부가세로 보강.
            total = _num(_cell(ws, r, _A_SUPPLY)) + _num(_cell(ws, r, _A_VAT))
        if total <= 0:
            skipped += 1
            continue
        if date_from == "income":
            sched_raw = _cell(ws, r, _A_INC_SCHED)
            date = _resolve_date(sched_raw, base)
        else:
            # 결제: J(결제예정일2) 우선, 단 J가 '날짜로 해석되면'만 채택. J가 메모('26Y 1Q' 등)라
            # 해석 실패하면 H(결제조건: '마감 후 N일' 등)로 재시도한다. 둘 다 실패면 원본 텍스트 보존.
            j = _cell(ws, r, _A_PAY_DATE)
            h = _cell(ws, r, _A_PAY_COND)
            date = _resolve_date(j, base)
            if date is not None:
                sched_raw = j
            else:
                dh = _resolve_date(h, base)
                date, sched_raw = (dh, h) if dh is not None else (None, j or h)
        if date is None:
            undated += 1
        bank = "" if side == "income" else str(_cell(ws, r, 5) or "")   # E 은행(결제)
        acct = "" if side == "income" else str(_cell(ws, r, 6) or "")   # F 계좌(결제)
        lines.append(ClosingLine(
            vendor=vs, amount=round(total, 2), date=date,
            schedule_text=str(sched_raw or "").strip(),
            currency=currency, side=side, bank=bank, account=acct,
        ))
    return lines, skipped, undated


def pick_closing_sheet(path: Path) -> str:
    """마감 데이터가 든 시트명을 고른다 — 'HQ' 포함 시트 우선, 없으면 행수 최대 시트."""
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True, read_only=False)
    try:
        for name in wb.sheetnames:
            if _SHEET_HINT in name:
                return name
        # 폴백: 섹션 앵커가 보이는 시트.
        for name in wb.sheetnames:
            ws = wb[name]
            if _find_anchor(ws, _ANCHOR_INC_KRW) is not None:
                return name
        return wb.sheetnames[0]
    finally:
        wb.close()


def read_closing(path: Path, sheet: str | None = None) -> ClosingData:
    """마감자료 → ClosingData(입금예정=수입, 결제예정=지출, 원화/외화 분리)."""
    import openpyxl

    path = Path(path)
    if sheet is None:
        sheet = pick_closing_sheet(path)
    wb = openpyxl.load_workbook(path, data_only=True, read_only=False)
    try:
        ws = wb[sheet]
        base = _detect_base_date(ws)

        nx = [_ANCHOR_INC_FX, _ANCHOR_PAY_KRW, _ANCHOR_PAY_FX]
        krw_inc, s1, u1 = _read_section(
            ws, _ANCHOR_INC_KRW, side="income", currency="KRW", base=base,
            next_anchors=nx, date_from="income")
        usd_inc, s2, u2 = _read_section(
            ws, _ANCHOR_INC_FX, side="income", currency="USD", base=base,
            next_anchors=[_ANCHOR_PAY_KRW, _ANCHOR_PAY_FX], date_from="income")
        krw_exp, s3, u3 = _read_section(
            ws, _ANCHOR_PAY_KRW, side="expense", currency="KRW", base=base,
            next_anchors=[_ANCHOR_PAY_FX], date_from="expense")
        usd_exp, s4, u4 = _read_section(
            ws, _ANCHOR_PAY_FX, side="expense", currency="USD", base=base,
            next_anchors=[], date_from="expense")
    finally:
        wb.close()

    return ClosingData(
        base_date=base,
        krw_income=krw_inc, krw_expense=krw_exp,
        usd_income=usd_inc, usd_expense=usd_exp,
        skipped_zero=s1 + s2 + s3 + s4, undated=u1 + u2 + u3 + u4,
    )


def _detect_base_date(ws) -> datetime.date:
    """'마감 기준일' 라벨 옆 셀에서 기준일을 찾는다. 없으면 datetime 셀 최빈/최댓값."""
    for r in range(1, min(ws.max_row, 15) + 1):
        for c in range(1, min(ws.max_column, 10) + 1):
            v = _cell(ws, r, c)
            if isinstance(v, str) and "마감" in v and "기준" in v:
                # 같은 행 오른쪽에서 날짜 탐색.
                for c2 in range(c + 1, min(ws.max_column, c + 4) + 1):
                    nv = _cell(ws, r, c2)
                    if isinstance(nv, (datetime.date, datetime.datetime)):
                        return nv.date() if isinstance(nv, datetime.datetime) else nv
    # 폴백: 시트 내 가장 큰 datetime 의 그 달 말일.
    best: datetime.date | None = None
    for r in range(1, min(ws.max_row, 40) + 1):
        for c in range(1, min(ws.max_column, 10) + 1):
            v = _cell(ws, r, c)
            if isinstance(v, (datetime.date, datetime.datetime)):
                d = v.date() if isinstance(v, datetime.datetime) else v
                if best is None or d > best:
                    best = d
    if best is None:
        raise ValueError("마감 기준일을 찾지 못했습니다.")
    return best
