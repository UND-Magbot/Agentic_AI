// 제품 시리얼 등록(사용자 2026-10-19, 영업팀 요청 20번) — 영업팀이 최종 고객과 협의된 견적서 판 기준으로 부품마다 직접 적는다.
// 입구 두 곳: 영업 건 견적서 카드의 [제품 시리얼 등록](경고창 먼저) · [AI 와 수주 진행 →](수주 판에 빈 칸이 있으면 이동을 묻는다).
// 빈 칸 = 미등록. 시리얼이 없는 품목은 '특주'처럼 적는다(숫자 없는 글 = 시리얼 없음). 출하 기록 전에 다 적어야 한다.
"use client";

import { useEffect, useMemo, useState } from "react";
import { Icon } from "@/components/shared/ui/icon";
import { useDragMove } from "@/components/shared/ui/use-drag-move";
import { Muted, won } from "@/components/domains/sales/deal-format";
import { PreviewDialog } from "@/components/domains/sales/quote-workspace";
import type { DealQuoteDoc, DealRow, IssueUnits } from "@/lib/shared/sales-deals";
import { UNIT_STATES } from "@/lib/shared/unit-states";
import { cn } from "@/lib/shared/utils";

const BACKDROP = "anim-fade-in fixed inset-0 z-[60] grid place-items-center bg-black/40 px-4";
const PANEL = "anim-fade-in-up w-full rounded-2xl border border-border bg-surface-raised p-5 text-[13px] shadow-[0_30px_70px_-30px_rgba(15,30,70,0.55)]";
const CELL = "h-8 w-full rounded-md border border-border bg-surface px-2 text-[12.5px] outline-none focus:border-accent disabled:opacity-60";
const MAKERS = ["UND", "Dobot", "Realman", "Doosan", "Universal Robots"];

/** 이 견적서에서 시리얼을 적을 판 — 수주 중인 판이 이 견적서에 있으면 그 판, 없으면 가장 최근 판. */
function defaultRevision(deal: DealRow, quote: DealQuoteDoc): number {
  const wonIssue = deal.won ? quote.issues.find((h) => (h.issue_no ?? h.quote_no) === deal.display_no) : undefined;
  return wonIssue?.revision ?? Math.max(...quote.issues.map((h) => h.revision));
}

/** [제품 시리얼 등록] 경고창 — 누를 수는 있지만 최종 협의된 견적서인지 한 번 확인(사용자 2026-10-19). */
export function SerialConfirmDialog({ deal, quote, onCancel, onConfirm }: {
  deal: DealRow; quote: DealQuoteDoc; onCancel: () => void; onConfirm: (revision: number) => void;
}) {
  const [rev, setRev] = useState(() => defaultRevision(deal, quote));
  const issue = quote.issues.find((h) => h.revision === rev) ?? quote.issues[quote.issues.length - 1];
  const no = issue?.issue_no ?? issue?.quote_no ?? quote.quote_no ?? "견적서";
  const wonNo = deal.won ? deal.display_no : null;
  const wonHere = quote.issues.some((h) => (h.issue_no ?? h.quote_no) === wonNo);
  return (
    <div role="alertdialog" aria-modal="true" aria-labelledby="serial-confirm-title" className={BACKDROP}
         onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel(); }}>
      <div className={cn(PANEL, "max-w-[460px]")}>
        <h3 id="serial-confirm-title" className="flex items-center gap-2 text-[15px] font-semibold text-amber-700 dark:text-amber-300">
          <span aria-hidden>⚠</span> 제품 시리얼 번호 등록
        </h3>
        <p className="mt-2 leading-relaxed text-foreground">
          시리얼 번호는 <b>고객과 최종 협의된 견적서</b> 기준으로 등록하셔야 합니다. 이 견적서가 맞나요?
        </p>
        <div className="mt-3 rounded-xl border border-border bg-surface p-3">
          <div className="flex flex-wrap items-center gap-2">
            <b className="font-mono">{no}</b>
            {quote.issues.length > 1 ? (
              <select value={rev} onChange={(e) => setRev(Number(e.target.value))} aria-label="견적서 판"
                      className="h-7 rounded-md border border-border bg-surface px-1.5 text-[12px]">
                {[...quote.issues].reverse().map((h) => (
                  <option key={h.revision} value={h.revision}>v{h.revision + 1} · {h.issue_no ?? h.quote_no ?? ""}</option>
                ))}
              </select>
            ) : <span className="text-[12px] text-foreground-muted">v{(issue?.revision ?? 0) + 1}</span>}
          </div>
          {issue && (
            <p className="mt-1 text-[12px] text-foreground-muted">
              발행 {issue.issued_at.slice(0, 10)} · 합계 {issue.currency === "USD" ? `$${issue.total.toLocaleString("en-US")}` : `${won(issue.total)}원`}
            </p>
          )}
        </div>
        {wonNo && (!wonHere || (issue?.issue_no ?? issue?.quote_no) !== wonNo) && (
          <p className="mt-2 rounded-lg bg-red-500/10 px-3 py-2 text-[12.5px] text-red-700 dark:text-red-300">
            수주 중인 견적서는 <b className="font-mono">{wonNo}</b> 입니다. 다른 판에 등록하면 출하 확인에 반영되지 않습니다.
          </p>
        )}
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" onClick={onCancel}
                  className="h-9 rounded-lg border border-border px-4 text-[13px] font-semibold text-foreground-muted hover:text-foreground">취소</button>
          <button type="button" autoFocus onClick={() => onConfirm(issue?.revision ?? rev)}
                  className="h-9 rounded-lg bg-accent px-4 text-[13px] font-semibold text-white hover:brightness-105">맞습니다, 등록</button>
        </div>
      </div>
    </div>
  );
}

