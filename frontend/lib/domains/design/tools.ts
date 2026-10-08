export const tools = [
  {
    name: "drawing.lookup",
    description: "도면 번호 또는 키워드로 도면을 조회하고 메타데이터/썸네일을 반환한다.",
    inputs: {
      query: { type: "string", description: "도면 번호 또는 키워드" },
    },
  },
  {
    name: "bom.expand",
    description: "최종 제품 또는 어셈블리의 BOM 트리를 단계별로 펼친다.",
    inputs: {
      partNo: { type: "string", description: "부품 번호" },
      depth: { type: "number?", description: "전개 깊이 (기본 2)" },
    },
  },
  {
    name: "eco.impact",
    description: "ECO(설계변경)의 영향 도면/BOM/공정을 추적한다.",
    inputs: {
      ecoId: { type: "string", description: "ECO 번호" },
    },
  },
] as const;
