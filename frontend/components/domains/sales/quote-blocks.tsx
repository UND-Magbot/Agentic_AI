"use client";

// 견적서 묶음(사용자 2026-10-13, 영업팀 요청 14·9번) — 기본 품목 표(B0) 아래에 붙는 묶음들과 NEGO.
// 품목 묶음(소제목·소계) · 옵션 묶음(단가만, 합계 제외 — LG CNS 견적서) · 사양·서술형 표(항목명/내용) · 문구 줄 · NEGO(금액/%).
// 저장은 견적서 작성 화면의 [변경 저장]으로 같이(blocks·nego·줄의 block). 새 묶음은 임시 id(new-…)로 두면 서버가 B1… 로 바꾼다.
import { useLayoutEffect, useRef, useState } from "react";

import type { QuoteBlock, QuoteLine, QuoteNego } from "@/lib/shared/product-recommend";
import { Icon } from "@/components/shared/ui/icon";
import { cn } from "@/lib/shared/utils";
import { inputCls } from "./proposal-actions";

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

/** NEGO 할인 금액(양수) — base = 옵션 뺀 합계(견적 통화) */
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
const smallBtn = "rounded border border-border px-1.5 text-[11.5px] text-foreground-muted hover:text-foreground disabled:opacity-40";

type PricePick = { model: string; item: string; unit_price: number };

