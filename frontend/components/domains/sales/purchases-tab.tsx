// [제품 구매 건 관리] 탭(사용자 2026-11-02, 이름 2026-11-11) — 우리가 공급사에서 제품을 살 때의 진행을 한 건씩 리스트로 관리한다(판매의 역순).
// 순서(사용자 2026-11-11): 국내 견적 → 재가 → 발주 → 송금 · 배송 · 입고(+ 거래명세서·세금계산서 선택),
// 해외 견적 → 재가 → 발주 → 제조사 송금 · 배송 · 통관 서류 · 관세사 송금 · 입고(발주 뒤는 순서 자유). 상태는 기록에서 계산된 표시다
// (목록의 단계별 '진행' 칸은 상태와 겹쳐 뺐다 — 사용자 2026-11-10, 단계별 진행은 상세에서).
// 거래명세서·세금계산서가 첨부됐는지, 선결제·선발행으로 넘어갔는지는 '서류' 칸과 상태 칸 배지로 보인다. 줄을 누르면 오른쪽에 상세가 열린다.
"use client";

import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { PurchaseDetail } from "@/components/domains/sales/purchase-detail";
import { Icon } from "@/components/shared/ui/icon";
import { INPUT, Muted, shortDate, stamp, todayIso, won } from "@/components/domains/sales/deal-format";
import { purchaseRequest, REGION_LABEL, type PurchaseListData, type PurchaseRegion, type PurchaseRow } from "@/lib/shared/purchases";
import { cn } from "@/lib/shared/utils";
import { secondaryBtn } from "./proposal-actions";

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

// 상태 칩(사용자 2026-11-10) — 단계 칩에 더해 알림·선결제·국내/해외로도 거른다. 칩은 한 번에 하나만(영업 건 관리와 같게)
type Extra = "overdue" | "receipt_due" | "review" | "prepaid" | "domestic" | "overseas";
// 완료 건은 [완료] 칩을 눌렀을 때만 — 전체·국내·해외에서는 뺀다(사용자 2026-11-11, 영업 건 관리와 같게).
// 알림·선결제·선발행 칩은 완료 건도 보인다(선결제·선발행 기록은 대부분 완료 건에 있음)
const DONE = "완료";
const HIDES_DONE: (Extra | "")[] = ["", "domestic", "overseas"];
const EXTRA: [Extra, string, (r: PurchaseRow) => boolean, string][] = [
  ["overdue", "⏰ 송금 예정일 지남", (r) => r.alerts.some((a) => a.code === "OVERDUE"), "bg-red-500/12 text-red-700 dark:text-red-300"],
  ["receipt_due", "📦 입고 예정일 지남", (r) => r.alerts.some((a) => a.code === "RECEIPT_DUE"), "bg-red-500/12 text-red-700 dark:text-red-300"],
  ["review", "⚠ 확인 필요", (r) => r.alerts.some((a) => a.level !== "info"), "bg-sky-500/12 text-sky-700 dark:text-sky-300"],
  ["prepaid", "💳 선결제·선발행", (r) => r.prepaid.statement || r.prepaid.tax, "bg-indigo-500/12 text-indigo-700 dark:text-indigo-300"],
  ["domestic", "국내", (r) => r.region !== "overseas", "bg-foreground/8 text-foreground-muted"],
  ["overseas", "해외", (r) => r.region === "overseas", "bg-sky-500/12 text-sky-700 dark:text-sky-300"],
];

