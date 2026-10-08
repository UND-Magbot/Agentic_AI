// 도메인 레지스트리. 도메인 간 직접 import 금지 원칙을 깨지 않기 위해
// 도메인을 횡단으로 알아야 하는 유일한 곳이 여기다.
//
// app/UI 코드는 lib/domains/<domain>/* 를 직접 import 하지 말고,
// 이 레지스트리(또는 router)를 통해서만 도메인 정보에 접근한다.

import * as finance from "@/lib/domains/finance";
import * as sales from "@/lib/domains/sales";
import * as dev from "@/lib/domains/dev";
import * as design from "@/lib/domains/design";
import * as normal from "@/lib/domains/normal";
import type { DomainKey, DomainMeta } from "@/lib/shared/types";
import type { DomainPolicy } from "@/lib/core/auth/rbac";

type DomainModule = {
  meta: DomainMeta;
  systemPrompt: { base: string; guardrails: readonly string[] };
  tools: readonly { name: string; description: string }[];
  policy: DomainPolicy;
};

const registry: Record<DomainKey, DomainModule> = {
  finance,
  sales,
  dev,
  design,
  normal,
};

export const DOMAIN_ORDER: readonly DomainKey[] = ["finance", "sales", "dev", "design", "normal"];

export function getDomain(key: DomainKey): DomainModule {
  return registry[key];
}

export function tryGetDomain(key: string): DomainModule | null {
  return (DOMAIN_ORDER as readonly string[]).includes(key)
    ? registry[key as DomainKey]
    : null;
}

export function listDomainMetas(): DomainMeta[] {
  return DOMAIN_ORDER.map((k) => registry[k].meta);
}
