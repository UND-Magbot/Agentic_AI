// 대화 제목 자동 생성 — backend `/v1/title` 프록시.
// 첫 user/assistant 메시지를 받아 한 줄 제목 문자열을 반환한다.

import { NextRequest } from "next/server";

export const runtime = "nodejs";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8002";
// 내부 서비스 토큰 — backend /v1/title 가드 통과용(설정 시). 비어 있으면 미첨부.
const INTERNAL_SERVICE_TOKEN = process.env.INTERNAL_SERVICE_TOKEN ?? "";

type Body = {
  messages: Array<{ role: "user" | "assistant"; content: string }>;
};

export async function POST(req: NextRequest) {
  let body: Body;
  try {
    body = (await req.json()) as Body;
  } catch {
    return new Response("invalid JSON", { status: 400 });
  }
  if (!Array.isArray(body.messages) || body.messages.length === 0) {
    return new Response("messages required", { status: 400 });
  }

  const r = await fetch(`${BACKEND_URL}/v1/title`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(INTERNAL_SERVICE_TOKEN ? { "X-Internal-Token": INTERNAL_SERVICE_TOKEN } : {}),
    },
    body: JSON.stringify({ messages: body.messages }),
  });
  if (!r.ok) {
    const txt = await r.text().catch(() => "");
    return new Response(txt || r.statusText, { status: r.status });
  }
  const data = (await r.json()) as { title?: string };
  return Response.json({ title: (data.title ?? "").trim() });
}
