/**
 * 서버 컴포넌트에서 현재 사용자 정보를 가져오는 헬퍼.
 * 미들웨어가 토큰 검사를 이미 끝낸 후 호출되므로 토큰이 없는 경우는 거의 없지만,
 * 만료/무효일 수 있어 null fallback 을 둔다.
 */
import { cookies } from "next/headers";

import type { DomainKey } from "./types";

export type AuthUser = {
  id: number;
  username: string;
  alias: string | null;
  email: string | null;
  role: "superadmin" | "domain_admin" | "member" | "viewer";
  domain: "all" | "finance" | "sales" | "design" | "develop";
  is_active: boolean;
  permissions?: { id: number; code: string; domain: string; description: string | null }[];
};

const TOKEN_COOKIE = "und_cortex_token";
const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8002";

/** 로그인 토큰(httpOnly 쿠키). 서버 라우트가 사용자 명의로 backend 를 부를 때. */
export async function getAuthToken(): Promise<string | null> {
  return (await cookies()).get(TOKEN_COOKIE)?.value ?? null;
}

export async function getCurrentUser(): Promise<AuthUser | null> {
  const c = await cookies();
  const token = c.get(TOKEN_COOKIE)?.value;
  if (!token) return null;

  try {
    const r = await fetch(`${BACKEND_URL}/v1/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    });
    if (!r.ok) return null;
    return (await r.json()) as AuthUser;
  } catch {
    return null;
  }
}

/** 화면 표시용 이름. alias → username → 'UND' fallback. */
export function displayName(user: AuthUser | null | undefined): string {
  if (!user) return "UND";
  return (user.alias && user.alias.trim()) || user.username || "UND";
}

/**
 * 사용자가 진입할 수 있는 도메인 키 목록.
 *
 * - superadmin (domain='all') 또는 user 정보 없음(가드 우회 안전망): 모든 도메인 허용.
 * - 그 외 사용자: 본인 도메인 + 'normal'(일반) 만 허용. '일반'은 도메인-비특화 질의용이라 모두 열어둠.
 *
 * UI 키(`dev`)와 DB 키(`develop`) 매핑은 여기서만 처리한다.
 */
export function getAllowedDomains(user: AuthUser | null | undefined): readonly DomainKey[] {
  if (!user || user.role === "superadmin" || user.domain === "all") {
    return ["finance", "sales", "dev", "design", "normal"];
  }
  const own: DomainKey =
    user.domain === "develop"
      ? "dev"
      : user.domain === "finance"
        ? "finance"
        : user.domain === "sales"
          ? "sales"
          : user.domain === "design"
            ? "design"
            : "normal";
  return [own, "normal"];
}

/** 단일 도메인 접근 가능 여부 검사. */
export function canAccessDomain(
  user: AuthUser | null | undefined,
  key: DomainKey,
): boolean {
  return getAllowedDomains(user).includes(key);
}
