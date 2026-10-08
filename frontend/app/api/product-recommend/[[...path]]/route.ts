/**
 * 회사 제품 추천 + 정정 학습 프록시.
 * POST /api/product-recommend                            → backend POST /v1/product-recommend (추천·정정)
 * POST /api/product-recommend/corrections/draft          → 학습 초안(저장 안 함, 사용자가 문구 확인)
 * POST /api/product-recommend/corrections                → 학습 저장(영업 관리자=바로 반영, 그 외=승인 대기)
 * GET  /api/product-recommend/corrections                → 학습된 정정 목록
 * POST /api/product-recommend/corrections/{id}/review    → 영업 관리자 승인·거절(backend 가 403 으로 거른다)
 * GET  /api/product-recommend/atc/spec                   → 툴체인저 질문지(J01~J10·후속) 원본
 * GET  /api/product-recommend/atc/test-samples[/{id}]    → 테스트용 정답지 샘플(질문 모두 채우기)
 * POST /api/product-recommend/atc/extract                → 채운 미팅 질문지(파일 multipart) → 요약·추출
 * POST /api/product-recommend/atc/evaluate               → 판정만(후속 질문 계산)
 * POST /api/product-recommend/atc/recommend              → 판정 + 기록
 * POST /api/product-recommend/recommendations/{id}/finalize → 최종 제안 확정(미팅·결과·근거·대화 저장)
 * POST /api/product-recommend/quotes/draft                 → 견적서 작성 시작(초안 또는 기존 견적)
 * POST /api/product-recommend/quotes/manual                → 견적서 수기 작성(AI 추천 없이 빈 견적서)
 * GET  /api/product-recommend/quotes/price-list            → 회사 단가표(수기 견적의 [단가표에서 추가])
 * GET  /api/product-recommend/quotes · /quotes/{id}        → 내 견적서 목록 · 한 건
 * POST /api/product-recommend/quotes/{id}/chat|edit|issue  → 대화로 고치기 · 직접 수정 저장 · 발행
 * GET  /api/product-recommend/quotes/{id}/file?fmt=pdf|xlsx[&revision=n] → 견적서 내려받기(바이트 그대로)
 * GET  /api/product-recommend/quotes/{id}/preview         → 지금 상태 그대로의 견적서 PDF 미리보기
 * GET  /api/product-recommend/proposals/{id}              → 영업 건의 [미팅 정보](최종 제안 확정 기록)
 * POST /api/product-recommend/atc/agent                  → GPT 식 대화(생각·단계·답을 NDJSON 으로 흘려 보냄)
 * POST /api/product-recommend/atc/answer                 → 대화로 받은 답을 질문 칸에 옮김
 * POST /api/product-recommend/atc/spec-search            → 실제 제품 툴의 공개 사양 외부 검색(외부 AI, 제품명만 전송)
 */
import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8002";
const TOKEN_COOKIE = "und_cortex_token";
const BASE = "/v1/product-recommend";

type Params = { params: Promise<{ path?: string[] }> };

const notFound = () => NextResponse.json({ detail: "찾을 수 없습니다." }, { status: 404 });

