// 도메인 횡단으로 쓰이는 공용 타입.
// 도메인별 타입은 lib/domains/<domain>/schemas.ts 에 둔다.

export type DomainKey = "finance" | "sales" | "dev" | "design" | "normal";

export type DomainAccent = {
  /** 진한 배경 (액티브 표시용) */
  bg: string;
  /** 진한 텍스트 */
  text: string;
  /** 링/보더 (hover 등 prefix 없이 단독 사용) */
  ring: string;
  /** focus-within 상태용 prefix-포함 풀클래스. Tailwind JIT 가 잡을 수 있도록 리터럴로 미리 지정. */
  focusRing: string;
  /** 옅은 배경 (배지·칩) */
  soft: string;
  /** 옅은 배경 위 텍스트 */
  softText: string;
  /** 도트 (사이드바 아이템 옆 점) */
  dot: string;
};

export type IconName =
  | "ledger"
  | "trend"
  | "code"
  | "ruler"
  | "spark"
  | "send"
  | "plus"
  | "user"
  | "logo"
  | "more"
  | "sidebar-toggle"
  | "search"
  | "chat"
  | "paperclip"
  | "mic"
  | "chevron-down"
  | "bell"
  | "help"
  | "copy"
  | "refresh"
  | "thumb-up"
  | "thumb-down"
  | "stop"
  | "sun"
  | "moon"
  | "eye"
  | "eye-off"
  | "logout"
  | "folder"
  | "star"
  | "back"
  | "trash"
  | "edit"
  | "download"
  | "file-spreadsheet"
  | "check"
  | "x"
  | "bug";

export type DomainMeta = {
  key: DomainKey;
  /** 풀네임. 예: "경영·회계" */
  label: string;
  /** 짧은 라벨. 사이드바 등 좁은 곳용 */
  shortLabel: string;
  /** 한 줄 소개. 랜딩/빈 상태에 노출 */
  tagline: string;
  /** 두 문장 정도의 설명 */
  description: string;
  accent: DomainAccent;
  icon: IconName;
  /** 빈 상태에서 보여줄 추천 프롬프트 (3~4개) */
  examples: string[];
  /** 입력창 placeholder 도메인별 차별화. 비어있으면 기본 fallback 사용. */
  placeholder?: string;
};

export type ChatRole = "user" | "assistant" | "system";

export type ChatAttachment = {
  id: number;
  filename: string;
  mime: string;
  size_bytes: number;
};

export type RagSource = {
  id: number;
  source_path: string;
  source_label: string;
  score: number;
  snippet: string;
  metadata: Record<string, unknown>;
};

export type ChatMessage = {
  id: string;
  role: ChatRole;
  content: string;
  /** ISO timestamp */
  createdAt: string;
  /**
   * 메시지가 속한 도메인.
   *  - user: 어느 모드에서 보냈는지 ([] = auto)
   *  - assistant: 실제 응답을 만든 도메인. 길이 0 = 분류 중, 1 = 단일, 2+ = orchestrator
   */
  domains: DomainKey[];
  /** user 메시지의 첨부 파일(1차는 화면 표시용 — DB persistence 는 후속). */
  attachments?: ChatAttachment[];
  /** assistant 메시지에 부착된 RAG 출처. 사내 자료 검색 결과가 답변 근거로 사용된 경우. */
  sources?: RagSource[];
};

export type Conversation = {
  id: string;
  domain: DomainKey;
  title: string;
  /** ISO timestamp */
  updatedAt: string;
  /** 검색 결과에서만 채워짐. 매치된 메시지 본문 ±50자 발췌. */
  preview?: string;
  /** 즐겨찾기(★) 여부. 사이드바 RECENT 에서 항상 상단으로. */
  starred?: boolean;
};
