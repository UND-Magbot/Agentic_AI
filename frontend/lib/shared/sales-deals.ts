// 영업 건 관리 — 타입 + 서버 컴포넌트 전용 조회.
// 한 줄이 한 건(제품 추천 확정 → 견적 → 수주 → 거래명세서 발급·입금 확인 → 출하). 단계는 기록에서 backend 가 계산한다.
// 세금계산서는 이 프로그램 밖에서 처리한다(사용자 2026-10-07) — invoices 는 이관한 옛 기록(보기 전용).
import { cookies } from "next/headers";

const TOKEN_COOKIE = "und_cortex_token";
const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8002";

export type DealStage = "제품 추천 확정" | "견적" | "수주" | "출하 후 입금 대기" | "완료 (출하·완납)" | "드랍" | "출하 기록만 (이전)"
  /** 대여 건만(사용자 2026-10-22) — 출하하면 대여 중, [회수 시작] → 회수 중, [회수 완료] → 회수 완료 */
  | "대여 중" | "회수 중" | "회수 완료";

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
  lines?: { name: string; qty: number | null; qty_text?: string | null; photos: string[]; line_no?: number }[];
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
  /** issue_no = 그 판의 발행번호(새 체계 QT-…, 예전 판은 그때 견적서에 찍힌 번호 AL20261008 등),
   *  uploaded = 밖에서 고친 파일로 발행했거나 교체한 판, replaced_at = 같은 번호로 파일을 교체한 때 */
  issues: { revision: number; quote_no?: string | null; issue_no?: string | null; uploaded?: boolean; issued_at: string; total: number;
            currency: "KRW" | "USD"; file_name: string; replaced_at?: string | null }[];
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
  /** 발행번호 TS-LU20261012-01 — 발급할 때 서버가 매긴다(발급 전 초안은 '') */
  no: string;
  /** 발행자 이니셜 — 기본 = 로그인 계정(내 정보), 폼에서 고칠 수 있음 */
  initials?: string;
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
}

