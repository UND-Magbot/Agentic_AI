/**
 * 영업 건 관리 프록시.
 * GET  /api/sales-deals                      → backend GET /v1/sales-deals (리스트 — 회사 제품 추천의 [영업 건 관리] 탭)
 * GET  /api/sales-deals/{id}                 → backend GET /v1/sales-deals/{id} (한 건 + 변경 이력)
 * GET  /api/sales-deals/photos/{name}        → 이관한 출하 사진
 * POST /api/sales-deals                      → 새 견적 건
 * GET  /api/sales-deals/export               → 리스트 전체 엑셀(공급가액)
 * GET/POST /api/sales-deals/initials         → 담당자 건 번호 이니셜(AL·VT…)
 * POST /api/sales-deals/{id}/{action}        → won · invoices · payments · paid-full · shipments · remove · review · drop
 * POST /api/sales-deals/{id}/po              → 발주서 파일 첨부(multipart — 본문·경계 그대로 넘김)
 * POST /api/sales-deals/{id}/po/{n}/compare  → 발주서 ↔ 견적 품목 AI 대조(결과는 발주서 기록에 저장)
 * POST /api/sales-deals/{id}/history/rollback → 히스토리 되돌리기(관리자) {keep_id}
 * GET  /api/sales-deals/{id}/po/{index}      → 발주서 파일 보기·내려받기(바이트·헤더 그대로)
 * 수주 뒤 진행(거래명세서 → 결제 조건 → 회차별 입금 확인 → 출하):
 * GET  /api/sales-deals/{id}/statement/draft · /statement/file?fmt=pdf|xlsx · /ship-photos/{pid}
 * POST /api/sales-deals/{id}/statement · /terms · /confirm-term · /unconfirm-term (JSON) · /ship-photos (multipart)
 * 수주 진행 탭(AI 대화): GET /{id}/order · POST /{id}/order/draft · /order/ship · /order/note (JSON)
 *                      · /order/agent (생각·단계·답을 NDJSON 으로 흘려 보냄 — 모으지 않고 그대로 잇는다)
 */
import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8002";
const TOKEN_COOKIE = "und_cortex_token";
const BASE = "/v1/sales-deals";
const ACTIONS = new Set(["won", "invoices", "payments", "paid-full", "shipments", "shipment-edit", "remove", "review", "statement", "terms", "confirm-term", "unconfirm-term", "drop", "meeting", "reset-to-quote"]);
// 파일을 올리는 동작 — 본문·경계를 그대로 넘긴다
const UPLOADS = new Set(["po", "ship-photos"]);

/** 파일 응답 — 바이트와 형식·이름 헤더를 그대로 넘긴다. */
async function passFile(url: string, tok: string) {
  const r = await fetch(url, { headers: { Authorization: `Bearer ${tok}` }, cache: "no-store" });
  if (!r.ok) return NextResponse.json(await r.json().catch(() => ({})), { status: r.status });
  const headers = new Headers();
  for (const k of ["content-type", "content-disposition", "content-length", "cache-control", "x-content-type-options"]) {
    const v = r.headers.get(k);
    if (v) headers.set(k, v);
  }
  return new NextResponse(r.body, { status: 200, headers });
}

type Params = { params: Promise<{ path?: string[] }> };

const notFound = () => NextResponse.json({ detail: "찾을 수 없습니다." }, { status: 404 });

async function token() {
  return (await cookies()).get(TOKEN_COOKIE)?.value;
}

