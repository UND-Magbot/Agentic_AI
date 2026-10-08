// backend `/health` 프록시. 현재 사용 중인 모델 등 실시간 상태를 frontend 가 가져올 때 사용.

import { NextResponse } from "next/server";

export const runtime = "nodejs";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8002";

export async function GET() {
  try {
    const r = await fetch(`${BACKEND_URL}/health`, { cache: "no-store" });
    if (!r.ok) {
      return NextResponse.json({ ok: false, status: r.status }, { status: r.status });
    }
    const data = await r.json();
    return NextResponse.json(data, { headers: { "Cache-Control": "no-store" } });
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json({ ok: false, error: msg }, { status: 502 });
  }
}
