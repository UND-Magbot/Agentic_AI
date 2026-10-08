import type { DomainMeta } from "@/lib/shared/types";

export const meta: DomainMeta = {
  key: "finance",
  label: "재무관리",
  shortLabel: "재무관리",
  tagline: "재무·예산·회계 자동화",
  description:
    "월결산, 예산 대비 실적, 거래처 정산, 세무 신고 자료 등 회계·경영 관리 업무를 도와줍니다. 사내 ERP 데이터와 결합해 답변합니다.",
  accent: {
    bg: "bg-sky-600",
    text: "text-sky-700 dark:text-sky-300",
    ring: "ring-sky-500/40",
    focusRing: "focus-within:ring-sky-500/40",
    soft: "bg-sky-50 dark:bg-sky-500/10",
    softText: "text-sky-700 dark:text-sky-200",
    dot: "bg-sky-500",
  },
  icon: "ledger",
  examples: [
    "이번 달 부서별 예산 집행률을 표로 정리해줘",
    "거래처 A의 미수금 현황과 최근 3개월 추이를 알려줘",
    "지난 분기 손익계산서를 요약하고 전년 동기와 비교해줘",
    "법인카드 사용내역에서 접대비 한도 초과분을 찾아줘",
  ],
  placeholder: "예산·결산·미수금·세무 — 무엇을 알아볼까요?",
};