export function PurchasesTab() {
  const [data, setData] = useState<PurchaseListData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);
  const [q, setQ] = useState("");
  const [stage, setStage] = useState("");
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

  const rows = useMemo(() => {
    const k = q.trim().toLowerCase();
    const extra = EXTRA.find(([key]) => key === stage);
    const hideDone = HIDES_DONE.includes(stage as Extra | "");
    return (data?.rows ?? []).filter((r) => (!stage || (extra ? extra[2](r) : r.stage === stage))
      && !(hideDone && r.stage === DONE)
      && (!k || [r.supplier, r.title, r.owner, r.po.no ?? ""].some((v) => v.toLowerCase().includes(k))));
  }, [data, q, stage]);

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
    if (!selected.size || !window.confirm(`선택한 ${selected.size}건을 지울까요? 기록과 이력이 모두 지워집니다.`)) return;
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

      {/* 단계 칩은 0건이어도 늘 보인다 · 그 아래 알림·선결제(있을 때만)·국내/해외 칩(사용자 2026-11-10·11) — 칩은 한 번에 하나만 */}
      <div className="mb-2 flex flex-wrap gap-2">
        {[["", "전체", data.rows.filter((r) => r.stage !== DONE).length] as const, ...data.stages.map((s) => [s, s, data.counts[s] ?? 0] as const)].map(([key, label, n]) => (
          <button key={label} type="button" onClick={() => setStage(stage === key ? "" : key)} aria-pressed={stage === key}
                  className={cn("rounded-full px-3 py-1 text-[12.5px] font-semibold", key ? PURCHASE_STAGE_TONE[key] : "bg-foreground/8 text-foreground",
                    stage === key && "ring-2 ring-accent/60")}>
            {label} <span className="tabular-nums opacity-70">{n}</span>
          </button>
        ))}
      </div>
      <div className="mb-3 flex flex-wrap gap-2">
        {/* 알림·선결제 칩은 해당 건이 있을 때만(사용자 2026-11-11) — 국내/해외는 늘 보임, 선택 중인 칩은 0건이어도 남김 */}
        {EXTRA.map(([key, label, test, tone]) => [key, label, data.rows.filter((r) => test(r) && !(HIDES_DONE.includes(key) && r.stage === DONE)).length, tone] as const)
          .filter(([key, , n]) => n > 0 || key === "domestic" || key === "overseas" || stage === key)
          .map(([key, label, n, tone]) => (
          <button key={key} type="button" onClick={() => setStage(stage === key ? "" : key)} aria-pressed={stage === key}
                  className={cn("rounded-full px-3 py-1 text-[12.5px] font-semibold", tone, stage === key && "ring-2 ring-accent/60")}>
            {label} <span className="tabular-nums opacity-70">{n}</span>
          </button>
        ))}
      </div>

      <div className="overflow-x-auto rounded-xl border border-border bg-surface">
        <table className="w-full min-w-[1080px] border-collapse text-[13px]">
          <thead>
            <tr className="border-b border-border bg-foreground/[0.03] text-left text-[12px] text-foreground-muted">
              {selecting && <th className="w-10 px-3 py-2" />}
              {["공급사 · 품목", "서류", "발주번호", "담당", "발주 금액", "입고 예정일", "상태", "알림", "최근 변경"].map((h) => (
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
                <td className="whitespace-nowrap px-3 py-2"><Docs r={r} /></td>
                <td className="whitespace-nowrap px-3 py-2 font-mono text-[12px]">{r.po.no ?? <Muted>발주 전</Muted>}</td>
                <td className="whitespace-nowrap px-3 py-2">{r.owner || <Muted>-</Muted>}</td>
                <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums">
                  {r.total == null ? <Muted>-</Muted> : won(r.total, r.currency)}
                  {r.total != null && r.currency === "KRW" && <div className="text-[11px] text-foreground-subtle">VAT 별도</div>}
                </td>
                <td className="whitespace-nowrap px-3 py-2"><ExpectedDate row={r} /></td>
                <td className="whitespace-nowrap px-3 py-2">
                  <span className={cn("rounded-full px-2.5 py-0.5 text-[12px] font-semibold", PURCHASE_STAGE_TONE[r.stage])}>{r.stage === "배송 중" ? "🚚 " : ""}{r.stage}</span>
                  {r.stage_note && <div className="mt-0.5 text-[11.5px] text-foreground-muted">{r.stage_note}</div>}
                  {(r.prepaid.statement || r.prepaid.tax) && (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {r.prepaid.statement && <span className="rounded bg-indigo-500/12 px-1.5 text-[11px] font-semibold text-indigo-700 dark:text-indigo-300">명세서 {r.docs.statement.label}</span>}
                      {r.prepaid.tax && <span className="rounded bg-indigo-500/12 px-1.5 text-[11px] font-semibold text-indigo-700 dark:text-indigo-300">계산서 {r.docs.tax.label}</span>}
                    </div>
                  )}
                </td>
                <td className="max-w-[240px] px-3 py-2 text-[12px]">
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
                  : !stage && !q.trim() && data.rows.every((r) => r.stage === DONE) ? "진행 중인 구매 건이 없습니다. 끝난 건은 [완료] 칩에서 볼 수 있습니다."
                  : "조건에 맞는 건이 없습니다."}
              </td></tr>
            )}
          </tbody>
        </table>
      </div>

      {open && <PurchaseDetail key={open.id} id={open.id} admin={admin} onClose={() => setOpenId(null)} onChanged={() => void load()} />}
    </section>
  );
}