export function QuoteBlocks({ blocks, lines, nego, en, fx, busy, draftVer, totalNote, footNotes, autoBlock, onChange, children, pricePicker,
                             photoCell }: {
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
  const hasOption = blocks.some((b) => b.type === "option");
  /** 사용자 지정 묶음 — 적은 이름으로 만들고 빈 항목 한 줄. < OPTION > 묶음은 하나만 */
  function addBlock(type: "items" | "option") {
    const title = type === "option" ? "< OPTION >" : groupName.trim().slice(0, 60);
    if (!title) return;
    const id = `new-${Date.now()}`;
    onChange({ blocks: [...blocks, { id, type, title, ...(type === "items" ? { subtotal: true } : {}) }],
               lines: [...lines, { id: `new${Date.now()}`, kind: "extra", key: "extra", name: "", commodity: "", qty: 1, unit_price: null, remark: "", block: id }] });
    if (type === "items") setGroupName("");
  }

  return (
    <div className="flex flex-col gap-2.5">
      {extra.map((b, k) => {
        const own = lines.map((l, idx) => ({ l, idx })).filter(({ l }) => blockOf(l, blocks, autoBlock) === b.id);
        const sub = own.reduce((s, { l }) => s + (l.kind === "note" ? 0 : (l.qty ?? 0) * ((en ? usdOf(l, fx) : l.unit_price) ?? 0)), 0);
        return (
          <div key={b.id} className="flex flex-col gap-2 rounded-lg border border-border p-2.5">
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
                <button type="button" className={cn(smallBtn, "hover:text-red-600")} disabled={busy} aria-label={`${TYPE_LABEL[b.type]} 삭제`}
                        title="묶음과 그 안의 항목을 뺍니다" onClick={() => remove(b.id)}>삭제</button>
              </div>
            </div>

            {(b.type === "items" || b.type === "option") && (
              <>
                <div className="overflow-x-auto">
                  {/* 칸 배치(사용자 2026-10-13: 어수선함 정리) — 머리글 한 줄, 수량·단위 따로, 규격은 넓고 내용만큼 늘어남, 칸은 위쪽 맞춤 */}
                  <table className="w-full min-w-[980px] table-fixed text-[12.5px]">
                    <colgroup>
                      <col className="w-[150px]" /><col /><col className="w-[64px]" /><col className="w-[64px]" />
                      <col className="w-[130px]" /><col className="w-[112px]" /><col className="w-[180px]" /><col className="w-[30px]" />
                    </colgroup>
                    <thead>
                      <tr className="whitespace-nowrap border-b border-border text-left text-[11.5px] text-foreground-subtle">
                        <th className="px-1.5 py-1.5 font-medium">{en ? "Commodity" : "품목"}</th>
                        <th className="px-1.5 py-1.5 font-medium">{en ? "Specifications" : "규격"}</th>
                        <th className="px-1.5 py-1.5 font-medium">{en ? "Qty" : "수량"}</th>
                        <th className="px-1.5 py-1.5 font-medium">{en ? "Unit" : "단위"}</th>
                        <th className="px-1.5 py-1.5 font-medium">{en ? "Price ($)" : "단가"}</th>
                        <th className="px-1.5 py-1.5 text-right font-medium">{en ? "Amount" : "금액"}</th>
                        <th className="px-1.5 py-1.5 font-medium">{en ? "Remark" : "비고"}</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border align-top">
                      {own.map(({ l, idx }) => {
                        const unit = en ? usdOf(l, fx) : l.unit_price;
                        if (isAuto(l)) return (                     // 물류비·인건비 — 아래 납품 방식 칸에서 바꾼다
                          <tr key={l.id} className="text-foreground-muted">
                            <td className="px-1.5 py-2">{l.commodity}</td><td className="px-1.5 py-2">{l.name}</td>
                            <td className="px-1.5 py-2">{l.qty}</td><td className="px-1.5 py-2">{l.unit ?? ""}</td>
                            <td className="px-1.5 py-2">{en ? usd(unit) : won(l.unit_price)}</td>
                            <td className="whitespace-nowrap px-1.5 py-2 text-right font-semibold">{en ? usd((l.qty ?? 0) * (unit ?? 0)) : won((l.qty ?? 0) * (l.unit_price ?? 0))}</td>
                            <td className="px-1.5 py-2 text-[11.5px]" colSpan={2}>납품 방식에서 설정</td>
                          </tr>
                        );
                        if (l.kind === "note") return (            // 서술형 줄 — 항목명 / 내용(가격 없음), 견적서엔 내용 가운데 정렬
                          <tr key={l.id} className="bg-emerald-500/[0.03]">
                            <td className="px-1.5 py-1.5">
                              <LineArea className={cn(inputCls, "min-h-8 text-[12.5px]")} value={l.commodity ?? ""} maxLength={40} aria-label="서술형 항목명" disabled={busy}
                                     placeholder={en ? "e.g. Weight" : "항목명 — 예: 무게"} onChange={(e) => setLine(idx, { commodity: e.target.value })} />
                            </td>
                            <td className="px-1.5 py-1.5" colSpan={6}>
                              <AutoTextarea className={cn(inputCls, "text-center text-[12.5px]")} value={l.name} maxLength={300}
                                            aria-label="서술형 내용" disabled={busy} placeholder={en ? "e.g. 55kg" : "내용 — 예: 55kg (여러 줄 가능, 가격 없음)"}
                                            onChange={(e) => setLine(idx, { name: e.target.value })} />
                            </td>
                            <td className="px-1 py-1.5 text-center">
                              <button type="button" aria-label="서술형 줄 빼기" disabled={busy} className="mt-1.5 text-foreground-subtle hover:text-red-600"
                                      onClick={() => onChange({ lines: lines.filter((_, j) => j !== idx) })}>
                                <Icon name="trash" className="h-4 w-4" />
                              </button>
                            </td>
                          </tr>
                        );
                        return (
                          <tr key={l.id}>
                            <td className="px-1.5 py-1.5">
                              <LineArea className={cn(inputCls, "min-h-8 text-[12.5px]")} value={l.commodity ?? ""} aria-label="품목" disabled={busy}
                                     placeholder={en ? "e.g. Charging Dock" : "예: 충전 스테이션"} onChange={(e) => setLine(idx, { commodity: e.target.value })} />
                            </td>
                            <td className="px-1.5 py-1.5">
                              <AutoTextarea className={cn(inputCls, "text-[12.5px]")} value={l.name} aria-label="규격" disabled={busy}
                                            placeholder={en ? "Model / spec — multiple lines\ne.g. CS-01\n- 24V, 2A" : "모델·사양 — 여러 줄 가능\n예: CS-01\n- 24V, 2A"}
                                            onChange={(e) => setLine(idx, { name: e.target.value })} />
                            </td>
                            <td className="px-1.5 py-1.5">
                              <input className={cn(inputCls, "h-8 text-center text-[12.5px]", !l.qty && "border-amber-500")} type="number" min={1}
                                     value={l.qty ?? ""} aria-label="수량" disabled={busy}
                                     onChange={(e) => setLine(idx, { qty: Math.max(0, Math.floor(Number(e.target.value) || 0)) || null })} />
                            </td>
                            <td className="px-1.5 py-1.5">
                              <input className={cn(inputCls, "h-8 text-center text-[12.5px]")} value={l.unit ?? ""} maxLength={8} aria-label="수량 단위" disabled={busy}
                                     placeholder="ea" title="수량 단위 — 예: MD·식·개월·대 (비우면 ea)" onChange={(e) => setLine(idx, { unit: e.target.value })} />
                            </td>
                            <td className="px-1.5 py-1.5">
                              {en ? (
                                <input key={`usd-${l.id}-${draftVer}`} className={cn(inputCls, "h-8 text-right text-[12.5px]", unit == null && "border-amber-500")}
                                       inputMode="decimal" defaultValue={l.usd ?? ""} aria-label="달러 단가" disabled={busy}
                                       placeholder={unit != null ? `${usd(unit)} (환율)` : "$ 단가"}
                                       onChange={(e) => { const v = Number(e.target.value.replace(/[,$\s]/g, "")); setLine(idx, { usd: v > 0 ? v : null }); }} />
                              ) : (
                                <input className={cn(inputCls, "h-8 text-right text-[12.5px]", l.unit_price == null && "border-amber-500")} inputMode="numeric"
                                       value={l.unit_price ?? ""} aria-label="단가" placeholder="단가" disabled={busy}
                                       onChange={(e) => setLine(idx, { unit_price: Number(e.target.value.replace(/,/g, "")) || null })} />
                              )}
                            </td>
                            <td className="whitespace-nowrap px-1.5 py-1.5 text-right font-semibold leading-8">
                              {b.type === "option" ? <span className="text-[11.5px] font-normal text-foreground-subtle">합계 제외</span>
                                : (en ? usd((l.qty ?? 0) * (unit ?? 0)) : won((l.qty ?? 0) * (l.unit_price ?? 0)))}
                            </td>
                            <td className="px-1.5 py-1.5">
                              <div className="flex flex-col gap-1">
                                <LineArea className={cn(inputCls, "min-h-8 text-[12px]")} value={l.remark ?? ""} aria-label="비고" disabled={busy}
                                       placeholder={en ? "Remark" : "비고"} onChange={(e) => setLine(idx, { remark: e.target.value })} />
                                {photoCell?.(l)}
                              </div>
                            </td>
                            <td className="px-1 py-1.5 text-center">
                              <button type="button" aria-label={`${l.name || "항목"} 빼기`} disabled={busy} className="mt-1.5 text-foreground-subtle hover:text-red-600"
                                      onClick={() => onChange({ lines: lines.filter((_, j) => j !== idx) })}>
                                <Icon name="trash" className="h-4 w-4" />
                              </button>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                <div className="flex flex-wrap items-center gap-2 text-[12px]">
                  <button type="button" disabled={busy} onClick={() => addLine(b.id)}
                          className="inline-flex items-center gap-1 rounded border border-border px-2 py-0.5 text-foreground-muted hover:text-foreground">
                    <Icon name="plus" className="h-3.5 w-3.5" /> 항목 추가
                  </button>
                  {pricePicker?.((p) => onChange({ lines: [...lines, { id: `new${Date.now()}`, kind: "extra", key: "extra",
                    name: `${p.model} ${p.item}`.slice(0, 60), commodity: "", qty: 1, unit_price: p.unit_price, remark: "", block: b.id }] }))}
                  {b.type === "items" && (
                    <button type="button" disabled={busy} onClick={() => addLine(b.id, "note")}
                            title="항목명 / 내용만 적는 줄(가격 없음) — 사양·설명용"
                            className="inline-flex items-center gap-1 rounded border border-border px-2 py-0.5 text-foreground-muted hover:text-foreground">
                      <Icon name="plus" className="h-3.5 w-3.5" /> 서술형 줄
                    </button>
                  )}
                  <span className="ml-auto text-foreground-subtle">
                    {b.type === "option" ? "견적서엔 단가만, 공급가액 칸은 비움 · 합계·영업 건 금액에 안 들어감 · 소계 없음"
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

      <div className="flex flex-col gap-1.5 rounded-lg border border-dashed border-accent/50 p-2.5">
        <span className="text-[12px] font-semibold text-foreground">+ 묶음 추가</span>
        <div className="flex flex-wrap items-center gap-1.5">
          <input className={cn(inputCls, "min-w-0 flex-1 basis-48 py-1 text-[12.5px]")} value={groupName} maxLength={60} disabled={busy}
                 aria-label="새 묶음 이름" placeholder={en ? "묶음 이름 — 예: Maintenance" : "묶음 이름 — 예: 설치 및 시운전, 유지보수, 방역 로봇 주요 사양"}
                 onChange={(e) => setGroupName(e.target.value)}
                 onKeyDown={(e) => { if (e.key === "Enter" && !e.nativeEvent.isComposing) { e.preventDefault(); addBlock("items"); } }} />
          <button type="button" disabled={busy || !groupName.trim() || blocks.length >= 12} onClick={() => addBlock("items")}
                  className="rounded-md border border-accent/60 px-2.5 py-1 text-[12.5px] font-semibold text-accent hover:bg-accent/5 disabled:opacity-40">
            사용자 지정 묶음 만들기
          </button>
          <button type="button" disabled={busy || hasOption || blocks.length >= 12} onClick={() => addBlock("option")}
                  title={hasOption ? "< OPTION > 묶음은 하나만 둡니다" : "단가만 보이고 합계에서 빠지는 묶음"}
                  className="rounded-md border border-amber-500/60 px-2.5 py-1 text-[12.5px] font-semibold text-amber-700 hover:bg-amber-500/5 disabled:opacity-40 dark:text-amber-300">
            &lt; OPTION &gt; 묶음
          </button>
          <button type="button" disabled={busy || !!nego} onClick={() => onChange({ nego: { mode: "amount", value: null, label: "" } })}
                  className="rounded-md border border-red-500/50 px-2.5 py-1 text-[12.5px] font-semibold text-red-700 hover:bg-red-500/5 disabled:opacity-40 dark:text-red-300">
            NEGO(할인)
          </button>
        </div>
        <span className="text-[11px] text-foreground-subtle">이름을 적고 만들면 그 묶음이 생깁니다 — 안에 항목(가격 있음)이나 서술형 줄(항목명 / 내용)을 넣으세요.</span>
      </div>

      {children}

      {nego && (
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
          <button type="button" className={cn(smallBtn, "hover:text-red-600")} disabled={busy} onClick={() => onChange({ nego: null })}>삭제</button>
          <span className="basis-full text-[11.5px] text-foreground-subtle">
            Total 바로 위에 &quot;NEGO&quot; 줄로 들어갑니다. %는 옵션을 뺀 합계 기준으로 계산해 견적서엔 금액으로 나옵니다. 값을 비우면 빠집니다.
          </span>
        </div>
      )}

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
              <button type="button" aria-label={`주석 ${i + 1} 빼기`} disabled={busy} className="pt-1.5 text-foreground-subtle hover:text-red-600"
                      onClick={() => onChange({ foot_notes: footNotes.filter((_, j) => j !== i) })}>
                <Icon name="trash" className="h-4 w-4" />
              </button>
            </div>
          ))}
          <button type="button" disabled={busy || footNotes.length >= 8} onClick={() => onChange({ foot_notes: [...footNotes, ""] })}
                  className="inline-flex w-fit items-center gap-1 rounded border border-border px-2 py-0.5 text-foreground-muted hover:text-foreground disabled:opacity-40">
            <Icon name="plus" className="h-3.5 w-3.5" /> 주석 추가
          </button>
        </div>
      </div>
    </div>
  );
}
