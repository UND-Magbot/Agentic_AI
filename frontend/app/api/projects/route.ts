/**
 * 프로젝트(대화 그룹) 목록/생성 프록시.
 *
 * GET  /api/projects        → backend GET  /v1/projects
 * POST /api/projects        → backend POST /v1/projects
 *
 * httpOnly 쿠키의 토큰을 Authorization 헤더로 변환해 백엔드에 전달한다.
 */
import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8002";
const TOKEN_COOKIE = "und_cortex_token";

async function authHeader(): Promise<string | null> {
  const c = await cookies();
  const token = c.get(TOKEN_COOKIE)?.value;
  return token ? `Bearer ${token}` : null;
}

export async function GET(req: NextRequest) {
  const auth = await authHeader();
  if (!auth) {
    return NextResponse.json({ detail: "인증이 필요합니다." }, { status: 401 });
  }
  const url = new URL(req.url);
  const params = new URLSearchParams();
  params.set("limit", url.searchParams.get("limit") ?? "100");
  const sortBy = url.searchParams.get("sort_by");
  if (sortBy) params.set("sort_by", sortBy);
  const q = url.searchParams.get("q");
  if (q && q.trim()) params.set("q", q.trim());

  const r = await fetch(`${BACKEND_URL}/v1/projects?${params.toString()}`, {
    headers: { Authorization: auth },
    cache: "no-store",
  });
  const data = await r.json().catch(() => ({}));
  return NextResponse.json(data, { status: r.status });
}

export async function POST(req: NextRequest) {
  const auth = await authHeader();
  if (!auth) {
    return NextResponse.json({ detail: "인증이 필요합니다." }, { status: 401 });
  }
  const body = await req.json().catch(() => null);
  if (!body) {
    return NextResponse.json({ detail: "잘못된 요청입니다." }, { status: 400 });
  }
  const r = await fetch(`${BACKEND_URL}/v1/projects`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: auth },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  const data = await r.json().catch(() => ({}));
  return NextResponse.json(data, { status: r.status });
}
