import type { DomainMeta } from "@/lib/shared/types";

export const meta: DomainMeta = {
  key: "sales",
  label: "기술영업",
  shortLabel: "기술영업",
  tagline: "고객·파이프라인·견적 자동화",
  description:
    "회사 제품 추천, 제안서·컨셉 이미지 작성, 견적서 작성·발행, 제품 영업 건(수주·거래명세서·입금·출하) 관리까지 영업 흐름을 이어서 도와줍니다.",
  accent: {
    bg: "bg-emerald-600",
    text: "text-emerald-700 dark:text-emerald-300",
    ring: "ring-emerald-500/40",
    focusRing: "focus-within:ring-emerald-500/40",
    soft: "bg-emerald-50 dark:bg-emerald-500/10",
    softText: "text-emerald-700 dark:text-emerald-200",
    dot: "bg-emerald-500",
  },
  icon: "trend",
  examples: [
    "이번 분기 파이프라인을 단계별로 요약하고 막힌 딜을 찾아줘",
    "고객사 B와의 최근 미팅 내용을 바탕으로 후속 메일 초안을 써줘",
    "지난주 신규 리드 중 유망 고객 5곳을 점수와 함께 알려줘",
    "표준 견적서 양식으로 항목 5개짜리 견적 초안을 만들어줘",
  ],
  placeholder: "리드·딜·견적서 — 영업 어디부터 도울까요?",
};
