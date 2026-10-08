// finance 도메인의 공개 표면. 외부(=lib/agents, app/api)는 이 파일을 통해서만 접근한다.
// 다른 도메인(sales, dev, design)에서 이 파일을 import 하지 않는다 — 도메인 간 직접 의존 금지.

export { meta } from "./meta";
export { systemPrompt } from "./prompts";
export { tools } from "./tools";
export { policy } from "./rbac";
export type * from "./schemas";