/** [AI 와 수주 진행 →] 을 눌렀는데 수주 판에 빈 시리얼이 있을 때(사용자 2026-10-19). */
export function SerialRequiredDialog({ deal, onMove, onLater, onCancel }: {
  deal: DealRow; onMove: () => void; onLater: () => void; onCancel: () => void;
}) {
  const u = deal.units;
  if (!u) return null;
  return (
    <div role="alertdialog" aria-modal="true" aria-labelledby="serial-required-title" className={BACKDROP}
         onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel(); }}>
      <div className={cn(PANEL, "max-w-[440px]")}>
        <h3 id="serial-required-title" className="text-[15px] font-semibold text-foreground">시리얼 번호를 등록하셔야 합니다</h3>
        <p className="mt-2 leading-relaxed text-foreground">
          <b className="font-mono">{u.issue_no}</b> 기준 제품 {u.total}개 중 <b>{u.done}개</b>만 등록되어 있습니다. 등록 화면으로 이동하시겠습니까?
        </p>
        <p className="mt-1 text-[12px] text-foreground-muted">출하 기록 전에는 꼭 등록해야 합니다. 시리얼이 없는 품목은 &lsquo;특주&rsquo;처럼 적으면 됩니다.</p>
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" onClick={onLater}
                  className="h-9 rounded-lg border border-border px-4 text-[13px] font-semibold text-foreground-muted hover:text-foreground">나중에 하고 수주 진행</button>
          <button type="button" autoFocus onClick={onMove}
                  className="h-9 rounded-lg bg-accent px-4 text-[13px] font-semibold text-white hover:brightness-105">이동</button>
        </div>
      </div>
    </div>
  );
}

type Draft = { key: string; id?: number; part_name: string; serial: string; maker: string; purpose: string; months: string; note: string;
               shipped?: string | null };
type Line = IssueUnits["lines"][number];

let seq = 0;
// 새 칸 상태 — 대여 건은 R(대여), 판매 건은 S(판매)(사용자 2026-10-22)
const blank = (ln: Line, purpose = "S"): Draft => ({ key: `n${++seq}`, part_name: ln.part, serial: "", maker: "UND", purpose,
                                      months: ln.suggest_months == null ? "" : String(ln.suggest_months), note: "" });

/** 줄마다 등록한 부품 + 수량까지 빈 칸 */
function drafts(v: IssueUnits): Record<number, Draft[]> {
  return Object.fromEntries(v.lines.map((ln) => {
    const have: Draft[] = ln.units.map((u) => ({ key: `u${u.id}`, id: u.id, part_name: u.part_name, serial: u.serial, maker: u.maker,
                                                  purpose: u.purpose, months: u.warranty_months == null ? "" : String(u.warranty_months),
                                                  note: u.note, shipped: u.warranty_start }));
    return [ln.line_no, [...have, ...Array.from({ length: Math.max(0, ln.qty - have.length) }, () => blank(ln, v.biz === "rental" ? "R" : "S"))]];
  }));
}

