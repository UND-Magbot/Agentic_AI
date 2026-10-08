// normal(일반) 도메인의 공개 표면. 다른 도메인 모듈과 동일한 형태로 노출한다.
// 도메인 간 직접 import 금지 원칙은 그대로 — 외부에서는 lib/agents 를 통해 접근한다.

export { meta } from "./meta";
export { systemPrompt } from "./prompts";
export { tools } from "./tools";
export { policy } from "./rbac";
export type * from "./schemas";
