// 제품 구매 건 관리(사용자 2026-11-02) — backend/app/purchases 의 화면 행 타입 + 저장 요청.
// 국내: 견적 → 재가 → 발주 → (송금 · 배송 · 입고) + 거래명세서·세금계산서(선택),
// 해외: 견적 → 재가 → 발주 → (제조사 송금 · 배송 · 통관 서류 · 관세사 송금 · 입고) — 사용자 2026-11-11.
// 발주 뒤는 정석 순서만 안내하고 기록 순서는 자유. 발주 단계에서 입고 예정일을 꼭 적는다(사용자 2026-11-02).

export type PurchaseStepKey = "quote" | "approval" | "order" | "pay" | "ship" | "customs" | "broker" | "receipt" | "statement" | "tax";
export type PurchaseRegion = "domestic" | "overseas";
export type PurchaseCurrency = "KRW" | "USD";
export const REGION_LABEL: Record<PurchaseRegion, string> = { domestic: "국내", overseas: "해외" };

export interface PurchaseFile {
  filename: string;
  mime: string;
  size: number;
  uploaded_at: string;
  uploaded_by: string;
}

export interface PurchaseStep {
  key: PurchaseStepKey;
  label: string;
  done: boolean;
  ready: boolean;
  note: string | null;
  /** 완료 조건에 안 넣는 단계(거래명세서·세금계산서) */
  optional: boolean;
}

export interface ExpenseCheck {
  status: "ok" | "mismatch" | "unreadable" | "failed";
  summary: string;
  reason?: "amount" | "supplier" | "none";
  found?: string[];
  checked_at: string;
  filename?: string;
  uploaded_by?: string;
}

export interface PurchaseTerm {
  index: number;
  label: string;
  pct: number;
  when: string;
  expected_date: string | null;
  amount: number;
  amount_vat: number;
  confirmed: boolean;
  paid_date: string | null;
  mismatch: boolean;
  mismatch_note?: string | null;
  check: ExpenseCheck | null;
}

export interface PoItem {
  /** Commodity 칸(모델명) — Description 과 나눔(사용자 2026-11-11, 에이딘로보틱스 발주서 예시) */
  commodity?: string;
  description: string;
  moq: number | null;
  qty: number | null;
  price: number | null;
  /** 해외 양식의 Remark 칸 */
  remark?: string;
}

export interface PoUploadFile {
  filename: string;
  mime: string;
}

/** 고친 발주서 파일을 올린 뒤 읽은 값(/po/upload) — 확인 창에 채운다 */
export interface PoUploadRead {
  upload: Record<string, unknown> & { filename: string };
  status: string;
  summary: string;
  doc_no: string | null;
  date: string | null;
  items: PoItem[];
  /** 파일에서 읽은 머리(공급사·계좌·Comments — 예전 양식 포함). 발행하면 발주서 머리에 들어가 [발주서 고치기]에서 그대로 고친다 */
  header?: { supplier?: Partial<PoHeader["supplier"]>; bank?: string; address?: string; swift?: string; account_no?: string;
             account_holder?: string; comments?: string };
}

export interface PoHeader {
  no?: string;
  date: string;
  lang?: "ko" | "en";
  supplier: { name: string; contact: string; email: string; tel?: string };
  bank: string;
  address: string;
  /** 해외 양식 Swift Code */
  swift?: string;
  account_no: string;
  account_holder: string;
  issuer: { name: string; title: string; address: string; tel: string; fax: string; attn: string };
  comments: string;
}

export interface PoDraft {
  header: PoHeader;
  items: PoItem[];
  initials: string;
  source: "draft" | "issued" | "new";
  region: PurchaseRegion;
  lang: "ko" | "en";
  currency: PurchaseCurrency;
  /** 입고 예정일 — 발행할 때 꼭(사용자 2026-11-02) */
  expected_date: string;
  /** 발주서 AI 대화(사용자 2026-11-11) */
  chat?: { role: "user" | "assistant"; text: string }[];
  /** 내 정보(계정 기본 정보)로 만든 우리 담당·이니셜 — 비어 있으면 화면이 [내 정보] 넣는 곳을 안내(사용자 2026-11-11) */
  mine?: { issuer: PoHeader["issuer"]; initials: string };
}

export interface ReceiptLine {
  line_no: number;
  name: string;
  qty: number;
  photos: string[];
  /** S/N(사용자 2026-11-11 — 입고 때 또는 나중에 따로 등록) */
  serials?: string[];
}

export interface ReceiptRecord {
  date: string;
  carrier: string;
  tracking: string[];
  receiver: string;
  location: string;
  note: string;
  lines: ReceiptLine[];
  final: boolean;
  by: string;
  at: string;
  edited_by?: string;
}

export interface PurchaseDocStep {
  files: PurchaseFile[];
  mode?: string;
  date?: string;
  done_at?: string;
  done_by?: string;
  no_file?: boolean;
  /** [견적서 없이 넘어가기](재가 칸에 둔다, 사용자 2026-11-11) */
  quote_skip?: { by: string; at: string };
}

export interface PurchaseEvent {
  id: number;
  action: string;
  label: string;
  detail: Record<string, unknown>;
  created_at: string;
  user_name: string | null;
}

