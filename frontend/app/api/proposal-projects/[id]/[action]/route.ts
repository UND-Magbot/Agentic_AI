/**
 * 제안서 작업 단계 동작 프록시.
 * POST /api/proposal-projects/{id}/read             → 자료 다시 읽기
 * POST /api/proposal-projects/{id}/answers          → 질문 답 제출
 * POST /api/proposal-projects/{id}/finish-questions → 핵심 질문 마치기
 * 3~6단계(propose·alternatives·image·image-approve·labels·finish-concept·structure·approve-structure·
 * produce·back·quick)도 같은 방식으로 넘긴다 — 허용 목록 밖은 404.
 */
import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8002";
const TOKEN_COOKIE = "und_cortex_token";
const ACTIONS = new Set([
  "read", "answers", "finish-questions",
  "propose", "extra", "revise", "plan-select", "plan-confirm", "experience", "save-knowledge", "save-knowledge-all", "alternatives", "image", "image-approve", "labels", "finish-concept",
  "structure", "approve-structure", "produce", "deck-edit", "back", "quick",
]);

type Params = { params: Promise<{ id: string; action: string }> };

export async function POST(req: NextRequest, { params }: Params) {
  const token = (await cookies()).get(TOKEN_COOKIE)?.value;
  if (!token) return NextResponse.json({ detail: "인증이 필요합니다." }, { status: 401 });
  const { id, action } = await params;
  if (!ACTIONS.has(action)) return NextResponse.json({ detail: "알 수 없는 동작입니다." }, { status: 404 });
  const body = await req.text();
  const r = await fetch(
    `${BACKEND_URL}/v1/proposal-projects/${encodeURIComponent(id)}/${action}`,
    {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, ...(body ? { "Content-Type": "application/json" } : {}) },
      body: body || undefined,
      cache: "no-store",
    },
  );
  const data = await r.json().catch(() => ({}));
  return NextResponse.json(data, { status: r.status });
}
