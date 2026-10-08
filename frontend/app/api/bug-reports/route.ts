/**
 * 버그 등록 프록시 — POST /api/bug-reports → backend POST /v1/bug-reports (사용자 2026-10-08).
 * multipart {title, content, files[]} 를 다시 만들어 넘긴다(boundary 를 fetch 가 새로 만들게 — attachments 와 같은 방식).
 */
import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const maxDuration = 60;

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8002";
const TOKEN_COOKIE = "und_cortex_token";

export async function POST(req: NextRequest) {
  const token = (await cookies()).get(TOKEN_COOKIE)?.value;
  if (!token) return NextResponse.json({ detail: "인증이 필요합니다." }, { status: 401 });

  let incoming: FormData;
  try {
    incoming = await req.formData();
  } catch {
    return NextResponse.json({ detail: "등록 내용을 읽지 못했습니다." }, { status: 400 });
  }
  const outgoing = new FormData();
  outgoing.append("title", String(incoming.get("title") ?? ""));
  outgoing.append("content", String(incoming.get("content") ?? ""));
  for (const f of incoming.getAll("files")) {
    if (f instanceof File) outgoing.append("files", f, f.name);
  }

  const r = await fetch(`${BACKEND_URL}/v1/bug-reports`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: outgoing,
    cache: "no-store",
  });
  return NextResponse.json(await r.json().catch(() => ({})), { status: r.status });
}
