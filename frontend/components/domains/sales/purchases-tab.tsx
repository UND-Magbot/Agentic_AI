// [제품 구매 건 관리] 탭(사용자 2026-11-02, 이름 2026-11-11) — 우리가 공급사에서 제품을 살 때의 진행을 한 건씩 리스트로 관리한다(판매의 역순).
// 순서(사용자 2026-11-11): 국내 견적 → 재가 → 발주 → 송금 · 배송 · 입고(+ 거래명세서·세금계산서 선택),
// 해외 견적 → 재가 → 발주 → 제조사 송금 · 배송 · 통관 서류 · 관세사 송금 · 입고(발주 뒤는 순서 자유). 상태는 기록에서 계산된 표시다
// (목록의 단계별 '진행' 칸은 상태와 겹쳐 뺐다 — 사용자 2026-11-10, 단계별 진행은 상세에서).
// 선결제·선발행으로 넘어갔는지는 상태 칸 배지로 보인다('서류' 칸은 구매 사유 칸으로 바꿈 — 사용자 2026-11-17). 줄을 누르면 오른쪽에 상세가 열린다.
"use client";

import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { PurchaseDetail } from "@/components/domains/sales/purchase-detail";
import { POPUP_BTN_HOVER } from "@/components/shared/ui/confirm-dialog";
import { askConfirm } from "@/components/shared/ui/confirm-host";
import { Icon } from "@/components/shared/ui/icon";
import { INPUT, Muted, shortDate, stamp, todayIso, won } from "@/components/domains/sales/deal-format";
import { purchaseRequest, REGION_LABEL, type PurchaseListData, type PurchaseRegion, type PurchaseRow } from "@/lib/shared/purchases";
import { cn } from "@/lib/shared/utils";
import { secondaryBtn } from "./proposal-actions";
import { REQ, REQ_SCOPE, ReqLabel, ReqMark } from "@/components/domains/sales/required";

export const PURCHASE_STAGE_TONE: Record<string, string> = {
  "견적 대기": "bg-slate-500/12 text-slate-700 dark:text-slate-300",
  "재가 대기": "bg-violet-500/12 text-violet-700 dark:text-violet-300",
  "발주 대기": "bg-blue-500/12 text-blue-700 dark:text-blue-300",
  "송금 대기": "bg-amber-500/12 text-amber-700 dark:text-amber-300",
  "제조사 송금 대기": "bg-amber-500/12 text-amber-700 dark:text-amber-300",
  "배송 대기": "bg-cyan-500/12 text-cyan-700 dark:text-cyan-300",
  "배송 중": "bg-sky-500/15 text-sky-700 dark:text-sky-300",
  "통관 서류 대기": "bg-fuchsia-500/12 text-fuchsia-700 dark:text-fuchsia-300",
  "관세사 송금 대기": "bg-pink-500/12 text-pink-700 dark:text-pink-300",
  "입고 대기": "bg-orange-500/12 text-orange-700 dark:text-orange-300",
  "완료": "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300",
  "취소": "bg-foreground/8 text-foreground-subtle",
};

/** 입고 예정일 칸 — 지났는데 입고 전이면 빨강, 7일 안이면 주황(사용자 2026-11-02: 리스트·상세에 보이게) */
export function ExpectedDate({ row, today = todayIso() }: { row: PurchaseRow; today?: string }) {
  if (!row.expected_date) return <Muted>{row.po.no ? (row.region === "overseas" ? "납기일 미기록" : "미기록") : "-"}</Muted>;
  const days = Math.round((Date.parse(row.expected_date) - Date.parse(today)) / 86_400_000);
  const open = !row.receipt.done && !row.dropped;
  return (
    <span className={cn("tabular-nums", open && days < 0 && "font-semibold text-red-600 dark:text-red-400",
                        open && days >= 0 && days <= 7 && "font-semibold text-amber-700 dark:text-amber-300",
                        !open && "text-foreground-muted")}>
      {shortDate(row.expected_date)}
      {open && <span className="ml-1 text-[11.5px] font-normal">{days < 0 ? `(${-days}일 지남)` : days === 0 ? "(오늘)" : `(D-${days})`}</span>}
      {row.receipt.done && <span className="ml-1 text-[11.5px]">입고됨</span>}
    </span>
  );
}

