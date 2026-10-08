/**
 * 비밀번호 변경 프록시. 쿠키의 토큰을 Authorization 헤더로 backend 에 전달.
 * 본문은 그대로 forwarding (current_password / new_password).
 */
import { cookies } from "next/headers";
import { NextResponse } from "next/server";

export const runtime = "nodejs";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8002";
const TOKEN_COOKIE = "und_cortex_token";

export async function POST(req: Request) {
  const c = await cookies();
  const token = c.get(TOKEN_COOKIE)?.value;
  if (!token) {
    return NextResponse.json(
      { ok: false, detail: "인증되지 않았습니다." },
      { status: 401 },
    );
  }

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json(
      { ok: false, detail: "요청 본문이 올바르지 않습니다." },
      { status: 400 },
    );
  }
  const { current_password, new_password } = (body ?? {}) as Record<string, unknown>;
  if (typeof current_password !== "string" || typeof new_password !== "string") {
    return NextResponse.json(
      { ok: false, detail: "현재/새 비밀번호가 필요합니다." },
      { status: 400 },
    );
  }
  if (new_password.length < 8) {
    return NextResponse.json(
      { ok: false, detail: "새 비밀번호는 8자 이상이어야 합니다." },
      { status: 400 },
    );
  }

  let r: Response;
  try {
    r = await fetch(`${BACKEND_URL}/v1/auth/change-password`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ current_password, new_password }),
      cache: "no-store",
    });
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json(
      { ok: false, detail: `백엔드 연결 실패: ${msg}` },
      { status: 502 },
    );
  }

  if (r.status === 204) {
    return NextResponse.json({ ok: true });
  }

  const data = await r.json().catch(() => ({}) as Record<string, unknown>);
  const detail =
    typeof (data as { detail?: unknown }).detail === "string"
      ? (data as { detail: string }).detail
      : "비밀번호 변경에 실패했습니다.";
  return NextResponse.json({ ok: false, detail }, { status: r.status });
}
