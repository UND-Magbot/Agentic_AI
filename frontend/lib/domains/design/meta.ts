import type { DomainMeta } from "@/lib/shared/types";

export const meta: DomainMeta = {
  key: "design",
  label: "기구설계",
  shortLabel: "기구설계",
  tagline: "도면·사양서·BOM 자동화",
  description:
    "기구설계 도면, 사양서, BOM(자재명세서), 변경관리 노트 등 엔지니어링 산출물 작성과 검토를 도와줍니다.",
  accent: {
    bg: "bg-amber-600",
    text: "text-amber-700 dark:text-amber-300",
    ring: "ring-amber-500/40",
    focusRing: "focus-within:ring-amber-500/40",
    soft: "bg-amber-50 dark:bg-amber-500/10",
    softText: "text-amber-700 dark:text-amber-200",
    dot: "bg-amber-500",
  },
  icon: "ruler",
  examples: [
    "이 사양서 초안에서 누락된 항목과 모호한 표현을 짚어줘",
    "부품 X 의 BOM 을 정리하고 단가 출처를 함께 알려줘",
    "변경관리 요청 #ECO-221 의 영향 범위를 도면 단위로 분석해줘",
    "유사 과거 기구설계와 비교해 비용 절감 가능 포인트를 찾아줘",
  ],
  placeholder: "도면·사양서·BOM·ECO — 기구설계 어디부터 볼까요?",
};
