# -*- coding: utf-8 -*-
"""복사본 셀 기입값 산출 + 별도 `_검증` 워크북 작성. 원본 미수정.

손상 방지: 메인 복사본은 openpyxl save 로 다시 쓰지 않는다(도형·외부데이터 드롭됨).
대신 기입할 셀을 (셀참조→값) 매핑으로 산출해 xlsx_patch 로 외과적 패치한다.
`_검증` 은 메인 파일에 끼워넣지 않고 **별도 신규 .xlsx** 로 저장(신규 파일이라 안전).

기입 정책(안전 우선):
- (A) 당일잔액 O 는 **원본이 공란인 계좌 행에만** 기입(신규 일자 채우기).
  이미 값이 있는 행(예: 05-29 정답)은 건드리지 않고 재현검증으로만 대조.
  N(증감액)은 수식(=O-M)이라 보존 → 엑셀에서 자동 재계산. 소계/Total 동일.
- (B) 자금계획 수입은 자금일계표 좌측이 **완전히 공란일 때만** 기입(신규 일자).
  채워진 경우(05-29)는 덮어쓰지 않고 대조 결과만 `_검증`/콘솔에 보고.
"""
from __future__ import annotations

from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import layout as L
from .models import AccountBalance, DaybookLine, DaybookReconResult, PlanActualRecon, PlanLine


def balance_updates(
    results: list[AccountBalance], *, only_blanks: bool = True
) -> dict[str, float]:
    """당일잔액 O 기입 매핑 {셀참조: 값}. only_blanks 면 원본 공란 행만."""
    col = get_column_letter(L.COL_TODAY)
    updates: dict[str, float] = {}
    for r in results:
        if only_blanks and r.original_today is not None:
            continue
        updates[f"{col}{r.row}"] = r.today_balance
    return updates


def daybook_fill_updates(
    plan_income: list[DaybookLine],
    plan_expense: list[DaybookLine],
    *,
    occupied: bool,
    base_row: int | None = None,
) -> tuple[dict[str, float], dict[str, str], bool]:
    """자금일계표 좌측(수입)+우측(지출)을 자금계획으로 채우는 기입 매핑.

    좌측: C 내역, D 금액. 우측: F 내역, G 거래처, H 금액.
    occupied=True(기존 내용 존재)면 보존 위해 생략(skipped=True).

    base_row 가 주어지면 자금계획 블록의 상대 행 위치를 보존해 배치한다
    (일계표행 = DAYBOOK_ROWS.start + (line.row − base_row)). 수작업 양식과 동일하게
    같은 거래의 수입·지출이 같은 행에 정렬되고 빈 행도 유지된다. base_row 가 없거나
    한 줄이라도 일계표 범위를 벗어나면 9행부터 순차 패킹으로 안전 폴백.

    Returns:
        (숫자셀, 텍스트셀, skipped)
    """
    if occupied:
        return {}, {}, True
    c_inc_detail = get_column_letter(L.DB_COL_INCOME_DETAIL)
    c_inc_amt = get_column_letter(L.DB_COL_INCOME_AMOUNT)
    c_exp_item = get_column_letter(L.DB_COL_EXP_ITEM)
    c_exp_detail = get_column_letter(L.DB_COL_EXP_DETAIL)
    c_exp_vendor = get_column_letter(L.DB_COL_EXP_VENDOR)
    c_exp_amt = get_column_letter(L.DB_COL_EXP_AMOUNT)
    rows = list(L.DAYBOOK_ROWS)
    lo, hi = rows[0], rows[-1]

    # 자금계획 행 구조 보존(레이아웃)이 가능한지 — 모든 라인이 일계표 범위 안에 드는가.
    use_layout = base_row is not None and all(
        lo <= lo + (ln.row - base_row) <= hi
        for ln in (*plan_income, *plan_expense)
    )

    def _row_for(ln: DaybookLine, seq: int) -> int | None:
        if use_layout:
            return lo + (ln.row - base_row)
        return rows[seq] if seq < len(rows) else None

    nums: dict[str, float] = {}
    texts: dict[str, str] = {}
    for i, line in enumerate(plan_income):
        r = _row_for(line, i)
        if r is None:
            continue
        texts[f"{c_inc_detail}{r}"] = line.detail
        if line.amount > 0:                          # 금액 없는 메모 줄은 금액 칸 공란 유지
            nums[f"{c_inc_amt}{r}"] = line.amount
    for i, line in enumerate(plan_expense):
        r = _row_for(line, i)
        if r is None:
            continue
        if line.item:                                # E 항목 (이전엔 누락되던 칸)
            texts[f"{c_exp_item}{r}"] = line.item
        texts[f"{c_exp_detail}{r}"] = line.detail    # F 내역
        texts[f"{c_exp_vendor}{r}"] = line.vendor    # G 거래처
        if line.amount > 0:                          # 금액 없는 메모 줄은 금액 칸 공란 유지
            nums[f"{c_exp_amt}{r}"] = line.amount
    return nums, texts, False


