/**
 * 현재 사용자 조회. 쿠키의 토큰을 backend `/v1/auth/me` 로 전달한다.
 * 토큰이 없거나 만료/무효면 401 + 쿠키 폐기 응답.
 */
import { cookies } from "next/headers";
import { NextResponse } from "next/server";

export const runtime = "nodejs";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8002";
const TOKEN_COOKIE = "und_cortex_token";

export async function GET() {
  const c = await cookies();
  const token = c.get(TOKEN_COOKIE)?.value;
  if (!token) {
    return NextResponse.json(
      { ok: false, detail: "인증되지 않았습니다." },
      { status: 401 },
    );
  }

  let r: Response;
  try {
    r = await fetch(`${BACKEND_URL}/v1/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    });
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json(
      { ok: false, detail: `백엔드 연결 실패: ${msg}` },
      { status: 502 },
    );
  }

  if (r.status === 401) {
    const res = NextResponse.json(
      { ok: false, detail: "토큰이 만료되었습니다." },
      { status: 401 },
    );
    // 만료 토큰 즉시 폐기 — 다음 요청은 미들웨어가 /login 으로 보냄.
    res.cookies.set({
      name: TOKEN_COOKIE,
      value: "",
      httpOnly: true,
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production",
      path: "/",
      maxAge: 0,
    });
    return res;
  }

  const data = await r.json().catch(() => ({}));
  return NextResponse.json(data, { status: r.status });
}
