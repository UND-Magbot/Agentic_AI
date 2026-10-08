/**
 * 첨부 다운로드 프록시 — backend 가 객체 본문을 stream 으로 흘려줌.
 * GET /api/attachments/{id}/download
 *
 * 헤더(Content-Type / Content-Disposition / Content-Length) 도 그대로 forward.
 */
import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8002";
const TOKEN_COOKIE = "und_cortex_token";

type Params = { params: Promise<{ id: string }> };

export async function GET(_req: NextRequest, { params }: Params) {
  const c = await cookies();
  const token = c.get(TOKEN_COOKIE)?.value;
  if (!token) return NextResponse.json({ detail: "인증이 필요합니다." }, { status: 401 });
  const { id } = await params;

  const r = await fetch(`${BACKEND_URL}/v1/attachments/${encodeURIComponent(id)}/download`, {
    method: "GET",
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  if (!r.ok) {
    const data = await r.json().catch(() => ({}));
    return NextResponse.json(data, { status: r.status });
  }
  const headers = new Headers();
  const ct = r.headers.get("content-type");
  if (ct) headers.set("Content-Type", ct);
  const cd = r.headers.get("content-disposition");
  if (cd) headers.set("Content-Disposition", cd);
  const cl = r.headers.get("content-length");
  if (cl) headers.set("Content-Length", cl);
  headers.set("Cache-Control", "private, max-age=0, no-store");
  return new NextResponse(r.body, { status: r.status, headers });
}
