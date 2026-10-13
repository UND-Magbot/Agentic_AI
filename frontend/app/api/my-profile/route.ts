/**
 * 내 정보 프록시 — GET/PUT /api/my-profile → backend /v1/me/profile (사용자 2026-10-12).
 * 이니셜·한글 이름·직함·휴대폰·이메일 — 견적서·거래명세서 폼을 이 값으로 미리 채운다.
 */
import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8002";
const TOKEN_COOKIE = "und_cortex_token";

async function forward(method: "GET" | "PUT", body?: string) {
  const token = (await cookies()).get(TOKEN_COOKIE)?.value;
  if (!token) return NextResponse.json({ detail: "인증이 필요합니다." }, { status: 401 });
  const r = await fetch(`${BACKEND_URL}/v1/me/profile`, {
    method,
    headers: { Authorization: `Bearer ${token}`, ...(body ? { "Content-Type": "application/json" } : {}) },
    body,
    cache: "no-store",
  });
  return NextResponse.json(await r.json().catch(() => ({})), { status: r.status });
}

export async function GET() {
  return forward("GET");
}

export async function PUT(req: NextRequest) {
  return forward("PUT", await req.text());
}
