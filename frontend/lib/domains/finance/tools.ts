// 도메인이 노출하는 도구 정의. router 가 이 목록을 LLM 에 전달.

export const tools = [
  {
    name: "ledger.query",
    description: "회계 원장에서 기간/계정/코스트센터 조건으로 거래를 조회한다.",
    inputs: {
      from: { type: "string", description: "조회 시작일 (YYYY-MM-DD)" },
      to: { type: "string", description: "조회 종료일 (YYYY-MM-DD)" },
      account: { type: "string?", description: "계정과목 코드 (선택)" },
    },
  },
  {
    name: "budget.utilization",
    description: "부서/프로젝트별 예산 집행률을 계산한다.",
    inputs: {
      period: { type: "string", description: "기간 식별자 (예: 2026-Q2)" },
      groupBy: { type: "'department' | 'project'", description: "집계 단위" },
    },
  },
] as const;
