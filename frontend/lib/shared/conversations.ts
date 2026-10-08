/**
 * 대화 영속화용 클라이언트.
 *
 * 서버 컴포넌트(layout.tsx)는 `getRecentConversations()` 로 사이드바/검색 표시 데이터를 가져온다.
 * 클라이언트 컴포넌트는 `/api/conversations/*` 프록시를 통해 같은 백엔드와 통신한다.
 *
 * 응답 스키마는 backend `app/schemas_chat.py` 의 ConversationSummary / ConversationDetail 와 동일.
 */
import { cookies } from "next/headers";

import type { Conversation, DomainKey } from "./types";

const TOKEN_COOKIE = "und_cortex_token";
const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8002";

export type ConversationSummaryDTO = {
  id: number;
  title: string;
  /** DB user_domain ENUM: all | finance | sales | design | develop. */
  domain: "all" | "finance" | "sales" | "design" | "develop";
  /** UI 5종 키 (백엔드가 변환해서 보내줌). */
  domain_key: DomainKey;
  starred?: boolean;
  started_at: string;
  updated_at: string;
};

export type MessageDTO = {
  id: number;
  role: "user" | "assistant" | "system";
  content: string;
  domain: "all" | "finance" | "sales" | "design" | "develop" | null;
  model: string | null;
  created_at: string;
};

export type ConversationDetailDTO = ConversationSummaryDTO & {
  messages: MessageDTO[];
};

/** 서버 컴포넌트 전용 — 단건 조회. 권한 없거나 미존재면 null. */
export async function getConversationDetail(id: string | number): Promise<ConversationDetailDTO | null> {
  const c = await cookies();
  const token = c.get(TOKEN_COOKIE)?.value;
  if (!token) return null;
  try {
    const r = await fetch(`${BACKEND_URL}/v1/conversations/${encodeURIComponent(String(id))}`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    });
    if (!r.ok) return null;
    return (await r.json()) as ConversationDetailDTO;
  } catch {
    return null;
  }
}

/** 서버 컴포넌트 전용 — 사이드바 RECENT 표시를 위한 최근 대화 로드. */
export async function getRecentConversations(limit = 50): Promise<ConversationSummaryDTO[]> {
  const c = await cookies();
  const token = c.get(TOKEN_COOKIE)?.value;
  if (!token) return [];
  try {
    const r = await fetch(`${BACKEND_URL}/v1/conversations?limit=${limit}`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    });
    if (!r.ok) return [];
    return (await r.json()) as ConversationSummaryDTO[];
  } catch {
    return [];
  }
}

/** DTO → Conversation (사이드바/검색이 쓰는 공용 타입). id 는 string 으로 정규화. */
export function toConversation(dto: ConversationSummaryDTO): Conversation {
  return {
    id: String(dto.id),
    domain: dto.domain_key,
    title: dto.title,
    updatedAt: dto.updated_at,
    starred: Boolean(dto.starred),
  };
}

/** 도메인키 별로 그룹화 — 사이드바 layout 이 사용하는 형태. */
export function groupByUiDomain(items: Conversation[]): Record<DomainKey, Conversation[]> {
  const empty: Record<DomainKey, Conversation[]> = {
    finance: [],
    sales: [],
    dev: [],
    design: [],
    normal: [],
  };
  for (const it of items) {
    empty[it.domain].push(it);
  }
  return empty;
}
