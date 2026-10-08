export const tools = [
  {
    name: "crm.search",
    description: "CRM 에서 고객/딜/활동을 자연어 조건으로 검색한다.",
    inputs: {
      query: { type: "string", description: "검색 자연어" },
      entity: { type: "'account' | 'deal' | 'activity'", description: "검색 대상" },
    },
  },
  {
    name: "quote.draft",
    description: "표준 견적 양식으로 초안 견적서를 생성한다.",
    inputs: {
      account: { type: "string", description: "고객사명" },
      items: { type: "Array<{sku, qty, unitPrice}>", description: "견적 라인" },
    },
  },
] as const;
