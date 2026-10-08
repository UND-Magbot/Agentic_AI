# -*- coding: utf-8 -*-
"""도메인 모델 — 순수 데이터 컨테이너(dataclass).

엑셀 I/O·검증 로직과 분리된 값 객체만 정의한다. JSON 직렬화 가능(asdict).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TxLine:
    """은행 거래내역 1행(정규화 후)."""

    dt: str                 # 거래일시 "YYYY-MM-DD HH:MM"
    bank: str               # 은행
    account_raw: str        # 계좌번호 원문(하이픈 포함 가능)
    account_key: str        # 정규화 키(숫자만)
    alias: str              # 계좌 별칭
    currency: str           # 통화(KRW/USD ...)
    deposit_native: float   # 입금액(통화 단위)
    withdraw_native: float  # 출금액(통화 단위)
    balance_native: float | None   # 거래후잔액(통화 단위)
    deposit_krw: float      # 입금액(KRW)
    withdraw_krw: float     # 출금액(KRW)
    balance_krw: float | None      # 거래후잔액(KRW)
    summary: str            # 적요


@dataclass
class AccountBalance:
    """(A) 계좌별 증감액·당일잔액 산출 결과.

    section: 'common'(보통예금) | 'project'(과제) | 'foreign'(외화)
    is_foreign 이면 native(통화) 컬럼, 아니면 KRW 컬럼 기준.
    """

    row: int                # 05-29 시트의 계좌 행 번호
    bank: str
    account_raw: str
    account_key: str
    alias: str
    section: str
    is_foreign: bool
    prev_balance: float           # 전일잔액 M (확정값, 불변)
    delta: float                  # 증감액 N = Σ(입금-출금)
    today_balance: float          # 당일잔액 O = 전일잔액 + 증감액
    tx_count: int                 # 당일 거래 건수
    last_tx_balance: float | None # 당일 마지막 거래의 거래후잔액(교차검증용)
    original_today: float | None  # 시트에 이미 기입된 당일잔액(재현검증용 정답)
    xcheck_ok: bool | None        # today_balance == last_tx_balance
    repro_ok: bool | None         # today_balance == original_today (재현검증)


@dataclass
class PlanLine:
    """(B) 자금계획_원화 일자 블록의 수입(보유자금/수입) 1행."""

    detail: str             # C 내역
    vendor: str             # D 거래처
    account: str            # E 발생계좌
    amount: float           # F 금액
    note: str               # G 비고


@dataclass
class CurrencyPlan:
    """원화(KRW)·외화(USD) 계획을 통화별로 분리 보관(환산하지 않음).

    원화는 원화끼리, 외화는 외화끼리 총액·순증감을 따로 비교하기 위한 컨테이너.
    """

    krw_income: list["DaybookLine"] = field(default_factory=list)
    krw_expense: list["DaybookLine"] = field(default_factory=list)
    usd_income: list["DaybookLine"] = field(default_factory=list)
    usd_expense: list["DaybookLine"] = field(default_factory=list)

    @property
    def krw_income_total(self) -> float:
        return sum(l.amount for l in self.krw_income)

    @property
    def krw_expense_total(self) -> float:
        return sum(l.amount for l in self.krw_expense)

    @property
    def krw_net(self) -> float:
        """원화 계획 순증감 = 수입계 − 지출계."""
        return self.krw_income_total - self.krw_expense_total

    @property
    def usd_income_total(self) -> float:
        return sum(l.amount for l in self.usd_income)

    @property
    def usd_expense_total(self) -> float:
        return sum(l.amount for l in self.usd_expense)

    @property
    def usd_net(self) -> float:
        """외화 계획 순증감(USD) = 수입계 − 지출계."""
        return self.usd_income_total - self.usd_expense_total


@dataclass
class DaybookReconResult:
    """(B) 자금일계표 좌우 대조 결과."""

    plan_income_total: float        # 자금계획 수입 합계(좌측 채울 값)
    actual_income_total: float      # 실적 자금일계표 수입계(D45)
    actual_expense_total: float     # 실적 자금일계표 지출계(H45)
    opening_balance: float          # 기초잔액 B45
    closing_balance: float          # 기말잔액 I45
    identity_ok: bool               # 기초 + 수입 − 지출 == 기말
    income_lines: list[PlanLine] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)  # 객관적 실패(항등식 등) — 종료코드에 반영
    info: list[str] = field(default_factory=list)    # 정보성 차이(계획vs실적) — 실패 아님


@dataclass
class DaybookLine:
    """자금일계표/자금계획의 한 라인(수입 또는 지출)."""

    side: str           # 'income'(수입) | 'expense'(지출)
    detail: str         # 내역/적요
    vendor: str         # 거래처(수입은 비어있을 수 있음 → 내역에 포함)
    amount: float       # 금액
    row: int = 0        # 출처 행(참고용)
    item: str = ""      # 지출 항목(자금일계표 E열). 수입은 항목 칸이 없어 비움.


@dataclass
class LineMatch:
    """계획 라인 ↔ 실적 라인 매칭 결과(한 건)."""

    side: str           # income | expense
    status: str         # MATCH | MISSING(계획에만, 실적 없음) | EXTRA(실적에만, 계획 없음)
    plan: DaybookLine | None
    actual: DaybookLine | None
    note: str = ""      # 예: '거래처 상이'(금액만 일치)


@dataclass
class PlanActualRecon:
    """(B) 계획 ↔ 실적 성격별(수입/지출) 라인 대조 결과."""

    income_matches: list[LineMatch] = field(default_factory=list)
    expense_matches: list[LineMatch] = field(default_factory=list)
    plan_income_total: float = 0.0
    actual_income_total: float = 0.0
    plan_expense_total: float = 0.0
    actual_expense_total: float = 0.0

    @property
    def income_total_ok(self) -> bool:
        return abs(self.plan_income_total - self.actual_income_total) <= 0.5

    @property
    def expense_total_ok(self) -> bool:
        return abs(self.plan_expense_total - self.actual_expense_total) <= 0.5

    def counts(self, side: str) -> dict[str, int]:
        ms = self.income_matches if side == "income" else self.expense_matches
        return {
            "match": sum(1 for m in ms if m.status == "MATCH"),
            "missing": sum(1 for m in ms if m.status == "MISSING"),
            "extra": sum(1 for m in ms if m.status == "EXTRA"),
        }


@dataclass
class VerifySummary:
    """다층 교차검증 요약(사실만, 가공 점수 없음)."""

    date: str
    account_total_krw: float        # 원화 Total 당일잔액(계산)
    closing_balance: float          # 자금일계표 기말잔액 I45
    accounts_checked: int
    xcheck_fail: list[str] = field(default_factory=list)   # 거래후잔액 불일치 계좌
    repro_fail: list[str] = field(default_factory=list)    # 원본값 불일치 계좌(재현검증)
    notes: list[str] = field(default_factory=list)
