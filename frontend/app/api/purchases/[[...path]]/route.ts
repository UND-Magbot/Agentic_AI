/**
 * 제품 구매 건 관리 프록시(사용자 2026-11-02) → backend /v1/purchases (경로 목록은 backend/app/api_purchases.py 머리말).
 * - GET  JSON(리스트·건·발주서 초안) 또는 파일(첨부 보기·발주서 PDF/엑셀)을 그대로 넘긴다.
 * - POST JSON 또는 multipart(파일 첨부·지출내역 대조 — 본문·경계 그대로). 응답이 PDF(발주서 미리보기)면 바이트 그대로.
 * 경로 조각은 영문 소문자·숫자·하이픈·밑줄(customs_etc)만 받는다(다른 백엔드 경로로 새지 않게).
 */
import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8002";
const TOKEN_COOKIE = "und_cortex_token";
const BASE = "/v1/purchases";
const SEGMENT = /^[a-z0-9_-]{1,30}$/;
const PHOTO = /^[0-9a-f]{32}\.(jpg|jpeg|png|webp)$/;
const PASS_HEADERS = ["content-type", "content-disposition", "content-length", "cache-control", "x-content-type-options"];

type Params = { params: Promise<{ path?: string[] }> };

async function relay(r: Response) {
  const type = r.headers.get("content-type") ?? "";
  if (type.includes("application/json") || !r.ok) {
    return NextResponse.json(await r.json().catch(() => ({})), { status: r.status });
  }
  const headers = new Headers();
  for (const k of PASS_HEADERS) {
    const v = r.headers.get(k);
    if (v) headers.set(k, v);
  }
  return new NextResponse(r.body, { status: r.status, headers });
}

async function target(params: Params["params"], req: NextRequest) {
  const { path = [] } = await params;
  // 입고 사진 이름(uuid.확장자)만 점을 허용한다 — /{id}/receipt-photos/{name}
  const ok = (p: string, i: number) => SEGMENT.test(p) || (i === 2 && path[1] === "receipt-photos" && PHOTO.test(p));
  if (path.length > 5 || !path.every(ok)) return null;
  const qs = req.nextUrl.searchParams;
  const keep = new URLSearchParams();
  for (const k of ["fmt", "revision"]) {
    const v = qs.get(k);
    if (v && /^[a-z0-9]{1,8}$/.test(v)) keep.set(k, v);
  }
  return `${BACKEND_URL}${BASE}${path.length ? `/${path.join("/")}` : ""}${keep.size ? `?${keep}` : ""}`;
}

export async function GET(req: NextRequest, { params }: Params) {
  const url = await target(params, req);
  if (!url) return NextResponse.json({ detail: "찾을 수 없습니다." }, { status: 404 });
  const tok = (await cookies()).get(TOKEN_COOKIE)?.value;
  if (!tok) return NextResponse.json({ detail: "인증이 필요합니다." }, { status: 401 });
  return relay(await fetch(url, { headers: { Authorization: `Bearer ${tok}` }, cache: "no-store" }));
}

export async function POST(req: NextRequest, { params }: Params) {
  const url = await target(params, req);
  if (!url) return NextResponse.json({ detail: "찾을 수 없습니다." }, { status: 404 });
  const tok = (await cookies()).get(TOKEN_COOKIE)?.value;
  if (!tok) return NextResponse.json({ detail: "인증이 필요합니다." }, { status: 401 });
  const type = req.headers.get("content-type") ?? "application/json";
  const multipart = type.startsWith("multipart/form-data");
  return relay(await fetch(url, {
    method: "POST",
    headers: { Authorization: `Bearer ${tok}`, "Content-Type": multipart ? type : "application/json" },
    body: multipart ? await req.arrayBuffer() : await req.text(),
    cache: "no-store",
  }));
}
