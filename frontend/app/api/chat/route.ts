// 단일/다중 도메인 채팅 엔드포인트.
//
// 도메인 결정 규칙:
//  - body.domain 이 string  → 그대로 단일 도메인 호출 (UI 의 [domain] 라우트가 명시).
//  - body.domain 이 string[] → 길이 1 이면 단일, 그 이상이면 orchestrator 로 결합 호출.
//  - body.domain 이 "auto"   → classifyMultiDomain 으로 자동 분류 후 동일 규칙 적용.
//
// RBAC: 모든 경로에서 사용자 권한(`getAllowedDomains`)으로 필터/검증.
//  - auto 모드: 권한 없는 도메인은 분류 결과에서 제외 후 normal fallback.
//  - 명시 모드: 권한 없는 도메인은 403 거부.

import { NextRequest } from "next/server";
import { tryGetDomain } from "@/lib/agents/registry";
import { classifyMultiDomain, runDomain, type RagSourceMeta } from "@/lib/agents/router";
import { runMultiDomain } from "@/lib/agents/orchestrator";
import { displayName, getAllowedDomains, getAuthToken, getCurrentUser } from "@/lib/shared/auth";
import {
  CONCEPT_MAP_PROMPT,
  isFreeConceptMapRequest,
  uploadRequestText,
} from "@/lib/shared/free-request-tasks";
import type { ChatMessage, DomainKey } from "@/lib/shared/types";

export const runtime = "nodejs";

type Body = {
  domain: DomainKey | DomainKey[] | "auto";
  history: ChatMessage[];
  text: string;
  /** 재생성 시 sampling 다양화 신호. true 이면 backend 가 temperature/seed 를 풀어준다. */
  regenerate?: boolean;
  /**
   * 프로젝트(대화 그룹) Instructions. 도메인 system prompt 뒤에 별도 섹션으로 부착된다.
   * 일반 대화에서는 비어있다.
   */
  projectInstructions?: string;
  /**
   * 사용자가 입력창에 첨부한 파일 메타(업로드 순). LLM 이 도구 호출 시 receipt_attachment_ids 등으로
   * 활용. 본문에는 노출하지 않고 system prompt 의 컨텍스트 블록으로만 주입.
   */
  attachments?: { id: number; filename: string; mime: string }[];
};

type ResolveResult =
  | { ok: true; domains: DomainKey[] }
  | { ok: false; error: string; status: number };

function resolveDomains(
  req: Body["domain"],
  text: string,
  allowedDomains: readonly DomainKey[],
): ResolveResult {
  const allowedSet = new Set(allowedDomains);
  if (req === "auto") {
    return { ok: true, domains: classifyMultiDomain(text, allowedDomains) };
  }
  const arr = Array.isArray(req) ? req : [req];
  if (arr.length === 0) return { ok: false, error: "domain required", status: 400 };
  for (const k of arr) {
    if (!tryGetDomain(k)) return { ok: false, error: `unknown domain: ${k}`, status: 404 };
    if (!allowedSet.has(k)) {
      return { ok: false, error: `access denied: ${k}`, status: 403 };
    }
  }
  return { ok: true, domains: arr };
}

