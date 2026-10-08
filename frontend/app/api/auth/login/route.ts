/**
 * 로그인 프록시.
 * 클라이언트로부터 username/password 를 받아 backend `/v1/auth/login` 로 전달하고,
 * 응답의 access_token 을 httpOnly 쿠키 `und_cortex_token` 에 저장한다.
 * 토큰 자체는 응답 본문에 포함하지 않는다(브라우저 JS 노출 방지).
 */
import { NextResponse } from "next/server";

export const runtime = "nodejs";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8002";
const TOKEN_COOKIE = "und_cortex_token";

export async function POST(req: Request) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json(
      { ok: false, detail: "요청 본문이 올바르지 않습니다." },
      { status: 400 },
    );
  }
  const { username, password } = (body ?? {}) as Record<string, unknown>;
  if (typeof username !== "string" || typeof password !== "string") {
    return NextResponse.json(
      { ok: false, detail: "username/password 가 필요합니다." },
      { status: 400 },
    );
  }

  let r: Response;
  try {
    r = await fetch(`${BACKEND_URL}/v1/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
      cache: "no-store",
    });
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json(
      { ok: false, detail: `백엔드 연결 실패: ${msg}` },
      { status: 502 },
    );
  }

  const data = await r.json().catch(() => ({}) as Record<string, unknown>);
  if (!r.ok) {
    const d = data as { detail?: unknown; field?: unknown; reason?: unknown };
    // 백엔드가 명시 detail 을 안 주면(예: 422 validation, 5xx 등) 비밀번호 오류 문구로 통일.
    const detail = typeof d.detail === "string" ? d.detail : "비밀번호가 일치하지 않습니다.";
    const field =
      d.field === "username" || d.field === "password" ? d.field : undefined;
    const reason = typeof d.reason === "string" ? d.reason : undefined;
    return NextResponse.json(
      { ok: false, detail, field, reason },
      { status: r.status },
    );
  }

  const token = (data as { access_token?: unknown }).access_token;
  const expiresIn = (data as { expires_in?: unknown }).expires_in;
  const user = (data as { user?: unknown }).user;
  if (typeof token !== "string") {
    return NextResponse.json(
      { ok: false, detail: "백엔드 응답이 올바르지 않습니다." },
      { status: 502 },
    );
  }

  const maxAge = typeof expiresIn === "number" && expiresIn > 0 ? expiresIn : 1800;

  const res = NextResponse.json({ ok: true, user });
  res.cookies.set({
    name: TOKEN_COOKIE,
    value: token,
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge,
  });
  return res;
}
