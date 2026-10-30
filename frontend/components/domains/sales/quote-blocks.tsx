"use client";

// 견적서 묶음(사용자 2026-10-13, 영업팀 요청 14·9번) — 기본 품목 표(B0) 아래에 붙는 묶음들과 NEGO.
// 품목 묶음(소제목·소계) · 옵션 묶음(줄마다 Total 반영/미반영 — 반영한 줄만 옵션 Sub total·Total, 사용자 2026-10-19) · 사양·서술형 표(항목명/내용) · 문구 줄 · NEGO(금액/%).
// 저장은 견적서 작성 화면의 [변경 저장]으로 같이(blocks·nego·줄의 block). 새 묶음은 임시 id(new-…)로 두면 서버가 B1… 로 바꾼다.
import { useEffect, useLayoutEffect, useRef, useState } from "react";

import type { QuoteBlock, QuoteLine, QuoteNego } from "@/lib/shared/product-recommend";
import { Icon } from "@/components/shared/ui/icon";
import { cn } from "@/lib/shared/utils";
import { ITEM_BTN, inputCls, primaryBtn } from "./proposal-actions";
import { AMOUNT_BOX, HEAD_AREA, ROW_CARD, ROW_CARD_AUTO, ROW_FIELD, ROW_ICON_BTN, ROW_LABEL } from "./quote-styles";

/** 칸 높이를 내용만큼 */
function useAutoHeight(value: string) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight + 2}px`;
  }, [value]);
  return ref;
}

/** 내용만큼 늘어나는 여러 줄 칸 — 규격·서술형 내용(기본 두 줄 높이, 사용자 2026-10-13: 규격 칸 더 크게) */
export function AutoTextarea({ value, className, ...rest }: React.TextareaHTMLAttributes<HTMLTextAreaElement> & { value: string }) {
  const ref = useAutoHeight(value);
  return <textarea ref={ref} rows={2} value={value} className={cn(className, "min-h-[52px] resize-none overflow-hidden py-1.5 leading-snug")} {...rest} />;
}

/** 한 줄 칸처럼 쓰되 견적서 칸 안에서 줄을 바꿀 수 있는 칸 — 엑셀처럼 Alt+Enter 로만 줄바꿈(Enter 는 예전 한 줄 칸처럼 그대로,
 *  사용자 2026-10-16). 줄을 바꾸면 내용만큼 늘어난다. Alt+Enter 자체는 AltEnterNewline 이 넣는다. */
export function LineArea({ value, className, onKeyDown, ...rest }: React.TextareaHTMLAttributes<HTMLTextAreaElement> & { value: string }) {
  const ref = useAutoHeight(value);
  return (
    <textarea ref={ref} rows={1} value={value} className={cn(className, "resize-none overflow-hidden leading-snug")} {...rest}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.altKey && !e.nativeEvent.isComposing) e.preventDefault(); onKeyDown?.(e); }} />
  );
}

export const MAIN_BLOCK = "B0";
export const MAIN_DEFAULT: QuoteBlock = { id: MAIN_BLOCK, type: "items", title: "", subtotal: false };

export const won = (n: number | null | undefined) => (n == null ? "-" : `${Math.round(n).toLocaleString("ko-KR")}원`);
export const usd = (n: number | null | undefined) =>
  (n == null ? "-" : `$${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);
const isAuto = (l: QuoteLine) => l.kind === "work" || l.kind === "freight";

// 대여 견적서 — 단가 = 1대당 월 납입금이라 금액 = 수량 × 단가 × 대여 개월(사용자 2026-10-22, 서버 quote_session.month_factor 와 같게).
// 한 번만 내는 줄(품목 Service·단위 '식'·물류비·배송비·인건비·서술형)과 단위가 이미 기간인 줄('36 개월')은 1
const TIME_UNITS = new Set(["개월", "월", "년", "일", "주", "month", "months", "year", "years", "day", "days", "week", "weeks"]);
const ONCE_UNITS = new Set(["식", "lot", "lots"]);
const FREIGHT_RE = /(물류|운송|화물|배송|운반|택배)\s*(비|료)|freight|shipping/i;
export function monthFactor(l: QuoteLine, months: number): number {
  if (!months || months <= 0 || isAuto(l) || l.kind === "note") return 1;
  if ((l.commodity ?? "").trim().toLowerCase() === "service") return 1;
  const unit = (l.unit ?? "").trim().toLowerCase();
  if (TIME_UNITS.has(unit) || ONCE_UNITS.has(unit)) return 1;
  if (FREIGHT_RE.test(`${l.commodity ?? ""} ${l.name ?? ""}`)) return 1;
  return months;
}

/** 해외 견적 줄 달러 단가 — 적은 달러 단가, 없으면 원화 ÷ 환율(서버 quote_session.usd_price 와 같은 식) */
export function usdOf(l: QuoteLine, fx: number | null): number | null {
  if (!isAuto(l) && l.usd && l.usd > 0) return l.usd;
  return fx && fx > 0 && l.unit_price && l.unit_price > 0 ? Math.round((l.unit_price / fx) * 100) / 100 : null;
}

