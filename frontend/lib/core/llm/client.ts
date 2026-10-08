// 자체 호스팅 LLM(현재: 로컬 Ollama 의 qwen3-vl:8b) 어댑터.
// 이 파일은 도메인을 모른다 — 도메인 시스템 프롬프트는 lib/agents/router 가 합성해서 넘긴다.

export type GenerationRequest = {
  systemPrompt: string;
  messages: Array<{ role: "user" | "assistant"; content: string }>;
  /** 보안 등급. 사내 데이터는 자체 호스팅 강제. */
  classification: "internal" | "public";
  /** 재생성/다양성 요청 시 sampling override. 미지정이면 backend 기본값. */
  temperature?: number;
  topP?: number;
  seed?: number;
  /** 인증된 요청 사용자 ID. backend 도구가 만든 파일(예: Codex 생성 이미지)의 소유자가 된다. */
  userId?: number;
};

export type GenerationChunk = { delta: string; done?: boolean };

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8002";
// 내부 서비스 토큰 — BFF(이 서버) 만 backend /v1/generate 를 호출하도록 식별. 비어 있으면 미첨부.
const INTERNAL_SERVICE_TOKEN = process.env.INTERNAL_SERVICE_TOKEN ?? "";

/**
 * backend `/v1/generate` 로 프록시. 응답 본문은 text/plain delta 스트림.
 */
export async function* streamLlama(req: GenerationRequest): AsyncGenerator<GenerationChunk> {
  const resp = await fetch(`${BACKEND_URL}/v1/generate`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(INTERNAL_SERVICE_TOKEN ? { "X-Internal-Token": INTERNAL_SERVICE_TOKEN } : {}),
    },
    body: JSON.stringify({
      systemPrompt: req.systemPrompt,
      messages: req.messages,
      classification: req.classification,
      // 옵셔널 sampling override — 명시된 경우만 backend 가 settings 값 위에 덮어씀.
      ...(req.temperature !== undefined ? { temperature: req.temperature } : {}),
      ...(req.topP !== undefined ? { top_p: req.topP } : {}),
      ...(req.seed !== undefined ? { seed: req.seed } : {}),
      ...(req.userId !== undefined ? { user_id: req.userId } : {}),
    }),
  });

  if (!resp.ok || !resp.body) {
    const text = await resp.text().catch(() => "");
    throw new Error(`backend ${resp.status}: ${text || resp.statusText}`);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      const delta = decoder.decode(value, { stream: true });
      if (delta) yield { delta };
    }
    const tail = decoder.decode();
    if (tail) yield { delta: tail };
  } finally {
    reader.releaseLock();
  }
  yield { delta: "", done: true };
}
