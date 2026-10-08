# -*- coding: utf-8 -*-
"""(A) 계좌별 증감액·당일잔액 산출 + 거래후잔액 교차검증.

알고리즘(docs/design/fund_daily_reconcile_plan.md §1-4):
  증감액(N) = Σ(당일 입금 − 출금)              (해당 계좌, 거래일시 날짜 == 대상일)
  당일잔액(O) = 전일잔액(M) + 증감액(N)
  교차검증: O(계산) == 당일 마지막 거래의 거래후잔액 → 불일치 시 플래그
  당일 거래 없음 → 증감액=0, 당일잔액=전일잔액

원화 계좌는 KRW 컬럼(O/P/Q), 외화 계좌는 native 컬럼(F/G/H)을 사용한다.
"""
from __future__ import annotations

from .actual_reader import ActualAccountRow
from .models import AccountBalance, TxLine

# 부동소수 비교 허용 오차(외화 환산·소수점). 원화는 정수라 사실상 0.
_EPS = 0.5


def _account_txs(lines: list[TxLine], key: str) -> list[TxLine]:
    """해당 계좌 거래를 거래일시 순으로 정렬해 반환."""
    txs = [t for t in lines if t.account_key == key]
    txs.sort(key=lambda t: t.dt)
    return txs


def _chain_end_balance(txs: list[TxLine], is_fx: bool) -> float | None:
    """거래후잔액 체인의 끝(당일 최종 잔액)을 도출. 시각 동률·역순에 강건.

    은행 거래내역은 같은 분(分)에 다건이 역순으로 들어와 거래일시 정렬로는 '마지막
    거래'를 못 가린다. 대신 거래후잔액 체인을 쓴다 — 각 거래의 '직전 잔액 = 거래후잔액
    − 순증감'. 어느 거래의 직전 잔액과도 겹치지 않는 거래후잔액이 곧 당일 최종 잔액.
    잔액 누락·체인 모호(끝이 0개 또는 2개 이상) 시 None(교차검증 생략).
    """
    def bal(t: TxLine):
        return t.balance_native if is_fx else t.balance_krw

    def net(t: TxLine) -> float:
        return (t.deposit_native - t.withdraw_native if is_fx
                else t.deposit_krw - t.withdraw_krw)

    if not txs or any(bal(t) is None for t in txs):
        return None
    afters = [round(bal(t), 2) for t in txs]
    befores = {round(bal(t) - net(t), 2) for t in txs}
    ends = [a for a in afters if a not in befores]
    return ends[0] if len(ends) == 1 else None


def compute_account_balance(
    acc: ActualAccountRow, day_txs: list[TxLine]
) -> AccountBalance:
    """단일 계좌의 당일 증감액·당일잔액 산출(+교차/재현 검증).

    Args:
        acc: 실적 시트에서 읽은 계좌 행(전일잔액·정답값 포함).
        day_txs: 해당 계좌의 '대상일' 거래(정렬됨). 빈 리스트면 무거래.
    """
    is_fx = acc.is_foreign
    if is_fx:
        inflow = sum(t.deposit_native for t in day_txs)
        outflow = sum(t.withdraw_native for t in day_txs)
    else:
        inflow = sum(t.deposit_krw for t in day_txs)
        outflow = sum(t.withdraw_krw for t in day_txs)
    # 거래후잔액 체인의 끝 = 당일 최종 잔액(거래일시 동률·역순에 강건).
    last_bal = _chain_end_balance(day_txs, is_fx) if day_txs else None

    delta = inflow - outflow
    today = acc.prev_balance + delta

    xcheck_ok = None
    if day_txs and last_bal is not None:
        xcheck_ok = abs(today - last_bal) <= _EPS

    repro_ok = None
    if acc.original_today is not None:
        repro_ok = abs(today - acc.original_today) <= _EPS

    return AccountBalance(
        row=acc.row,
        bank=acc.bank,
        account_raw=acc.account_raw,
        account_key=acc.account_key,
        alias=acc.alias,
        section=acc.section,
        is_foreign=is_fx,
        prev_balance=acc.prev_balance,
        delta=delta,
        today_balance=today,
        tx_count=len(day_txs),
        last_tx_balance=last_bal,
        original_today=acc.original_today,
        xcheck_ok=xcheck_ok,
        repro_ok=repro_ok,
    )


def compute_all(
    accounts: list[ActualAccountRow], day_lines: list[TxLine]
) -> list[AccountBalance]:
    """계좌 List 전체에 대해 당일잔액을 산출한다.

    Args:
        accounts: 실적 시트 계좌 List(보통+과제+외화).
        day_lines: 대상일로 이미 필터된 거래 라인 전체.
    """
    results: list[AccountBalance] = []
    for acc in accounts:
        day_txs = _account_txs(day_lines, acc.account_key)
        results.append(compute_account_balance(acc, day_txs))
    return results


def subtotal(results: list[AccountBalance], section: str) -> tuple[float, float, float]:
    """섹션(common/project/foreign)별 (전일계, 증감계, 당일계)."""
    rows = [r for r in results if r.section == section]
    return (
        sum(r.prev_balance for r in rows),
        sum(r.delta for r in rows),
        sum(r.today_balance for r in rows),
    )
