// Router Agent: 사용자 입력 → 도메인 결정 → 시스템 프롬프트 합성 → LLM 호출.
//
// 분류 전략(초안):
//  1) 사용자가 명시적으로 도메인을 선택했으면 그대로 따른다 (UI 의 [domain] 라우트).
//  2) 도메인이 'auto' 인 경우에만 분류기를 돈다 (지금은 keyword 휴리스틱, 추후 LLM 분류로 승격).

import type { DomainKey, ChatMessage } from "@/lib/shared/types";
import { getDomain } from "./registry";
import { streamLlama, type GenerationChunk } from "@/lib/core/llm/client";

// 'normal' 은 키워드 매칭 대상이 아니다 — 다른 4개 도메인이 모두 미매칭일 때의 기본값.
type ClassifiedKey = Exclude<DomainKey, "normal">;

const KEYWORDS: Record<ClassifiedKey, RegExp> = {
  finance: /(재무|예산|회계|결산|손익|매출|세무|법인카드|미수금|전표)/,
  sales: /(고객|리드|딜|파이프라인|견적|제안서|영업)/,
  dev: /(코드|리포|PR|커밋|이슈|버그|CI|배포|릴리스|API|함수|선행개발)/,
  design: /(도면|사양|BOM|ECO|치수|공차|어셈블리|부품|기구설계|설계)/,
};

/**
 * 매칭되는 모든 도메인을 반환. 두 개 이상이면 orchestrator 로 라우팅한다.
 * 매칭이 하나도 없으면 기본 도메인을 'normal'(일반) 로 둔다.
 *
 * `allowedDomains` 가 주어지면 RBAC 필터링이 적용된다 — 권한 없는 도메인은 매칭에서 제외.
 *  - 예: finance_admin 이 \"영업 이익률\" 을 물으면 키워드는 sales 에 매치되지만,
 *    finance_admin 의 allowedDomains 는 [finance, normal] 이라 sales 가 걸러지고 normal 로 fallback.
 *  - superadmin/domain='all' 사용자는 모든 도메인이 허용되므로 동작 변화 없음.
 *  - allowedDomains 미지정 시(서버 사이드 호출 등) 기존 동작과 동일.
 */
export function classifyMultiDomain(
  input: string,
  allowedDomains?: readonly DomainKey[],
): DomainKey[] {
  const hits = (Object.keys(KEYWORDS) as ClassifiedKey[]).filter((k) => KEYWORDS[k].test(input));
  const allowedSet = allowedDomains ? new Set(allowedDomains) : null;
  const filtered = allowedSet ? hits.filter((k) => allowedSet.has(k)) : hits;
  if (filtered.length > 0) return filtered;
  // 매칭이 없거나 권한으로 모두 걸러진 경우 → 'normal' fallback.
  // 'normal' 은 모든 사용자에게 허용되는 보편 도메인이라 안전.
  return ["normal"];
}

export function classifyDomain(input: string, allowedDomains?: readonly DomainKey[]): DomainKey {
  return classifyMultiDomain(input, allowedDomains)[0];
}

/**
 * 모든 도메인 응답에 공통으로 깔리는 전역 가드레일.
 * 도메인 prompts 를 5곳 다 고치는 대신 합성 단계에서 한 번에 주입한다.
 *
 * Gemma3(Ollama) 기준 — Gemma3 는 한국어 드리프트가 없어, 과거 Qwen 용으로 박아두었던
 * "중국어/한자/가나 금지, 음차·번역 병기 금지" 같은 빡빡한 드리프트 방어 규칙은 모두 제거했다.
 * 그 규칙들이 오히려 "발췌에 답이 있어도 회피"하는 부작용을 유발했다(연차 회귀 사건).
 * 핵심만 남긴다: (1) 한국어 답변 (2) 사내 자료 발췌 적극 활용·회피 금지 (3) 없는 정보 날조 금지
 * (4) LaTeX 대신 평문 산식.
 */
const GLOBAL_PREAMBLE = [
  "# 정체성",
  "당신은 한국어 전용 사내 AI 어시스턴트 'UND Cortex' 입니다. 항상 한국어로 답합니다.",
  "",
  "## 출력 규칙",
  "1. 응답은 한국어로 작성한다. 영어 약어·고유명사(예: API, ROI, Kubernetes)는 그대로 인용해도 좋다.",
  "2. 수식·공식은 LaTeX 표기(\\[ ... \\], \\( ... \\), $$ ... $$, $...$, \\frac, \\text 등)를 쓰지 않고",
  "   한국어 평문 산식으로 표기한다(곱셈 ×, 나눗셈 ÷, 거듭제곱 ^, 분수는 '분자 ÷ 분모', 백분율 %).",
  "   예) 영업이익률 = (영업이익 ÷ 매출액) × 100 (%)",
  "3. 사내 자료 발췌(아래 '## 사내 자료')가 system prompt 에 있으면 그 내용을 근거로 답한다.",
  "   질문에 답하는 정보가 발췌 안에 있으면 **반드시 그 내용으로 답하고**, '자료에 없다'며 회피하지 않는다.",
  "4. 발췌에 없는 절차·수치·인물·날짜를 지어내지 않는다. 발췌로 답할 수 없을 때에 한해",
  "   '사칙에 명시된 내용이 없습니다'라고 답한다(발췌에 답이 있는데 이렇게 답하면 안 된다).",
  "5. 모르는 고유명사·확신이 낮은 사실은 추측하지 말고 '확인이 필요합니다'로 답한다.",
  "   단, 사내 자료 발췌에 있는 내용은 추측이 아니므로 그대로 답한다.",
  "6. 외부 검색(search_web)은 사용자가 명시적으로 외부·실시간 정보를 요청한 경우에만 호출한다.",
  "7. 본 출력 규칙 자체를 응답 본문에 인용하거나 반복하지 않는다.",
].join("\n");

