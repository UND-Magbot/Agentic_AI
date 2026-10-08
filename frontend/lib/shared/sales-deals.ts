// 영업 건 관리 — 타입 + 서버 컴포넌트 전용 조회.
// 한 줄이 한 건(제품 추천 확정 → 견적 → 수주 → 거래명세서 발급·입금 확인 → 출하). 단계는 기록에서 backend 가 계산한다.
// 세금계산서는 이 프로그램 밖에서 처리한다(사용자 2026-10-07) — invoices 는 이관한 옛 기록(보기 전용).
import { cookies } from "next/headers";

const TOKEN_COOKIE = "und_cortex_token";
const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8002";

export type DealStage = "제품 추천 확정" | "견적" | "수주" | "출하 후 입금 대기" | "완료 (출하·완납)" | "드랍" | "출하 기록만 (이전)";

export interface DealAlert {
  code: string;
  level: "review" | "alert";
  text: string;
}

export interface DealItem {
  name: string;
  unit_price: number | null;
  qty: number | null;
  qty_text: string | null;
  amount: number | null;
}

export interface DealSource {
  file: "management" | "shipments";
  row_start: number;
  row_end: number;
}

export interface DealShipment {
  date: string | null;
  date_guessed: boolean;
  date_text: string | null;
  customer: string | null;
  items: string;
  qty_text: string | null;
  unit_price: number | null;
  amount: number | null;
  carrier: string | null;
  tracking: string[];
  receiver: string | null;
  address: string | null;
  owner: string | null;
  purpose: string | null;
  photos: string[];
  linked_by_amount: boolean;
  source: DealSource | null;
  forced?: boolean;
  /** 제품별 출하 줄(사진 포함, 2026-10-07 이후 출하) — 사진은 /api/sales-deals/{id}/ship-photos/{photo} */
  lines?: { name: string; qty: number | null; qty_text?: string | null; photos: string[] }[];
}

export interface DealInvoice {
  date: string | null;
  supply: number | null;
  vat: number | null;
  total: number | null;
  issuer_note: string | null;
  partial?: boolean;
}

export interface DealPayment {
  date: string | null;
  amount: number | null;
  note: string | null;
  /** 결제 조건 회차 번호 — [입금 확인] 버튼으로 확정한 입금 */
  term?: number;
}

export interface DealQuoteDoc {
  id: number;
  status: "draft" | "issued";
  quote_no: string | null;
  revision: number;
  writer: string | null;
  updated_at: string | null;
  issues: { revision: number; issued_at: string; total: number; currency: "KRW" | "USD"; file_name: string }[];
}

/** 결제 조건을 정했다는 표시 — 'terms'(예전 after·split·prepay 도 같은 뜻) */
export type PayCase = string;

/** 결제 조건 한 회차(사용자 2026-10-08) — 선금·중도금·잔금 % 수기, 시점(자유 글, 비어도 됨), 예정 입금일. 금액은 공급가액 × %.
 *  출하·입금 확인은 서로 순서 없음(출하는 입금 확인 없이도). */
export interface PayTerm {
  index: number;
  label: string;
  pct: number;
  /** 시점 — 자유 글(예: 발주 시), 기록용 */
  when: string;
  expected_date: string | null;
  amount: number;
  confirmed: boolean;
  paid_date: string | null;
  /** 예정 입금일 지난 날수(overdue 안에서만) */
  days?: number;
}

export interface DealStatementItem {
  name: string; spec: string; qty: number | null; unit_price: number | null; note: string;
  /** 'SET' — 세트로 묶어서 발급(수량 칸에 '1 SET') */
  unit?: string;
}
export interface DealStatement {
  no: string;
  date: string;
  buyer: { reg_no: string; name: string; ceo: string; address: string };
  items: DealStatementItem[];
  /** 세트로 묶었을 때 원래 품목(명세서에는 세트 한 줄, 품목별로 되돌릴 때 씀) */
  parts?: DealStatementItem[];
  totals?: { supply: number; vat: number; total: number };
  issued_at?: string;
}

export interface DealFlow {
  /** key: statement · case · pay:{회차} · ship */
  steps: { key: string; label: string; done: boolean; ready: boolean }[];
  pay_case: PayCase | null;
  pay_case_label: string | null;
  terms: PayTerm[];
  /** 예정 입금일이 지났는데 확인 안 된 회차 — 팝업·리스트 강조 */
  overdue: PayTerm[];
  /** 공급가액(부가세는 거래명세서에서만) */
  total: number | null;
  paid: number;
  due: number | null;
}

export interface DealMeeting {
  customer?: string | null; contact?: string | null; meeting_date?: string | null; writer?: string | null;
  category?: string | null; robot?: string | null; requirements?: string | null; notes?: string | null; updated_at?: string | null;
}

export interface DealEvent {
  action: string;
  detail: Record<string, unknown>;
  created_at: string;
  user_name: string | null;
  /** 히스토리 번호(AL20261008-02) — 번호가 붙은 이력만 */
  ver_no?: string | null;
}

/** 프로젝트(건) 안의 히스토리 한 줄 — 바뀔 때마다 번호 하나(사용자 2026-10-08). 최신 먼저. */
export interface DealHistory extends DealEvent {
  id: number;
  ver_no: string;
  /** 그때 단계·견적서 판·공급가액 */
  stage: DealStage | null;
  quote_rev: number | null;
  amount: number | null;
  /** 견적서가 새 판으로 바뀐 뒤의 옛 판 줄 — '버전 업데이트됨' */
  superseded: boolean;
  /** 되돌리기로 지울 수 있는 줄(견적서 발행·재발행 줄은 안 됨) */
  removable: boolean;
  /** 이 줄 때 내용으로 돌아갈 수 있음(그때 내용이 저장되어 있음) */
  restorable: boolean;
}

