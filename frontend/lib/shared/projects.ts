/**
 * 프로젝트(대화 그룹) 영속화용 클라이언트.
 *
 * 서버 컴포넌트는 `getProjects()` / `getProjectDetail()` 로 페이지 데이터를 가져온다.
 * 클라이언트 컴포넌트는 `/api/projects/*` 프록시를 통해 같은 백엔드와 통신한다.
 */
import { cookies } from "next/headers";

import type { ConversationSummaryDTO } from "./conversations";
import type { DomainKey } from "./types";

const TOKEN_COOKIE = "und_cortex_token";
const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8002";

export type ProjectSummaryDTO = {
  id: number;
  name: string;
  description: string | null;
  domain: "all" | "finance" | "sales" | "design" | "develop" | null;
  domain_key: DomainKey | null;
  starred: boolean;
  conversation_count: number;
  created_at: string;
  updated_at: string;
};

export type ProjectDetailDTO = ProjectSummaryDTO & {
  system_prompt: string | null;
  conversations: ConversationSummaryDTO[];
};

export type ProjectSortBy = "activity" | "name" | "created";

async function authHeader(): Promise<Record<string, string>> {
  const c = await cookies();
  const token = c.get(TOKEN_COOKIE)?.value;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** 서버 컴포넌트 전용 — 프로젝트 목록. 인증 없으면 빈 배열. */
export async function getProjects(opts: {
  q?: string;
  sortBy?: ProjectSortBy;
  limit?: number;
} = {}): Promise<ProjectSummaryDTO[]> {
  const headers = await authHeader();
  if (!headers.Authorization) return [];
  const params = new URLSearchParams();
  params.set("limit", String(opts.limit ?? 100));
  if (opts.sortBy) params.set("sort_by", opts.sortBy);
  if (opts.q?.trim()) params.set("q", opts.q.trim());
  try {
    const r = await fetch(`${BACKEND_URL}/v1/projects?${params.toString()}`, {
      headers,
      cache: "no-store",
    });
    if (!r.ok) return [];
    return (await r.json()) as ProjectSummaryDTO[];
  } catch {
    return [];
  }
}

/** 서버 컴포넌트 전용 — 단건 상세. 권한 없거나 미존재면 null. */
export async function getProjectDetail(
  id: string | number,
): Promise<ProjectDetailDTO | null> {
  const headers = await authHeader();
  if (!headers.Authorization) return null;
  try {
    const r = await fetch(`${BACKEND_URL}/v1/projects/${encodeURIComponent(String(id))}`, {
      headers,
      cache: "no-store",
    });
    if (!r.ok) return null;
    return (await r.json()) as ProjectDetailDTO;
  } catch {
    return null;
  }
}
