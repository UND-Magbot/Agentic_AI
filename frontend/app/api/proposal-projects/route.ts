/**
 * 제안서 작업 프로젝트 프록시.
 * GET  /api/proposal-projects → backend GET  /v1/proposal-projects (내 목록)
 * POST /api/proposal-projects → backend POST /v1/proposal-projects (공통 질문 폼 제출)
 */
import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8002";
const TOKEN_COOKIE = "und_cortex_token";

async function forward(method: "GET" | "POST", body?: string) {
  const token = (await cookies()).get(TOKEN_COOKIE)?.value;
  if (!token) return NextResponse.json({ detail: "인증이 필요합니다." }, { status: 401 });
  const r = await fetch(`${BACKEND_URL}/v1/proposal-projects`, {
    method,
    headers: { Authorization: `Bearer ${token}`, ...(body ? { "Content-Type": "application/json" } : {}) },
    body,
    cache: "no-store",
  });
  const data = await r.json().catch(() => ({}));
  return NextResponse.json(data, { status: r.status });
}

export function GET() {
  return forward("GET");
}

export async function POST(req: NextRequest) {
  return forward("POST", await req.text());
}