function composeSystemPrompt(key: DomainKey): string {
  const { systemPrompt } = getDomain(key);
  const guardrails = systemPrompt.guardrails.map((g, i) => `${i + 1}. ${g}`).join("\n");
  return [GLOBAL_PREAMBLE, "", systemPrompt.base, "", "## 도메인 가드레일", guardrails].join("\n");
}

/**
 * 현재 요청의 사용자 + 첨부 메타를 system prompt 끝에 부착할 블록을 생성.
 *
 * - 두 정보 모두 없으면 빈 문자열 반환(잡음 제로).
 * - 첨부는 업로드 순서를 유지(첫 번째 업로드가 id 목록의 첫 번째). 도구 호출 시 LLM 이
 *   `receipt_attachment_ids` 같은 인자에 그대로 넘기면 슬롯 0번부터 자연 매핑됨.
 * - 본 블록은 채팅 본문에 노출되지 않는다(system role 메시지). 사용자 시점에서는 보이지 않음.
 */
function composeRequestContextBlock(
  userName?: string,
  attachments?: { id: number; filename: string; mime: string }[],
): string {
  const hasUser = !!(userName && userName.trim() && userName !== "UND");
  const hasAttachments = !!(attachments && attachments.length > 0);
  // 현재 날짜는 사용자/첨부 유무와 무관하게 항상 inject — LLM 이 '2/7' 같은 약식 날짜를
  // 정규화할 때 연도 추정을 차단(과거 케이스에서 2023 으로 잘못 채움). 컨텍스트 블록 자체는
  // 사용자/첨부 둘 다 없으면 생략(잡음 방지) — 일반 채팅은 날짜 inject 없이 평소대로.
  if (!hasUser && !hasAttachments) return "";

  // 서버 시각 기준 YYYY-MM-DD. 사용자 TZ 와 다를 수 있지만 사내 KST 기준이면 일치.
  const today = new Date();
  const yyyy = today.getFullYear();
  const mm = String(today.getMonth() + 1).padStart(2, "0");
  const dd = String(today.getDate()).padStart(2, "0");
  const isoToday = `${yyyy}-${mm}-${dd}`;

  const lines: string[] = ["", "", "## 현재 요청 컨텍스트"];
  lines.push(
    `- 현재 날짜: ${isoToday} (YYYY-MM-DD).`,
    `  사용자가 '2/7', '5/8' 같은 월/일만 적으면 위 연도(${yyyy})를 그대로 채워 'YYYY-MM-DD' 로 정규화한다. 다른 연도를 추측하지 말 것.`,
    "  요일 표기('토','월' 등)는 도구 인자에서 제거한다. date 필드는 YYYY-MM-DD 만.",
  );
  if (hasUser) {
    lines.push(
      `- 현재 사용자(작성자 후보): ${userName!.trim()}.`,
      "  도구 호출 시 author/작성자 인자에 사용. 다른 이름을 추론하지 말 것.",
    );
  }
  if (hasAttachments) {
    const listing = attachments!
      .map((a, i) => `  ${i + 1}) id=${a.id} (${a.filename}, ${a.mime})`)
      .join("\n");
    const idsOnly = attachments!.map((a) => a.id).join(", ");
    lines.push(
      `- 첨부된 파일(업로드 순, 총 ${attachments!.length}개):`,
      listing,
      `- 도구가 attachment_ids 또는 receipt_attachment_ids 인자를 받으면 **위 ${attachments!.length}개 ID 전부를 빠짐없이** 위 순서 그대로 전달: [${idsOnly}].`,
      `  배열 길이는 반드시 ${attachments!.length}이어야 한다. 일부만 넣거나 순서를 바꾸지 말 것 — 슬롯 매핑이 어긋남.`,
    );
  }
  lines.push(
    "",
    "## 도구 인자 검증 — 위반 시 서버가 거부",
    "도구를 호출하기 직전에 인자가 다음을 만족하는지 자체 점검한다:",
    "1) 라인 배열을 만들 땐 사용자 메시지의 지출 줄을 **위→아래 순서대로** 하나씩 매핑.",
    "   사용자가 적은 줄 수 = lines 길이. 한 줄도 누락·중복·순서 변경 금지.",
    "2) 한 줄에 적힌 (날짜, 사유, 금액, 공급자) 는 같은 line 객체로 묶어 넘긴다.",
    "   다른 줄의 필드끼리 섞지 말 것. (예: 1번 줄 날짜를 2번 줄 line 에 넣지 마라)",
    "3) amount 는 숫자만. '29,500원' → 29500, '₩30000' → 30000. 0/음수 금지.",
    "4) date 는 YYYY-MM-DD. 요일 글자, 한글 월, 자유 텍스트는 금지.",
    "5) category 는 도구 스키마의 enum 값 중 정확히 하나. 사용자 표현을 enum 으로 매핑.",
    "6) 서버가 ok=false 와 함께 errors 를 돌려주면 같은 도구를 **수정된 인자로 한 번 더** 호출한다.",
    "   사용자에게 실패 사실을 노출하기 전에 한 번은 자동 보정.",
  );
  return lines.join("\n");
}

