/**
 * 프로젝트에서 특정 대화 매핑 해제.
 *
 * DELETE /api/projects/{id}/conversations/{convId}
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

type Params = { params: Promise<{ id: string; convId: string }> };

export async function DELETE(_req: NextRequest, { params }: Params) {
  const auth = await authHeader();
  if (!auth) return NextResponse.json({ detail: "인증이 필요합니다." }, { status: 401 });
  const { id, convId } = await params;
  const r = await fetch(
    `${BACKEND_URL}/v1/projects/${encodeURIComponent(id)}/conversations/${encodeURIComponent(convId)}`,
    {
      method: "DELETE",
      headers: { Authorization: auth },
      cache: "no-store",
    },
  );
  if (r.status === 204) return new NextResponse(null, { status: 204 });
  const data = await r.json().catch(() => ({}));
  return NextResponse.json(data, { status: r.status });
}