/** 줄이 속한 묶음 — 물류비·인건비 줄은 납품 방식 칸에서 고른 묶음(auto), 없는 묶음을 가리키는 줄은 기본 묶음(서버 block_of 와 같음) */
export function blockOf(l: QuoteLine, blocks: QuoteBlock[], auto: string = MAIN_BLOCK): string {
  if (isAuto(l)) return blocks.some((b) => b.id === auto && b.type === "items") ? auto : MAIN_BLOCK;
  return l.block && blocks.some((b) => b.id === l.block) ? l.block : MAIN_BLOCK;
}

/** NEGO 할인 금액(양수) — base = 미반영 옵션 뺀 합계(견적 통화) */
export function negoCut(nego: QuoteNego | null | undefined, base: number, en: boolean): number {
  if (!nego?.value || nego.value <= 0) return 0;
  const v = nego.mode === "percent" ? (base * nego.value) / 100 : nego.value;
  return en ? Math.round(v * 100) / 100 : Math.round(v);
}

// 묶음은 이름을 직접 정하는 '사용자 지정 묶음'과 '< OPTION > 묶음' 두 가지(사용자 2026-10-13). 사양·서술형은 묶음 안 [+ 서술형 줄].
// spec·text 묶음은 그 전에 만든 견적용으로 보여 주기만 한다(새로 만들지 않음).
const TYPE_LABEL: Record<QuoteBlock["type"], string> = { items: "사용자 지정 묶음", option: "< OPTION > 묶음", spec: "사양·서술형 표", text: "문구 줄" };
const TYPE_TAG: Record<QuoteBlock["type"], string> = {
  items: "bg-accent/10 text-accent",
  option: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  spec: "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300",
  text: "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300",
};
// 묶음 머리 버튼(↑·↓·삭제) — 누를 수 있게 보이도록 테두리·높이를 품목 줄 버튼과 맞춤(사용자 2026-10-23)
const smallBtn = "inline-flex h-8 min-w-8 items-center justify-center rounded-[9px] border border-border-strong bg-surface-raised px-2 text-[12px] font-semibold text-foreground-muted hover:border-foreground/30 hover:bg-foreground/[0.04] hover:text-foreground disabled:pointer-events-none disabled:opacity-40";

type PricePick = { model: string; item: string; unit_price: number };

