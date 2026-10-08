// 일반 도메인은 도메인 전용 입출력 스키마가 없다.
// 빈 스키마 모듈을 두는 이유: index.ts 가 다른 도메인과 동일한 표면(`export type * from "./schemas"`)
// 을 유지해 registry/orchestrator 가 분기 없이 다룰 수 있도록.

export type _NormalDomainHasNoSchemas = never;
