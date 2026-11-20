// 원화 금액 끝수 처리 — 회사 규칙: 원 미만은 무조건 절삭(사용자 2026-11-20). backend/app/money.py 와 같은 계산.
// 부가세 = 공급가액 × 10% 절삭, 부가세 포함 = 공급가액 + 부가세, 부가세 포함 → 공급가액 = 합계 − (합계 ÷ 11 절삭).

/** 원 미만 절삭(0 쪽으로) */
export const wonCut = (v: number): number => Math.trunc(v);
/** 공급가액의 부가세(10%, 원 미만 절삭) — 정수 ÷ 10 은 오차 없이 계산된다 */
export const vatOf = (supply: number): number => Math.trunc(Math.trunc(supply) / 10);
/** 공급가액 → 부가세 포함 금액 */
export const withVat = (supply: number): number => Math.trunc(supply) + vatOf(supply);
/** 부가세 포함 금액 → 공급가액 */
export const supplyOf = (total: number): number => Math.trunc(total) - Math.trunc(Math.trunc(total) / 11);