export function QuoteBlocks({ blocks, lines, nego, en, fx, busy, draftVer, totalNote, footNotes, autoBlock, onChange, children, pricePicker,
                             photoCell, months = 0, free = false }: {
  /** 대여 견적서의 대여 개월 수(판매 = 0) — 줄 금액·소계에 곱한다(단가 = 월 납입금) */
  months?: number;
  /** 무상 대여(사용자 2026-10-30) — 품목 단가·금액·Total 반영·OPTION·NEGO 를 숨긴다(물류비·인건비 줄은 그대로) */
  free?: boolean;
  blocks: QuoteBlock[]; lines: QuoteLine[]; nego: QuoteNego | null; en: boolean; fx: number | null; busy: boolean; draftVer: number;
  totalNote: string; footNotes: string[]; autoBlock: string; children?: React.ReactNode;
  /** [단가표에서 추가] 버튼 — 고르면 그 묶음에 품목 한 줄(사용자 2026-10-13: 옵션 묶음에서도) */
  pricePicker?: (onPick: (p: PricePick) => void) => React.ReactNode;
  /** Remark 칸 [사진] — 기본 품목 표와 같은 것(견적서 Remark 칸에 작게) */
  photoCell?: (l: QuoteLine) => React.ReactNode;
  onChange: (patch: { blocks?: QuoteBlock[]; lines?: QuoteLine[]; nego?: QuoteNego | null; total_note?: string; foot_notes?: string[] }) => void;
}) {
  const extra = blocks.filter((b) => b.id !== MAIN_BLOCK);
  const setBlock = (id: string, patch: Partial<QuoteBlock>) => onChange({ blocks: blocks.map((b) => (b.id === id ? { ...b, ...patch } : b)) });
  const move = (id: string, dir: -1 | 1) => {
    const i = extra.findIndex((b) => b.id === id);
    const j = i + dir;
    if (i < 0 || j < 0 || j >= extra.length) return;
    const next = [...extra];
    [next[i], next[j]] = [next[j], next[i]];
    onChange({ blocks: [blocks.find((b) => b.id === MAIN_BLOCK) ?? MAIN_DEFAULT, ...next] });
  };
  const remove = (id: string) => onChange({ blocks: blocks.filter((b) => b.id !== id), lines: lines.filter((l) => l.block !== id) });
  const setLine = (idx: number, patch: Partial<QuoteLine>) => onChange({ lines: lines.map((l, j) => (j === idx ? { ...l, ...patch } : l)) });
  const addLine = (blockId: string, kind: "extra" | "note" = "extra") =>
    onChange({ lines: [...lines, { id: `new${Date.now()}`, kind, key: kind, name: "", commodity: "", qty: kind === "note" ? null : 1,
                                   unit_price: null, remark: "", block: blockId }] });
  const [groupName, setGroupName] = useState("");
  const [bigNote, setBigNote] = useState<number | null>(null);
  // [묶음]을 눌러야 이름 칸이 나온다 · 견적서 문구(Total 아래·표 아래 주석)는 평소 접어 둔다(사용자 2026-10-23)
  const [naming, setNaming] = useState(false);
  const [notesOpen, setNotesOpen] = useState(false);
  const hasOption = blocks.some((b) => b.type === "option");
  /** 사용자 지정 묶음 — 적은 이름으로 만들고 빈 항목 한 줄. < OPTION > 묶음은 하나만, 빈 묶음으로 만들고 줄은 [항목 추가]로(사용자 2026-10-23) */
  function addBlock(type: "items" | "option") {
    const title = type === "option" ? "< OPTION >" : groupName.trim().slice(0, 60);
    if (!title) return;
    const id = `new-${Date.now()}`;
    onChange(type === "option"
      ? { blocks: [...blocks, { id, type, title }] }
      : { blocks: [...blocks, { id, type, title, subtotal: true }],
          lines: [...lines, { id: `new${Date.now()}`, kind: "extra", key: "extra", name: "", commodity: "", qty: 1, unit_price: null, remark: "", block: id }] });
    if (type === "items") { setGroupName(""); setNaming(false); }
  }

  return (
    <div className="flex flex-col gap-2.5">
      {extra.map((b, k) => {
        const own = lines.map((l, idx) => ({ l, idx })).filter(({ l }) => blockOf(l, blocks, autoBlock) === b.id);
        const sub = own.reduce((s, { l }) => s + (l.kind === "note" || (b.type === "option" && !l.in_total) ? 0
          : (l.qty ?? 0) * ((en ? usdOf(l, fx) : l.unit_price) ?? 0) * monthFactor(l, months)), 0);
        const inCount = own.filter(({ l }) => l.kind !== "note" && l.in_total).length;
        return (
          <div key={b.id} className="flex flex-col gap-3 rounded-2xl border border-border bg-foreground/[0.015] p-3.5">
            <div className="flex flex-wrap items-center gap-2">
              <span className={cn("rounded px-1.5 py-0.5 text-[11px] font-semibold", TYPE_TAG[b.type])}>{TYPE_LABEL[b.type]}</span>
              {b.type !== "text" && (
                <LineArea className={cn(inputCls, "min-w-0 flex-1 basis-40 py-0.5 text-[12.5px] font-semibold")} value={b.title} disabled={busy}
                       aria-label={`${TYPE_LABEL[b.type]} 제목`} maxLength={60}
                       placeholder={b.type === "items" ? (en ? "묶음 이름 — 예: Installation & Commissioning" : "묶음 이름 — 예: 설치 및 시운전")
                         : b.type === "option" ? "< OPTION >" : (en ? "예: Main Specifications" : "제목(선택) — 예: 방역 로봇 주요 사양")}
                       onChange={(e) => setBlock(b.id, { title: e.target.value })} />
              )}

              <div className="ml-auto flex items-center gap-1">
                <button type="button" className={smallBtn} disabled={busy || k === 0} aria-label="위로" onClick={() => move(b.id, -1)}>↑</button>
                <button type="button" className={smallBtn} disabled={busy || k === extra.length - 1} aria-label="아래로" onClick={() => move(b.id, 1)}>↓</button>
                {/* 지우기는 품목 줄과 같은 휴지통 버튼으로 통일(사용자 2026-10-23) */}
                <button type="button" className={cn(smallBtn, "hover:border-red-400/60 hover:bg-red-500/5 hover:text-red-600")} disabled={busy}
                        aria-label={`${TYPE_LABEL[b.type]} 빼기`} title="묶음과 그 안의 항목을 뺍니다" onClick={() => remove(b.id)}>
                  <Icon name="trash" className="h-4 w-4" />
                </button>
              </div>
            </div>

            {(b.type === "items" || b.type === "option") && (
              <>
                {/* 묶음 안 품목 줄 — 기본 품목과 같은 두 단 카드(가로 스크롤 없이, 사용자 2026-10-23) */}
                {own.length === 0 && (
                  <p className="rounded-xl border border-dashed border-border px-3 py-3 text-center text-[12.5px] text-foreground-subtle">
                    아직 줄이 없습니다 — 아래 [항목 추가]나 [단가표에서 추가]로 넣으세요.
                  </p>
                )}
                <ul className="flex flex-col gap-2.5">
                  {own.map(({ l, idx }) => {
                    const unit = en ? usdOf(l, fx) : l.unit_price;
                    const amount = en ? usd((l.qty ?? 0) * (unit ?? 0) * monthFactor(l, months)) : won((l.qty ?? 0) * (l.unit_price ?? 0) * monthFactor(l, months));
                    const oneTime = !!months && monthFactor(l, months) === 1;
                    if (isAuto(l)) return (                     // 물류비·인건비 — 아래 납품 방식 칸에서 바꾼다
                      <li key={l.id} className={cn(ROW_CARD_AUTO, "flex flex-wrap items-center gap-x-4 gap-y-1 text-[13px] text-foreground-muted")}>
                        <span className="font-semibold text-foreground">{l.commodity}</span>
                        <span className="min-w-0 flex-1 whitespace-pre-line">{l.name}</span>
                        <span>{l.qty}{l.unit ? ` ${l.unit}` : ""} × {en ? usd(unit) : won(l.unit_price)}</span>
                        <span className="text-[16px] font-extrabold text-foreground">{amount}</span>
                        <span className="basis-full text-[11.5px]">납품 방식에서 설정</span>
                      </li>
                    );
                    if (l.kind === "note") return (            // 서술형 줄 — 항목명 / 내용(가격 없음), 견적서엔 내용 가운데 정렬
                      <li key={l.id} className="rounded-xl border border-emerald-500/25 bg-emerald-500/[0.03] p-3.5">
                        <div className="grid items-start gap-2.5 sm:grid-cols-[190px_minmax(0,1fr)_auto]">
                          <div className={ROW_LABEL}>
                            <span className="flex items-center gap-1.5">{en ? "Item" : "항목명"}
                              <span className="rounded-md bg-emerald-500/12 px-1.5 py-px text-[11px] font-bold text-emerald-700 dark:text-emerald-300">서술형</span></span>
                            <LineArea className={HEAD_AREA} value={l.commodity ?? ""} maxLength={40} aria-label="서술형 항목명" disabled={busy}
                                      placeholder={en ? "e.g. Weight" : "예: 무게"} onChange={(e) => setLine(idx, { commodity: e.target.value })} />
                          </div>
                          <div className={ROW_LABEL}>
                            <span>{en ? "Content (no price)" : "내용 (가격 없음)"}</span>
                            <AutoTextarea className={cn(HEAD_AREA, "text-center")} value={l.name} maxLength={300}
                                          aria-label="서술형 내용" disabled={busy} placeholder={en ? "e.g. 55kg" : "예: 55kg (여러 줄 가능)"}
                                          onChange={(e) => setLine(idx, { name: e.target.value })} />
                          </div>
                          <button type="button" aria-label="서술형 줄 빼기" title="이 줄 빼기" disabled={busy} className={cn(ROW_ICON_BTN, "mt-6 hover:border-red-400/60 hover:bg-red-500/5 hover:text-red-600")}
                                  onClick={() => onChange({ lines: lines.filter((_, j) => j !== idx) })}>
                            <Icon name="trash" className="h-4 w-4" />
                          </button>
                        </div>
                      </li>
                    );
                    return (
                      <li key={l.id} className={ROW_CARD}>
                        <div className="grid gap-2.5 sm:grid-cols-[190px_minmax(0,1fr)]">
                          <div className={ROW_LABEL}>
                            <span className="flex items-center gap-1.5">{en ? "Commodity" : "품목"}
                              {oneTime && <span className="rounded-md bg-emerald-500/12 px-1.5 py-px text-[11px] font-bold text-emerald-700 dark:text-emerald-300">1회</span>}
                            </span>
                            <LineArea className={HEAD_AREA} value={l.commodity ?? ""} aria-label="품목" disabled={busy}
                                      placeholder={en ? "e.g. Charging Dock" : "예: 충전 스테이션"} onChange={(e) => setLine(idx, { commodity: e.target.value })} />
                          </div>
                          <div className={ROW_LABEL}>
                            <span>{en ? "Specifications" : "규격"}</span>
                            <AutoTextarea className={HEAD_AREA} value={l.name} aria-label="규격" disabled={busy}
                                          placeholder={en ? "Model / spec — multiple lines\ne.g. CS-01\n- 24V, 2A" : "모델·사양 — 여러 줄 가능\n예: CS-01\n- 24V, 2A"}
                                          onChange={(e) => setLine(idx, { name: e.target.value })} />
                          </div>
                        </div>
                        <div className="mt-2.5 flex flex-wrap items-end gap-2.5">
                          <div className={ROW_LABEL}>
                            <span>{en ? "Qty · Unit" : "수량 · 단위"}</span>
                            <span className="flex">
                              <input className={cn(ROW_FIELD, "w-[64px] rounded-r-none text-right", !l.qty && "border-amber-500")} type="number" min={1}
                                     value={l.qty ?? ""} aria-label="수량" disabled={busy}
                                     onChange={(e) => setLine(idx, { qty: Math.max(0, Math.floor(Number(e.target.value) || 0)) || null })} />
                              <input className={cn(ROW_FIELD, "-ml-px w-[64px] rounded-l-none bg-foreground/[0.03] font-normal")} value={l.unit ?? ""} maxLength={8}
                                     list="quote-units" aria-label="수량 단위" disabled={busy} placeholder="ea"
                                     title="수량 단위 — 예: MD·식·개월·대 (비우면 ea)" onChange={(e) => setLine(idx, { unit: e.target.value })} />
                            </span>
                          </div>
                          {!free && <div className={ROW_LABEL}>
                            <span>{en ? (months ? "Price ($ / month)" : "Price ($)") : months ? (oneTime ? "금액 (1회)" : "월 단가") : "단가"}</span>
                            {en ? (
                              <input key={`usd-${l.id}-${draftVer}`} className={cn(ROW_FIELD, "w-[140px] text-right", unit == null && "border-amber-500")}
                                     inputMode="decimal" defaultValue={l.usd ?? ""} aria-label="달러 단가" disabled={busy}
                                     placeholder={unit != null ? `${usd(unit)} (환율)` : "$ 단가"}
                                     onChange={(e) => { const v = Number(e.target.value.replace(/[,$\s]/g, "")); setLine(idx, { usd: v > 0 ? v : null }); }} />
                            ) : (
                              <input className={cn(ROW_FIELD, "w-[140px] text-right", l.unit_price == null && "border-amber-500")} inputMode="numeric"
                                     value={l.unit_price ?? ""} aria-label="단가" placeholder={months ? "월 납입금 또는 1회 지불" : "단가"} disabled={busy}
                                     onChange={(e) => setLine(idx, { unit_price: Number(e.target.value.replace(/,/g, "")) || null })} />
                            )}
                          </div>}
                          {b.type === "option" && !free && (
                            // 옵션 줄은 Total 반영/미반영(사용자 2026-10-19, 미반영이어도 공급가액은 견적서에 보인다)
                            <div className={ROW_LABEL}>
                              <span>Total</span>
                              <select value={l.in_total ? "in" : "out"} disabled={busy} aria-label={`${l.name || "옵션"} Total 반영`}
                                      title="반영 = 옵션 Sub total·Total 에 포함 / 미반영 = 공급가액만 보이고 합계 제외"
                                      onChange={(e) => setLine(idx, { in_total: e.target.value === "in" })}
                                      className={cn(ROW_FIELD, "w-[96px] px-2 font-semibold",
                                                    l.in_total ? "border-accent/60 bg-accent/10 text-accent" : "text-foreground-muted")}>
                                <option value="in">반영</option>
                                <option value="out">미반영</option>
                              </select>
                            </div>
                          )}
                          {!free && <div className={cn(ROW_LABEL, "ml-auto flex-1 items-end")}>
                            <span>{en ? "Amount" : "금액"}{months ? <span className="font-normal text-foreground-subtle"> · {oneTime ? "1회 지불" : `× ${months}개월`}</span> : null}
                              {b.type === "option" && !l.in_total && <span className="font-normal text-foreground-subtle"> · 미반영, 합계 제외</span>}</span>
                            <span className={cn(AMOUNT_BOX, b.type === "option" && !l.in_total && "text-foreground-subtle")}>
                              {!en && l.unit_price == null ? <span className="text-[12.5px] font-medium text-foreground-subtle">단가를 넣으면 계산</span> : amount}
                            </span>
                          </div>}
                        </div>
                        <div className="mt-2.5 flex flex-wrap items-start gap-2">
                          <LineArea className={cn(HEAD_AREA, "min-w-0 flex-1 basis-64 text-[12.5px]")} value={l.remark ?? ""} aria-label="비고" disabled={busy}
                                    placeholder={en ? "Remark" : "비고"} onChange={(e) => setLine(idx, { remark: e.target.value })} />
                          {photoCell?.(l)}
                          <button type="button" aria-label={`${l.name || "항목"} 빼기`} title="이 줄 빼기" disabled={busy}
                                  className={cn(ROW_ICON_BTN, "hover:border-red-400/60 hover:bg-red-500/5 hover:text-red-600")}
                                  onClick={() => onChange({ lines: lines.filter((_, j) => j !== idx) })}>
                            <Icon name="trash" className="h-4 w-4" />
                          </button>
                        </div>
                      </li>
                    );
                  })}
                </ul>
                <div className="flex flex-wrap items-center gap-2 text-[12px]">
                  <button type="button" disabled={busy} onClick={() => addLine(b.id)} className={ITEM_BTN}>
                    <Icon name="plus" className="h-3.5 w-3.5 text-blue-600 dark:text-blue-400" /> 항목 추가
                  </button>
                  {pricePicker?.((p) => onChange({ lines: [...lines, { id: `new${Date.now()}`, kind: "extra", key: "extra",
                    name: `${p.model} ${p.item}`.slice(0, 60), commodity: "", qty: 1, unit_price: p.unit_price, remark: "", block: b.id }] }))}
                  {b.type === "items" && (
                    <button type="button" disabled={busy} onClick={() => addLine(b.id, "note")}
                            title="항목명 / 내용만 적는 줄(가격 없음) — 사양·설명용"
                            className={ITEM_BTN}>
                      <Icon name="plus" className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" /> 서술형 줄
                    </button>
                  )}
                  <span className="ml-auto text-foreground-subtle">
                    {b.type === "option"
                      ? (inCount ? `옵션 Sub total ${en ? usd(sub) : won(sub)} (반영 ${inCount}줄 · Total 에 포함)` : "반영한 옵션 없음 · 공급가액만 보이고 Sub total·Total 제외")
                      : `Sub total ${en ? usd(sub) : won(sub)} (견적서에 자동 표시)`}
                  </span>
                </div>
              </>
            )}

            {b.type === "spec" && (
              <div className="flex flex-col gap-1.5">
                {(b.rows ?? []).map((r, ri) => (
                  <div key={ri} className="grid grid-cols-[minmax(0,1fr)_minmax(0,2fr)_auto] items-start gap-1.5">
                    <LineArea className={cn(inputCls, "text-[12.5px]")} value={r.label} maxLength={40} disabled={busy} aria-label={`${ri + 1}번 항목명`}
                           placeholder={en ? "예: Weight" : "항목명 — 예: 무게"}
                           onChange={(e) => setBlock(b.id, { rows: (b.rows ?? []).map((x, j) => (j === ri ? { ...x, label: e.target.value } : x)) })} />
                    <textarea className={cn(inputCls, "min-h-[34px] resize-y text-center text-[12.5px]")} rows={1} value={r.value} maxLength={300}
                              disabled={busy} aria-label={`${ri + 1}번 내용`} placeholder={en ? "예: 55kg" : "내용 — 예: 55kg (여러 줄 가능, 견적서엔 가운데 정렬)"}
                              onChange={(e) => setBlock(b.id, { rows: (b.rows ?? []).map((x, j) => (j === ri ? { ...x, value: e.target.value } : x)) })} />
                    <button type="button" aria-label={`${ri + 1}번 줄 빼기`} disabled={busy} className="pt-1.5 text-foreground-subtle hover:text-red-600"
                            onClick={() => setBlock(b.id, { rows: (b.rows ?? []).filter((_, j) => j !== ri) })}>
                      <Icon name="trash" className="h-4 w-4" />
                    </button>
                  </div>
                ))}
                <div className="flex flex-wrap items-center gap-2 text-[12px]">
                  <button type="button" disabled={busy || (b.rows ?? []).length >= 20}
                          onClick={() => setBlock(b.id, { rows: [...(b.rows ?? []), { label: "", value: "" }] })}
                          className="inline-flex items-center gap-1 rounded border border-border px-2 py-0.5 text-foreground-muted hover:text-foreground">
                    <Icon name="plus" className="h-3.5 w-3.5" /> 줄 추가
                  </button>
                  <span className="ml-auto text-foreground-subtle">가격 칸 없음 · 합계와 무관 · 20줄까지</span>
                </div>
              </div>
            )}

            {b.type === "text" && (
              <textarea className={cn(inputCls, "min-h-[56px] resize-y text-[12.5px]")} value={b.text ?? ""} maxLength={300} disabled={busy}
                        aria-label="문구" placeholder={en ? "예: Rental terms follow the separate contract." : "표 폭 전체에 들어갈 문구 — 예: 렌탈 조건은 별도 계약서를 따릅니다. (여러 줄 가능)"}
                        onChange={(e) => setBlock(b.id, { text: e.target.value })} />
            )}
          </div>
        );
      })}

      {/* 더 넣기 — 묶음·OPTION 묶음·NEGO 를 한 줄로(넓은 점선 상자 대신) */}
      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[12.5px] font-semibold text-foreground-muted">더 넣기</span>
          <button type="button" disabled={busy || blocks.length >= 12} aria-expanded={naming} onClick={() => setNaming(!naming)}
                  title="이름을 정해 품목 묶음을 만듭니다(예: 설치 및 시운전, 유지보수) — 묶음마다 Sub total"
                  className={cn("h-8 rounded-full border px-3 text-[12.5px] font-semibold disabled:opacity-40",
                    naming ? "border-accent bg-accent/10 text-accent" : "border-border bg-surface-raised text-foreground-muted hover:text-foreground")}>
            + 묶음
          </button>
          {!free && <button type="button" disabled={busy || hasOption || blocks.length >= 12} onClick={() => addBlock("option")}
                  title={hasOption ? "< OPTION > 묶음은 하나만 둡니다" : "줄마다 Total 반영/미반영을 고르는 묶음 — 반영한 줄만 옵션 Sub total·Total 에 포함"}
                  className="inline-flex h-8 items-center gap-1.5 rounded-full border border-border bg-surface-raised px-3 text-[12.5px] font-semibold text-foreground-muted hover:text-foreground disabled:opacity-40">
            <span className="h-1.5 w-1.5 rounded-full bg-cyan-600" aria-hidden /> OPTION 묶음
          </button>}
          {!free && <button type="button" disabled={busy || !!nego} onClick={() => onChange({ nego: { mode: "amount", value: null, label: "" } })}
                  className="inline-flex h-8 items-center gap-1.5 rounded-full border border-border bg-surface-raised px-3 text-[12.5px] font-bold text-red-700 hover:bg-red-500/5 disabled:opacity-40 dark:text-red-300">
            <span className="h-1.5 w-1.5 rounded-full bg-red-600" aria-hidden /> NEGO (할인)
          </button>}
        </div>
        {naming && (
          <div className="flex flex-wrap items-center gap-2 rounded-xl border border-accent/30 bg-accent/[0.03] p-2.5">
            <input className={cn(inputCls, "h-9 min-w-0 flex-1 basis-48 text-[12.5px]")} value={groupName} maxLength={60} disabled={busy} autoFocus
                   aria-label="새 묶음 이름" placeholder={en ? "묶음 이름 — 예: Maintenance" : "묶음 이름 — 예: 설치 및 시운전, 유지보수, 방역 로봇 주요 사양"}
                   onChange={(e) => setGroupName(e.target.value)}
                   onKeyDown={(e) => { if (e.key === "Enter" && !e.nativeEvent.isComposing) { e.preventDefault(); addBlock("items"); } }} />
            <button type="button" disabled={busy || !groupName.trim() || blocks.length >= 12} onClick={() => addBlock("items")}
                    className={groupName.trim() ? cn(ITEM_BTN, "border-accent/60 text-accent") : ITEM_BTN}>
              묶음 만들기
            </button>
            <span className="basis-full text-[11px] text-foreground-subtle">만든 묶음 안에 항목(가격 있음)이나 서술형 줄(항목명 / 내용)을 넣으세요.</span>
          </div>
        )}
      </div>

      {children}

      {nego && !free && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-red-500/30 bg-red-500/[0.04] p-2.5 text-[12.5px]">
          <span className="rounded bg-red-500/12 px-1.5 py-0.5 text-[11px] font-semibold text-red-700 dark:text-red-300">NEGO(할인)</span>
          <div className="flex overflow-hidden rounded-md border border-border text-[12px]">
            {(["amount", "percent"] as const).map((m) => (
              <button key={m} type="button" disabled={busy} onClick={() => onChange({ nego: { ...nego, mode: m } })}
                      className={cn("px-2 py-0.5", nego.mode === m ? "bg-accent text-white" : "text-foreground-muted")}>
                {m === "amount" ? (en ? "금액($)" : "금액(원)") : "%"}
              </button>
            ))}
          </div>
          <input className={cn(inputCls, "w-[120px] py-0.5 text-[12.5px]", !nego.value && "border-amber-500")} inputMode="decimal"
                 value={nego.value ?? ""} disabled={busy} aria-label="NEGO 값" placeholder={nego.mode === "percent" ? "예: 5" : en ? "예: 500" : "예: 800000"}
                 onChange={(e) => { const v = Number(e.target.value.replace(/[,$%\s]/g, "")); onChange({ nego: { ...nego, value: v > 0 ? v : null } }); }} />
          <input className={cn(inputCls, "min-w-0 flex-1 basis-36 py-0.5 text-[12.5px]")} value={nego.label} maxLength={40} disabled={busy}
                 aria-label="NEGO 이름" placeholder={en ? "이름(선택) — 예: Special discount" : "이름(선택) — 예: 특별 할인"}
                 onChange={(e) => onChange({ nego: { ...nego, label: e.target.value } })} />
          <button type="button" className={cn(smallBtn, "hover:border-red-400/60 hover:bg-red-500/5 hover:text-red-600")} disabled={busy}
                  aria-label="NEGO 빼기" title="NEGO 줄 빼기" onClick={() => onChange({ nego: null })}>
            <Icon name="trash" className="h-4 w-4" />
          </button>
          <span className="basis-full text-[11.5px] text-foreground-subtle">
            Total 바로 위에 &quot;NEGO&quot; 줄로 들어갑니다. %는 미반영 옵션을 뺀 합계 기준으로 계산해 견적서엔 금액으로 나옵니다. 값을 비우면 빠집니다.
          </span>
        </div>
      )}

      <button type="button" aria-expanded={notesOpen} onClick={() => setNotesOpen(!notesOpen)}
              className="inline-flex w-fit items-center gap-2 py-1 text-[12.5px] font-bold text-accent hover:underline">
        {notesOpen ? "견적서 문구 접기 ▴" : "견적서 문구 더 보기 ▾"}
        {(totalNote.trim() || footNotes.some((x) => x.trim())) && (
          <span className="rounded-full border border-border bg-surface-raised px-2 py-px text-[11.5px] font-semibold text-foreground-muted">
            {[totalNote.trim() ? "Total 아래 문구" : "", footNotes.filter((x) => x.trim()).length ? `주석 ${footNotes.filter((x) => x.trim()).length}개` : ""].filter(Boolean).join(" · ")}
          </span>
        )}
      </button>
      {notesOpen && (
      <div className="grid gap-2 sm:grid-cols-2">
        <label className="flex flex-col gap-1 text-[12px] text-foreground-muted">
          <span>Total 아래 보조 문구 <span className="text-[11px] text-foreground-subtle">· 적은 것만 들어감(비우면 없음)</span></span>
          <LineArea className={cn(inputCls, "text-[12.5px]")} value={totalNote} maxLength={200} disabled={busy}
                 placeholder={en ? "예: Monthly USD 750" : "예: 월 995,000원"} onChange={(e) => onChange({ total_note: e.target.value })} />
        </label>
        <div className="flex flex-col gap-1 text-[12px] text-foreground-muted">
          <span>표 아래 주석 <span className="text-[11px] text-foreground-subtle">· 칸마다 한 줄씩 견적서 맨 아래에(가운데 정렬), 앞에 * 자동 · 8칸까지</span></span>
          {footNotes.map((nt, i) => (
            <div key={i} className="flex items-start gap-1.5">
              <textarea className={cn(inputCls, "min-h-[34px] flex-1 resize-y text-[12.5px]")} rows={1} value={nt} maxLength={200} disabled={busy}
                        aria-label={`주석 ${i + 1}`}
                        placeholder={en ? "예: This quotation is valid for 1 month." : "예: 본 견적은 1개월 유효하며, 상세 견적은 최종 미팅 이후 별도 제공 가능합니다."}
                        onChange={(e) => onChange({ foot_notes: footNotes.map((x, j) => (j === i ? e.target.value : x)) })} />
              <button type="button" aria-label={`주석 ${i + 1} 크게 보기`} title="크게 보기 — 넓은 칸에서 고치고 견적서 모양 확인" disabled={busy}
                      className="pt-1.5 text-foreground-subtle hover:text-accent" onClick={() => setBigNote(i)}>
                <Icon name="eye" className="h-4 w-4" />
              </button>
              <button type="button" aria-label={`주석 ${i + 1} 빼기`} disabled={busy} className="pt-1.5 text-foreground-subtle hover:text-red-600"
                      onClick={() => onChange({ foot_notes: footNotes.filter((_, j) => j !== i) })}>
                <Icon name="trash" className="h-4 w-4" />
              </button>
            </div>
          ))}
          {bigNote != null && bigNote < footNotes.length && (
            <NoteDialog index={bigNote} value={footNotes[bigNote]} en={en} busy={busy} onClose={() => setBigNote(null)}
                        onChange={(v) => onChange({ foot_notes: footNotes.map((x, j) => (j === bigNote ? v : x)) })} />
          )}
          <button type="button" disabled={busy || footNotes.length >= 8} onClick={() => onChange({ foot_notes: [...footNotes, ""] })}
                  className="inline-flex w-fit items-center gap-1 rounded border border-border px-2 py-0.5 text-foreground-muted hover:text-foreground disabled:opacity-40">
            <Icon name="plus" className="h-3.5 w-3.5" /> 주석 추가
          </button>
        </div>
      </div>
      )}
    </div>
  );
}