# ── _검증 워크북(별도 파일) ──────────────────────────────────────────────────
_HDR = Font(bold=True, color="FFFFFF")
_HDR_FILL = PatternFill("solid", fgColor="2563EB")
_OK_FILL = PatternFill("solid", fgColor="DCFCE7")
_BAD_FILL = PatternFill("solid", fgColor="FEE2E2")
_TITLE = Font(bold=True, size=12)
_RIGHT = Alignment(horizontal="right")


def _set(ws, r, c, v, *, font=None, fill=None, align=None):
    cell = ws.cell(r, c, v)
    if font:
        cell.font = font
    if fill:
        cell.fill = fill
    if align:
        cell.alignment = align
    return cell


def save_verify_workbook(
    path,
    *,
    date: str,
    results: list[AccountBalance],
    recon: DaybookReconResult,
    written_balances: int,
    daybook_skipped: bool,
    pa_recon: PlanActualRecon | None = None,
) -> None:
    """다층 검증 결과를 **별도 신규 .xlsx** 로 저장.

    메인 복사본에 시트를 끼워넣으면 openpyxl save 가 필요해 도형·외부데이터가 손상되므로,
    검증 결과는 독립 파일로 분리한다. 가공 점수 없이 사실만 출력.
    """
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = L.VERIFY_SHEET_NAME.lstrip("_")
    ws.sheet_properties.tabColor = "2563EB"

    _set(ws, 1, 1, f"일일자금수지 자동 검증 — {date}", font=_TITLE)
    _set(ws, 2, 1, "원본 미수정(복사본 작업). 거래내역 기반 산출값을 원본/거래후잔액과 대조.")

    # (A) 계좌별 표 -----------------------------------------------------------
    r = 4
    _set(ws, r, 1, "■ (A) 계좌별 당일잔액 산출 + 교차검증", font=_TITLE)
    r += 1
    headers = ["구분", "은행", "별칭", "계좌번호", "전일잔액", "증감액(계산)",
               "당일잔액(계산)", "거래건수", "거래후잔액", "거래후잔액검증",
               "원본당일잔액", "재현검증"]
    for c, h in enumerate(headers, start=1):
        _set(ws, r, c, h, font=_HDR, fill=_HDR_FILL)
    r += 1

    sec_ko = {"common": "보통예금", "project": "과제", "foreign": "외화"}
    for b in results:
        _set(ws, r, 1, sec_ko.get(b.section, b.section))
        _set(ws, r, 2, b.bank)
        _set(ws, r, 3, b.alias)
        _set(ws, r, 4, b.account_raw)
        _set(ws, r, 5, b.prev_balance, align=_RIGHT)
        _set(ws, r, 6, b.delta, align=_RIGHT)
        _set(ws, r, 7, b.today_balance, align=_RIGHT)
        _set(ws, r, 8, b.tx_count, align=_RIGHT)
        _set(ws, r, 9, b.last_tx_balance, align=_RIGHT)
        xc = "-" if b.xcheck_ok is None else ("일치" if b.xcheck_ok else "불일치")
        _set(ws, r, 10, xc,
             fill=(_BAD_FILL if b.xcheck_ok is False else None))
        _set(ws, r, 11, b.original_today, align=_RIGHT)
        rp = "-" if b.repro_ok is None else ("일치" if b.repro_ok else "불일치")
        _set(ws, r, 12, rp,
             fill=(_OK_FILL if b.repro_ok else (_BAD_FILL if b.repro_ok is False else None)))
        r += 1

    # 소계/합계(계산 기준) ----------------------------------------------------
    r += 1
    krw = [b for b in results if not b.is_foreign]
    _set(ws, r, 1, "원화 Total(계산)", font=Font(bold=True))
    _set(ws, r, 5, sum(b.prev_balance for b in krw), font=Font(bold=True), align=_RIGHT)
    _set(ws, r, 6, sum(b.delta for b in krw), font=Font(bold=True), align=_RIGHT)
    _set(ws, r, 7, sum(b.today_balance for b in krw), font=Font(bold=True), align=_RIGHT)
    r += 2

    # (B) 자금일계표 대조 -----------------------------------------------------
    _set(ws, r, 1, "■ (B) 자금일계표 좌우 대조 / 회계 항등식", font=_TITLE)
    r += 1
    pairs = [
        ("기초잔액(B45)", recon.opening_balance),
        ("실적 수입계(D45)", recon.actual_income_total),
        ("실적 지출계(H45)", recon.actual_expense_total),
        ("기말잔액(I45)", recon.closing_balance),
        ("자금계획 수입합(좌측 소스)", recon.plan_income_total),
        ("항등식(기초+수입−지출=기말)", "통과" if recon.identity_ok else "실패"),
    ]
    for label, val in pairs:
        _set(ws, r, 1, label)
        _set(ws, r, 5, val, align=_RIGHT,
             fill=(_BAD_FILL if val == "실패" else (_OK_FILL if val == "통과" else None)))
        r += 1

    # (B-2) 계획 ↔ 실적 성격별 라인 대조 -------------------------------------
    if pa_recon is not None:
        r += 1
        _set(ws, r, 1, "■ (B) 계획 ↔ 실적 성격별 대조 (수입/지출 라인)", font=_TITLE)
        r += 1
        for side, ko in (("income", "수입"), ("expense", "지출")):
            c = pa_recon.counts(side)
            ptot = (pa_recon.plan_income_total if side == "income"
                    else pa_recon.plan_expense_total)
            atot = (pa_recon.actual_income_total if side == "income"
                    else pa_recon.actual_expense_total)
            tot_ok = (pa_recon.income_total_ok if side == "income"
                      else pa_recon.expense_total_ok)
            _set(ws, r, 1, f"[{ko}] 매칭 {c['match']} · 누락(계획만) {c['missing']} "
                           f"· 계획외(실적만) {c['extra']}", font=Font(bold=True))
            r += 1
            _set(ws, r, 1, "  계획 총액")
            _set(ws, r, 5, ptot, align=_RIGHT)
            _set(ws, r, 7, "실적 총액")
            _set(ws, r, 8, atot, align=_RIGHT)
            _set(ws, r, 11, "총액일치" if tot_ok else "총액불일치",
                 fill=(_OK_FILL if tot_ok else _BAD_FILL))
            r += 1
            # 라인 상세 헤더
            for col, h in enumerate(["상태", "계획 내역/거래처", "계획 금액",
                                     "실적 내역/거래처", "실적 금액", "비고"], start=1):
                _set(ws, r, col, h, font=_HDR, fill=_HDR_FILL)
            r += 1
            ms = pa_recon.income_matches if side == "income" else pa_recon.expense_matches
            st_ko = {"MATCH": "일치", "MISSING": "누락(계획만)", "EXTRA": "계획외(실적만)"}
            for m in ms:
                fill = (_OK_FILL if m.status == "MATCH" else _BAD_FILL)
                _set(ws, r, 1, st_ko.get(m.status, m.status), fill=fill)
                if m.plan:
                    _set(ws, r, 2, (m.plan.vendor or m.plan.detail)[:40])
                    _set(ws, r, 3, m.plan.amount, align=_RIGHT)
                if m.actual:
                    _set(ws, r, 4, (m.actual.vendor or m.actual.detail)[:40])
                    _set(ws, r, 5, m.actual.amount, align=_RIGHT)
                if m.note:
                    _set(ws, r, 6, m.note)
                r += 1
            r += 1

    # 플래그(누락·차액) -------------------------------------------------------
    r += 1
    _set(ws, r, 1, "■ 플래그 / 참고", font=_TITLE)
    r += 1
    notes: list[str] = []
    if written_balances:
        notes.append(f"공란이던 당일잔액 {written_balances}건을 복사본에 기입함.")
    else:
        notes.append("당일잔액은 모두 기존 값 존재 → 미기입(재현검증으로만 대조).")
    if daybook_skipped:
        notes.append("자금일계표 좌측에 기존 내용 존재 → 자금계획 수입 미기입(대조만 수행).")
    notes += recon.flags or []
    xfail = [f"{b.bank}/{b.alias}({b.account_raw})" for b in results if b.xcheck_ok is False]
    rfail = [f"{b.bank}/{b.alias}({b.account_raw})" for b in results if b.repro_ok is False]
    if xfail:
        notes.append("거래후잔액 불일치 계좌: " + ", ".join(xfail))
    if rfail:
        notes.append("원본 당일잔액 불일치(재현검증 실패) 계좌: " + ", ".join(rfail))
    if not (xfail or rfail or recon.flags):
        notes.append("객관적 검증(교차·재현·항등식) 모두 통과.")
    notes += recon.info or []   # 정보성 차이(계획 vs 실적) — 실패 아님, 사람 확정.
    for n in notes:
        _set(ws, r, 1, "• " + n)
        r += 1

    # 열 너비 보기 좋게.
    widths = [10, 10, 22, 20, 16, 14, 16, 8, 16, 12, 16, 10]
    for c, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(c)].width = w

    wb.save(path)
