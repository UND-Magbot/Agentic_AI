/**
 * 로그아웃 — 쿠키 삭제만 수행. 백엔드는 stateless JWT 라 호출 불필요.
 * 추후 서버측 토큰 폐기(블랙리스트) 도입 시 backend 호출을 여기에 추가.
 */
import { NextResponse } from "next/server";

export const runtime = "nodejs";

const TOKEN_COOKIE = "und_cortex_token";

export async function POST() {
  const res = NextResponse.json({ ok: true });
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
