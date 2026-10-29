import type { DomainMeta } from "@/lib/shared/types";

export const meta: DomainMeta = {
  key: "sales",
  label: "기술영업",
  shortLabel: "기술영업",
  tagline: "제품 추천·견적서·영업 건 관리",
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
  // 누르면 바로 답이 나오는 질문만(사용자 2026-10-28 — 예전 예시는 없는 기능이었다): 영업 데이터·제품 사양은 DB 에서 바로,
  // 화면 이동은 버튼으로(backend deal_query · spec_answer · screen_goto)
  examples: [
    "이번 분기 수주 금액 얼마야?",
    "미입금 건 알려줘",
    "진행 중인 영업 건 몇 개야?",
    "TCV1 사양 알려줘",
    "견적서 수기 작성 열어줘",
  ],
  placeholder: "견적서·영업 건·제품 사양 — 무엇이든 물어보세요",
};
