# -*- coding: utf-8 -*-
"""(B) 자금일계표 좌측(수입) 구성 + 좌우 대조/회계 항등식 검산.

설계(docs/design/fund_daily_reconcile_plan.md §0-(B), §6):
- 좌측(수입)은 자금계획_원화의 해당일 수입(C/D/F)으로 구성.
- 우측(지출)은 실거래 기반(실적 시트 자금일계표 우측 = 지출계 H45).
- 좌우 총액·내역 대조로 누락·차액을 플래그.
- 회계 항등식: 기초 + 수입 − 지출 == 기말.
"""
from __future__ import annotations

import re

from .actual_reader import DaybookActuals
from .models import DaybookLine, DaybookReconResult, LineMatch, PlanActualRecon, PlanLine

_EPS = 0.5

# 거래처/내역 비교용 정규화에서 떼어낼 법인격·접미 토큰.
_VENDOR_NOISE = ("주식회사", "(주)", "(주", "주)", "㈜", "(유)", "유한회사", "(외화)")


def _norm_name(s: str) -> str:
    """거래처/내역 비교용 정규화 — 법인격·공백 제거 후 소문자."""
    t = s or ""
    for tok in _VENDOR_NOISE:
        t = t.replace(tok, "")
    t = re.sub(r"\s+", "", t)
    return t.lower()


def _line_name(line: DaybookLine) -> str:
    """매칭 키 텍스트 — 거래처 우선, 없으면 내역."""
    return _norm_name(line.vendor) or _norm_name(line.detail)


def _name_overlap(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return a == b or a in b or b in a


def _match_side(
    plan: list[DaybookLine], actual: list[DaybookLine], side: str
) -> list[LineMatch]:
    """한 성격(수입 또는 지출)의 계획↔실적 라인 매칭.

    규칙: 금액이 일치(±0.5)하고 거래처/내역이 겹치면 MATCH. 금액만 일치하고 거래처가
    다르면 MATCH + note='거래처 상이'. 매칭 안 된 계획=MISSING(실적 없음),
    매칭 안 된 실적=EXTRA(계획 외). greedy 1:1 배정.
    """
    matches: list[LineMatch] = []
    used = [False] * len(actual)
    for p in plan:
        pn = _line_name(p)
        best = -1
        best_name_ok = False
        for j, a in enumerate(actual):
            if used[j] or abs(p.amount - a.amount) > _EPS:
                continue
            name_ok = _name_overlap(pn, _line_name(a))
            # 거래처까지 맞는 후보를 우선, 없으면 금액만 맞는 첫 후보.
            if name_ok:
                best, best_name_ok = j, True
                break
            if best < 0:
                best = j
        if best >= 0:
            used[best] = True
            note = "" if best_name_ok else "거래처 상이(금액만 일치)"
            matches.append(LineMatch(side=side, status="MATCH",
                                     plan=p, actual=actual[best], note=note))
        else:
            matches.append(LineMatch(side=side, status="MISSING", plan=p, actual=None))
    for j, a in enumerate(actual):
        if not used[j]:
            matches.append(LineMatch(side=side, status="EXTRA", plan=None, actual=a))
    return matches


def reconcile_plan_actual(
    plan_income: list[DaybookLine],
    plan_expense: list[DaybookLine],
    actual_income: list[DaybookLine],
    actual_expense: list[DaybookLine],
) -> PlanActualRecon:
    """계획 ↔ 실적 성격별(수입/지출) 라인 대조 + 성격별 총액.

    누락(계획에만)·계획외(실적에만)·금액일치/거래처상이를 라인 단위로 판정한다.
    가공 점수 없이 사실(매칭 상태)만 산출.
    """
    return PlanActualRecon(
        income_matches=_match_side(plan_income, actual_income, "income"),
        expense_matches=_match_side(plan_expense, actual_expense, "expense"),
        plan_income_total=sum(l.amount for l in plan_income),
        actual_income_total=sum(l.amount for l in actual_income),
        plan_expense_total=sum(l.amount for l in plan_expense),
        actual_expense_total=sum(l.amount for l in actual_expense),
    )


def reconcile_daybook(
    plan_income: list[PlanLine], actuals: DaybookActuals
) -> DaybookReconResult:
    """자금계획 수입 ↔ 실적 자금일계표(수입계/지출계/기초/기말) 대조.

    Args:
        plan_income: 자금계획_원화에서 추출한 대상일 수입 라인.
        actuals: 실적 시트 자금일계표 합계 실측값.
    """
    plan_total = sum(line.amount for line in plan_income)
    actual_income = actuals.income_total or 0.0
    actual_expense = actuals.expense_total or 0.0
    opening = actuals.opening or 0.0
    closing = actuals.closing or 0.0

    flags: list[str] = []   # 객관적 실패(시트 내부 정합성)
    info: list[str] = []    # 정보성 차이(계획 vs 실적) — 사람 확정 대상, 실패 아님

    # 회계 항등식: 기초 + 수입 − 지출 == 기말. (시트 내부 정합성 — 어기면 실패)
    identity_ok = abs(opening + actual_income - actual_expense - closing) <= _EPS
    if not identity_ok:
        flags.append(
            f"항등식 불일치: 기초({opening:,.0f}) + 수입({actual_income:,.0f}) "
            f"− 지출({actual_expense:,.0f}) = {opening + actual_income - actual_expense:,.0f} "
            f"≠ 기말({closing:,.0f})"
        )

    # 금일 증감액(D6) = 수입 − 지출 교차 확인. (시트 내부 정합성)
    if actuals.delta_today is not None:
        expected = actual_income - actual_expense
        if abs(actuals.delta_today - expected) > _EPS:
            flags.append(
                f"금일증감액(D6={actuals.delta_today:,.0f}) ≠ 수입−지출({expected:,.0f})"
            )

    # 좌(자금계획 수입) vs 우(실적 수입계) 총액 대조 — 계획과 실적의 차이는
    # 본질적으로 존재(예측 vs 실현)하므로 정보성으로 보고하고 사람이 확정한다.
    if abs(plan_total - actual_income) > _EPS:
        info.append(
            f"수입 총액 차이(계획 vs 실적): 자금계획({plan_total:,.0f}) "
            f"vs 실적 수입계({actual_income:,.0f}) = 차액 {actual_income - plan_total:,.0f} "
            f"— 자금계획에 미반영된 수입(어음만기·대출 등) 가능. 사람 확정 필요."
        )

    return DaybookReconResult(
        plan_income_total=plan_total,
        actual_income_total=actual_income,
        actual_expense_total=actual_expense,
        opening_balance=opening,
        closing_balance=closing,
        identity_ok=identity_ok,
        income_lines=list(plan_income),
        flags=flags,
        info=info,
    )
