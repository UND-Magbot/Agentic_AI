/**
 * 제품 이미지 프록시 — 컨셉 이미지의 외형 참고로 쓰는 제품 사진.
 * GET  /api/product-images                    → backend GET  /v1/proposal-projects/product-images (제품별 사진 목록)
 * GET  /api/product-images/file/{imageId}     → 사진(JPEG)
 * POST /api/product-images/{cardId}           → 사진 올리기(multipart, 필드 file) — 승인 대기로 들어간다
 * POST /api/product-images/review/{imageId}   → 승인·반려(영업 관리자만, backend 가 403 으로 거른다)
 */
import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8002";
const TOKEN_COOKIE = "und_cortex_token";
const BASE = "/v1/proposal-projects/product-images";

type Params = { params: Promise<{ path?: string[] }> };

const notFound = () => NextResponse.json({ detail: "찾을 수 없습니다." }, { status: 404 });
const isId = (s: string | undefined) => !!s && /^\d+$/.test(s);

async function token() {
  return (await cookies()).get(TOKEN_COOKIE)?.value;
}

export async function GET(_req: NextRequest, { params }: Params) {
  const { path = [] } = await params;
  const t = await token();
  if (!t) return NextResponse.json({ detail: "인증이 필요합니다." }, { status: 401 });
  if (path.length === 0) {
    const r = await fetch(`${BACKEND_URL}${BASE}`, { headers: { Authorization: `Bearer ${t}` }, cache: "no-store" });
    return NextResponse.json(await r.json().catch(() => ({})), { status: r.status });
  }
  if (path.length !== 2 || path[0] !== "file" || !isId(path[1])) return notFound();
  const r = await fetch(`${BACKEND_URL}${BASE}/file/${path[1]}`, { headers: { Authorization: `Bearer ${t}` } });
  if (!r.ok) return NextResponse.json(await r.json().catch(() => ({})), { status: r.status });
  return new NextResponse(await r.arrayBuffer(), {
    headers: { "Content-Type": "image/jpeg", "Cache-Control": "private, max-age=3600" },
  });
}

export async function POST(req: NextRequest, { params }: Params) {
  const { path = [] } = await params;
  const t = await token();
  if (!t) return NextResponse.json({ detail: "인증이 필요합니다." }, { status: 401 });
  let target: string;
  let headers: Record<string, string>;
  if (path.length === 1 && isId(path[0])) {
    target = `${BASE}/${path[0]}`;
    headers = { "Content-Type": req.headers.get("content-type") ?? "" };
  } else if (path.length === 2 && path[0] === "review" && isId(path[1])) {
    target = `${BASE}/review/${path[1]}`;
    headers = { "Content-Type": "application/json" };
  } else {
    return notFound();
  }
  const r = await fetch(`${BACKEND_URL}${target}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${t}`, ...headers },
    body: await req.arrayBuffer(),
    cache: "no-store",
  });
  return NextResponse.json(await r.json().catch(() => ({})), { status: r.status });
}
