import type { DomainMeta } from "@/lib/shared/types";

export const meta: DomainMeta = {
  key: "dev",
  label: "선행개발",
  shortLabel: "선행개발",
  tagline: "코드·이슈·릴리스 자동화",
  description:
    "사내 코드베이스/이슈 트래커/CI 로그를 기반으로 코드 리뷰, 버그 분석, 릴리스 노트 작성 등을 도와줍니다.",
  accent: {
    bg: "bg-violet-600",
    text: "text-violet-700 dark:text-violet-300",
    ring: "ring-violet-500/40",
    focusRing: "focus-within:ring-violet-500/40",
    soft: "bg-violet-50 dark:bg-violet-500/10",
    softText: "text-violet-700 dark:text-violet-200",
    dot: "bg-violet-500",
  },
  icon: "code",
  examples: [
    "최근 일주일 main 브랜치 변경 사항으로 릴리스 노트를 작성해줘",
    "이슈 #1284 의 재현 시나리오와 의심 모듈을 정리해줘",
    "이 PR 의 변경점에 대해 보안·성능 관점 리뷰 코멘트를 달아줘",
    "CI 실패 로그를 요약하고 가장 가능성 높은 원인을 추정해줘",
  ],
  placeholder: "코드·PR·이슈·CI — 어떤 선행개발 작업을 도울까요?",
};