/** 프로젝트(건) 안의 히스토리 한 줄 — 시간순 순번 1, 2, 3 …(사용자 2026-10-12). 최신 먼저. 되돌리기 없음. */
export interface DealHistory extends DealEvent {
  id: number;
  /** 시간순 순번(처음 일 = 1) */
  seq: number;
  /** 견적서·거래명세서 발행 줄의 발행번호(QT-LU20261012-01 · TS-…). 새 체계 전 견적 줄은 그때 견적서에 찍힌 번호(AL20261008 등) */
  doc_no: string | null;
  /** doc_no 가 새 체계(10-13) 전 견적서의 번호인지 */
  doc_legacy?: boolean;
  /** 그때 단계·견적서 판·공급가액 */
  stage: DealStage | null;
  quote_rev: number | null;
  amount: number | null;
  /** 같은 문서가 다시 발행된 뒤의 예전 발행 줄 — '버전 업데이트됨' */
  superseded: boolean;
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
  /** 화면 표시 번호 — 최근 견적서 발행번호(QT-LU20261012-01, 사용자 2026-10-12). 새 체계 전 건은 예전 번호 그대로. */
  display_no: string | null;
  /** 최근 거래명세서 발행번호(TS-…) — 발급했을 때만 */
  statement_no: string | null;
  /** 미팅 정보 직접 입력(AI 제품 추천 기록이 없는 건) — updated_at 이 있으면 입력됨 */
  meeting?: DealMeeting | null;
  /** (예전) 히스토리 번호 차례 — 더 쓰지 않음. 견적서 판은 quote.revision */
  version: number;
  /** 히스토리(시간순 순번, 최신 먼저) — 리스트에서 건 아래로 펼쳐 본다 */
  history?: DealHistory[];
  /** 추천 기록 번호(한 건 상세에서만) — 견적서 발행 전 건에서 [견적서 작성 →] */
  recommendation_id?: number | null;
  /** 드랍(수주까지 못 간 건·진행을 접음 — 실주 없이 드랍 하나로) */
  dropped: boolean;
  drop_reason: string | null;
  kind: "recommend" | "quote" | "sales_only" | "shipment_only";
  /** 판매 / 대여(대여 견적서로 낸 건, 사용자 2026-10-22) — rental = 계약 개월 수·시작일·끝나는 날·월 납입금 */
  biz: DealBiz;
  rental: DealRental | null;
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
    /** 최근 견적서 발행번호(QT-…) */
    no?: string;
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
  /** 수주 판의 제품 시리얼 등록 현황(한 건 상세에서만, 사용자 2026-10-19) — 수주 판이 없거나 대상 품목이 없으면 null */
  units?: UnitSummary | null;
  /** 이 건의 제품 시리얼 전부 — 출하 창 체크 목록 */
  unit_list?: ProductUnit[];
  /** A/S 진행 중(완료 아님)·전체 건수(사용자 2026-10-19) — 단계와 따로(출하·완납 뒤에도 A/S 는 생김) */
  as_open?: number;
  /** [A/S 들어감 확인]을 누른 '진행 중' 건수(사용자 2026-10-22) — 있으면 리스트의 그 줄을 연하게 */
  as_active?: number;
  as_total?: number;
  /** 입금 예정일 연장 요청(회차별 최근 것, 한 건 상세에서만) */
  due_requests?: ApprovalRequest[];
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
  summary: Record<DealStage | "확인 필요" | "알림" | "A/S 진행 중", number>;
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

/** 관리자 승인 요청(사용자 2026-10-13, 영업팀 요청 8번) — 지금은 입금 예정일 연장(pay_due_extend) */
export type ApprovalRequest = {
  id: number; kind: string; status: "pending" | "approved" | "rejected"; deal_id: number; term_index: number | null; term_label: string | null;
  deal_no: string | null; customer: string | null; title: string | null;
  current_due: string | null; requested_due: string | null; decided_due: string | null;
  reason: string; decision_note: string; requested_by: string | null; decided_by: string | null; recipients: string[];
  /** 나에게 온 요청(받는 관리자로 골라짐) */
  to_me: boolean; created_at: string; decided_at: string | null;
};
export type ApprovalAdmin = { id: number; name: string; role: string };

// ── 제품 시리얼·A/S(사용자 2026-10-19, 영업팀 요청 20번) ──

/** 수주 판의 시리얼 등록 현황 — 품목 줄마다 수량만큼 적으면 그 줄은 끝. '특주'처럼 적은 것도 등록으로 본다. */
export interface UnitSummary {
  quote_id: number;
  revision: number;
  issue_no: string | null;
  total: number;
  done: number;
  missing: number;
  missing_lines: string[];
}

/** 부품 하나 = 한 줄. no_serial = 숫자 없는 글('특주' 등) — 시리얼이 없는 품목. */
export interface ProductUnit {
  id: number;
  deal_id: number | null;
  quote_id: number | null;
  quote_revision: number | null;
  issue_no: string | null;
  line_no: number | null;
  line_name: string;
  part_name: string;
  serial: string;
  no_serial: boolean;
  maker: string;
  /** 상태 이니셜(unit-states.ts UNIT_STATES 의 code) */
  purpose: string;
  /** 이 제품의 프로젝트(영업 건) — 시리얼 찾기·상세에서 [프로젝트 건 열기](사용자 2026-10-22). 검색·상세 응답에만 */
  deal_title?: string | null;
  deal_display_no?: string | null;
  deal_biz?: DealBiz;
  warranty_months: number | null;
  warranty_start: string | null;
  warranty_until: string | null;
  shipment_index: number | null;
  status: string;
  note: string;
  customer?: string | null;
  deal_no?: string | null;
  as_count?: number;
  as_open?: number;
  warranty_state?: string;
}

export interface WarrantyPolicy { id?: number; keyword: string; months: number | null; note: string }

/** 견적서 판 하나의 시리얼 등록 화면 값 */
export interface IssueUnits {
  quote_id: number;
  revision: number;
  issue_no: string | null;
  issued_at: string | null;
  total: number | null;
  currency: string | null;
  customer: string | null;
  won: boolean;
  is_won_issue: boolean;
  /** 대여 건이면 새 칸 상태 기본이 R(대여) */
  biz?: DealBiz;
  won_issue_no: string | null;
  /** 이 건에서 이미 시리얼을 등록한 다른 판 */
  other_issues: string[];
  lines: { line_no: number; name: string; qty: number; part: string; suggest_months: number | null; units: ProductUnit[] }[];
  summary: Omit<UnitSummary, "quote_id" | "revision" | "issue_no">;
  policies: WarrantyPolicy[];
}

export interface AsCase {
  id: number;
  unit_id: number | null;
  deal_id: number | null;
  serial: string;
  part_name: string;
  customer: string | null;
  /** 고객사 담당자(이름·연락처) — A/S 접수서 */
  contact: string;
  /** 사진 id — /api/sales-deals/as-photos/{id} */
  photos: string[];
  /** 접수자(우리 쪽, 로그인 사용자) */
  received_by: string | null;
  received_on: string;
  symptom: string;
  cause: "확인 중" | "제품 결함" | "고객 과실" | "기타";
  in_warranty: boolean | null;
  charge: "미정" | "무상" | "유상";
  status: "접수" | "진행 중" | "완료";
  action: string;
  closed_on: string | null;
  note: string;
  created_at: string;
  updated_at: string;
}

export interface UnitDetail extends ProductUnit {
  shipment: { date: string | null; carrier: string | null; tracking: string[] | null; receiver: string | null } | null;
  cases: AsCase[];
}

export type DealBiz = "sale" | "rental";
export type DealRental = { months: number; start: string | null; end: string | null; monthly: number | null;
  /** 회수 — started 회수 시작일, done 회수 완료일 */
  return?: { started?: string; done?: string } };
