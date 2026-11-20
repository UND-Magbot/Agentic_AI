"""원화 금액 끝수 처리 — 회사 규칙: 원 미만은 무조건 절삭(사용자 2026-11-20, 예전 반올림·사사오입을 모두 바꿈).

- 부가세 = 공급가액 × 10% 의 원 미만 절삭.
- 부가세 포함 금액 = 공급가액 + 부가세(= 공급가액 × 1.1 절삭).
- 부가세 포함 금액 → 공급가액: 그 안의 부가세(합계 ÷ 11, 원 미만 절삭)를 뺀 값. 그래서 공급가액 → 부가세 포함 → 공급가액이 늘 같고
  (예: 12,345 → 13,579 → 12,345), 공급가액 + 부가세 = 합계가 원 단위까지 맞는다.
float 오차(110,000 ÷ 1.1 = 99,999.999…)가 없게 Decimal 로 계산한다. 음수(할인)도 0 쪽으로 자른다.
화면 쪽 같은 계산: frontend/lib/shared/money.ts
"""
from __future__ import annotations

from decimal import ROUND_DOWN, Decimal
from typing import Any

VAT_RATE = Decimal("0.1")


def _d(v: Any) -> Decimal:
    return v if isinstance(v, Decimal) else Decimal(str(v or 0))


def won(v: Any) -> int:
    """원 미만 절삭."""
    return int(_d(v).to_integral_value(rounding=ROUND_DOWN))


def vat_of(supply: Any) -> int:
    """공급가액의 부가세(10%, 원 미만 절삭)."""
    return won(_d(supply) * VAT_RATE)


def with_vat(supply: Any) -> int:
    """공급가액 → 부가세 포함 금액(× 1.1, 원 미만 절삭)."""
    return won(_d(supply) * (1 + VAT_RATE))


def supply_of(total: Any) -> int:
    """부가세 포함 금액 → 공급가액(합계 − 합계 안의 부가세[합계 ÷ 11, 원 미만 절삭])."""
    t = _d(total)
    return won(t) - won(t / 11)
