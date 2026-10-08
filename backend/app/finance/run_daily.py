# -*- coding: utf-8 -*-
"""일일자금수지 실적 자동화 CLI 엔트리.

(A) 계좌 List 당일잔액 산출·교차검증 + (B) 자금일계표 좌우 대조를 수행하고,
원본 미수정 복사본에 `_검증` 시트를 붙여 work/ 에 저장한다.

실행:
    python -m app.finance.run_daily --date 2026-05-29
    python backend/app/finance/run_daily.py --date 2026-05-29 \
        --actual "docs/finance/[사내양식]자금실적_FY26_260601.xlsx" \
        --plan   "docs/finance/[사내양식]자금계획_FY26_260602.xlsx" \
        --tx     "docs/[클로브]…은행 거래내역….xlsx" \
        --out work

종료코드: 0 = 모든 교차/재현 검증·항등식 통과, 1 = 불일치/이상 존재.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

# 패키지/스크립트 양쪽 실행 지원.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app.finance import balance, daybook, plan_reader, tx_reader, writer
    from app.finance import actual_reader, layout, workbook_safe, xlsx_patch
    from app.finance.actual_reader import DaybookActuals
    from app.finance.models import (
        AccountBalance, DaybookLine, DaybookReconResult, PlanActualRecon,
    )
else:
    from . import balance, daybook, plan_reader, tx_reader, writer
    from . import actual_reader, layout, workbook_safe, xlsx_patch
    from .actual_reader import DaybookActuals
    from .models import (
        AccountBalance, DaybookLine, DaybookReconResult, PlanActualRecon,
    )

ROOT = Path(__file__).resolve().parents[3]
DOCS = ROOT / "docs"
DEFAULT_ACTUAL = DOCS / "finance" / "[사내양식]자금실적_FY26_260601.xlsx"
DEFAULT_PLAN = DOCS / "finance" / "[사내양식]자금계획_FY26_260602.xlsx"
DEFAULT_TX = DOCS / "finance" / "[클로브]주식회사 유엔디_은행 거래내역_20260401~20260531_20260616.xlsx"


def _fmt(v) -> str:
    if isinstance(v, (int, float)):
        return f"{v:,.0f}" if abs(v - round(v)) < 1e-6 else f"{v:,.2f}"
    return "-" if v is None else str(v)


@dataclass
class ReconcileOutcome:
    """run_reconcile 결과 — CLI 출력과 서비스 요약이 공통으로 쓰는 구조화 산출물.

    원본 미수정. copy_path 는 당일잔액·자금일계표가 채워진 복사본(외과적 패치),
    verify_path 는 별도 검증 워크북. ok 는 객관적 검증(교차·재현·항등식) 전부 통과 여부.
    """

    date: str
    sheet: str
    results: list[AccountBalance]
    recon: DaybookReconResult
    pa_recon: PlanActualRecon
    db_actuals: DaybookActuals
    plan_income: list[DaybookLine]
    plan_expense: list[DaybookLine]
    copy_path: Path
    verify_path: Path
    written: int
    db_skipped: bool
    originals_ok: bool
    ok: bool


class SheetMissingError(RuntimeError):
    """대상 일자(MM-DD) 시트가 자금실적 워크북에 없을 때."""


def run_reconcile(
    date: str,
    actual_p: Path,
    plan_p: Path,
    tx_p: Path,
    out_dir: Path,
    *,
    label: str = "자금실적_FY26",
) -> ReconcileOutcome:
    """핵심 대조·검증·기입 로직(출력 없음). CLI(run)·서비스가 공통 호출.

    원본 3종(actual/plan/tx)은 read-only. 결과는 out_dir 의 복사본에만 외과적 패치.
    plan_p 는 자금실적 파일에 자금계획 시트가 내장된 경우 actual_p 와 같을 수 있다.
    """
    sheet = actual_reader.date_to_sheet(date)

    # 0) 원본 무변경 보장 — 작업 전 해시 기록(같은 경로 중복은 set 으로 1회). ----
    pre_hashes = {p: workbook_safe.sha256(p) for p in {actual_p, plan_p, tx_p}}

    # 1) 거래내역 읽기 + 대상일 필터. ----------------------------------------
    all_tx = tx_reader.read_tx_lines(tx_p)
    day_tx = tx_reader.filter_by_date(all_tx, date)

    # 2) 자금실적 원본을 일반 모드로 열어 계좌 List·전일잔액·정답 읽기. --------
    import openpyxl
    wb_src = openpyxl.load_workbook(actual_p, data_only=True)
    if sheet not in wb_src.sheetnames:
        wb_src.close()
        raise SheetMissingError(
            f"자금실적에 '{sheet}' 시트가 없습니다. 보유 시트: "
            + ", ".join(s for s in wb_src.sheetnames)
        )
    ws_src = wb_src[sheet]
    layout.verify_layout(ws_src)  # 좌표/헤더 검증(조용한 오기입 차단)
    accounts = actual_reader.read_account_rows(ws_src)
    db_actuals = actual_reader.read_daybook_actuals(ws_src)
    actual_income, actual_expense = actual_reader.read_daybook_lines(ws_src)
    db_occupied = any(
        ws_src.cell(r, layout.DB_COL_INCOME_DETAIL).value not in (None, "")
        or ws_src.cell(r, layout.DB_COL_INCOME_AMOUNT).value not in (None, "")
        for r in layout.DAYBOOK_ROWS
    )
    wb_src.close()

    # 3) (A) 계좌별 당일잔액 산출. -------------------------------------------
    results = balance.compute_all(accounts, day_tx)

    # 4) (B) 자금계획 수입+지출 블록 → 성격별 대조(계획↔실적) + 항등식. -------
    #   대조·합계엔 금액>0 라인만, 자금일계표 기입엔 금액 없는 메모성 줄(항목/적요만 있는
    #   줄, 예: '퇴직연금계좌')도 포함해 수작업 양식과 동일하게 남긴다.
    fill_income, fill_expense = plan_reader.read_blocks(plan_p, date, include_text_only=True)
    plan_income = [ln for ln in fill_income if ln.amount > 0]
    plan_expense = [ln for ln in fill_expense if ln.amount > 0]
    recon = daybook.reconcile_daybook(plan_income, db_actuals)
    pa_recon = daybook.reconcile_plan_actual(
        plan_income, plan_expense, actual_income, actual_expense
    )

    # 5) 복사본 생성(byte 동일) + 외과적 셀 패치. ---------------------------
    copy_path = workbook_safe.make_working_copy(
        actual_p, out_dir, label=label, date=date
    )
    workbook_safe.assert_writable_copy(copy_path)
    o_fills = writer.balance_updates(results, only_blanks=True)
    # 자금일계표를 자금계획에서 채울 때, 수작업 양식처럼 자금계획 블록의 상대 행 위치를
    # 보존(같은 거래의 수입·지출 정렬, 빈 행 유지). 블록 시작행을 못 찾으면 순차 패킹.
    db_base = plan_reader.block_start_row(plan_p, date)
    db_nums, db_texts, db_skipped = writer.daybook_fill_updates(
        fill_income, fill_expense, occupied=db_occupied, base_row=db_base
    )
    num_updates = dict(o_fills)
    num_updates.update(db_nums)

    # 수식 캐시 갱신 — O(당일잔액)를 채우면 N(증감액=O-M)·소계·합계 수식의 입력 캐시가
    # 낡아(예: O 공란 저장 시 -전일잔액으로 캐시) 재계산 안 하는 뷰어에서 음수로 보인다.
    # 종속 수식 셀의 캐시를 올바른 계산값으로 갱신(수식은 보존, Excel 재계산도 그대로).
    # 단, 실제로 채운 게 없으면(이미 채워진 파일) 손대지 않아 복사본=원본 동일 유지.
    from openpyxl.utils import get_column_letter as _gc
    n_col, o_col = _gc(layout.COL_DELTA), _gc(layout.COL_TODAY)

    def _eff_today(b):
        return b.today_balance if b.original_today is None else b.original_today

    def _eff_delta(b):
        return _eff_today(b) - b.prev_balance

    fcache: dict[str, float] = {}
    if o_fills:                              # 당일잔액을 실제로 채운 경우에만 종속 캐시 갱신
        for b in results:
            if b.original_today is None:     # 우리가 O 를 채운 행만 N 캐시 갱신
                fcache[f"{n_col}{b.row}"] = b.delta
        for sec, srow in (("common", layout.COMMON_SUBTOTAL_ROW),
                          ("project", layout.PROJECT_SUBTOTAL_ROW),
                          ("foreign", layout.FOREIGN_SUBTOTAL_ROW)):
            secb = [b for b in results if b.section == sec]
            if secb:
                fcache[f"{o_col}{srow}"] = sum(_eff_today(b) for b in secb)
                fcache[f"{n_col}{srow}"] = sum(_eff_delta(b) for b in secb)
        krwb = [b for b in results if not b.is_foreign]
        fxb = [b for b in results if b.is_foreign]
        fcache[f"{o_col}{layout.TOTAL_KRW_ROW}"] = sum(_eff_today(b) for b in krwb)
        fcache[f"{n_col}{layout.TOTAL_KRW_ROW}"] = sum(_eff_delta(b) for b in krwb)
        if fxb:
            fcache[f"{o_col}{layout.TOTAL_FX_ROW}"] = sum(_eff_today(b) for b in fxb)
            fcache[f"{n_col}{layout.TOTAL_FX_ROW}"] = sum(_eff_delta(b) for b in fxb)
    if not db_skipped:                       # 자금일계표를 자금계획에서 채운 경우 합계 캐시도
        inc_t = sum(l.amount for l in plan_income)
        exp_t = sum(l.amount for l in plan_expense)
        op = db_actuals.opening
        fcache[layout.CELL_OPENING] = op
        fcache[layout.CELL_INCOME_TOTAL] = inc_t
        fcache[layout.CELL_EXPENSE_TOTAL] = exp_t
        fcache[layout.CELL_CLOSING] = op + inc_t - exp_t
        fcache[layout.CELL_DELTA_TODAY] = inc_t - exp_t

    written = xlsx_patch.patch_cells(
        copy_path, sheet, num_updates, db_texts, formula_cache=fcache
    )

    # 검증 결과는 별도 신규 파일로 저장(메인 복사본 무손상 유지).
    verify_path = copy_path.with_name(copy_path.stem + "_검증.xlsx")
    writer.save_verify_workbook(
        verify_path, date=date, results=results, recon=recon,
        written_balances=len(writer.balance_updates(results, only_blanks=True)),
        daybook_skipped=db_skipped, pa_recon=pa_recon,
    )

    # 6) 원본 무변경 재확인. -------------------------------------------------
    originals_ok = all(
        workbook_safe.sha256(p) == h for p, h in pre_hashes.items()
    )

    xfail = [b for b in results if b.xcheck_ok is False]
    rfail = [b for b in results if b.repro_ok is False]
    ok = originals_ok and not xfail and not rfail and not recon.flags
    return ReconcileOutcome(
        date=date, sheet=sheet, results=results, recon=recon, pa_recon=pa_recon,
        db_actuals=db_actuals, plan_income=plan_income, plan_expense=plan_expense,
        copy_path=copy_path, verify_path=verify_path, written=written,
        db_skipped=db_skipped, originals_ok=originals_ok, ok=ok,
    )


def run(date: str, actual_p: Path, plan_p: Path, tx_p: Path, out_dir: Path) -> int:
    sheet = actual_reader.date_to_sheet(date)
    print(f"=== 일일자금수지 자동화 — {date} (시트 '{sheet}') ===")
    print(f"원본 자금실적: {actual_p.name}")
    print(f"원본 자금계획: {plan_p.name}")
    print(f"원본 거래내역: {tx_p.name}\n")

    try:
        oc = run_reconcile(date, actual_p, plan_p, tx_p, out_dir)
    except SheetMissingError as e:
        print(f"오류: {e}")
        return 1

    if not oc.originals_ok:
        print("치명적: 작업 중 원본이 변경됨 — 중단")
        return 1

    print(f"[자금실적] 계좌 {len(oc.results)}개, 자금계획 {oc.date} 수입 "
          f"{len(oc.plan_income)}건({_fmt(oc.pa_recon.plan_income_total)}) · "
          f"지출 {len(oc.plan_expense)}건({_fmt(oc.pa_recon.plan_expense_total)})\n")

    # ── 콘솔 리포트 (가공 점수 없이 사실만) ─────────────────────────────────
    _print_report(oc, plan_p)
    if oc.written:
        print(f"\n복사본(셀 {oc.written}개 외과적 패치): {oc.copy_path}")
    else:
        print(f"\n복사본(원본과 바이트 동일 — 기입 0건, 재현검증만): {oc.copy_path}")
    print(f"검증 결과(별도 파일): {oc.verify_path}")
    print("원본 SHA256 무변경 확인 완료. 메인 복사본은 도형·외부데이터 보존(openpyxl 미저장).")
    return 0 if oc.ok else 1


def _print_pa_recon(pa) -> None:
    """계획 ↔ 실적 성격별 라인 대조를 콘솔에 출력(사람 최종 검토용)."""
    print("\n=== (B) 계획 ↔ 실적 성격별 대조 (수입/지출) ===")
    for side, ko in (("income", "수입"), ("expense", "지출")):
        c = pa.counts(side)
        ptot = pa.plan_income_total if side == "income" else pa.plan_expense_total
        atot = pa.actual_income_total if side == "income" else pa.actual_expense_total
        tot_ok = pa.income_total_ok if side == "income" else pa.expense_total_ok
        ms = pa.income_matches if side == "income" else pa.expense_matches
        print(f"\n[{ko}] 매칭 {c['match']} · 누락(계획만) {c['missing']} · "
              f"계획외(실적만) {c['extra']}")
        print(f"  총액  계획 {_fmt(ptot)}  vs  실적 {_fmt(atot)}  "
              f"→ {'일치' if tot_ok else f'불일치(차액 {_fmt(atot - ptot)})'}")
        for m in ms:
            if m.status == "MATCH":
                tag = "  ✓ 일치"
                extra = f"  {m.note}" if m.note else ""
                name = (m.plan.vendor or m.plan.detail)[:28]
                print(f"{tag}  {name}  {_fmt(m.plan.amount)}{extra}")
            elif m.status == "MISSING":
                name = (m.plan.vendor or m.plan.detail)[:28]
                print(f"  ✗ 누락(계획에만)  {name}  {_fmt(m.plan.amount)} — 실적 미집행 가능")
        extras = [m for m in ms if m.status == "EXTRA"]
        if extras:
            print(f"  ⓘ 계획외(실적에만) {len(extras)}건 "
                  f"(합계 {_fmt(sum(m.actual.amount for m in extras))}):")
            for m in extras[:8]:
                name = (m.actual.vendor or m.actual.detail)[:28]
                print(f"      - {name}  {_fmt(m.actual.amount)}")
            if len(extras) > 8:
                print(f"      … 외 {len(extras) - 8}건")


def _print_report(oc: ReconcileOutcome, plan_p: Path) -> None:
    date, results, recon, pa_recon = oc.date, oc.results, oc.recon, oc.pa_recon
    sec_ko = {"common": "보통", "project": "과제", "foreign": "외화"}
    print("=== (A) 계좌별 당일잔액 (계산값) ===")
    print(f"{'구분':<5}{'별칭':<22}{'전일잔액':>16}{'증감액':>16}"
          f"{'당일잔액':>16}{'건':>4} {'거래후검증':<10}{'재현검증'}")
    for b in results:
        xc = "-" if b.xcheck_ok is None else ("일치" if b.xcheck_ok else "✗불일치")
        rp = "-" if b.repro_ok is None else ("일치" if b.repro_ok else "✗불일치")
        print(f"{sec_ko.get(b.section,''):<5}{b.alias[:20]:<22}"
              f"{_fmt(b.prev_balance):>16}{_fmt(b.delta):>16}"
              f"{_fmt(b.today_balance):>16}{b.tx_count:>4} {xc:<10}{rp}")

    krw = [b for b in results if not b.is_foreign]
    print(f"\n원화 Total(계산): 전일 {_fmt(sum(b.prev_balance for b in krw))} "
          f"증감 {_fmt(sum(b.delta for b in krw))} "
          f"당일 {_fmt(sum(b.today_balance for b in krw))}")

    if not oc.db_skipped:
        # 자금일계표 공란 → 자금계획에서 채움. line별 계획↔실적은 자명(실적=계획)이라 생략하고,
        # '계획 기반 일계표 기말' vs '거래내역 기반 당일잔액 Total' 교차검증을 표시(핵심 정합성).
        db = oc.db_actuals
        krw_today = sum(b.today_balance for b in results if not b.is_foreign)
        krw_close_plan = db.opening + (pa_recon.plan_income_total - pa_recon.plan_expense_total)
        krw_ok = abs(krw_close_plan - krw_today) < 0.5
        print("\n=== (B) 자금일계표(계획 기반) ↔ 당일잔액 대조 ===")
        print(f"  기초 {_fmt(db.opening)} + 계획수입 {_fmt(pa_recon.plan_income_total)} "
              f"− 계획지출 {_fmt(pa_recon.plan_expense_total)} = 기말 {_fmt(krw_close_plan)}")
        print(f"  일계표 기말 {_fmt(krw_close_plan)} vs 당일잔액 Total {_fmt(krw_today)} "
              f"→ {'일치' if krw_ok else '✗ 점검 필요(계획 누락 또는 당일잔액·거래 오류)'}")
        try:
            cur = plan_reader.read_blocks_both(plan_p, date)
            if cur.usd_income or cur.usd_expense:
                fx_delta = sum(b.delta for b in results if b.is_foreign)
                usd_ok = abs(cur.usd_net - fx_delta) < 0.5
                print(f"  (외화 USD) 계획 순증감 {_fmt(cur.usd_net)} vs 당일 증감 {_fmt(fx_delta)} "
                      f"→ {'일치' if usd_ok else '✗ 점검 필요'} (환산 안 함)")
        except Exception:
            pass
    else:
        print("\n=== (B) 자금일계표 항등식 ===")
        print(f"  기초잔액 {_fmt(recon.opening_balance)}  + 수입 {_fmt(recon.actual_income_total)}"
              f"  − 지출 {_fmt(recon.actual_expense_total)}  = 기말 {_fmt(recon.closing_balance)}")
        print(f"  회계 항등식: {'통과' if recon.identity_ok else '✗실패'}")
        if pa_recon is not None:
            _print_pa_recon(pa_recon)

    rfail = [b for b in results if b.repro_ok is False]
    xfail = [b for b in results if b.xcheck_ok is False]
    print("\n=== 검증 요약 (사실만) ===")
    rep = [b for b in results if b.repro_ok is not None]
    print(f"  재현검증(원본 정답 대조): {sum(1 for b in rep if b.repro_ok)}/{len(rep)} 일치")
    xch = [b for b in results if b.xcheck_ok is not None]
    print(f"  거래후잔액 교차검증: {sum(1 for b in xch if b.xcheck_ok)}/{len(xch)} 일치")
    for b in xfail:
        print(f"   ✗ 거래후잔액 불일치: {b.bank}/{b.alias} "
              f"계산 {_fmt(b.today_balance)} vs 거래후 {_fmt(b.last_tx_balance)}")
    for b in rfail:
        print(f"   ✗ 재현 불일치: {b.bank}/{b.alias} "
              f"계산 {_fmt(b.today_balance)} vs 원본 {_fmt(b.original_today)}")
    for f in recon.flags:
        print(f"   ✗ {f}")
    if not (xfail or rfail or recon.flags):
        print("  → 객관적 검증(재현·교차·항등식) 모두 통과.")
    for n in recon.info:
        print(f"   ⓘ {n}")


def main() -> int:
    ap = argparse.ArgumentParser(description="일일자금수지 실적 자동화")
    ap.add_argument("--date", default="2026-05-29", help="대상 일자 YYYY-MM-DD")
    ap.add_argument("--actual", default=str(DEFAULT_ACTUAL), help="자금실적 엑셀")
    ap.add_argument("--plan", default=str(DEFAULT_PLAN), help="자금계획 엑셀")
    ap.add_argument("--tx", default=str(DEFAULT_TX), help="은행 거래내역 엑셀")
    ap.add_argument("--out", default=str(ROOT / "work"), help="복사본 출력 디렉터리")
    args = ap.parse_args()

    actual_p, plan_p = Path(args.actual), Path(args.plan)
    tx_p, out_dir = Path(args.tx), Path(args.out)
    for p in (actual_p, plan_p, tx_p):
        if not p.exists():
            print(f"입력 파일 없음: {p}")
            return 1
    try:
        return run(args.date, actual_p, plan_p, tx_p, out_dir)
    except layout.LayoutError as e:
        print(f"레이아웃 오류(양식 변경 가능): {e}")
        return 1


if __name__ == "__main__":
    # 콘솔 한글 안전 출력(Windows cp949 회피). CLI 실행 시에만 적용(import 부작용 방지).
    sys.stdout = __import__("io").TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace"
    )
    raise SystemExit(main())
