// 도메인별 입출력 스키마. zod 도입 전까지는 TS 타입으로만.

export type LedgerQuery = {
  period: { from: string; to: string };
  account?: string;
  costCenter?: string;
};

export type LedgerRow = {
  date: string;
  account: string;
  debit: number;
  credit: number;
  memo?: string;
};