// 서류 칸(사용자 2026-11-11) — 견적서·재가·발주서·명세서·계산서(해외는 통관 서류)가 올라왔는지 리스트에서 바로 보이게.
// 첨부 없이 넘긴 단계(견적서 없이·재가 첨부 없이·명세서 선결제/선발행)는 옅은 초록 원(마우스를 올리면 사유). 표시만 한다(파일 받기는 상세에서).
function Docs({ r }: { r: PurchaseRow }) {
  const docs: [string, boolean, boolean, string][] = [
    ["견적", r.quote_files.length > 0, !r.quote_files.length && !!r.approval.quote_skip,
     r.quote_files.at(-1)?.filename ?? (r.approval.quote_skip ? "견적서 없이 넘어감" : "견적서 없음")],
    ["재가", r.approval.files.length > 0, !r.approval.files.length && !!r.approval.done_at,
     r.approval.files.at(-1)?.filename ?? (r.approval.done_at ? "재가 완료(첨부 없이)" : "재가 서류 없음")],
    ["발주서", !!r.po.no, false, r.po.no ?? "발주서 없음"],
    ...(r.region === "overseas"
      ? [["통관", r.customs.files.length + r.customs.etc_files.length > 0, !!r.customs.done_at,
          [r.customs.files.length ? `원산지 증명서 ${r.customs.files.length}` : r.customs.no_origin ? "원산지 증명서 없음" : "",
           ...r.customs.etc_files.map((f) => f.label)].filter(Boolean).join(" · ") || (r.customs.done_at ? "첨부 없이 제출 완료" : "통관 서류 없음")] as [string, boolean, boolean, string]]
      : (["statement", "tax"] as const).map((k): [string, boolean, boolean, string] => {
          const d = k === "statement" ? r.statement : r.tax_invoice;
          const st = r.docs[k];
          const name = k === "statement" ? "거래명세서" : "세금계산서";
          return [k === "statement" ? "명세서" : "계산서", st.state === "file", st.state === "skip",
                  st.state === "file" ? d.files.at(-1)?.filename ?? name : st.state === "skip" ? `${name} ${st.label}${st.note ? ` — ${st.note}` : ""}` : `${name} 미등록(선택)`];
        })),
  ];
  return (
    <span className="inline-flex items-start">
      {/* 위에 작은 글자, 아래 원 — 서류끼리는 '/' 로 나눈다(순서가 아니라 첨부 여부라 화살표 대신, 사용자 2026-11-11).
          있음: 초록 원 + 흰 체크 · 없음: 빈 원 · 첨부 없이 확인: 옅은 초록 원 + 체크 */}
      {docs.map(([label, has, skipped, title], i) => {
        const on = has || skipped;
        return (
          <Fragment key={label}>
            {i > 0 && <span aria-hidden className="mt-[13px] w-3 shrink-0 text-center text-[13px] leading-none text-foreground/25">/</span>}
            <span title={title} className="flex w-9 flex-col items-center gap-1">
              <span className={cn("whitespace-nowrap text-[10.5px] leading-none", has ? "font-semibold text-foreground" : "text-foreground-subtle")}>{label}</span>
              <span className={cn("grid h-4 w-4 place-items-center rounded-full",
                has ? "bg-emerald-500 shadow-[0_0_0_3px_rgba(16,185,129,0.15)]" : skipped ? "bg-emerald-500/40" : "border-[1.5px] border-foreground/20 bg-surface")}>
                {on && (
                  <svg aria-hidden viewBox="0 0 12 12" className="h-2.5 w-2.5 text-white">
                    <path d="M2.5 6.2 5 8.5l4.5-5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                )}
              </span>
            </span>
          </Fragment>
        );
      })}
    </span>
  );
}

function NewPurchase({ me, onClose, onCreated }: { me: string; onClose: () => void; onCreated: (id: number) => void }) {
  const [v, setV] = useState<{ supplier: string; title: string; owner: string; note: string; region: PurchaseRegion }>(
    { supplier: "", title: "", owner: me, note: "", region: "domestic" });
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
            국내 · 해외 *
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
          <label className="grid gap-1">공급사 *<input className={INPUT} value={v.supplier} onChange={(e) => setV({ ...v, supplier: e.target.value })} placeholder="예: ㈜크레켄" autoFocus /></label>
          <label className="grid gap-1">
            <span>품목·건명 <span className="text-[12px] font-normal text-foreground-subtle">(선택)</span></span>
            <input className={INPUT} value={v.title} onChange={(e) => setV({ ...v, title: e.target.value })} placeholder="예: M8 패널마운트 커넥터" />
            {/* 사용자 2026-11-11 — 견적서를 받아야 아는 경우가 많아 비워도 된다는 걸 분명히 */}
            <span className="text-[11.5px] text-foreground-subtle">견적서를 받기 전이면 비워 두세요. 나중에 상세의 기본 정보에서 채울 수 있습니다.</span>
          </label>
          <label className="grid gap-1">담당자<input className={INPUT} value={v.owner} onChange={(e) => setV({ ...v, owner: e.target.value })} /></label>
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
