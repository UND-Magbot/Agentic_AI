// 다중 도메인 오케스트레이터.
//
// 단일 도메인 호출(router.runDomain)과 분리해 둔 이유:
//  - 다중 도메인은 system prompt 합성 규칙이 다르다 (가드레일 OR 합집합, tool 네임스페이스 충돌 회피).
//  - RBAC 가 N개 정책의 교집합/합집합으로 평가되어야 하므로 호출 전 검증이 별도로 필요.
//  - 응답 포맷이 다르다 (도메인별 출처 표기 + 결합 결론 섹션).
//
// 도메인 간 직접 import 금지 원칙은 그대로 유지된다 — 도메인을 횡단으로 아는 곳은
// 여전히 lib/agents/* 한 층뿐이다.

import type { DomainKey, ChatMessage } from "@/lib/shared/types";
import { getDomain } from "./registry";
import { FEW_SHOT_KOREAN_TONE, GLOBAL_PREAMBLE } from "./router";
import { streamLlama, type GenerationChunk } from "@/lib/core/llm/client";

function composeMultiSystemPrompt(domains: DomainKey[]): string {
  const sections = domains.map((k) => {
    const { meta, systemPrompt, tools } = getDomain(k);
    const guards = systemPrompt.guardrails.map((g, i) => `  ${i + 1}. ${g}`).join("\n");
    const toolList = tools.map((t) => `  - ${t.name}: ${t.description}`).join("\n");
    return [
      `### ${meta.label} (${k})`,
      systemPrompt.base,
      "도구:",
      toolList,
      "가드레일:",
      guards,
    ].join("\n");
  });

  return [
    GLOBAL_PREAMBLE,
    "",
    "당신은 여러 사내 도메인을 결합해 답변하는 오케스트레이터입니다.",
    "아래 도메인 각각의 base 지침과 가드레일을 모두 준수합니다.",
    "응답은 (1) 도메인별 근거를 출처와 함께 분리해 제시하고, (2) 마지막에 '결합 결론' 섹션으로 묶습니다.",
    "도구 이름이 충돌하면 'domain.tool' 형식(예: sales.crm.search)으로 호출합니다.",
    "",
    sections.join("\n\n"),
  ].join("\n");
}

export async function* runMultiDomain(opts: {
  domains: DomainKey[];
  history: ChatMessage[];
  userText: string;
  /** 재생성/다양성 옵션. */
  temperature?: number;
  topP?: number;
  seed?: number;
  /** 프로젝트 Instructions. 합성된 multi-domain system prompt 뒤에 부착. */
  projectInstructions?: string;
  /** 현재 요청 사용자 ID. backend 도구 산출물의 소유자로 전달. */
  userId?: number;
}): AsyncGenerator<GenerationChunk> {
  if (opts.domains.length === 0) throw new Error("at least one domain required");

  const base = composeMultiSystemPrompt(opts.domains);
  const systemPrompt = opts.projectInstructions?.trim()
    ? `${base}\n\n## 프로젝트 Instructions\n${opts.projectInstructions.trim()}`
    : base;
  // 한국어 톤 고정용 few-shot 페어 prepend(runDomain 과 동일 처리).
  const messages = [
    ...FEW_SHOT_KOREAN_TONE,
    ...opts.history.map((m) => ({
      role: m.role === "assistant" ? ("assistant" as const) : ("user" as const),
      content: m.content,
    })),
    { role: "user" as const, content: opts.userText },
  ];

  yield* streamLlama({
    systemPrompt,
    messages,
    classification: "internal",
    temperature: opts.temperature,
    topP: opts.topP,
    seed: opts.seed,
    userId: opts.userId,
  });
}