// 목록 거르기(사용자 2026-11-17 — 칩이 많아 지저분: 큰 묶음 칩 몇 개 + 세부 단계는 드롭다운).
//  · 큰 묶음 칩: 전체 / 발주 전(견적·재가·발주) / 진행 중(송금·배송·통관·입고) / 완료 — 하나만 고름
//  · 챙길 것 칩(지남·확인 필요·선결제): 해당 건이 있을 때만 보임 · 국내/해외: 오른쪽 작은 전환 · [단계 ▾]: 세부 단계(취소 포함)
//  묶음·단계 중 하나 + 챙길 것 + 국내/해외를 함께 건다. 완료 건은 [완료]·단계 '완료'·챙길 것 칩에서만(영업 건 관리와 같게)
const DONE = "완료";
type Group = "" | "before" | "progress" | "done";
const GROUPS: [Group, string, string[]][] = [
  ["before", "발주 전", ["견적 대기", "재가 대기", "발주 대기"]],
  ["progress", "진행 중", ["송금 대기", "제조사 송금 대기", "배송 대기", "배송 중", "통관 서류 대기", "관세사 송금 대기", "입고 대기"]],
  ["done", "완료", [DONE]],
];
type Watch = "" | "overdue" | "receipt_due" | "review" | "prepaid";
const WATCH: [Exclude<Watch, "">, string, (r: PurchaseRow) => boolean, string][] = [
  ["overdue", "⏰ 송금 예정일 지남", (r) => r.alerts.some((a) => a.code === "OVERDUE"), "bg-red-500/12 text-red-700 dark:text-red-300"],
  ["receipt_due", "📦 입고 예정일 지남", (r) => r.alerts.some((a) => a.code === "RECEIPT_DUE"), "bg-red-500/12 text-red-700 dark:text-red-300"],
  ["review", "⚠ 확인 필요", (r) => r.alerts.some((a) => a.level !== "info"), "bg-amber-500/12 text-amber-800 dark:text-amber-300"],
  ["prepaid", "💳 선결제·선발행", (r) => r.prepaid.statement || r.prepaid.tax, "bg-indigo-500/12 text-indigo-700 dark:text-indigo-300"],
];
type Region = "" | PurchaseRegion;