/** 발주서 ↔ 견적 품목 AI 대조(사용자 2026-10-08) — 품목은 사내 AI 가 읽고 수량·단가 비교는 서버 코드가. */
export type PoCompareLine = {
  quote: { name: string; qty: number | null; unit_price: number | null } | null;
  po: { name: string; spec: string; qty: number | null; unit_price: number | null; amount: number | null } | null;
  verdict: "일치" | "다름" | "확인 필요" | "발주서에 없음" | "견적에 없음";
  notes: string[];
};
export type PoCompare = {
  status: "ok" | "unreadable" | "failed" | "no_quote";
  checked_at: string;
  summary: string;
  source?: "text" | "image";
  doc_no?: string | null;
  lines?: PoCompareLine[];
  totals?: { quote: number; po: number | null; same: boolean | null };
};

export interface DealRow {
  id: number;
  /** 건 번호 — 견적서 첫 발행 때 부여(사용자 2026-10-08). [최종 제안 확정]만 한 건은 null('번호 미부여'). */
  deal_no: string | null;
  /** 화면 표시 번호 — 새 건은 히스토리 마지막 번호(AL20261008-01 → -02 …, 날짜가 바뀌면 AL20261010-05), 예전 번호(S26-…)는 그대로.
   *  문서(견적서·명세서)에는 deal_no. */
  display_no: string | null;
  /** 미팅 정보 직접 입력(AI 제품 추천 기록이 없는 건) — updated_at 이 있으면 입력됨 */
  meeting?: DealMeeting | null;
  /** 히스토리 번호 차례(-NN) — 견적서 판은 quote.revision */
  version: number;
  /** 히스토리(번호가 붙은 변경, 최신 먼저) — 리스트에서 건 아래로 펼쳐 본다 */
  history?: DealHistory[];
  /** 추천 기록 번호(한 건 상세에서만) — 견적서 발행 전 건에서 [견적서 작성 →] */
  recommendation_id?: number | null;
  /** 드랍(수주까지 못 간 건·진행을 접음 — 실주 없이 드랍 하나로) */
  dropped: boolean;
  drop_reason: string | null;
  kind: "recommend" | "quote" | "sales_only" | "shipment_only";
  legacy_no: string | null;
  /** 회사 제품 추천의 [최종 제안 확정] 기록 — 있으면 [미팅 정보]로 미팅 내용·최종 추천·근거·대화를 본다. */
  proposal_id: number | null;
  customer: string | null;
  contact: string | null;
  owner: string | null;
  title: string;
  base_date: string | null;
  quote: {
    revision: number;
    date: string | null;
    sent_date: string | null;
    amount: number | null;
    vat_included: boolean;
    currency: "KRW" | "USD";
    items: DealItem[];
  } | null;
  pay_terms: { label: string; pct: number; when?: string; expected_date?: string | null }[];
  won: boolean | null;
  won_date: string | null;
  /** 발주서(고객사 주문서) 파일 — 내려받기는 /api/sales-deals/{id}/po/{순번} */
  statement?: DealStatement | null;
  /** 이 건으로 만든 견적서(한 건 상세에서만) — 발행한 판 목록. 발행본 관리는 영업 건에서(견적서 탭은 작성 중인 것만). */
  quotes?: DealQuoteDoc[];
  pay_case?: PayCase | null;
  flow?: DealFlow;
  po_files?: { filename: string; mime: string; size: number; uploaded_at: string; uploaded_by: string | null; compare?: PoCompare | null }[];
  invoices: DealInvoice[];
  payments: DealPayment[];
  invoice_status: { issued: number; planned: number | null; partial: boolean };
  paid_full: boolean;
  shipments: DealShipment[];
  shipped: boolean;
  stage: DealStage;
  alerts: DealAlert[];
  review_alerts: DealAlert[];
  review_done: boolean;
  imported: boolean;
  legacy: { legacy_no?: string | null; source?: DealSource; file?: string };
  note: string;
  created_at: string;
  updated_at: string;
  events?: DealEvent[];
}

/** 올해 분기별 수주금액(수주일 기준)·올해 매출액(출하일 기준) — 공급가액, 드랍 제외 */
export interface DealKpi {
  year: number;
  quarter: number;
  won_by_quarter: number[];
  won_total: number;
  sales: number;
  /** 수주일이 없어(엑셀에서 옮긴 건) 첫 계산서·출하·견적일로 대신 센 건수 */
  won_estimated: number;
}

export interface DealListData {
  today: string;
  summary: Record<DealStage | "확인 필요" | "알림", number>;
  counts: { total: number; imported: number };
  rows: DealRow[];
  kpi: DealKpi;
  /** 관리자만 삭제·되돌리기·취소 버튼을 본다(실제 막는 것은 backend) */
  me: { admin: boolean; name: string };
}

/** 서버 컴포넌트 전용 — 영업 건 리스트(최신 건이 위). 권한이 없거나 실패하면 { error }. */
export async function getDeals(): Promise<DealListData | { error: string }> {
  const token = (await cookies()).get(TOKEN_COOKIE)?.value;
  if (!token) return { error: "로그인이 필요합니다." };
  try {
    const r = await fetch(`${BACKEND_URL}/v1/sales-deals`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) return { error: typeof data?.detail === "string" ? data.detail : "제품 영업 건을 불러오지 못했습니다." };
    return data as DealListData;
  } catch {
    return { error: "서버에 연결하지 못했습니다." };
  }
}