export async function POST(req: NextRequest) {
  let body: Body;
  try {
    body = (await req.json()) as Body;
  } catch {
    return new Response("invalid JSON", { status: 400 });
  }
  if (!body.text?.trim()) return new Response("text required", { status: 400 });

  // RBAC: 사용자 권한 도메인 결정.
  // 비로그인(미들웨어가 사실상 차단하지만 안전망) 시엔 normal 만 허용.
  const user = await getCurrentUser();
  const allowedDomains = user ? getAllowedDomains(user) : (["normal"] as const);

  // 자유 문장 개념도 요청(버튼 없이 채팅·외부 클라이언트로 들어온 것) → 요청 원문을 사용자 명의
  // .txt 첨부로 올리고 표준 프롬프트로 바꿔 backend 개념도 fast-path 로 보낸다. 첨부 ID 는 단일
  // 도메인 경로에서만 system prompt 에 들어가므로 도메인을 하나로 고정한다. 업로드 실패 시 원래대로.
  if (user && isFreeConceptMapRequest(body.text, body.attachments?.length ?? 0)) {
    const token = await getAuthToken();
    const att = token ? await uploadRequestText(body.text, token) : null;
    if (att) {
      body = {
        ...body,
        text: CONCEPT_MAP_PROMPT,
        attachments: [att],
        domain: allowedDomains.includes("sales") ? "sales" : "normal",
      };
    }
  }

  const resolved = resolveDomains(body.domain, body.text, allowedDomains);
  if (!resolved.ok) return new Response(resolved.error, { status: resolved.status });
  const domains = resolved.domains;

  // 재생성 신호가 오면 sampling 다양화. seed 는 매번 새로 생성하여 같은 입력도 다른 답.
  const diversify = body.regenerate
    ? { temperature: 0.7, topP: 0.9, seed: Math.floor(Math.random() * 1_000_000_000) }
    : {};

  // RAG: 모든 단일 도메인(normal/finance/sales/design/dev)에서 사내 자료 검색.
  // 사칙은 backend 에서 domain="all" 로 저장되어 도메인 무관하게 매칭된다. 재무관리 도메인에서
  // "법인카드 사용 규정" 같은 사칙 질문이 들어와도 사칙 chunk 가 system prompt 에 부착돼 인용 가능.
  // 검색은 스트림 시작 전 await — 결과를 system prompt 에 합치고 sources 는 응답 헤더에 직렬화.
  // (multi-domain 은 후속 — orchestrator 가 ragContext 를 아직 받지 않음.)
  let ragSources: RagSourceMeta[] = [];
  let ragContext = "";
  if (domains.length === 1) {
    try {
      const r = await fetch(`${req.nextUrl.origin}/api/rag/search`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          // 쿠키 직접 전달 — 서버 컴포넌트 fetch 는 자동 전파 안 함.
          cookie: req.headers.get("cookie") ?? "",
        },
        // top_k=4 / min_score=0.50 — 캘리브레이션 결과:
        //  실측 점수 분포:
        //   - 정답 정합 질의: 0.51 ~ 0.77 (연차 당일 0.511, 카톡 보고 0.683, 점심 식대 0.725 …)
        //   - 비질의/노이즈("기억해둬" 등): 0.45 ~ 0.48
        //   → 0.50 컷이 정답은 통과시키고 비질의는 자연 컷.
        //  무관 chunk 합성 hallucination 은 별도 가드(system prompt 규칙 11 + context 블록 안내문) 로 처리.
        body: JSON.stringify({ query: body.text, top_k: 4, min_score: 0.5 }),
        cache: "no-store",
      });
      if (r.ok) {
        const data = (await r.json()) as {
          context_block?: string;
          sources?: RagSourceMeta[];
        };
        ragSources = Array.isArray(data?.sources) ? data.sources : [];
        ragContext = (data?.context_block ?? "").trim();
      }
    } catch {
      /* 임베딩/검색 실패는 silent — RAG 없이 진행. */
    }
  }

  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      try {
        const projectInstructions = body.projectInstructions?.trim() || undefined;
        // 첨부/사용자 컨텍스트 — 단일 도메인 경로에서만 system prompt 에 inject.
        // (multi-domain orchestrator 는 아직 컨텍스트 inject 미지원 — 후속 작업.)
        const attachments = body.attachments ?? [];
        const userName = displayName(user);
        const userId = user?.id;
        const gen =
          domains.length === 1
            ? runDomain({
                domain: domains[0],
                history: body.history ?? [],
                userText: body.text,
                projectInstructions,
                ragContext,
                attachments,
                userName,
                userId,
                ...diversify,
              })
            : runMultiDomain({
                domains,
                history: body.history ?? [],
                userText: body.text,
                projectInstructions,
                userId,
                ...diversify,
              });
        for await (const chunk of gen) {
          if (chunk.delta) controller.enqueue(encoder.encode(chunk.delta));
          if (chunk.done) break;
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        controller.enqueue(encoder.encode(`\n\n⚠ stream error: ${msg}`));
      } finally {
        controller.close();
      }
    },
  });

  // sources 는 base64-encoded JSON 으로 헤더에 실음(헤더 값에 한글/특수문자 안전 운반).
  const sourcesB64 = Buffer.from(JSON.stringify(ragSources), "utf-8").toString("base64");
  return new Response(stream, {
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "no-store",
      "X-Domain": domains.join(","),
      "X-Mode": domains.length === 1 ? "single" : "multi",
      "X-Sources": sourcesB64,
    },
  });
}