export interface PurchaseRow {
  id: number;
  supplier: string;
  title: string;
  owner: string;
  region: PurchaseRegion;
  currency: PurchaseCurrency;
  /** 입고 예정일(YYYY-MM-DD) — 리스트·상세 표시, 지나면 알림 */
  expected_date: string | null;
  expected_history: { date: string; before: string | null; at: string; by: string; reason: string }[];
  note: string;
  dropped: boolean;
  drop_reason: string;
  created_at: string;
  updated_at: string;
  quote_files: PurchaseFile[];
  approval: PurchaseDocStep;
  po: {
    no: string | null; date: string | null; total: number | null; issued_at: string | null; issued_by: string | null;
    revision: number | null; file_name: string | null; items: PoItem[]; has_draft: boolean; currency: PurchaseCurrency | null;
    /** 고친 발주서 파일로 발행한 판이면 그 파일(내려받기는 그 파일 그대로) */
    upload: PoUploadFile | null;
    /** AI 판독 실패로 [버그 신고 후 넘어가기] 해 파일만 발행(사용자 2026-11-11) — 품목은 [발주서 고치기]에서 채움 */
    forced?: { bug_report: string; at: string; by: string } | null;
    /** 예전 판(다시 발행하기 전) — 판마다 보기·다시 다운로드(사용자 2026-11-11, 견적서와 같게) */
    history: { no: string; date: string; total: number; issued_at: string; issued_by: string; revision: number; file_name: string | null;
               upload: PoUploadFile | null }[];
  };
  pay_terms: { label: string; pct: number; when: string; expected_date: string | null }[];
  terms: PurchaseTerm[];
  receipt: {
    done: boolean;
    records: ReceiptRecord[];
    /** 발주 품목 줄별 발주·입고 수량 */
    lines: { line_no: number; name: string; ordered: number | null; received: number }[];
  };
  /** 해외 통관 서류 — 원산지 증명서(files)·증빙 자료 없음·기타 서류(CI 등)·관세사 송금·제조사 송금 확인증 */
  customs: PurchaseDocStep & {
    note?: string;
    no_origin?: boolean;
    etc_files: (PurchaseFile & { label: string })[];
    remit_files: PurchaseFile[];
    broker?: DateNote;
  };
  /** [배송 시작] 기록 — 있고 입고 기록이 없으면 배송 중 */
  shipping: DateNote | null;
  in_transit: boolean;
  /** 송금 버튼 한 번 기록(해외 제조사 송금, 예전 국내 한 번에 확인) */
  payment: { date: string; by: string; note: string } | null;
  statement: PurchaseDocStep;
  tax_invoice: PurchaseDocStep;
  steps: PurchaseStep[];
  stage: string;
  /** 상태 이름에 안 드러난 남은 일(예: '송금 남음') */
  stage_note: string;
  /** 거래명세서·세금계산서 — 첨부·선결제·선발행·미등록 */
  docs: { statement: DocState; tax: DocState };
  alerts: { code: string; level: "alert" | "review" | "info"; text: string }[];
  total: number | null;
  prepaid: { statement: boolean; tax: boolean };
  history?: PurchaseEvent[];
  me?: { admin: boolean; name: string };
}

export interface PurchaseListData {
  rows: PurchaseRow[];
  counts: Record<string, number>;
  stages: string[];
  me: { admin: boolean; name: string };
}

export interface DateNote {
  date: string;
  note?: string;
  by?: string;
  at?: string;
}

export interface DocState {
  state: "file" | "skip" | "none";
  label: string;
  mode?: string;
  note?: string;
  files?: number;
}

/** 거래명세서·세금계산서를 못 끊는 경우(사용자 2026-11-11) — 첨부 없이 사유만 기록 */
export const DOC_SKIP_MODES = { prepaid: "선결제", pre_issued: "선발행" } as const;
export const DOC_SKIP_HELP: Record<keyof typeof DOC_SKIP_MODES, string> = {
  prepaid: "전달에 미리 지불해 이번 건으로 따로 끊지 않는 경우",
  pre_issued: "물건이 오기 전에 이미 발행돼 이번 건으로 따로 받지 않는 경우",
};
/** 통관 기타 서류 이름 예시 */
export const CUSTOMS_DOC_EXAMPLES = ["CI", "Packing List", "B/L", "수입신고필증"];

async function parse(r: Response, fallback: string) {
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof d?.detail === "string" ? d.detail : fallback);
  return d;
}

/** JSON 요청(본문이 없으면 GET) — 실패하면 서버 문장으로 Error. */
export async function purchaseRequest<T = PurchaseRow>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(`/api/purchases${path}`, body === undefined ? { cache: "no-store" } : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  return parse(r, "요청을 처리하지 못했습니다.") as Promise<T>;
}

/** 파일 한 개 올리기(multipart). label = 통관 기타 서류 이름 */
export async function purchaseUpload(path: string, file: File, label?: string): Promise<PurchaseRow> {
  const fd = new FormData();
  fd.append("file", file);
  if (label) fd.append("label", label);
  const r = await fetch(`/api/purchases${path}`, { method: "POST", body: fd });
  return parse(r, "파일을 올리지 못했습니다.") as Promise<PurchaseRow>;
}
