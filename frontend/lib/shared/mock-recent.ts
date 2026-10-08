// 사이드바/채팅 페이지 공통 mock 데이터.
// 모듈 로드 시점에 한 번만 timestamp 를 계산해 객체로 고정한다.
// 같은 모듈 인스턴스(예: client) 내에서는 동일 객체가 반환되므로 호출자 사이의 일관성이 유지된다.
// SSR/CSR 모듈은 별개이므로, 시간에 의존하는 표시(상대시간/그룹)는 mount 이후에만 렌더해야 한다(소비 측 책임).
//
// DB 도입 시 이 모듈만 backend 호출로 교체.

import type { Conversation, DomainKey } from "./types";

const BASELINE = Date.now();
const minAgo = (n: number) => new Date(BASELINE - n * 60_000).toISOString();

const RECENT_BY_DOMAIN: Record<DomainKey, Conversation[]> = {
  finance: [
    { id: "f1", domain: "finance", title: "3월 부서 예산 집행률", updatedAt: minAgo(35) },
    { id: "f2", domain: "finance", title: "거래처 A 미수금 정리", updatedAt: minAgo(220) },
  ],
  sales: [
    { id: "s1", domain: "sales", title: "Q2 파이프라인 진단", updatedAt: minAgo(80) },
    { id: "s2", domain: "sales", title: "고객사 B 후속 메일 초안", updatedAt: minAgo(60 * 26) },
  ],
  dev: [
    { id: "d1", domain: "dev", title: "v1.42 릴리스 노트", updatedAt: minAgo(15) },
    { id: "d2", domain: "dev", title: "이슈 #1284 원인 분석", updatedAt: minAgo(60 * 4) },
  ],
  design: [{ id: "g1", domain: "design", title: "ECO-221 영향 도면", updatedAt: minAgo(60 * 9) }],
  normal: [],
};

const RECENT_FLAT: Conversation[] = Object.values(RECENT_BY_DOMAIN)
  .flat()
  .sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime());

export function getMockRecentByDomain(): Record<DomainKey, Conversation[]> {
  return RECENT_BY_DOMAIN;
}

export function getMockRecentFlat(): Conversation[] {
  return RECENT_FLAT;
}