/** 시리얼 등록 화면 — 견적서 판 하나의 품목 줄마다 수량만큼 칸. 줄바꿈으로 여러 개를 붙여 넣으면 아래 칸으로 나눠 들어간다. */
export function UnitRegisterDialog({ dealId, quoteId, revision, onClose, onSaved }: {
  dealId: number; quoteId: number; revision: number; onClose: () => void; onSaved: () => void;
}) {
  const [view, setView] = useState<IssueUnits | null>(null);
  const [rows, setRows] = useState<Record<number, Draft[]>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState(false);
  const drag = useDragMove();
  // 견적서 보기가 떠 있으면 Esc 는 그것만 닫는다(영업 건 상세의 Esc 가 등록 창까지 닫지 않게 먼저 잡음)
  useEffect(() => {
    if (!preview) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      setPreview(false);
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [preview]);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let alive = true;
    fetch(`/api/sales-deals/${dealId}/units?quote_id=${quoteId}&revision=${revision}`, { cache: "no-store" })
      .then(async (r) => {
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(typeof d?.detail === "string" ? d.detail : "시리얼 등록 화면을 열지 못했습니다.");
        return d as IssueUnits;
      })
      .then((v) => { if (alive) { setView(v); setRows(drafts(v)); } })
      .catch((e) => { if (alive) setError(e instanceof Error ? e.message : "시리얼 등록 화면을 열지 못했습니다."); });
    return () => { alive = false; };
  }, [dealId, quoteId, revision]);

  const count = useMemo(() => {
    if (!view) return { total: 0, done: 0 };
    let total = 0, done = 0;
    for (const ln of view.lines) {
      total += ln.qty;
      done += Math.min(ln.qty, (rows[ln.line_no] ?? []).filter((d) => d.serial.trim()).length);
    }
    return { total, done };
  }, [view, rows]);

  const setRow = (lineNo: number, key: string, patch: Partial<Draft>) =>
    setRows((r) => ({ ...r, [lineNo]: r[lineNo].map((d) => (d.key === key ? { ...d, ...patch } : d)) }));

  /** 여러 줄을 붙여 넣으면 그 칸부터 아래로 하나씩(모자라면 칸을 더 만든다) */
  const pasteMany = (ln: Line, key: string, text: string): boolean => {
    const vals = text.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
    if (vals.length < 2) return false;
    setRows((r) => {
      const list = [...r[ln.line_no]];
      let i = list.findIndex((d) => d.key === key);
      for (const v of vals) {
        while (i < list.length && list[i].shipped) i++;
        if (i >= list.length) list.push(blank(ln, view?.biz === "rental" ? "R" : "S"));
        list[i] = { ...list[i], serial: v };
        i++;
      }
      return { ...r, [ln.line_no]: list };
    });
    return true;
  };

  const save = async () => {
    if (!view) return;
    setBusy(true);
    setError(null);
    try {
      const body = {
        quote_id: quoteId, revision,
        lines: view.lines.map((ln) => ({
          line_no: ln.line_no,
          units: (rows[ln.line_no] ?? []).filter((d) => d.serial.trim()).map((d) => ({
            id: d.id ?? null, part_name: d.part_name, serial: d.serial, maker: d.maker, purpose: d.purpose,
            warranty_months: d.months.trim() === "" ? null : Number(d.months), note: d.note,
          })),
        })),
      };
      const r = await fetch(`/api/sales-deals/${dealId}/units`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof d?.detail === "string" ? d.detail : Array.isArray(d?.detail) ? "입력값을 확인해 주세요(무상 기간은 0~120개월)." : "저장하지 못했습니다.");
      setView(d as IssueUnits);
      setRows(drafts(d as IssueUnits));
      setSaved(true);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "저장하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div role="dialog" aria-modal="true" aria-labelledby="unit-reg-title" className={BACKDROP}
         onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}>
      {preview && (
        <div className="relative z-[70]" onMouseDown={(e) => e.stopPropagation()}>
          <PreviewDialog url={`/api/sales-deals/${dealId}/quote-file?quote=${quoteId}&revision=${revision}&fmt=pdf`}
                         title="견적서" sub={view ? `${view.issue_no ?? ""} · v${view.revision + 1} · 발행본 그대로` : `v${revision + 1} · 발행본 그대로`}
                         onClose={() => setPreview(false)} movable />
        </div>
      )}
      <div className={cn(PANEL, "flex max-h-[90vh] max-w-[1080px] flex-col")} style={drag.style}>
        {/* 머리를 잡아 끌어 옮기기 — 견적서 보기와 나란히 놓고 맞춰 볼 수 있게(사용자 2026-10-19) */}
        <div className={cn("flex items-start justify-between gap-3", drag.handleClass)} {...drag.handle}
             title="잡고 끌어 옮기기 · 두 번 누르면 가운데로">
          <div>
            <div className="flex flex-wrap items-center gap-3">
              <h2 id="unit-reg-title" className="text-[15px] font-semibold text-foreground">제품 시리얼 번호 등록</h2>
              {/* 견적서 작성 화면은 최신 편집본이라 수주 판과 다를 수 있다 — 이 판의 발행본을 그대로 띄워 맞춰 본다(사용자 2026-10-19) */}
              <button type="button" onClick={() => setPreview(true)}
                      className="inline-flex items-center gap-1 rounded-md border border-accent/40 px-2 py-0.5 text-[12px] font-semibold text-accent hover:bg-accent/10">
                <Icon name="eye" className="h-3.5 w-3.5" /> 이 판 견적서 보기
              </button>
            </div>
            <p className="mt-1 text-[12.5px] text-foreground-muted">
              {view ? <><b className="font-mono text-foreground">{view.issue_no}</b> v{view.revision + 1} · {view.customer ?? "고객사 없음"} — </> : null}
              부품마다 적습니다. 시리얼이 없는 품목은 &lsquo;특주&rsquo;처럼 적고, 빈 칸은 미등록으로 봅니다.
            </p>
          </div>
          <button type="button" onClick={onClose} disabled={busy} aria-label="닫기"
                  className="grid h-7 w-7 shrink-0 place-items-center rounded-sm text-foreground-subtle hover:bg-foreground/5 hover:text-foreground">
            <Icon name="x" className="h-4 w-4" />
          </button>
        </div>

        {view && (
          <div className="mt-2 flex flex-wrap items-center gap-2 text-[12.5px]">
            <span className={cn("rounded-full px-2.5 py-0.5 font-semibold",
              count.done >= count.total ? "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300" : "bg-amber-500/12 text-amber-700 dark:text-amber-300")}>
              {count.total}개 중 {count.done}개 적음
            </span>
            {view.is_won_issue && <span className="rounded bg-blue-500/12 px-1.5 text-[11.5px] font-semibold text-blue-700 dark:text-blue-300">수주 중인 판</span>}
            {!view.is_won_issue && view.won_issue_no && (
              <span className="text-red-700 dark:text-red-300">수주 중인 판은 <b className="font-mono">{view.won_issue_no}</b> — 출하 확인은 그 판 기준입니다.</span>
            )}
            {view.other_issues.length > 0 && (
              <span className="text-foreground-subtle">다른 판({view.other_issues.join(", ")})에도 등록한 시리얼이 있습니다.</span>
            )}
          </div>
        )}

        <div className="scroll-thin mt-3 min-h-0 flex-1 overflow-y-auto">
          {!view && !error && <Muted>불러오는 중…</Muted>}
          {view && view.lines.length === 0 && <Muted>이 견적서 판에는 시리얼을 적을 제품 줄이 없습니다(설치비·물류비 등만 있음).</Muted>}
          {view?.lines.map((ln) => {
            const list = rows[ln.line_no] ?? [];
            const filled = list.filter((d) => d.serial.trim()).length;
            return (
              <section key={ln.line_no} className="mb-3 rounded-xl border border-border p-3">
                <div className="mb-2 flex flex-wrap items-baseline gap-2">
                  <b className="whitespace-pre-line text-[13px]">{ln.name}</b>
                  <span className="text-[12px] text-foreground-muted">수량 {ln.qty}</span>
                  <span className={cn("text-[12px] font-semibold", filled >= ln.qty ? "text-emerald-700 dark:text-emerald-300" : "text-amber-700 dark:text-amber-300")}>
                    {Math.min(filled, ln.qty)}/{ln.qty}
                  </span>
                  {ln.suggest_months != null && <span className="text-[11.5px] text-foreground-subtle">무상 기간 기본 {ln.suggest_months}개월</span>}
                </div>
                <div className="grid grid-cols-[minmax(140px,1.4fr)_minmax(150px,1.4fr)_minmax(90px,0.8fr)_112px_86px_minmax(100px,1fr)_28px] items-center gap-1.5 text-[11.5px] text-foreground-muted max-md:hidden">
                  <span>부품명</span><span>시리얼</span><span>제조사</span><span>상태</span><span>무상(개월)</span><span>메모</span><span />
                </div>
                {list.map((d, i) => (
                  <div key={d.key} className="mt-1 grid grid-cols-[minmax(140px,1.4fr)_minmax(150px,1.4fr)_minmax(90px,0.8fr)_112px_86px_minmax(100px,1fr)_28px] items-center gap-1.5 max-md:grid-cols-2">
                    <input className={CELL} value={d.part_name} maxLength={120} aria-label={`${i + 1}번 부품명`} disabled={busy}
                           onChange={(e) => setRow(ln.line_no, d.key, { part_name: e.target.value })} />
                    <input className={cn(CELL, "font-mono")} value={d.serial} maxLength={80} placeholder="시리얼 또는 특주" aria-label={`${i + 1}번 시리얼`}
                           disabled={busy}
                           onPaste={(e) => { if (pasteMany(ln, d.key, e.clipboardData.getData("text"))) e.preventDefault(); }}
                           onChange={(e) => setRow(ln.line_no, d.key, { serial: e.target.value })} />
                    <input className={CELL} value={d.maker} maxLength={40} list="unit-makers" aria-label={`${i + 1}번 제조사`} disabled={busy}
                           onChange={(e) => setRow(ln.line_no, d.key, { maker: e.target.value })} />
                    <select className={cn(CELL, "px-1")} value={d.purpose} aria-label={`${i + 1}번 상태`} disabled={busy}
                            onChange={(e) => setRow(ln.line_no, d.key, { purpose: e.target.value })}>
                      {UNIT_STATES.map((s) => <option key={s.code} value={s.code} title={s.en}>{s.code} · {s.label}</option>)}
                    </select>
                    <input className={CELL} value={d.months} inputMode="numeric" placeholder="미정" aria-label={`${i + 1}번 무상 기간(개월)`} disabled={busy}
                           onChange={(e) => setRow(ln.line_no, d.key, { months: e.target.value.replace(/\D/g, "").slice(0, 3) })} />
                    <input className={CELL} value={d.note} maxLength={300} aria-label={`${i + 1}번 메모`} disabled={busy}
                           placeholder={d.shipped ? `출하 ${d.shipped}` : ""}
                           onChange={(e) => setRow(ln.line_no, d.key, { note: e.target.value })} />
                    <button type="button" disabled={busy || !!d.shipped} aria-label={`${i + 1}번 칸 지우기`}
                            title={d.shipped ? "출하한 부품은 지울 수 없습니다" : "칸 지우기"}
                            onClick={() => setRows((r) => ({ ...r, [ln.line_no]: r[ln.line_no].filter((x) => x.key !== d.key) }))}
                            className="grid h-7 w-7 place-items-center rounded text-foreground-subtle hover:bg-foreground/5 hover:text-foreground disabled:opacity-30">
                      <Icon name="trash" className="h-3.5 w-3.5" />
                    </button>
                  </div>
                ))}
                <button type="button" disabled={busy} onClick={() => setRows((r) => ({ ...r, [ln.line_no]: [...(r[ln.line_no] ?? []), blank(ln, view?.biz === "rental" ? "R" : "S")] }))}
                        className="mt-1.5 inline-flex items-center gap-1 rounded border border-border px-2 py-0.5 text-[12px] text-foreground-muted hover:text-foreground">
                  <Icon name="plus" className="h-3.5 w-3.5" /> 부품 추가 (한 줄을 여러 부품으로 나눌 때)
                </button>
              </section>
            );
          })}
          <datalist id="unit-makers">{MAKERS.map((m) => <option key={m} value={m} />)}</datalist>
        </div>

        {error && <p className="mt-2 rounded-lg bg-red-500/10 px-3 py-2 text-[12.5px] text-red-700 dark:text-red-300">{error}</p>}
        {saved && !error && <p className="mt-2 text-[12.5px] text-emerald-700 dark:text-emerald-300">저장했습니다.</p>}
        <div className="mt-3 flex items-center justify-end gap-2">
          <span className="mr-auto text-[11.5px] text-foreground-subtle">여러 시리얼을 줄바꿈으로 복사해 붙여 넣으면 아래 칸으로 나눠 들어갑니다.</span>
          <button type="button" onClick={onClose} disabled={busy}
                  className="h-9 rounded-lg border border-border px-4 text-[13px] font-semibold text-foreground-muted hover:text-foreground">닫기</button>
          <button type="button" onClick={() => void save()} disabled={busy || !view}
                  className="h-9 rounded-lg bg-accent px-5 text-[13px] font-semibold text-white hover:brightness-105 disabled:opacity-60">
            {busy ? "저장 중…" : "저장"}
          </button>
        </div>
      </div>
    </div>
  );
}
