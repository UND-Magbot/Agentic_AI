import type { DomainMeta } from "@/lib/shared/types";

export const meta: DomainMeta = {
  key: "normal",
  label: "일반",
  shortLabel: "일반",
  tagline: "사내 4개 도메인 외 일반 질의",
  description:
    "재무관리·기술영업·선행개발·기구설계 어느 쪽에도 명확히 해당하지 않는 질의를 처리합니다. 도메인 분류가 모호할 때 기본 라우트로 사용되며, 사내 데이터 권한이 필요한 작업은 다루지 않습니다.",
  accent: {
    bg: "bg-zinc-500",
    text: "text-zinc-700 dark:text-zinc-300",
    ring: "ring-zinc-400/40",
    focusRing: "focus-within:ring-zinc-400/40",
    soft: "bg-zinc-100 dark:bg-zinc-500/10",
    softText: "text-zinc-700 dark:text-zinc-200",
    dot: "bg-zinc-400",
  },
  icon: "spark",
  examples: [
    "오늘 서울 날씨 알려줘",
    "원/달러 환율 최근 추이를 요약해줘",
    "최근 반도체 업계 주요 이슈를 정리해줘",
    "이메일 답장 초안을 정중한 톤으로 써줘",
  ],
  placeholder: "그 외 일반 질의 — 무엇이든 편하게 물어보세요",
};
