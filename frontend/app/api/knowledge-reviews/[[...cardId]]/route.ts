/**
 * 회사 지식 승인 프록시 — 영업 관리자 전용(backend 가 403 으로 거른다).
 * GET  /api/knowledge-reviews          → backend GET  /v1/proposal-projects/knowledge-reviews (승인 대기 목록)
 * POST /api/knowledge-reviews/{cardId} → backend POST /v1/proposal-projects/knowledge-reviews/{cardId} (승인·반려)
 */
import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8002";
const TOKEN_COOKIE = "und_cortex_token";

type Params = { params: Promise<{ cardId?: string[] }> };

async function forward(method: "GET" | "POST", path: string, body?: string) {
  const token = (await cookies()).get(TOKEN_COOKIE)?.value;
  if (!token) return NextResponse.json({ detail: "인증이 필요합니다." }, { status: 401 });
  const r = await fetch(`${BACKEND_URL}/v1/proposal-projects/knowledge-reviews${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, ...(body ? { "Content-Type": "application/json" } : {}) },
    body,
    cache: "no-store",
  });
  const data = await r.json().catch(() => ({}));
  return NextResponse.json(data, { status: r.status });
}

export async function GET(_req: NextRequest, { params }: Params) {
  const { cardId } = await params;
  if (cardId?.length) return NextResponse.json({ detail: "찾을 수 없습니다." }, { status: 404 });
  return forward("GET", "");
}

export async function POST(req: NextRequest, { params }: Params) {
  const { cardId } = await params;
  if (cardId?.length !== 1 || !/^\d+$/.test(cardId[0])) {
    return NextResponse.json({ detail: "찾을 수 없습니다." }, { status: 404 });
  }
  return forward("POST", `/${cardId[0]}`, await req.text());
}
