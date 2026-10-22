// 영업 건 리스트·상세·등록이 함께 쓰는 표시 도우미 + 저장 요청.
import type { DealEvent, DealRow, DealStage } from "@/lib/shared/sales-deals";
import { UNIT_STATE_TONE, unitStateText } from "@/lib/shared/unit-states";
import { cn } from "@/lib/shared/utils";

/** 제품 상태 코드 배지 — 코드와 뜻을 색으로(S · 판매 / R · 대여 / A · A/S 중 …, 사용자 2026-10-22). 시리얼 찾기·출하에서 같이 쓴다 */
export function UnitStateBadge({ code }: { code: string }) {
  return <span className={cn("whitespace-nowrap rounded px-1.5 py-0.5 font-mono text-[11.5px] font-semibold", UNIT_STATE_TONE[code] ?? UNIT_STATE_TONE.O)}
               title="제품 상태 코드">{unitStateText(code)}</span>;
}

// 단계 이름(백엔드 sales_deals/status.STAGES 와 같게). 이 상수는 화면 코드에서 쓰므로 여기 둔다 —
// lib/shared/sales-deals.ts 는 서버 전용(next/headers)이라 타입만 가져와야 한다(값을 가져오면 브라우저 번들이 깨짐).
/** 출하·완납까지 끝난 단계(완료 팝업·목록 제외에 씀) */
export const STAGE_DONE: DealStage = "완료 (출하·완납)";
export const STAGE_SHIP_ONLY: DealStage = "출하 기록만 (이전)";

export const STAGE_TONE: Record<DealStage, string> = {
  "제품 추천 확정": "bg-teal-500/12 text-teal-700 dark:text-teal-300",
  견적: "bg-slate-500/12 text-slate-700 dark:text-slate-300",
  수주: "bg-blue-500/12 text-blue-700 dark:text-blue-300",
  "출하 후 입금 대기": "bg-violet-500/12 text-violet-700 dark:text-violet-300",
  "완료 (출하·완납)": "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300",
  드랍: "bg-zinc-500/15 text-zinc-600 line-through decoration-1 dark:text-zinc-300",
  "출하 기록만 (이전)": "bg-foreground/8 text-foreground-muted",
  "대여 중": "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300",
  "회수 중": "bg-amber-500/15 text-amber-800 dark:text-amber-300",
  "회수 완료": "bg-zinc-500/12 text-zinc-700 dark:text-zinc-300",
};

export function won(n: number | null | undefined, currency: "KRW" | "USD" = "KRW"): string {
  if (n == null) return "-";
  return currency === "USD" ? `$${n.toLocaleString("en-US")}` : n.toLocaleString("ko-KR");
}

export function shortDate(iso: string | null | undefined): string {
  return iso ? iso.slice(2).replaceAll("-", ".") : "-";
}

export function Muted({ children }: { children: React.ReactNode }) {
  return <span className="text-foreground-subtle">{children}</span>;
}

/** 견적 단계면 견적서 판(견적 V1·V2) — 사용자 2026-10-08 */
export const quoteVer = (stage: DealStage | null, rev: number | null | undefined) => (stage === "견적" && rev ? rev : null);

/** 단계 칩 — 견적 단계는 판까지('견적 V2') */
export function StageChip({ stage, version, className }: { stage: DealStage; version?: number | null; className?: string }) {
  return (
    <span className={cn("inline-flex items-center rounded-full px-2 py-0.5 text-[12px] font-semibold", STAGE_TONE[stage], className)}>
      {stage}{version != null && ` V${version}`}
    </span>
  );
}