async function forward(method: "GET" | "POST", path: string, body?: string) {
  const token = (await cookies()).get(TOKEN_COOKIE)?.value;
  if (!token) return NextResponse.json({ detail: "인증이 필요합니다." }, { status: 401 });
  const r = await fetch(`${BACKEND_URL}${BASE}${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, ...(body ? { "Content-Type": "application/json" } : {}) },
    body,
    cache: "no-store",
  });
  return NextResponse.json(await r.json().catch(() => ({})), { status: r.status });
}

export async function GET(req: NextRequest, { params }: Params) {
  const { path = [] } = await params;
  if (path.length === 1 && path[0] === "corrections") return forward("GET", "/corrections");
  if (path.length === 2 && path[0] === "proposals" && /^\d+$/.test(path[1])) return forward("GET", `/proposals/${path[1]}`);
  if (path.length === 2 && path[0] === "atc" && path[1] === "spec") return forward("GET", "/atc/spec");
  if (path.length === 2 && path[0] === "atc" && path[1] === "test-samples") return forward("GET", "/atc/test-samples");
  if (path.length === 3 && path[0] === "atc" && path[1] === "test-samples" && /^s\d+$/.test(path[2])) {
    return forward("GET", `/atc/test-samples/${path[2]}`);
  }
  if (path.length === 1 && path[0] === "quotes") return forward("GET", "/quotes");
  if (path.length === 2 && path[0] === "quotes" && path[1] === "price-list") return forward("GET", "/quotes/price-list");
  if (path.length === 2 && path[0] === "quotes" && /^\d+$/.test(path[1])) return forward("GET", `/quotes/${path[1]}`);
  if (path.length === 3 && path[0] === "quotes" && /^\d+$/.test(path[1]) && path[2] === "file") {
    const sp = req.nextUrl.searchParams;
    const fmt = sp.get("fmt") === "pdf" ? "pdf" : "xlsx";
    const rev = sp.get("revision");
    return forwardFile(`/quotes/${path[1]}/file?fmt=${fmt}${rev && /^\d+$/.test(rev) ? `&revision=${rev}` : ""}`);
  }
  if (path.length === 3 && path[0] === "quotes" && /^\d+$/.test(path[1]) && path[2] === "preview") {
    return forwardFile(`/quotes/${path[1]}/preview`);
  }
  return notFound();
}

/** 견적서 파일 — 바이트와 Content-Type·Content-Disposition 을 그대로 넘긴다. */
async function forwardFile(path: string) {
  const token = (await cookies()).get(TOKEN_COOKIE)?.value;
  if (!token) return NextResponse.json({ detail: "인증이 필요합니다." }, { status: 401 });
  const r = await fetch(`${BACKEND_URL}${BASE}${path}`, { headers: { Authorization: `Bearer ${token}` }, cache: "no-store" });
  if (!r.ok) return NextResponse.json(await r.json().catch(() => ({})), { status: r.status });
  const headers = new Headers();
  for (const k of ["content-type", "content-disposition", "content-length"]) {
    const v = r.headers.get(k);
    if (v) headers.set(k, v);
  }
  return new NextResponse(r.body, { status: 200, headers });
}

/** 흘려 보내는 응답(NDJSON) — 본문을 모으지 않고 그대로 이어 준다(생각 과정이 실시간으로 보이게). */
async function forwardStream(path: string, body: string) {
  const token = (await cookies()).get(TOKEN_COOKIE)?.value;
  if (!token) return NextResponse.json({ detail: "인증이 필요합니다." }, { status: 401 });
  const r = await fetch(`${BACKEND_URL}${BASE}${path}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body,
    cache: "no-store",
  });
  if (!r.ok || !r.body) return NextResponse.json(await r.json().catch(() => ({})), { status: r.status });
  return new NextResponse(r.body, {
    status: 200,
    headers: { "content-type": "application/x-ndjson; charset=utf-8", "cache-control": "no-store", "x-accel-buffering": "no" },
  });
}

/** 첨부 파일(multipart)은 본문을 바이트 그대로, 원래 Content-Type(경계 포함) 으로 넘긴다. */
async function forwardMultipart(req: NextRequest, path: string) {
  const token = (await cookies()).get(TOKEN_COOKIE)?.value;
  if (!token) return NextResponse.json({ detail: "인증이 필요합니다." }, { status: 401 });
  const r = await fetch(`${BACKEND_URL}${BASE}${path}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": req.headers.get("content-type") ?? "" },
    body: await req.arrayBuffer(),
    cache: "no-store",
  });
  return NextResponse.json(await r.json().catch(() => ({})), { status: r.status });
}

export async function POST(req: NextRequest, { params }: Params) {
  const { path = [] } = await params;
  if (path.length === 2 && path[0] === "atc" && path[1] === "extract") return forwardMultipart(req, "/atc/extract");
  const body = await req.text();
  if (path.length === 0) return forward("POST", "", body);
  if (path.length === 2 && path[0] === "atc" && path[1] === "agent") return forwardStream("/atc/agent", body);
  if (path.length === 1 && path[0] === "corrections") return forward("POST", "/corrections", body);
  if (path.length === 2 && path[0] === "corrections" && path[1] === "draft") return forward("POST", "/corrections/draft", body);
  if (path.length === 2 && path[0] === "atc" && (path[1] === "evaluate" || path[1] === "recommend" || path[1] === "answer" || path[1] === "spec-search" || path[1] === "chat")) {
    return forward("POST", `/atc/${path[1]}`, body);
  }
  if (path.length === 3 && path[0] === "recommendations" && /^\d+$/.test(path[1])
      && path[2] === "finalize") {
    return forward("POST", `/recommendations/${path[1]}/${path[2]}`, body);
  }
  if (path.length === 2 && path[0] === "quotes" && path[1] === "draft") return forward("POST", "/quotes/draft", body);
  if (path.length === 2 && path[0] === "quotes" && path[1] === "manual") return forward("POST", "/quotes/manual", body);
  if (path.length === 3 && path[0] === "quotes" && /^\d+$/.test(path[1]) && ["chat", "edit", "issue"].includes(path[2])) {
    return forward("POST", `/quotes/${path[1]}/${path[2]}`, body);
  }
  if (path.length === 3 && path[0] === "corrections" && /^\d+$/.test(path[1]) && path[2] === "review") {
    return forward("POST", `/corrections/${path[1]}/review`, body);
  }
  return notFound();
}