export function PurchasesTab({ initialOpen = null }: { initialOpen?: number | null } = {}) {
  const [data, setData] = useState<PurchaseListData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openId, setOpenId] = useState<number | null>(initialOpen);   // 재고 줄에서 구매 건 열기(사용자 2026-11-19)
  // 구매 사유 팝업(사용자 2026-11-17) — 목록의 [등록]·사유 글을 누르면 연다
  const [reasonRow, setReasonRow] = useState<PurchaseRow | null>(null);
  const [q, setQ] = useState("");
  // 거르기 — 큰 묶음(group)과 세부 단계(stage)는 둘 중 하나만, 챙길 것(watch)·국내/해외(region)는 함께
  const [group, setGroup] = useState<Group>("");
  const [stage, setStage] = useState("");
  const [watch, setWatch] = useState<Watch>("");
  const [region, setRegion] = useState<Region>("");
  const [adding, setAdding] = useState(false);
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());

  const load = useCallback(async () => {
    try {
      setData(await purchaseRequest<PurchaseListData>(""));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "구매 건을 불러오지 못했습니다.");
    }
  }, []);
  useEffect(() => {
    let alive = true;
    purchaseRequest<PurchaseListData>("")
      .then((d) => { if (alive) setData(d); })
      .catch((e) => { if (alive) setError(e instanceof Error ? e.message : "구매 건을 불러오지 못했습니다."); });
    return () => { alive = false; };
  }, []);

  // 국내/해외만 건 목록 — 칩·드롭다운 건수도 이 기준
  const inRegion = useMemo(() => (data?.rows ?? []).filter((r) => !region || r.region === region), [data, region]);
  const rows = useMemo(() => {
    const k = q.trim().toLowerCase();
    const g = GROUPS.find(([key]) => key === group);
    const w = WATCH.find(([key]) => key === watch);
    const showDone = group === "done" || stage === DONE || !!w;
    return inRegion.filter((r) => (stage ? r.stage === stage : g ? g[2].includes(r.stage) : true)
      && (!w || w[2](r))
      && (showDone || r.stage !== DONE)
      && (!k || [r.supplier, r.title, r.owner, r.po.no ?? "", r.reason ?? ""].some((v) => v.toLowerCase().includes(k))));
  }, [inRegion, q, group, stage, watch]);

  if (error) return <p className="py-16 text-center text-[13px] text-foreground-muted">{error}</p>;
  if (!data) return <p className="py-16 text-center text-[13px] text-foreground-subtle">구매 건을 불러오는 중…</p>;
  const open = data.rows.find((r) => r.id === openId) ?? null;
  const admin = data.me.admin;
  // 새 구매 건은 팝업 대신 탭 안 작성 화면으로(사용자 2026-11-11 "견적서 작성처럼") — 등록하면 목록으로 돌아와 상세를 연다
  if (adding) {
    return <NewPurchase me={data.me.name} onClose={() => setAdding(false)}
                        onCreated={(id) => { void load().then(() => { setAdding(false); setOpenId(id); }); }} />;
  }

  const remove = async () => {
    if (!selected.size || !(await askConfirm({ title: `선택한 ${selected.size}건을 지울까요?`, message: "기록과 이력이 모두 지워지고 되돌릴 수 없습니다.",
                                               confirmLabel: "삭제", danger: true }))) return;
    try {
      await purchaseRequest("/delete", { ids: [...selected] });
      setSelected(new Set());
      setSelecting(false);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "지우지 못했습니다.");
    }
  };

  return (
    <section>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <button type="button" onClick={() => setAdding(true)}
                className="h-9 rounded-lg bg-accent px-4 text-[13px] font-semibold text-white hover:opacity-90">+ 새 구매 건</button>
        {admin && (selecting ? (
          <>
            <button type="button" onClick={() => void remove()} disabled={!selected.size}
                    className="h-9 rounded-lg bg-red-600 px-4 text-[13px] font-semibold text-white disabled:opacity-40">선택한 {selected.size}건 삭제</button>
            <button type="button" onClick={() => { setSelecting(false); setSelected(new Set()); }}
                    className="h-9 rounded-lg border border-border px-4 text-[13px]">취소</button>
          </>
        ) : (
          <button type="button" onClick={() => setSelecting(true)} className="h-9 rounded-lg border border-border px-4 text-[13px]">삭제</button>
        ))}
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="공급사·품목·발주번호 검색"
               className="ml-auto h-9 w-64 rounded-lg border border-border bg-surface px-3 text-[13px] outline-none focus:border-accent" />
      </div>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        {([["", "전체", inRegion.filter((r) => r.stage !== DONE).length] as const,
           ...GROUPS.map(([key, label, ss]) => [key, label, inRegion.filter((r) => ss.includes(r.stage)).length] as const)]).map(([key, label, n]) => {
          const on = !stage && group === key;
          return (
            <button key={label} type="button" aria-pressed={on} onClick={() => { setGroup(key); setStage(""); }}
                    className={cn("rounded-full px-3.5 py-1.5 text-[13px] font-semibold transition-colors",
                      key === "done"   // 완료만 초록(사용자 2026-11-17)
                        ? on ? "bg-emerald-600 text-white" : "bg-emerald-500/12 text-emerald-700 hover:bg-emerald-500/20 dark:text-emerald-300"
                        : on ? "bg-accent text-white" : "bg-foreground/[0.06] text-foreground hover:bg-foreground/10")}>
              {label} <span className={cn("tabular-nums", on ? "opacity-90" : "opacity-60")}>{n}</span>
            </button>
          );
        })}
        {/* 챙길 것 — 해당 건이 있을 때만(고른 것은 0건이어도 남김) */}
        {WATCH.map(([key, label, test, tone]) => [key, label, inRegion.filter(test).length, tone] as const)
          .filter(([key, , n]) => n > 0 || watch === key)
          .map(([key, label, n, tone]) => (
            <button key={key} type="button" aria-pressed={watch === key} onClick={() => setWatch(watch === key ? "" : key)}
                    className={cn("rounded-full px-3 py-1 text-[12.5px] font-semibold", tone, watch === key && "ring-2 ring-accent/60")}>
              {label} <span className="tabular-nums opacity-70">{n}</span>
            </button>
          ))}
        <span className="ml-auto flex items-center gap-2">
          <span className="inline-flex rounded-lg border border-border p-0.5 text-[12.5px]" role="group" aria-label="국내·해외">
            {([["", "전체"], ["domestic", "국내"], ["overseas", "해외"]] as const).map(([key, label]) => (
              <button key={label} type="button" aria-pressed={region === key} onClick={() => setRegion(key)}
                      className={cn("rounded-md px-2.5 py-1 font-semibold", region === key ? "bg-foreground/10 text-foreground" : "text-foreground-muted hover:text-foreground")}>
                {label}
              </button>
            ))}
          </span>
          <select value={stage} onChange={(e) => { setStage(e.target.value); setGroup(""); }} aria-label="세부 단계"
                  className={cn("h-8 rounded-lg border bg-surface px-2 text-[12.5px] outline-none focus:border-accent",
                    stage ? "border-accent font-semibold text-accent" : "border-border text-foreground-muted")}>
            <option value="">단계: 전체</option>
            {data.stages.map((st) => <option key={st} value={st}>{st} ({inRegion.filter((r) => r.stage === st).length})</option>)}
          </select>
        </span>
      </div>

      <div className="overflow-x-auto rounded-xl border border-border bg-surface">
        <table className="w-full min-w-[1080px] border-collapse text-[13px]">
          <thead>
            <tr className="border-b border-border bg-foreground/[0.03] text-left text-[12px] text-foreground-muted">
              {selecting && <th className="w-10 px-3 py-2" />}
              {["공급사 · 품목", "구매 사유", "발주번호", "담당", "발주 금액 (VAT 별도)", "입고 예정일", "상태", "알림", "최근 변경"].map((h) => (
                <th key={h} className={cn("whitespace-nowrap px-3 py-2 font-semibold", h.startsWith("발주 금액") && "text-right")}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} onClick={() => (selecting ? setSelected((s) => { const n = new Set(s); if (n.has(r.id)) n.delete(r.id); else n.add(r.id); return n; }) : setOpenId(r.id))}
                  className={cn("cursor-pointer border-b border-border/70 last:border-0 hover:bg-accent/5", r.dropped && "opacity-60",
                    r.alerts.some((a) => a.code === "OVERDUE" || a.code === "RECEIPT_DUE") && "bg-red-500/[0.07] shadow-[inset_3px_0_0_#dc2626]",
                    openId === r.id && "bg-accent/8")}>
                {selecting && (
                  <td className="px-3 py-2"><input type="checkbox" readOnly checked={selected.has(r.id)} className="h-4 w-4 accent-red-600" /></td>
                )}
                <td className="max-w-[280px] px-3 py-2">
                  <div className="flex items-center gap-1.5">
                    <span className={cn("shrink-0 rounded px-1.5 text-[11px] font-semibold",
                      r.region === "overseas" ? "bg-sky-500/12 text-sky-700 dark:text-sky-300" : "bg-foreground/8 text-foreground-muted")}>{REGION_LABEL[r.region]}</span>
                    <span className="truncate font-semibold">{r.supplier}</span>
                  </div>
                  <div className="truncate text-[12.5px] text-foreground-muted">{r.title || <Muted>품목 미기재</Muted>}</div>
                </td>
                <td className="min-w-[120px] px-3 py-2"><ReasonCell r={r} onEdit={() => setReasonRow(r)} /></td>
                <td className="whitespace-nowrap px-3 py-2 font-mono text-[12px]">{r.po.no ?? <Muted>발주 전</Muted>}</td>
                <td className="whitespace-nowrap px-3 py-2">{r.owner || <Muted>-</Muted>}</td>
                <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums">
                  {r.total == null ? <Muted>-</Muted> : won(r.total, r.currency)}
                </td>
                <td className="whitespace-nowrap px-3 py-2"><ExpectedDate row={r} /></td>
                <td className="whitespace-nowrap px-3 py-2">
                  <span className={cn("rounded-full px-2.5 py-0.5 text-[12px] font-semibold", PURCHASE_STAGE_TONE[r.stage])}>{r.stage === "배송 중" ? "🚚 " : ""}{r.stage}</span>
                  {r.stage_note && <div className="mt-0.5 max-w-[170px] whitespace-normal text-[11.5px] text-foreground-muted">{r.stage_note}</div>}
                  {(r.prepaid.statement || r.prepaid.tax) && (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {r.prepaid.statement && <span className="rounded bg-indigo-500/12 px-1.5 text-[11px] font-semibold text-indigo-700 dark:text-indigo-300">명세서 {r.docs.statement.label}</span>}
                      {r.prepaid.tax && <span className="rounded bg-indigo-500/12 px-1.5 text-[11px] font-semibold text-indigo-700 dark:text-indigo-300">계산서 {r.docs.tax.label}</span>}
                    </div>
                  )}
                </td>
                <td className="min-w-[150px] max-w-[240px] px-3 py-2 text-[12px]">
                  {r.alerts.length ? r.alerts.map((a, i) => (
                    <div key={i} className={cn(a.level === "alert" ? "text-red-600 dark:text-red-400" : a.level === "review" ? "text-amber-700 dark:text-amber-300" : "text-foreground-muted")}>{a.text}</div>
                  )) : <Muted>-</Muted>}
                </td>
                <td className="whitespace-nowrap px-3 py-2 text-foreground-muted">{stamp(r.updated_at)}</td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={selecting ? 10 : 9} className="px-3 py-12 text-center text-foreground-muted">
                {!data.rows.length ? "아직 구매 건이 없습니다. [+ 새 구매 건]으로 시작하세요."
                  : !stage && !group && !watch && !q.trim() && inRegion.every((r) => r.stage === DONE) ? "진행 중인 구매 건이 없습니다. 끝난 건은 [완료]에서 볼 수 있습니다."
                  : "조건에 맞는 건이 없습니다."}
              </td></tr>
            )}
          </tbody>
        </table>
      </div>

      {open && <PurchaseDetail key={open.id} id={open.id} admin={admin} onClose={() => setOpenId(null)} onChanged={() => void load()} />}
      {reasonRow && <ReasonDialog row={reasonRow} onClose={() => setReasonRow(null)} onSaved={() => { setReasonRow(null); void load(); }} />}
    </section>
  );
}