/** 이력 시각 — 26.10.08 14:03(보는 사람 시각) */
export function stamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.slice(0, 16).replace("T", " ");
  const p = (n: number) => String(n).padStart(2, "0");
  return `${String(d.getFullYear()).slice(2)}.${p(d.getMonth() + 1)}.${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export const ACTION_LABEL: Record<string, string> = {
  import: "엑셀에서 이관", create: "새 건 등록", units: "제품 시리얼 등록", as_case: "A/S 접수", won: "수주 상태 변경", invoice: "세금계산서 기록", rental_return: "대여 회수", rental_start: "대여 시작일", as_case_delete: "A/S 기록 삭제",
  payment: "입금 확인", paid_full: "완납 표시 변경", shipment: "출하 기록", remove_invoice: "계산서 기록 삭제",
  remove_payment: "입금 기록 삭제", remove_shipment: "출하 기록 삭제", review_done: "확인 완료", review_reopen: "확인 되돌리기",
  po_upload: "발주서 첨부", remove_po_file: "발주서 삭제", po_compare: "발주서 품목 대조", shipment_edit: "출하 기록 수정",
  reset_to_quote: "견적 상태로 되돌림(테스트)",
  statement: "거래명세서 발급", pay_case: "결제 방식 선택", pay_terms: "결제 조건 저장", drop: "드랍", undrop: "드랍 되살리기", recommend: "제품 추천 확정", recommend_new: "제품 추천 확정", quote: "견적서 발행", quote_revision: "견적서 재발행", quote_upload: "견적서 첨부(외부 수정본)", quote_replace: "견적서 파일 교체",
  meeting: "미팅 정보 입력",
  due_extend: "입금기한 연장", due_request: "입금기한 연장 요청", due_reject: "입금기한 연장 거절",
};
const PAY_CASE_KO: Record<string, string> = { after: "납품 후 입금", split: "선금 50% → 납품 → 잔금", prepay: "전액 입금 → 납품" };

/** 이력 한 줄의 덧붙임 글 */
export function eventDetail(e: DealEvent): string {
  const d = e.detail as Record<string, unknown>;
  if (e.action === "won") return d.to === true ? `→ 수주${d.no ? ` (${d.no} 견적서)` : ""}` : d.to === false ? "→ 드랍" : "→ 미확인";
  if (e.action === "due_extend" || e.action === "due_request")
    return `${d.label ?? ""} ${d.from ?? "-"} → ${d.to}${d.reason ? ` · ${d.reason}` : ""}`;
  if (e.action === "due_reject") return `${d.reason ?? ""}`;
  if (e.action === "rental_start") return `${d.from ?? "미정"} → ${d.to} (끝나는 날 ${d.end})`;
  if (e.action === "rental_return")
    return `${d.step === "start" ? "회수 시작" : d.step === "done" ? "회수 완료" : "회수 기록 취소"} · ${d.date ?? ""}`;
  if (e.action === "invoice") return `${d.date} · 공급가 ${won(d.supply as number)}`;
  if (e.action === "payment") return `${d.date} · ${won(d.amount as number)}원${d.full ? " · 완납" : ""}`;
  if (e.action === "shipment") return `${d.date} · ${d.carrier ?? ""}${d.forced ? " · 입금 확인 전" : ""}`;
  if (e.action === "paid_full") return d.to ? "→ 완납" : "→ 완납 해제";
  if (e.action === "import" && d.legacy_no) return `옛 번호 ${d.legacy_no}`;
  if (e.action === "po_upload" || e.action === "remove_po_file") return String(d.filename ?? "");
  if (e.action === "statement") return `합계 ${won(d.total as number)}원${d.again ? " · 다시 발급" : ""}`;
  if (e.action === "pay_case") return `→ ${PAY_CASE_KO[String(d.to)] ?? d.to}`;
  if (e.action === "pay_terms") return Array.isArray(d.terms) ? d.terms.join(" · ") : "";
  if (e.action === "drop") return d.reason ? String(d.reason) : "";
  if (e.action === "remove_payment" && d.term != null) return `${Number(d.term) + 1}회차 입금 확인 취소`;
  if (e.action === "quote_replace") return `v${d.revision} · ${String(d.file ?? "")}`;
  if (e.action === "units") return `${d.no ?? ""} 기준 ${d.count ?? 0}개${d.removed ? ` · ${d.removed}개 뺌` : ""}`;
  if (e.action === "as_case") return [d.serial, d.status, d.charge].filter(Boolean).join(" · ");
  return "";
}

export function todayIso(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** 영업 건 저장 요청 — 성공하면 갱신된 건, 실패하면 화면에 보여줄 문구로 Error. */
export async function dealRequest(path: string, body?: unknown): Promise<DealRow> {
  const r = await fetch(`/api/sales-deals${path}`, body === undefined
    ? { cache: "no-store" }
    : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await r.json().catch(() => null);
  // 로그인이 끊기면 로그인 화면(HTML, 200)으로 넘어간다 — 그걸 영업 건으로 받으면 상세 창이 깨진다
  if (r.ok && (data === null || typeof data !== "object")) {
    throw new Error(r.redirected ? "로그인이 만료되었습니다. 다시 로그인해 주세요." : "응답을 읽지 못했습니다.");
  }
  if (!r.ok) {
    const d = data?.detail;
    throw new Error(typeof d === "string" ? d : Array.isArray(d) ? "입력값을 확인해 주세요." : "저장하지 못했습니다.");
  }
  return data as DealRow;
}

export const INPUT = "h-9 w-full rounded-lg border border-border bg-surface px-3 outline-none focus:border-accent";

export function Field({ label, children, wide, className }: {
  label: string; children: React.ReactNode; wide?: boolean; className?: string;
}) {
  return (
    <label className={cn("flex flex-col gap-1", wide && "md:col-span-2", className)}>
      <span className="text-[12px] text-foreground-muted">{label}</span>
      {children}
    </label>
  );
}
