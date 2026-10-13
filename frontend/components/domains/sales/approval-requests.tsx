"use client";

// [검토·승인 > 승인 요청](사용자 2026-10-13, 영업팀 요청 8번) — 일반 직원이 관리자에게 보낸 승인 요청을 승인·거절.
// 요청 종류(kind)마다 이름표를 붙인다 — 지금은 입금기한 연장뿐, 다른 종류가 생겨도 같은 화면에서 처리.
// 승인은 요청한 날짜 그대로 또는 고친 날짜로(그 회차 입금 예정일이 바로 바뀜), 거절은 사유를 적는다.
import { useCallback, useEffect, useRef, useState } from "react";

import type { ApprovalRequest } from "@/lib/shared/sales-deals";
import { cn } from "@/lib/shared/utils";
import { inputCls } from "./proposal-actions";

type Lists = { pending: ApprovalRequest[]; done: ApprovalRequest[] };

/** 요청 종류 이름표 — 새 종류가 생기면 여기에 이름을 더한다(모르는 종류는 '승인 요청') */
const KIND_LABEL: Record<string, string> = { pay_due_extend: "입금기한 연장" };
const kindLabel = (k: string) => KIND_LABEL[k] ?? "승인 요청";
const md = (iso: string | null) => (iso ? `${Number(iso.slice(5, 7))}/${Number(iso.slice(8, 10))}` : "-");
const at = (iso: string | null) => (iso ? iso.slice(0, 16).replace("T", " ") : "");

function detail(d: unknown, fallback: string): string {
  const x = (d as { detail?: unknown })?.detail;
  return typeof x === "string" ? x : fallback;
}