export async function GET(req: NextRequest, { params }: Params) {
  const { path = [] } = await params;
  const tok = await token();
  if (!tok) return NextResponse.json({ detail: "인증이 필요합니다." }, { status: 401 });
  // 리스트 — 회사 제품 추천 화면의 [영업 건 관리] 탭(클라이언트)이 받는다
  if (path.length === 0) {
    const r = await fetch(`${BACKEND_URL}${BASE}`, { headers: { Authorization: `Bearer ${tok}` }, cache: "no-store" });
    return NextResponse.json(await r.json().catch(() => ({})), { status: r.status });
  }
  if (path.length === 1 && path[0] === "export") return passFile(`${BACKEND_URL}${BASE}/export`, tok);
  if (path.length === 1 && path[0] === "initials") {
    const r = await fetch(`${BACKEND_URL}${BASE}/initials`, { headers: { Authorization: `Bearer ${tok}` }, cache: "no-store" });
    return NextResponse.json(await r.json().catch(() => ({})), { status: r.status });
  }
  if (path.length === 2 && path[0] === "photos" && /^ship_r\d+_\d+\.jpg$/.test(path[1])) {
    const r = await fetch(`${BACKEND_URL}${BASE}/photos/${path[1]}`, { headers: { Authorization: `Bearer ${tok}` }, cache: "no-store" });
    if (!r.ok) return NextResponse.json(await r.json().catch(() => ({})), { status: r.status });
    return new NextResponse(r.body, { status: 200, headers: { "content-type": "image/jpeg", "cache-control": "private, max-age=3600" } });
  }
  if (path.length === 1 && /^\d+$/.test(path[0])) {
    const r = await fetch(`${BACKEND_URL}${BASE}/${path[0]}`, { headers: { Authorization: `Bearer ${tok}` }, cache: "no-store" });
    return NextResponse.json(await r.json().catch(() => ({})), { status: r.status });
  }
  if (path.length === 2 && /^\d+$/.test(path[0]) && path[1] === "order") {
    const r = await fetch(`${BACKEND_URL}${BASE}/${path[0]}/order`, { headers: { Authorization: `Bearer ${tok}` }, cache: "no-store" });
    return NextResponse.json(await r.json().catch(() => ({})), { status: r.status });
  }
  if (path.length === 2 && /^\d+$/.test(path[0]) && path[1] === "quote-file") {
    const sp = req.nextUrl.searchParams;
    const q = sp.get("quote");
    const rev = sp.get("revision");
    if (!q || !/^\d+$/.test(q)) return notFound();
    const fmt = sp.get("fmt") === "xlsx" ? "xlsx" : "pdf";
    return passFile(`${BACKEND_URL}${BASE}/${path[0]}/quote-file?quote=${q}&fmt=${fmt}${rev && /^\d+$/.test(rev) ? `&revision=${rev}` : ""}`, tok);
  }
  if (path.length === 3 && /^\d+$/.test(path[0]) && path[1] === "order" && path[2] === "preview") {
    return passFile(`${BACKEND_URL}${BASE}/${path[0]}/order/preview`, tok);
  }
  if (path.length === 3 && /^\d+$/.test(path[0]) && path[1] === "statement" && path[2] === "draft") {
    const r = await fetch(`${BACKEND_URL}${BASE}/${path[0]}/statement/draft`, { headers: { Authorization: `Bearer ${tok}` }, cache: "no-store" });
    return NextResponse.json(await r.json().catch(() => ({})), { status: r.status });
  }
  if (path.length === 3 && /^\d+$/.test(path[0]) && path[1] === "statement" && path[2] === "file") {
    const fmt = req.nextUrl.searchParams.get("fmt") === "xlsx" ? "xlsx" : "pdf";
    return passFile(`${BACKEND_URL}${BASE}/${path[0]}/statement/file?fmt=${fmt}`, tok);
  }
  if (path.length === 3 && /^\d+$/.test(path[0]) && path[1] === "ship-photos" && /^[0-9a-f]{32}\.(jpg|jpeg|png|webp)$/.test(path[2])) {
    return passFile(`${BACKEND_URL}${BASE}/${path[0]}/ship-photos/${path[2]}`, tok);
  }
  if (path.length === 3 && /^\d+$/.test(path[0]) && path[1] === "po" && /^\d+$/.test(path[2])) {
    const r = await fetch(`${BACKEND_URL}${BASE}/${path[0]}/po/${path[2]}`, { headers: { Authorization: `Bearer ${tok}` }, cache: "no-store" });
    if (!r.ok) return NextResponse.json(await r.json().catch(() => ({})), { status: r.status });
    const headers = new Headers();
    for (const k of ["content-type", "content-disposition", "content-length", "cache-control", "x-content-type-options"]) {
      const v = r.headers.get(k);
      if (v) headers.set(k, v);
    }
    return new NextResponse(r.body, { status: 200, headers });
  }
  return notFound();
}

export async function POST(req: NextRequest, { params }: Params) {
  const { path = [] } = await params;
  const isOrder = path.length === 3 && /^\d+$/.test(path[0]) && path[1] === "order";
  if (isOrder && !["draft", "note", "agent", "ship"].includes(path[2])) return notFound();
  // 리스트에서 체크한 건들 삭제(JSON {ids}, 관리자) · 담당자 이니셜 저장
  const isBatch = path.length === 1 && (path[0] === "delete" || path[0] === "initials");
  const isUpload = path.length === 2 && /^\d+$/.test(path[0]) && UPLOADS.has(path[1]);
  const isPoCompare = path.length === 4 && /^\d+$/.test(path[0]) && path[1] === "po" && /^\d+$/.test(path[2]) && path[3] === "compare";
  // 히스토리 되돌리기(관리자 — 사용자 2026-10-08)
  const isHistory = path.length === 3 && /^\d+$/.test(path[0]) && path[1] === "history" && path[2] === "rollback";
  const ok = path.length === 0 || isUpload || isOrder || isBatch || isPoCompare || isHistory || (path.length === 2 && /^\d+$/.test(path[0]) && ACTIONS.has(path[1]));
  if (!ok) return notFound();
  const tok = await token();
  if (!tok) return NextResponse.json({ detail: "인증이 필요합니다." }, { status: 401 });
  if (isOrder && path[2] === "agent") {
    const r = await fetch(`${BACKEND_URL}${BASE}/${path[0]}/order/agent`, {
      method: "POST",
      headers: { Authorization: `Bearer ${tok}`, "Content-Type": "application/json" },
      body: await req.text(),
      cache: "no-store",
    });
    if (!r.ok || !r.body) return NextResponse.json(await r.json().catch(() => ({})), { status: r.status });
    return new NextResponse(r.body, {
      status: 200,
      headers: { "content-type": "application/x-ndjson; charset=utf-8", "cache-control": "no-store", "x-accel-buffering": "no" },
    });
  }
  if (isUpload) {
    const r = await fetch(`${BACKEND_URL}${BASE}/${path.join("/")}`, {
      method: "POST",
      headers: { Authorization: `Bearer ${tok}`, "Content-Type": req.headers.get("content-type") ?? "" },
      body: await req.arrayBuffer(),
      cache: "no-store",
    });
    return NextResponse.json(await r.json().catch(() => ({})), { status: r.status });
  }
  const r = await fetch(`${BACKEND_URL}${BASE}${path.length ? `/${path.join("/")}` : ""}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${tok}`, "Content-Type": "application/json" },
    body: await req.text(),
    cache: "no-store",
  });
  return NextResponse.json(await r.json().catch(() => ({})), { status: r.status });
}
