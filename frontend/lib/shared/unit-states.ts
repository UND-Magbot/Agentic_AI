// 시리얼 상태 이니셜 — 화면 컴포넌트에서 값으로 쓰므로 서버 전용 sales-deals.ts(next/headers) 밖에 둔다.

/** 제품 상태 이니셜(사용자 2026-10-19, 영업팀 요청) — 영문 단어 첫 글자. 백엔드 units.UNIT_STATES 와 같이 바꾼다. */
export const UNIT_STATES = [
  { code: "S", en: "Sale", label: "판매" },
  { code: "R", en: "Rental", label: "대여" },
  { code: "D", en: "Demo", label: "데모·시연" },
  { code: "A", en: "After-service", label: "A/S 중" },
  { code: "E", en: "Exchange", label: "A/S 교체품" },
  { code: "O", en: "Other", label: "기타" },
] as const;

/** 상태 배지 색(사용자 2026-10-22 — 판매·대여·A/S 중이 한눈에) */
export const UNIT_STATE_TONE: Record<string, string> = {
  S: "bg-blue-500/12 text-blue-700 dark:text-blue-300",
  R: "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300",
  D: "bg-violet-500/12 text-violet-700 dark:text-violet-300",
  A: "bg-amber-500/15 text-amber-800 dark:text-amber-300",
  E: "bg-foreground/8 text-foreground-muted",
  O: "bg-foreground/8 text-foreground-muted",
};

/** 'S' → 'S · 판매' (모르는 값은 그대로) */
export function unitStateText(code: string): string {
  const s = UNIT_STATES.find((x) => x.code === code);
  return s ? `${s.code} · ${s.label}` : code;
}
