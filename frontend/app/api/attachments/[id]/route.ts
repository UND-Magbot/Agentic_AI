/**
 * 첨부 단건 메타 + 삭제 프록시.
 *
 * GET    /api/attachments/{id}  → backend GET /v1/attachments/{id}   (presigned download_url 포함)
 * DELETE /api/attachments/{id}  → backend DELETE /v1/attachments/{id}
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

export async function GET(_req: NextRequest, { params }: Params) {
  const auth = await authHeader();
  if (!auth) return NextResponse.json({ detail: "인증이 필요합니다." }, { status: 401 });
  const { id } = await params;
  const r = await fetch(`${BACKEND_URL}/v1/attachments/${encodeURIComponent(id)}`, {
    headers: { Authorization: auth },
    cache: "no-store",
  });
  const data = await r.json().catch(() => ({}));
  return NextResponse.json(data, { status: r.status });
}

export async function DELETE(_req: NextRequest, { params }: Params) {
  const auth = await authHeader();
  if (!auth) return NextResponse.json({ detail: "인증이 필요합니다." }, { status: 401 });
  const { id } = await params;
  const r = await fetch(`${BACKEND_URL}/v1/attachments/${encodeURIComponent(id)}`, {
    method: "DELETE",
    headers: { Authorization: auth },
    cache: "no-store",
  });
  if (r.status === 204) return new NextResponse(null, { status: 204 });
  const data = await r.json().catch(() => ({}));
  return NextResponse.json(data, { status: r.status });
}