/** 표 아래 주석 크게 보기(사용자 2026-10-22) — 넓은 칸에서 고치고, 견적서 맨 아래에 들어가는 모양(가운데 정렬, 앞에 *)을 본다 */
function NoteDialog({ index, value, en, busy, onChange, onClose }: {
  index: number; value: string; en: boolean; busy: boolean; onChange: (v: string) => void; onClose: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const text = value.trim();
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/30 p-4" onClick={onClose}>
      <div role="dialog" aria-label={`주석 ${index + 1} 크게 보기`} onClick={(e) => e.stopPropagation()}
           className="scroll-thin max-h-full w-full max-w-[860px] overflow-y-auto rounded-2xl bg-background p-5 shadow-2xl">
        <div className="mb-3 flex items-center gap-2">
          <h2 className="text-[16px] font-bold">표 아래 주석 {index + 1} 크게 보기</h2>
          <span className="text-[12px] text-foreground-muted">고친 내용은 바로 반영되고, 닫은 뒤 [변경 저장]을 눌러야 저장됩니다.</span>
          <button type="button" onClick={onClose} aria-label="닫기"
                  className="ml-auto grid h-8 w-8 place-items-center rounded-lg text-foreground-muted hover:bg-foreground/8"><Icon name="x" className="h-4 w-4" /></button>
        </div>
        <label className="flex flex-col gap-1 text-[12.5px] text-foreground-muted">
          주석 <span className="text-[11px] text-foreground-subtle">{value.length} / 200자 · 앞의 * 는 자동으로 붙습니다</span>
          <textarea className={cn(inputCls, "min-h-[120px] resize-y text-[13px]")} value={value} maxLength={200} disabled={busy} autoFocus
                    placeholder={en ? "예: This quotation is valid for 1 month." : "예: 본 견적은 1개월 유효하며, 상세 견적은 최종 미팅 이후 별도 제공 가능합니다."}
                    onChange={(e) => onChange(e.target.value)} />
        </label>
        <p className="mt-4 text-[12.5px] font-semibold text-foreground">견적서 맨 아래에는 이렇게 들어갑니다</p>
        <div className="mt-1.5 rounded-lg border border-border bg-white px-4 py-3 text-center text-[11.5px] font-semibold leading-snug text-black">
          {text ? <span className="whitespace-pre-line">* {text}</span> : <span className="font-normal text-neutral-400">(비어 있으면 견적서에 안 들어갑니다)</span>}
        </div>
        <div className="mt-4 flex justify-end">
          <button type="button" onClick={onClose} className={primaryBtn}>완료</button>
        </div>
      </div>
    </div>
  );
}
