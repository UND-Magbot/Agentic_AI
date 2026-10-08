// 자유 문장으로 들어온 원클릭 작업 요청을 표준 fast-path 형태로 바꾼다.
//
// 개념도는 원래 버튼(모달) → 요청 원문 .txt 첨부 + 표준 프롬프트로만 동작한다. 채팅에 요청을
// 그대로 붙여넣고 "개념도 그려줄래?" 라고 묻거나 외부 클라이언트(Codex 등)가 /api/chat 으로 보내면
// 일반 RAG 답변("사내 자료에 없음")으로 빠졌다(2026-09-28 실측). 여기서 원문을 사용자 명의 첨부로
// 올리고 표준 프롬프트로 바꿔, backend(main._detect_concept_map_intent) 가 같은 경로로 처리하게 한다.

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8002";

// backend main._detect_concept_map_intent 와 chat-window 의 CONCEPT_MAP_PROMPT 와 같은 문구.
export const CONCEPT_MAP_PROMPT = "공정 개념도 작성 기능 수행";
const CONCEPT_CONTEXT_RE = /(개념도|컨셉도|concept\s*map)/i;
const CONCEPT_ACTION_RE = /(그려|그리|작성|만들|생성)/;
// "개념도는 어떻게 만들어?" 같은 기능 문의는 작업이 아니다.
const INFO_QUESTION_RE = /(뭐야|무엇|어떻게|설명해|알려\s*줘|방법)/;
// "개념도 그려줘" 한 줄은 요청 내용이 없다 — 공정 설명이 붙은 경우만 작업으로 본다.
const MIN_REQUEST_CHARS = 40;
// backend request_files.MAX_REQUEST_CHARS 와 같은 값.
const MAX_REQUEST_CHARS = 4000;
const REQUEST_FILENAME = "개념도_요청.txt";

export type UploadedAttachment = { id: number; filename: string; mime: string };

/** 첨부 없이 온 자유 문장이 공정 개념도 작성 요청인지. */
export function isFreeConceptMapRequest(text: string, attachmentCount: number): boolean {
  const t = text.trim();
  return (
    // '@…' '/…' 는 Codex 위임 명령이다 — 사내 개념도(gemma)로 가로채지 않는다(backend codex_delegate).
    !/^[@/]/.test(t) &&
    attachmentCount === 0 &&
    t !== CONCEPT_MAP_PROMPT &&
    t.length >= MIN_REQUEST_CHARS &&
    t.length <= MAX_REQUEST_CHARS &&
    CONCEPT_CONTEXT_RE.test(t) &&
    CONCEPT_ACTION_RE.test(t) &&
    // 기능 문의 판정은 '개념도' 가 든 문장만 본다 — 요청 본문의 "어떻게 달것인지 검토" 는 작업 내용이다.
    !triggerSentences(t).some((s) => INFO_QUESTION_RE.test(s))
  );
}

function triggerSentences(text: string): string[] {
  return text.split(/[.?!\n]/).filter((s) => CONCEPT_CONTEXT_RE.test(s));
}

/** 요청 원문을 사용자 토큰으로 .txt 첨부로 올린다. 실패하면 null (일반 답변으로 진행). */
export async function uploadRequestText(
  text: string,
  token: string,
): Promise<UploadedAttachment | null> {
  const fd = new FormData();
  fd.append("file", new File([text.trim()], REQUEST_FILENAME, { type: "text/plain" }));
  try {
    const r = await fetch(`${BACKEND_URL}/v1/attachments`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      body: fd,
      cache: "no-store",
    });
    if (!r.ok) return null;
    const d = (await r.json()) as { id: number; filename: string; mime: string };
    return { id: d.id, filename: d.filename, mime: d.mime };
  } catch {
    return null;
  }
}
