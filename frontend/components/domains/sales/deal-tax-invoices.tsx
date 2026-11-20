"use client";

// 영업 건 상세 — 세금계산서 회차(사용자 2026-11-20). 출하 칸 바로 위.
// 선금·중도금(중도금1·2… 늘릴 수 있음)·잔금 % (합 100) 로 공급가액을 나누고, 금액 오른쪽 달력에서 발행일을 골라 [확인].
// 처음엔 결제 조건 % 를 그대로 가져와 보여 준다(저장 전 초안). 마지막 회차(잔금)까지 확인하면 그 발행일이 매출일
// (영업 건 관리 위 분기별 매출액이 이 날짜 기준).

import { useState } from "react";
import { askConfirm } from "@/components/shared/ui/confirm-host";
import { Icon } from "@/components/shared/ui/icon";
import { INPUT, Muted, shortDate, won } from "@/components/domains/sales/deal-format";
import type { DealRow, DealTaxRow } from "@/lib/shared/sales-deals";
import { cn } from "@/lib/shared/utils";

type Act = (path: string, body: unknown) => Promise<boolean>;
type EditRow = { key: number; label: string; pct: string };

let seq = 0;
/** 중도금이 둘 이상이면 중도금1·중도금2…, 하나면 '중도금' */
function renumber(rows: EditRow[]): EditRow[] {
  const mids = rows.filter((r) => /^중도금\d*$/.test(r.label.trim()));
  let n = 0;
  return rows.map((r) => (/^중도금\d*$/.test(r.label.trim()) ? { ...r, label: mids.length > 1 ? `중도금${++n}` : "중도금" } : r));
}

function TaxEditor({ rows, busy, onSave, onCancel }: {
  rows: DealTaxRow[]; busy: boolean; onSave: (terms: { label: string; pct: number }[]) => Promise<void>; onCancel: () => void;
}) {
  const start: EditRow[] = rows.length
    ? rows.map((r) => ({ key: ++seq, label: r.label, pct: String(r.pct ?? "") }))
    : [{ key: ++seq, label: "선금", pct: "" }, { key: ++seq, label: "중도금", pct: "" }, { key: ++seq, label: "잔금", pct: "" }];
  const [list, setList] = useState<EditRow[]>(start);
  const sum = list.reduce((s, r) => s + (Number(r.pct) || 0), 0);
  const bad = list.some((r) => !r.label.trim() || !(Number(r.pct) > 0)) || Math.abs(sum - 100) > 1e-6;
  const set = (key: number, patch: Partial<EditRow>) => setList((xs) => xs.map((x) => (x.key === key ? { ...x, ...patch } : x)));
  // [+ 중도금 추가] — 잔금(마지막 줄) 바로 앞에
  const addMid = () => setList((xs) => renumber([...xs.slice(0, Math.max(xs.length - 1, 0)), { key: ++seq, label: "중도금", pct: "" }, ...xs.slice(-1)]));
  return (
    <div className="mb-3 space-y-2 rounded-xl border border-accent/40 bg-accent/[0.03] p-3 text-[12.5px]" data-editing="true">
      <p className="text-[12px] font-semibold text-foreground-muted">세금계산서 회차 — 구분과 %(합 100). 중도금을 나눠 끊으면 [+ 중도금 추가]</p>
      {list.map((r) => (
        <div key={r.key} className="flex items-center gap-2">
          <input className={cn(INPUT, "h-8 w-32")} value={r.label} maxLength={20} aria-label="회차 구분"
                 onChange={(e) => set(r.key, { label: e.target.value })} />
          <input className={cn(INPUT, "h-8 w-20 text-right tabular-nums", !(Number(r.pct) > 0) && "border-red-400")} value={r.pct}
                 inputMode="decimal" aria-label={`${r.label} 비율`} placeholder="%" onChange={(e) => set(r.key, { pct: e.target.value })} />
          <span className="text-foreground-muted">%</span>
          <button type="button" aria-label={`${r.label} 회차 지우기`} disabled={list.length <= 1}
                  onClick={() => setList((xs) => renumber(xs.filter((x) => x.key !== r.key)))}
                  className="grid h-8 w-8 place-items-center rounded-md text-foreground-subtle hover:bg-red-500/10 hover:text-red-600 disabled:opacity-30">
            <Icon name="trash" className="h-4 w-4" /></button>
        </div>
      ))}
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className="text-[12px] font-semibold text-accent hover:underline" onClick={addMid}>+ 중도금 추가</button>
        <span className={cn("ml-auto tabular-nums", Math.abs(sum - 100) > 1e-6 ? "font-semibold text-red-600" : "text-foreground-muted")}>합 {sum}% / 100%</span>
        <button type="button" className="h-8 rounded-lg px-3 text-[12.5px] text-foreground-muted hover:bg-foreground/[0.06]" disabled={busy} onClick={onCancel}>취소</button>
        <button type="button" className="h-8 rounded-lg bg-accent px-3.5 text-[12.5px] font-semibold text-white disabled:opacity-40" disabled={busy || bad}
                onClick={() => void onSave(list.map((r) => ({ label: r.label.trim(), pct: Number(r.pct) })))}>저장</button>
      </div>
    </div>
  );
}

