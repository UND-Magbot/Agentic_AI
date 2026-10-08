export const tools = [
  {
    name: "repo.search",
    description: "사내 코드베이스에서 정규식/심볼/자연어로 코드를 검색한다.",
    inputs: {
      query: { type: "string", description: "검색어" },
      repo: { type: "string?", description: "특정 저장소로 한정 (선택)" },
    },
  },
  {
    name: "issue.lookup",
    description: "이슈 트래커에서 이슈 번호 또는 키워드로 이슈를 조회한다.",
    inputs: {
      query: { type: "string", description: "이슈 번호 또는 검색어" },
    },
  },
  {
    name: "ci.logs",
    description: "CI 빌드/테스트 로그를 가져와 실패 구간을 추출한다.",
    inputs: {
      buildId: { type: "string", description: "빌드 ID" },
    },
  },
] as const;
