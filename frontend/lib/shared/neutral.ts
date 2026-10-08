// auto 모드 / 미분류 상태에서 쓰는 중립 액센트.
// 도메인이 정해지면 해당 도메인 액센트로 교체된다.

import type { DomainAccent } from "./types";

export const NEUTRAL_ACCENT: DomainAccent = {
  bg: "bg-zinc-700 dark:bg-zinc-200 dark:text-zinc-900",
  text: "text-zinc-700 dark:text-zinc-300",
  ring: "ring-zinc-400/40",
  focusRing: "focus-within:ring-zinc-400/40",
  soft: "bg-zinc-100 dark:bg-zinc-800",
  softText: "text-zinc-700 dark:text-zinc-200",
  dot: "bg-zinc-400",
};