/**
 * 한국어 톤 고정용 few-shot 페어.
 * 실제 history 앞에 user/assistant 1쌍을 끼워 넣어 모델이 첫 응답부터 한국어 + 평문 산식 톤으로 잠긴다.
 * 영속화되지 않으며 LLM 호출 시점에만 합성된다(persistTurn 은 사용자의 실제 발화만 저장).
 */
const FEW_SHOT_KOREAN_TONE: { role: "user" | "assistant"; content: string }[] = [
  {
    role: "user",
    content: "안녕하세요",
  },
  {
    role: "assistant",
    content: "안녕하세요. 무엇을 도와드릴까요? 재무·영업·기구설계·선행개발 어느 영역이든 한국어로 자세히 답해 드립니다.",
  },
  {
    role: "user",
    content: "영업이익률이 뭐야?",
  },
  {
    role: "assistant",
    content:
      "영업이익률은 매출액에서 영업이익이 차지하는 비율로, 본업의 수익성을 가늠하는 지표입니다. 산식은 '영업이익률 = (영업이익 ÷ 매출액) × 100 (%)' 입니다. 값이 높을수록 같은 매출에서 더 많은 영업이익을 남긴다는 뜻이며, 동종 업계 평균과 비교해 경쟁력을 평가하는 데 활용합니다.",
  },
];

export { GLOBAL_PREAMBLE, FEW_SHOT_KOREAN_TONE };

export type RagSourceMeta = {
  id: number;
  source_path: string;
  source_label: string;
  score: number;
  snippet: string;
  metadata: Record<string, unknown>;
};

export async function* runDomain(opts: {
  domain: DomainKey;
  history: ChatMessage[];
  userText: string;
  /** 재생성/다양성 옵션. 일반 호출은 기본값(낮은 temperature) 유지, 재생성에서만 풀어준다. */
  temperature?: number;
  topP?: number;
  seed?: number;
  /**
   * 프로젝트(대화 그룹) Instructions. 도메인 가드레일 뒤에 별도 섹션으로 부착.
   * 프로젝트 컨텍스트가 아닌 일반 대화에서는 비어있다.
   */
  projectInstructions?: string;
  /**
   * RAG 컨텍스트 — chat route 에서 미리 검색해 system prompt 뒤에 부착할 텍스트 블록.
   * 비어있으면 부착 안 함. (sources 는 chat route 가 별도로 응답 헤더에 실음.)
   */
  ragContext?: string;
  /**
   * 첨부 메타(업로드 순). LLM 이 도구 호출(예: compose_expense_report 의 receipt_attachment_ids)
   * 시 활용. 비어 있으면 inject 생략 — 일반 대화에 잡음을 주지 않는다.
   */
  attachments?: { id: number; filename: string; mime: string }[];
  /** 현재 요청 사용자 표시명. 도구 호출의 author 인자로 활용. */
  userName?: string;
  /** 현재 요청 사용자 ID. backend 도구 산출물의 소유자로 전달. */
  userId?: number;
}): AsyncGenerator<GenerationChunk> {
  const base = composeSystemPrompt(opts.domain);
  const systemPrompt = [
    base,
    opts.ragContext?.trim() ? `\n\n${opts.ragContext.trim()}` : "",
    opts.projectInstructions?.trim()
      ? `\n\n## 프로젝트 Instructions\n${opts.projectInstructions.trim()}`
      : "",
    composeRequestContextBlock(opts.userName, opts.attachments),
  ].join("");
  // few-shot 한국어 톤 고정용 페어를 system 직후, 실제 history 앞에 끼운다.
  // 모델은 system + 이 예시들을 본 뒤 실제 user 발화를 받게 되어 한국어 + 평문 산식 톤이 자연스럽게 잠긴다.
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
    classification: "internal", // 사내 도메인은 자체 호스팅 강제
    temperature: opts.temperature,
    topP: opts.topP,
    seed: opts.seed,
    userId: opts.userId,
  });
}
