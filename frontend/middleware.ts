/**
 * 라우트 가드 미들웨어.
 *  - httpOnly 쿠키 `und_cortex_token` 이 없으면 보호 라우트 접근 시 /login 으로 리다이렉트.
 *  - 토큰을 가진 사용자가 /login 에 접근하면 / 로 보냄.
 *  - 정적 자원(/_next/, 이미지, favicon)과 인증 API 자체는 통과.
 */
import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const TOKEN_COOKIE = "und_cortex_token";

const PUBLIC_PREFIXES = [
  "/_next/",
  "/api/auth/",     // login/logout/me 자체는 공개. me 는 내부적으로 토큰을 검사.
  "/api/health",    // 헬스 프로브 — 인증 우회.
];

const PUBLIC_PATHS = new Set<string>(["/login", "/favicon.ico"]);

function isPublic(pathname: string): boolean {
  if (PUBLIC_PATHS.has(pathname)) return true;
  if (PUBLIC_PREFIXES.some((p) => pathname.startsWith(p))) return true;
  // 이미지/폰트 등 정적 파일.
  if (/\.(png|jpg|jpeg|svg|gif|webp|ico|woff2?|ttf|otf|map)$/i.test(pathname)) return true;
  return false;
}

export function middleware(req: NextRequest) {
  const { pathname, search } = req.nextUrl;
  const token = req.cookies.get(TOKEN_COOKIE)?.value;

  // 로그인된 사용자가 /login 으로 가면 메인으로.
  if (token && pathname === "/login") {
    const url = req.nextUrl.clone();
    url.pathname = "/";
    url.search = "";
    return NextResponse.redirect(url);
  }

  if (token) return NextResponse.next();
  if (isPublic(pathname)) return NextResponse.next();

  // 미인증 → 로그인 페이지로. 원래 가려던 곳을 next 쿼리로 보존.
  const url = req.nextUrl.clone();
  url.pathname = "/login";
  url.search = "";
  if (pathname && pathname !== "/") {
    url.searchParams.set("next", pathname + search);
  }
  return NextResponse.redirect(url);
}

export const config = {
  // _next 정적 자원은 매처에서 미리 제외. 그래도 함수 안에서 한 번 더 isPublic 으로 방어.
  matcher: ["/((?!_next/static|_next/image).*)"],
};