/** 승인 요청 목록 — onCount 로 나에게 온 대기 수를 알려 탭 숫자를 맞춘다. onOpenDeal 이 있으면 영업 건 열기 */
export function ApprovalRequests({ onCount, onOpenDeal }: { onCount?: (n: number) => void; onOpenDeal?: (dealId: number) => void }) {
  const [data, setData] = useState<Lists | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | null>(null);

  // 부모가 넘기는 onCount 는 렌더마다 새 함수 — ref 로 들고 있어야 불러오기가 반복되지 않는다
  const countRef = useRef(onCount);
  countRef.current = onCount;
  const apply = useCallback((d: Lists) => {
    setData(d);
    countRef.current?.(d.pending.filter((x) => x.to_me).length);
  }, []);

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/sales-deals/approvals", { cache: "no-store" });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(detail(d, "승인 요청을 불러오지 못했습니다."));
      apply(d as Lists);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "승인 요청을 불러오지 못했습니다.");
    }
  }, [apply]);

  useEffect(() => {
    void load();
    const t = window.setInterval(() => void load(), 60_000);   // 열어 둔 동안에도 새 요청이 보이게
    return () => window.clearInterval(t);
  }, [load]);

  async function decide(id: number, body: { approve: boolean; date?: string; note: string }) {
    setBusy(id);
    setErr(null);
    try {
      const r = await fetch(`/api/sales-deals/approvals/${id}/decide`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(detail(d, "처리하지 못했습니다."));
      apply(d as Lists);
      return true;
    } catch (e) {
      setErr(e instanceof Error ? e.message : "처리하지 못했습니다.");
      return false;
    } finally {
      setBusy(null);
    }
  }

  if (!data) return <p className="text-[13px] text-foreground-muted">{err ?? "불러오는 중…"}</p>;
  return (
    <div className="flex flex-col gap-4">
      <p className="text-[12.5px] text-foreground-muted">
        일반 직원이 관리자에게 보낸 <b>승인 요청</b>입니다. 요청 내용을 확인하고 승인하면 바로 반영되고, 거절하면 요청자가 사유를 볼 수 있습니다.
        나를 받는 사람으로 고른 요청이 위에 나오며, 다른 관리자에게 간 요청도 처리할 수 있습니다.
      </p>
      {err && <p className="rounded-lg bg-red-500/10 px-3 py-2 text-[12.5px] text-red-700 dark:text-red-300">{err}</p>}

      <section className="flex flex-col gap-2">
        <h3 className="text-[14px] font-semibold text-foreground">처리 대기 <span className="text-foreground-muted">{data.pending.length}</span></h3>
        {data.pending.length === 0
          ? <p className="rounded-lg border border-dashed border-border px-3 py-4 text-center text-[12.5px] text-foreground-subtle">처리할 요청이 없습니다.</p>
          : data.pending.map((r) => <PendingCard key={r.id} r={r} busy={busy === r.id} onDecide={(b) => decide(r.id, b)} onOpenDeal={onOpenDeal} />)}
      </section>

      <section className="flex flex-col gap-1.5">
        <h3 className="text-[14px] font-semibold text-foreground">처리 내역 <span className="text-[12px] font-normal text-foreground-subtle">최근 50건</span></h3>
        {data.done.length === 0 ? <p className="text-[12.5px] text-foreground-subtle">아직 없습니다.</p> : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] border-collapse text-[12.5px]">
              <thead>
                <tr className="border-b border-border text-left text-[11.5px] text-foreground-muted">
                  {["결과", "종류", "영업 건", "내용", "요청", "처리", "메모"].map((h) => <th key={h} className="px-2 py-1.5 font-semibold">{h}</th>)}
                </tr>
              </thead>
              <tbody>
                {data.done.map((r) => (
                  <tr key={r.id} className="border-b border-border/60">
                    <td className="whitespace-nowrap px-2 py-1.5">
                      <span className={cn("rounded px-1.5 py-0.5 text-[11px] font-semibold",
                        r.status === "approved" ? "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300" : "bg-red-500/12 text-red-700 dark:text-red-300")}>
                        {r.status === "approved" ? "승인" : "거절"}
                      </span>
                    </td>
                    <td className="whitespace-nowrap px-2 py-1.5">{kindLabel(r.kind)}</td>
                    <td className="px-2 py-1.5">{r.customer} <span className="font-mono text-[11.5px] text-foreground-subtle">{r.deal_no}</span></td>
                    <td className="whitespace-nowrap px-2 py-1.5">
                      {r.kind === "pay_due_extend"
                        ? <>{r.term_label ?? "-"} {md(r.current_due)} → {r.status === "approved" ? <b>{md(r.decided_due)}</b> : <s>{md(r.requested_due)}</s>}</>
                        : "-"}
                    </td>
                    <td className="whitespace-nowrap px-2 py-1.5 text-foreground-muted">{r.requested_by} · {at(r.created_at)}</td>
                    <td className="whitespace-nowrap px-2 py-1.5 text-foreground-muted">{r.decided_by} · {at(r.decided_at)}</td>
                    <td className="px-2 py-1.5 text-foreground-muted">{r.decision_note || r.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function PendingCard({ r, busy, onDecide, onOpenDeal }: {
  r: ApprovalRequest; busy: boolean; onDecide: (b: { approve: boolean; date?: string; note: string }) => Promise<boolean>;
  onOpenDeal?: (dealId: number) => void;
}) {
  const [date, setDate] = useState(r.requested_due ?? "");
  const [note, setNote] = useState("");
  const [rejecting, setRejecting] = useState(false);
  const days = r.current_due ? Math.max(0, Math.round((Date.now() - new Date(`${r.current_due}T00:00:00`).getTime()) / 86_400_000)) : null;
  return (
    <div className={cn("flex flex-col gap-2 rounded-xl border p-3 text-[12.5px]", r.to_me ? "border-accent/50 bg-accent/[0.03]" : "border-border")}>
      <div className="flex flex-wrap items-center gap-2">
        {r.to_me && <span className="rounded bg-red-500 px-1.5 py-0.5 text-[11px] font-semibold text-white">나에게 온 요청</span>}
        <span className="rounded bg-foreground/[0.07] px-1.5 py-0.5 text-[11px] font-semibold text-foreground">{kindLabel(r.kind)}</span>
        <b className="text-[13.5px] text-foreground">{r.customer}</b>
        <span className="font-mono text-[11.5px] text-foreground-subtle">{r.deal_no}</span>
        {r.title && <span className="text-foreground-muted">{r.title}</span>}
        {onOpenDeal && (
          <button type="button" className="text-[12px] font-semibold text-accent hover:underline" onClick={() => onOpenDeal(r.deal_id)}>영업 건 보기 →</button>
        )}
        <span className="ml-auto text-[11.5px] text-foreground-subtle">{r.requested_by} · {at(r.created_at)} · 받는 사람 {r.recipients.join(", ")}</span>
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <span><span className="text-foreground-muted">회차</span> <b>{r.term_label ?? "-"}</b></span>
        <span><span className="text-foreground-muted">지금 예정일</span> <b className="text-red-600 dark:text-red-400">{r.current_due}</b>{days != null && ` (${days}일 지남)`}</span>
        <span><span className="text-foreground-muted">희망 예정일</span> <b>{r.requested_due}</b></span>
      </div>
      <p className="rounded-lg bg-foreground/[0.04] px-2.5 py-1.5"><span className="text-foreground-muted">사유 </span>{r.reason}</p>
      {!rejecting ? (
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-1.5 text-foreground-muted">
            승인할 예정일
            <input type="date" className={cn(inputCls, "w-auto py-0.5 text-[12.5px]")} value={date} disabled={busy} onChange={(e) => setDate(e.target.value)} />
          </label>
          <input className={cn(inputCls, "min-w-0 flex-1 basis-40 py-0.5 text-[12.5px]")} value={note} maxLength={500} disabled={busy}
                 placeholder="메모(선택) — 예: 3주로 조정, 고객사 재무팀 확인" onChange={(e) => setNote(e.target.value)} />
          <button type="button" disabled={busy || !date} onClick={() => void onDecide({ approve: true, date, note })}
                  className="h-8 rounded-lg bg-accent px-4 text-[12.5px] font-semibold text-white hover:brightness-105 disabled:opacity-40">
            {date && date !== r.requested_due ? "날짜 고쳐 승인" : "승인"}
          </button>
          <button type="button" disabled={busy} onClick={() => setRejecting(true)}
                  className="h-8 rounded-lg border border-red-500/50 px-3 text-[12.5px] font-semibold text-red-700 hover:bg-red-500/5 disabled:opacity-40 dark:text-red-300">
            거절
          </button>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <input className={cn(inputCls, "min-w-0 flex-1 basis-56 py-0.5 text-[12.5px]")} value={note} maxLength={500} autoFocus disabled={busy}
                 placeholder="거절 사유 * — 예: 고객사 입금 계획 확인 후 다시 요청" onChange={(e) => setNote(e.target.value)} />
          <button type="button" disabled={busy || !note.trim()} onClick={() => void onDecide({ approve: false, note })}
                  className="h-8 rounded-lg bg-red-600 px-4 text-[12.5px] font-semibold text-white hover:bg-red-700 disabled:opacity-40">거절하기</button>
          <button type="button" className="text-[12px] text-foreground-subtle hover:underline" onClick={() => setRejecting(false)}>취소</button>
        </div>
      )}
    </div>
  );
}
