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

/** 대여 단계(사용자 2026-10-27) — 따로 저장하지 않고 S/N 진행(status)으로 읽는다: 등록 = 대여 전, 출하 = 대여 중, 회수 = 대여 완료.
 *  R 이거나, 대여 건에서 나가 A/S 중(A)인 제품만. 그 밖은 null. */
export function rentalPhase(code: string, status: string | null | undefined, rentalDeal = false): string | null {
  if (code !== "R" && !(code === "A" && rentalDeal)) return null;
  if (status === "출하") return "대여 중";
  if (status === "회수") return "대여 완료";
  if (status === "등록" && code === "R") return "대여 전";
  return null;
}

/** 'S' → 'S · 판매' (모르는 값은 그대로) */
export function unitStateText(code: string): string {
  const s = UNIT_STATES.find((x) => x.code === code);
  return s ? `${s.code} · ${s.label}` : code;
}
