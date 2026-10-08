/**
 * 프로젝트 ↔ 대화 매핑 프록시.
 *
 * POST /api/projects/{id}/conversations  → 매핑 추가
 *   body: { conversation_id: number }
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

type Params = { params: Promise<{ id: string }> };

export async function POST(req: NextRequest, { params }: Params) {
  const auth = await authHeader();
  if (!auth) return NextResponse.json({ detail: "인증이 필요합니다." }, { status: 401 });
  const { id } = await params;
  const body = await req.json().catch(() => null);
  if (!body) return NextResponse.json({ detail: "잘못된 요청입니다." }, { status: 400 });

  const r = await fetch(
    `${BACKEND_URL}/v1/projects/${encodeURIComponent(id)}/conversations`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: auth },
      body: JSON.stringify(body),
      cache: "no-store",
    },
  );
  const data = await r.json().catch(() => ({}));
  return NextResponse.json(data, { status: r.status });
}
