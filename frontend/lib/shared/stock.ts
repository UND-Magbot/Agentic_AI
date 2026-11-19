// 제품 재고 관리(사용자 2026-11-19) — backend/app/purchases/stock.py 의 화면 행 타입 + 요청.
// 구매 입고 기록에서 제품 한 대 = 한 줄로 자동으로 채워지고, 구분·상태(+메모)·자체 개발용 영업팀 담당자만 재고 화면에서 정한다.

export type StockPurpose = "sale" | "dev";
/** 재고 구분 — 서버 purchases/stock.py CATEGORIES 와 같게(입고·제품 등록 작성 칸에서 고름, 사용자 2026-11-19) */
export const STOCK_CATEGORIES = ["QURI", "AMR", "ACR", "휴머노이드", "4축", "6축", "AGV", "기타 로봇",
  "ATC", "EOAT", "그리퍼", "모터", "PC", "컨트롤러", "카메라", "태블릿", "부품", "EP", "기타"] as const;

export const STOCK_PURPOSE_LABEL: Record<StockPurpose, string> = { sale: "판매용", dev: "보유 후 자체 개발용" };

export interface StockLast {
  label: string;
  detail: Record<string, unknown>;
  at: string;
  by: string | null;
}

export interface StockRow {
  id: number;
  purchase_id: number;
  purpose: StockPurpose;
  /** 입고 기록의 품목 줄('po:줄번호'·'free:이름') — 판매용 화면에서 같은 구매 건·같은 줄을 묶는다 */
  line_key: string;
  unit_index: number;
  /** 판매용 나눈 줄 번호 — 입고 기록에 나눠 적은 줄마다 목록 한 줄(사용자 2026-11-19) */
  part: number;
  /** 발주서·입고 기록의 품목명(그대로) */
  name: string;
  /** 재고 표시 이름(사용자 2026-11-19) — 비면 name 으로 보인다 */
  display_name: string;
  /** 제조사 — 재고에서 고친 값, 없으면 구매 공급사(maker_custom = 고쳤는지) */
  maker: string;
  maker_custom: boolean;
  category: string;
  customer: string;
  owner: string;
  sw_owner: string;
  maker_sn: string;
  und_sn: string;
  no_sn: boolean;
  received_on: string | null;
  status: string;
  status_note: string;
  supplier: string;
  /** 재고 직접 등록(구매 없이 [제품 등록], 사용자 2026-11-19) — 발주번호 대신 '직접 등록' */
  direct: boolean;
  /** 국내·해외 — 구매 건의 구분(사용자 2026-11-19) */
  region: "domestic" | "overseas";
  po_no: string | null;
  /** 입고 사진(구매 receipt-photos 이름) — 품목 줄의 전체 사진 · 그 대의 S/N 사진(자체 개발용) */
  line_photos: string[];
  photos: string[];
  /** 알림 — 아직 정하지 않아 비어 있다(사용자 2026-11-19) */
  alerts: string[];
  last: StockLast | null;
}

export interface StockListData {
  rows: StockRow[];
  /** 관리자 — 직접 등록 지우기 */
  me?: { admin: boolean };
  counts: Record<StockPurpose, number>;
  categories: string[];
  states: string[];
}

export interface StockHistoryEntry {
  id: number;
  label: string;
  detail: Record<string, unknown>;
  at: string;
  by: string | null;
}

export interface StockPatch {
  category?: string;
  status?: string;
  status_note?: string;
  owner?: string;
  display_name?: string;
  maker?: string;
  /** 판매 고객사·입고일(YYYY-MM-DD) — 그 입고 기록을 다시 저장하면 입고 기록 값으로(사용자 2026-11-19) */
  customer?: string;
  /** S/W 담당자(자체 개발용, 사용자 2026-11-19) — 고객사처럼 입고 기록을 다시 저장하면 입고 기록 값으로 */
  sw_owner?: string;
  received_on?: string;
}

/** 재고 줄 사진 넣고 빼기(사용자 2026-11-19 — [수정] 모드) — 입고 기록에 저장 */
export interface StockPhotoPatch {
  add?: string[];
  remove?: string[];
  scope?: "line" | "unit";
  kind?: "item" | "sn";
}

/** 재고에 보이는 이름 — 표시 이름, 없으면 발주서 품목명 */
export const stockLabel = (r: { name: string; display_name: string }) => r.display_name || r.name;
/** 제품별 묶기 열쇠 — 제조사 + 표시 이름, 대·소문자·띄어쓰기 차이는 같은 제품으로 */
export const productKey = (r: { name: string; display_name: string; maker: string }) =>
  `${r.maker}|${stockLabel(r)}`.toLowerCase().replace(/\s+/g, "");

export async function stockRequest<T>(path: string, body?: StockPatch & { ids?: number[] } | StockPhotoPatch): Promise<T> {
  const r = await fetch(`/api/stock${path}`, body === undefined ? { cache: "no-store" } : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof d?.detail === "string" ? d.detail : "요청을 처리하지 못했습니다.");
  return d as T;
}

/** 이력 한 줄 설명 — 상태·구분·담당자는 '이전 → 바뀐 값', 나머지는 '칸: 값' */
export function stockDetailText(detail: Record<string, unknown>): string {
  const { from, to, ...rest } = detail;
  const parts: string[] = [];
  if (from !== undefined || to !== undefined) parts.push(`${String(from ?? "-")} → ${String(to ?? "-")}`);
  for (const [k, v] of Object.entries(rest)) parts.push(`${k}: ${v === "" || v == null ? "-" : String(v)}`);
  return parts.join(" · ");
}