export function TaxInvoiceBlock({ deal, busy, act }: { deal: DealRow; busy: boolean; act: Act }) {
  const tax = deal.flow?.tax;
  const [editing, setEditing] = useState(false);
  const [days, setDays] = useState<Record<number, string>>({});
  if (!tax) return null;
  const locked = tax.rows.some((r) => r.confirmed);
  const dayOf = (r: DealTaxRow) => days[r.index] ?? r.date ?? "";
  return (
    <section className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-[12px] font-semibold uppercase tracking-[0.1em] text-foreground-subtle">세금계산서 (공급가액)</h3>
        {tax.done && <span className="rounded-full bg-emerald-500/12 px-2 py-0.5 text-[11.5px] font-semibold text-emerald-700 dark:text-emerald-300">✓ 모두 발행</span>}
        {!editing && !deal.dropped && (
          <button type="button" disabled={busy || locked} title={locked ? "확인한 회차가 있으면 [확인 취소] 뒤에 고칩니다" : undefined}
                  onClick={() => setEditing(true)}
                  className="ml-auto h-8 rounded-lg border border-border px-3 text-[12.5px] font-semibold hover:border-accent hover:text-accent disabled:opacity-40">회차 고치기</button>
        )}
      </div>
      {editing && (
        <TaxEditor rows={tax.rows} busy={busy} onCancel={() => setEditing(false)}
                   onSave={async (terms) => { if (await act("tax-terms", { terms })) setEditing(false); }} />
      )}
      {!tax.rows.length ? (
        <Muted>결제 조건이 없어 세금계산서 회차가 비어 있습니다 — [회차 고치기]에서 선금·중도금·잔금 %를 정해 주세요.</Muted>
      ) : (
        <table className="w-full border-collapse text-[12.5px]">
          <thead>
            <tr className="border-b border-border text-left text-[11.5px] text-foreground-muted">
              <th className="px-3 py-1.5 font-semibold">회차</th>
              <th className="px-3 py-1.5 text-right font-semibold">공급가액</th>
              <th className="px-3 py-1.5 font-semibold">발행일</th>
              <th className="px-3 py-1.5 font-semibold">확인</th>
            </tr>
          </thead>
          <tbody>
            {tax.rows.map((r) => (
              <tr key={r.index} className={cn("border-b border-border/60 last:border-0", r.confirmed && "bg-emerald-500/[0.06]")}>
                <td className="whitespace-nowrap px-3 py-1.5"><b>{r.label} {r.pct}%</b></td>
                <td className="whitespace-nowrap px-3 py-1.5 text-right tabular-nums">
                  <b>{won(r.amount)}원</b>
                  {r.vat > 0 && <span className="block text-[11px] text-foreground-subtle">부가세 포함 {won(r.total)}원</span>}
                </td>
                {/* 발행일 — 금액 오른쪽 달력(필수) */}
                <td className="whitespace-nowrap px-3 py-1.5">
                  {r.confirmed ? <span className="tabular-nums">{r.date}</span> : (
                    <input type="date" value={dayOf(r)} aria-label={`${r.label} 세금계산서 발행일`} disabled={busy || deal.dropped}
                           onClick={(e) => { try { e.currentTarget.showPicker?.(); } catch { /* 달력을 못 띄우는 브라우저 */ } }}
                           onChange={(e) => setDays((x) => ({ ...x, [r.index]: e.target.value }))}
                           className={cn(INPUT, "h-8 w-[150px] cursor-pointer", !dayOf(r) && "border-amber-500/60")} />
                  )}
                </td>
                <td className="whitespace-nowrap px-3 py-1.5">
                  {r.confirmed ? (
                    <span className="flex items-center gap-2 text-emerald-700 dark:text-emerald-300">
                      ✓ 확인{r.by ? ` · ${r.by}` : ""}
                      <button type="button" disabled={busy}
                              onClick={() => void askConfirm({ title: `${r.label} 세금계산서 확인을 취소할까요?`, message: "매출일이 잡혀 있었으면 함께 풀립니다. 변경 이력에 남습니다.", confirmLabel: "확인 취소" })
                                .then((ok) => { if (ok) void act("tax-unconfirm", { index: r.index }); })}
                              className="text-[11.5px] text-foreground-subtle hover:text-red-600 hover:underline">확인 취소</button>
                    </span>
                  ) : (
                    <button type="button" disabled={busy || deal.dropped || !dayOf(r)} title={dayOf(r) ? undefined : "발행일을 먼저 골라 주세요"}
                            onClick={() => {
                              const lastOne = tax.rows.every((x) => x.confirmed || x.index === r.index);
                              void askConfirm({ title: `${r.label} 세금계산서 발행을 확인할까요?`, confirmLabel: "확인",
                                                message: `발행일 ${dayOf(r)} · 공급가액 ${won(r.amount)}원 (VAT 별도)`
                                                  + (lastOne ? ` — 마지막 회차라 ${dayOf(r)}이 이 건의 매출일이 됩니다.` : "") })
                                .then((ok) => { if (ok) void act("tax-confirm", { index: r.index, date: dayOf(r) }); });
                            }}
                            className="h-7 rounded-md bg-accent px-2.5 text-[12px] font-semibold text-white hover:opacity-90 disabled:opacity-40">확인</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="text-[11.5px] text-foreground-subtle">
        {tax.done && tax.revenue_date
          ? <>매출일 <b className="text-foreground">{shortDate(tax.revenue_date)}</b> — 마지막 회차 세금계산서 발행일로 분기별 매출액에 들어갑니다.</>
          : `마지막 회차(${tax.rows.at(-1)?.label ?? "잔금"})까지 확인하면 그 발행일로 매출이 잡힙니다(수주일과 상관없이).`}
        {!tax.saved && tax.rows.length > 0 && " 지금 회차는 결제 조건 % 를 가져온 것 — 중도금을 나눠 끊으면 [회차 고치기]."}
      </p>
    </section>
  );
}
