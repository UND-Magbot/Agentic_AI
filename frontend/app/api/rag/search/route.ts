/**
 * RAG 검색 프록시 — backend `/v1/rag/search`.
 *
 * POST /api/rag/search { query, top_k?, domain?, min_score? }
 *   → { sources: [...], context_block: string }
 *
 * 프론트엔드 runDomain 이 normal 도메인(또는 사칙·정책 키워드 매칭) 질의 시
 * 이 프록시로 먼저 검색해 chunk 를 system prompt 에 부착한 뒤 LLM 호출.
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

export async function POST(req: NextRequest) {
  const auth = await authHeader();
  if (!auth) {
    return NextResponse.json({ detail: "인증이 필요합니다." }, { status: 401 });
  }
  const body = await req.json().catch(() => null);
  if (!body) {
    return NextResponse.json({ detail: "잘못된 요청입니다." }, { status: 400 });
  }
  const r = await fetch(`${BACKEND_URL}/v1/rag/search`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: auth },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  const data = await r.json().catch(() => ({}));
  return NextResponse.json(data, { status: r.status });
}
