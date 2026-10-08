/**
 * 결과 확정 프록시 — 채팅 확정 카드의 '그대로 확정' / '수정본 올리고 확정'.
 * POST /api/proposal-records/{id}/confirm { revised_attachment_id? }
 *   → backend POST /v1/proposal-records/{id}/confirm
 * 수정본 파일은 먼저 /api/attachments 로 올리고 그 id 만 넘긴다.
 */
import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
// 확정 시 요청 원문 임베딩 + 수정본 글자 추출이 있어 수 초 걸릴 수 있다.
export const maxDuration = 60;

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8002";
const TOKEN_COOKIE = "und_cortex_token";

type Params = { params: Promise<{ id: string }> };

export async function POST(req: NextRequest, { params }: Params) {
  const token = (await cookies()).get(TOKEN_COOKIE)?.value;
  if (!token) return NextResponse.json({ detail: "인증이 필요합니다." }, { status: 401 });
  const { id } = await params;

  const body = (await req.json().catch(() => ({}))) as { revised_attachment_id?: unknown };
  const revised = body.revised_attachment_id;
  if (revised !== undefined && revised !== null && !Number.isInteger(revised)) {
    return NextResponse.json({ detail: "revised_attachment_id 형식 오류" }, { status: 400 });
  }

  const r = await fetch(`${BACKEND_URL}/v1/proposal-records/${encodeURIComponent(id)}/confirm`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ revised_attachment_id: revised ?? null }),
    cache: "no-store",
  });
  const data = await r.json().catch(() => ({}));
  return NextResponse.json(data, { status: r.status });
}