// 구매 사유 칸(사용자 2026-11-17 — '서류' 칸 자리). 있으면 글(눌러 고치기), 없으면 [등록] — 둘 다 팝업에서 적는다.
function ReasonCell({ r, onEdit }: { r: PurchaseRow; onEdit: () => void }) {
  if (r.reason) {
    return (
      <button type="button" title={`${r.reason}\n(눌러서 고치기)`} onClick={(e) => { e.stopPropagation(); onEdit(); }}
              className="line-clamp-2 max-w-[220px] whitespace-pre-line text-left text-[12.5px] text-foreground hover:underline">
        {r.reason}
      </button>
    );
  }
  return (
    <button type="button" onClick={(e) => { e.stopPropagation(); onEdit(); }}
            className={cn("inline-flex items-center gap-1 whitespace-nowrap rounded-lg border border-dashed border-border px-2.5 py-1 text-[12px] font-semibold text-accent", POPUP_BTN_HOVER)}>
      <Icon name="plus" className="h-3.5 w-3.5" /> 등록
    </button>
  );
}

/** 구매 사유 팝업 — 등록·고치기(영업팀 누구나, 이력에 남음) */
function ReasonDialog({ row, onClose, onSaved }: { row: PurchaseRow; onClose: () => void; onSaved: () => void }) {
  const [v, setV] = useState(row.reason);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      await purchaseRequest(`/${row.id}/reason`, { reason: v });
      onSaved();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "저장하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div role="dialog" aria-modal="true" aria-labelledby="reason-title" className="fixed inset-0 z-[60] grid place-items-center bg-foreground/40 p-4 backdrop-blur-sm"
         onMouseDown={(e) => { if (!busy && e.target === e.currentTarget) onClose(); }}>
      <div className="anim-fade-in-up w-full max-w-md rounded-2xl border border-border bg-surface-raised p-5 shadow-2xl">
        <h3 id="reason-title" className="text-[16px] font-semibold">구매 사유 {row.reason ? "고치기" : "등록"}</h3>
        <p className="mt-1 text-[12.5px] text-foreground-muted">{REGION_LABEL[row.region]} · {row.supplier}{row.title ? ` · ${row.title}` : ""}</p>
        <textarea autoFocus value={v} maxLength={1000} onChange={(e) => setV(e.target.value)}
                  placeholder="예: ○○ 고객사 납품용 재고 확보 / 데모 장비 교체 / A/S 교체품"
                  className={cn(INPUT, "mt-3 h-32 py-2")} />
        {err && <p className="mt-2 rounded-lg bg-red-500/10 px-3 py-2 text-[12.5px] text-red-700 dark:text-red-300">{err}</p>}
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" disabled={busy} onClick={onClose}
                  className={cn("h-9 rounded-lg border border-border px-4 text-[13px]", POPUP_BTN_HOVER)}>취소</button>
          <button type="button" disabled={busy || v.trim() === row.reason.trim()} onClick={() => void save()}
                  className="und-grad h-9 rounded-lg px-4 text-[13px] font-semibold text-white hover:bg-none! hover:bg-blue-700! disabled:opacity-50">
            {busy ? "저장 중…" : "저장"}
          </button>
        </div>
      </div>
    </div>
  );
}

