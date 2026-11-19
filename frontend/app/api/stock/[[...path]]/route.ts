/**
 * 제품 재고 관리 프록시(사용자 2026-11-19) → backend /v1/stock (경로 목록은 backend/app/api_stock.py 머리말).
 * - GET  목록(?purpose=sale|dev) · 한 줄 변경 이력
 * - POST 한 줄 구분·상태·메모·담당자 고치기(JSON) · /bulk 여러 줄 한 번에
 * 경로 조각은 숫자·'history'·'bulk'·'direct'(직접 등록) 만 받는다(다른 백엔드 경로로 새지 않게).
 */
import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8002";
const TOKEN_COOKIE = "und_cortex_token";
const BASE = "/v1/stock";

type Params = { params: Promise<{ path?: string[] }> };

async function target(params: Params["params"], req: NextRequest) {
  const { path = [] } = await params;
  const ok = path.length === 0 || (path.length === 1 && path[0] === "bulk")
    // 재고 직접 등록 — direct/draft · direct/{id} · direct/{id}/delete
    || (path[0] === "direct" && ((path.length === 2 && (path[1] === "draft" || /^\d{1,12}$/.test(path[1])))
                                 || (path.length === 3 && /^\d{1,12}$/.test(path[1]) && path[2] === "delete")))
    || (/^\d{1,12}$/.test(path[0]) && (path.length === 1 || (path.length === 2 && (path[1] === "history" || path[1] === "photos"))));
  if (!ok) return null;
  const purpose = req.nextUrl.searchParams.get("purpose");
  const qs = purpose === "sale" || purpose === "dev" ? `?purpose=${purpose}` : "";
  return `${BACKEND_URL}${BASE}${path.length ? `/${path.join("/")}` : ""}${qs}`;
}

async function send(req: NextRequest, { params }: Params, init: RequestInit) {
  const url = await target(params, req);
  if (!url) return NextResponse.json({ detail: "찾을 수 없습니다." }, { status: 404 });
  const tok = (await cookies()).get(TOKEN_COOKIE)?.value;
  if (!tok) return NextResponse.json({ detail: "인증이 필요합니다." }, { status: 401 });
  const r = await fetch(url, { ...init, headers: { ...(init.headers ?? {}), Authorization: `Bearer ${tok}` }, cache: "no-store" });
  return NextResponse.json(await r.json().catch(() => ({})), { status: r.status });
}

export async function GET(req: NextRequest, ctx: Params) {
  return send(req, ctx, {});
}

export async function POST(req: NextRequest, ctx: Params) {
  return send(req, ctx, { method: "POST", headers: { "Content-Type": "application/json" }, body: await req.text() });
}