function NewPurchase({ me, onClose, onCreated }: { me: string; onClose: () => void; onCreated: (id: number) => void }) {
  const [v, setV] = useState<{ supplier: string; title: string; owner: string; note: string; reason: string; region: PurchaseRegion }>(
    { supplier: "", title: "", owner: me, note: "", reason: "", region: "domestic" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await purchaseRequest("", v);
      onCreated(r.id);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "등록하지 못했습니다.");
      setBusy(false);
    }
  };
  return (
    <section className="flex flex-col gap-3">
      <button type="button" className={cn(secondaryBtn, "self-start")} disabled={busy} onClick={onClose}>
        <Icon name="back" className="h-4 w-4" /> 구매 건 목록
      </button>
      <div className="w-full max-w-[720px] rounded-xl border border-border bg-surface p-5">
        <h2 className="text-[16px] font-bold">새 구매 건 작성</h2>
        <p className="mt-1 text-[12.5px] text-foreground-muted">공급사 하나에 한 건입니다. 등록 뒤 발주할 최종 견적서를 올리면 재가 단계로 넘어갑니다.</p>
        <div className="mt-4 grid gap-3 text-[13px]">
          <div className="grid gap-1">
            <ReqMark>국내 · 해외</ReqMark>
            <div className="flex gap-2">
              {(["domestic", "overseas"] as const).map((k) => (
                <button key={k} type="button" onClick={() => setV({ ...v, region: k })} aria-pressed={v.region === k}
                        className={cn("h-9 flex-1 rounded-lg border text-[13px] font-semibold",
                          v.region === k ? "border-accent bg-accent/10 text-accent" : "border-border text-foreground-muted")}>
                  {REGION_LABEL[k]}
                </button>
              ))}
            </div>
            <span className="text-[11.5px] text-foreground-subtle">
              {v.region === "domestic" ? "견적 → 재가 → 발주 → 송금 · 배송 · 입고(거래명세서·세금계산서는 선택), 원화 발주서"
                                       : "견적 → 재가 → 발주 → 제조사 송금 · 배송 · 통관 서류 · 관세사 송금 · 입고, 달러 Purchasing order"}
            </span>
          </div>
          <label className={cn("grid gap-1", REQ_SCOPE)}><ReqLabel>공급사</ReqLabel><input {...REQ} className={INPUT} value={v.supplier} onChange={(e) => setV({ ...v, supplier: e.target.value })} placeholder="예: ㈜크레켄" autoFocus /></label>
          <label className="grid gap-1">
            <span>품목·건명 <span className="text-[12px] font-normal text-foreground-subtle">(선택)</span></span>
            <input className={INPUT} value={v.title} onChange={(e) => setV({ ...v, title: e.target.value })} placeholder="예: M8 패널마운트 커넥터" />
            {/* 사용자 2026-11-11 — 견적서를 받아야 아는 경우가 많아 비워도 된다는 걸 분명히 */}
            <span className="text-[11.5px] text-foreground-subtle">견적서를 받기 전이면 비워 두세요. 나중에 상세의 기본 정보에서 채울 수 있습니다.</span>
          </label>
          <label className="grid gap-1">담당자<input className={INPUT} value={v.owner} onChange={(e) => setV({ ...v, owner: e.target.value })} /></label>
          <label className="grid gap-1">구매 사유<textarea className={cn(INPUT, "h-20 py-2")} maxLength={1000} value={v.reason}
                    placeholder="예: ○○ 고객사 납품용 재고 확보 (나중에 목록에서 [등록]으로 적어도 됩니다)"
                    onChange={(e) => setV({ ...v, reason: e.target.value })} /></label>
          <label className="grid gap-1">메모<textarea className={cn(INPUT, "h-28 py-2")} value={v.note} onChange={(e) => setV({ ...v, note: e.target.value })} /></label>
        </div>
        {err && <p className="mt-3 text-[12.5px] text-red-600">{err}</p>}
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" onClick={onClose} disabled={busy} className="h-9 rounded-lg border border-border px-4 text-[13px]">취소</button>
          <button type="button" onClick={() => void save()} disabled={busy || !v.supplier.trim()}
                  className="h-9 rounded-lg bg-accent px-4 text-[13px] font-semibold text-white disabled:opacity-40">{busy ? "등록 중…" : "등록"}</button>
        </div>
      </div>
    </section>
  );
}
