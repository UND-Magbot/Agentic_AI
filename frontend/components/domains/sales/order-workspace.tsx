// 회사 제품 추천 화면의 [수주 진행] 탭(사용자 2026-10-07) — 견적서 작성과 같은 구조:
// 왼쪽 = 진행(거래명세서 초안 바로 고치기·발급 → 결제 조건 → 회차별 입금 확인 → 출하), 오른쪽 = AI 와 대화(생각 과정·실제로 한 일·답).
// 절차·검증은 backend(sales_deals/flow.py · order_agent.py). 발급·입금 확인·출하 저장은 담당자가 버튼으로 확정한다.
// 결제 조건(사용자 2026-10-08): 선금·중도금·잔금 % 수기 + 시점 + 예정 입금일. 입금은 회사가 따로 확인하고 [입금 확인] 버튼으로 확정.
// 금액은 모두 공급가액(부가세는 거래명세서에서만). 입금 확인 취소는 관리자만.
"use client";

import { useEffect, useRef, useState } from "react";
import { AgentBubble, type AgentStep, streamNdjson } from "@/components/domains/sales/agent-chat";
import { type Act, ShipDialog, type ShipDraft, ShipForm, StatementDialog, StatementItems, shipmentDraft } from "@/components/domains/sales/deal-flow";
import { INPUT, Muted, STAGE_DONE, STAGE_SHIP_ONLY, STAGE_TONE, dealRequest, shortDate, todayIso, won } from "@/components/domains/sales/deal-format";
import { DownloadDialog, PreviewDialog } from "@/components/domains/sales/quote-workspace";
import { Icon } from "@/components/shared/ui/icon";
import type { DealListData, DealRow, DealStatement, PayTerm } from "@/lib/shared/sales-deals";
import { cn } from "@/lib/shared/utils";
import { ErrorBox, inputCls, primaryBtn, secondaryBtn } from "./proposal-actions";

type ChatMsg =
  | { role: "user"; text: string }
  | { role: "ai"; think: string; steps: AgentStep[]; answer: string; actions?: OrderAction[] };
type OrderAction = { type: "issue" } | { type: "ship" } | { type: "confirm"; index: number; label: string; amount: number };
type OrderState = {
  draft: DealStatement;
  ship: ShipDraft;
  chat: ChatMsg[];
};
type Order = { deal: DealRow; state: OrderState; admin?: boolean };
type OrderEvent =
  | { t: "think" | "answer" | "answer_set" | "error"; text: string }
  | ({ t: "step" } & AgentStep)
  | ({ t: "done"; actions: OrderAction[] } & Order);
type NoteEvent = "issue" | "payment" | "ship" | "case";

async function getJson<T>(url: string, body?: unknown): Promise<T> {
  const r = await fetch(url, body === undefined ? { cache: "no-store" }
    : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof d?.detail === "string" ? d.detail : "요청을 처리하지 못했습니다.");
  return d as T;
}

export function OrderTab({ openId, onOpen }: { openId: number | null; onOpen: (id: number | null) => void }) {
  const [view, setView] = useState<"deal" | "statement" | "pay" | "ship">("deal");
  // 건 하나를 끝까지(건별 진행) + 단계별로 여러 건을(거래명세서 발급·입금 확인·출하) — 사용자 2026-10-07.
  // 건을 연 상태(작업 화면)에서도 보기 버튼은 늘 보인다 — 건을 열면 '건별 진행', 다른 보기를 누르면 건을 닫고 그 보기로.
  const views = [["deal", "건별 진행"], ["statement", "거래명세서 발급"], ["pay", "입금 확인"], ["ship", "출하 대기"]] as const;
  const cur = openId ? "deal" : view;
  const go = (k: typeof view) => { setView(k); if (openId) onOpen(null); };
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-1.5" role="tablist" aria-label="수주 진행 보기">
        {views.map(([k, label]) => (
          <button key={k} type="button" role="tab" aria-selected={cur === k} onClick={() => go(k)}
                  className={cn("rounded-full border px-3.5 py-1.5 text-[13px] font-semibold",
                    cur === k ? "border-accent bg-accent text-white" : "border-border text-foreground-muted hover:bg-foreground/5")}>
            {label}
          </button>
        ))}
      </div>
      {openId ? <OrderWorkspace id={openId} onBack={() => onOpen(null)} /> : (
        <>
          {view === "deal" && <OrderList onOpen={onOpen} />}
          {view === "statement" && <StatementQueue onOpen={onOpen} />}
          {view === "pay" && <PayQueue onOpen={onOpen} />}
          {view === "ship" && <ShipQueue onOpen={onOpen} />}
        </>
      )}
    </div>
  );
}

/** 입금 확인 — 지금 확인할 차례인 회차(출하 전 회차, 출하 뒤 납품 후 회차)와 예정 입금일이 지난 회차를 여러 건 한꺼번에.
 * 입금은 회사가 따로 확인하고 여기서 [입금 확인]으로 확정한다(사용자 2026-10-08). 금액은 공급가액. */
function PayQueue({ onOpen }: { onOpen: (id: number) => void }) {
  const [rows, setRows] = useState<{ deal: DealRow; t: PayTerm; late: boolean }[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [dates, setDates] = useState<Record<string, string>>({});
  const [tick, setTick] = useState(0);
  const [finished, setFinished] = useState<DealRow | null>(null);
  useEffect(() => {
    let alive = true;
    getJson<DealListData>("/api/sales-deals")
      .then((d) => {
        if (!alive) return;
        const out: { deal: DealRow; t: PayTerm; late: boolean }[] = [];
        for (const r of d.rows) {
          if (r.dropped) continue;
          const late = new Set((r.flow?.overdue ?? []).map((t) => t.index));
          for (const t of r.flow?.terms ?? []) {
            const ready = !!r.flow?.steps.find((s) => s.key === `pay:${t.index}`)?.ready;
            if (!t.confirmed && (ready || late.has(t.index))) out.push({ deal: r, t, late: late.has(t.index) });
          }
        }
        // 예정일 지난 것 먼저, 그다음 예정일 빠른 순
        out.sort((a, b) => Number(b.late) - Number(a.late) || (a.t.expected_date ?? "9").localeCompare(b.t.expected_date ?? "9"));
        setRows(out);
      })
      .catch((e: Error) => { if (alive) setErr(e.message); });
    return () => { alive = false; };
  }, [tick]);

  async function confirmTerm(deal: DealRow, t: PayTerm) {
    const key = `${deal.id}-${t.index}`;
    setBusy(key);
    setErr(null);
    try {
      const after = await dealRequest(`/${deal.id}/confirm-term`, { index: t.index, date: dates[key] || todayIso() });
      await getJson(`/api/sales-deals/${deal.id}/order/note`, { event: "payment" }).catch(() => null);
      if (after.stage === STAGE_DONE) setFinished(after);
      setTick((x) => x + 1);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-4 text-[12.5px]">
      <div>
        <h2 className="text-[15px] font-semibold">입금 확인</h2>
        <p className="text-foreground-muted">지금 확인할 차례인 결제 회차입니다(예정 입금일이 지난 회차는 붉게, 맨 위). 회사에서 입금을 확인했으면 입금일을 고르고 [입금 확인]을 누르세요. 금액은 공급가액입니다.</p>
      </div>
      <ErrorBox error={err} />
      {rows === null ? <Muted>불러오는 중…</Muted> : rows.length === 0 ? (
        <p className="rounded-lg border border-dashed border-border px-3 py-6 text-center text-foreground-subtle">지금 입금 확인할 회차가 없습니다(결제 조건을 정한 건만 대상입니다).</p>
      ) : (
        <table className="w-full border-collapse text-[13px]">
          <thead>
            <tr className="border-b border-border text-left text-[12px] text-foreground-muted">
              {["건번호", "고객사", "회차", "공급가액", "예정 입금일", "입금일", ""].map((h) => <th key={h} className="px-2 py-2 font-semibold">{h}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map(({ deal, t, late }) => {
              const key = `${deal.id}-${t.index}`;
              return (
                <tr key={key} className={cn("border-b border-border/70", late && "bg-red-500/[0.07]")}>
                  <td className="px-2 py-2 font-mono text-[12px]">{deal.display_no ?? deal.deal_no ?? "번호 미부여"}</td>
                  <td className="px-2 py-2 font-medium">{deal.customer ?? "-"}</td>
                  <td className="px-2 py-2">{t.label} {t.pct}%{t.when && <span className="text-foreground-subtle"> ({t.when})</span>}</td>
                  <td className="px-2 py-2 tabular-nums">{won(t.amount)}원</td>
                  <td className={cn("px-2 py-2", late ? "font-semibold text-red-600 dark:text-red-400" : "text-foreground-muted")}>
                    {t.expected_date ? shortDate(t.expected_date) : "-"}{late ? " 지남" : ""}
                  </td>
                  <td className="px-2 py-2">
                    <input type="date" value={dates[key] ?? todayIso()} onChange={(e) => setDates((p) => ({ ...p, [key]: e.target.value }))}
                           className={cn(INPUT, "h-8 w-[140px]")} aria-label="입금일" />
                  </td>
                  <td className="whitespace-nowrap px-2 py-2 text-right">
                    <button type="button" className="mr-2 text-[12px] text-accent hover:underline" onClick={() => onOpen(deal.id)}>건 열기</button>
                    <button type="button" className={primaryBtn} disabled={!!busy}
                            onClick={() => void confirmTerm(deal, t)}>
                      {busy === key ? "저장 중…" : "입금 확인"}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {finished && <CompletionDialog deals={[finished]} onClose={() => setFinished(null)} />}
    </section>
  );
}

/** 거래명세서 발급 — 수주는 확인됐는데 거래명세서가 아직 없는 건. 눌러서 바로 발급(발급 창은 영업 건 상세와 같음). */
function StatementQueue({ onOpen }: { onOpen: (id: number) => void }) {
  const [rows, setRows] = useState<DealRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<DealRow | null>(null);
  const [issued, setIssued] = useState<DealRow | null>(null);
  const [busy, setBusy] = useState(false);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let alive = true;
    getJson<DealListData>("/api/sales-deals")
      .then((d) => { if (alive) setRows(d.rows.filter((r) => r.flow?.steps.some((s) => s.key === "statement" && s.ready))); })
      .catch((e: Error) => { if (alive) setErr(e.message); });
    return () => { alive = false; };
  }, [tick]);

  async function start(id: number) {
    setErr(null);
    try {
      setOpen(await dealRequest(`/${id}`));
    } catch (e) {
      setErr((e as Error).message);
    }
  }
  const act: Act = async (path, body) => {
    if (!open) return false;
    setBusy(true);
    try {
      const d = await dealRequest(`/${open.id}/${path}`, body);
      await getJson(`/api/sales-deals/${open.id}/order/note`, { event: "issue" }).catch(() => null);
      setIssued(d);
      setTick((t) => t + 1);
      return true;
    } catch (e) {
      setErr((e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-4 text-[12.5px]">
      <div>
        <h2 className="text-[15px] font-semibold">거래명세서 발급</h2>
        <p className="text-foreground-muted">수주는 확인됐는데 거래명세서를 아직 발급하지 않은 건입니다. [발급하기]로 견적 품목이 채워진 초안을 확인하고 발급하세요 — 발급하면 PDF·엑셀로 받을 수 있습니다.</p>
      </div>
      <ErrorBox error={err} />
      {rows === null ? <Muted>불러오는 중…</Muted> : rows.length === 0 ? (
        <p className="rounded-lg border border-dashed border-border px-3 py-6 text-center text-foreground-subtle">거래명세서를 발급할 건이 없습니다.</p>
      ) : (
        <table className="w-full border-collapse text-[13px]">
          <thead>
            <tr className="border-b border-border text-left text-[12px] text-foreground-muted">
              {["건번호", "고객사", "건명", "수주일", "공급가액", ""].map((h) => <th key={h} className="px-2 py-2 font-semibold">{h}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="border-b border-border/70">
                <td className="px-2 py-2 font-mono text-[12px]">{r.display_no ?? r.deal_no ?? "번호 미부여"}</td>
                <td className="px-2 py-2 font-medium">{r.customer ?? "-"}</td>
                <td className="max-w-[220px] truncate px-2 py-2">{r.title}</td>
                <td className="px-2 py-2 text-foreground-muted">{r.won_date ?? "-"}</td>
                <td className="px-2 py-2 tabular-nums">{won(r.flow?.total, r.quote?.currency)}</td>
                <td className="whitespace-nowrap px-2 py-2 text-right">
                  <button type="button" className="mr-2 text-[12px] text-accent hover:underline" onClick={() => onOpen(r.id)}>건 열기</button>
                  <button type="button" className={primaryBtn} disabled={busy} onClick={() => void start(r.id)}>발급하기</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {open && <StatementDialog deal={open} busy={busy} act={act} onClose={() => setOpen(null)} />}
      {issued?.statement && (
        <DownloadDialog urlFor={(fmt) => `/api/sales-deals/${issued.id}/statement/file?fmt=${fmt}`}
                        fileName={`(주)유엔디로보틱스_거래명세서_${issued.statement.buyer.name}_${issued.statement.no}`}
                        title="거래명세서를 발급했습니다 — 내려받기" onClose={() => setIssued(null)} />
      )}
    </section>
  );
}

/** 출하 대기 — 결제 조건의 출하 전 회차 입금을 모두 확인해 지금 출하할 수 있는 건. 눌러서 바로 출하 기록(제품별 사진). */
function ShipQueue({ onOpen }: { onOpen: (id: number) => void }) {
  const [rows, setRows] = useState<DealRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<DealRow | null>(null);
  const [busy, setBusy] = useState(false);
  const [tick, setTick] = useState(0);
  const [finished, setFinished] = useState<DealRow | null>(null);
  useEffect(() => {
    let alive = true;
    getJson<DealListData>("/api/sales-deals")
      .then((d) => { if (alive) setRows(d.rows.filter((r) => r.flow?.steps.some((s) => s.key === "ship" && s.ready))); })
      .catch((e: Error) => { if (alive) setErr(e.message); });
    return () => { alive = false; };
  }, [tick]);

  async function start(id: number) {
    setErr(null);
    try {
      setOpen(await dealRequest(`/${id}`));
    } catch (e) {
      setErr((e as Error).message);
    }
  }
  const act: Act = async (path, body) => {
    if (!open) return false;
    setBusy(true);
    try {
      const after = await dealRequest(`/${open.id}/${path}`, body);
      await getJson(`/api/sales-deals/${open.id}/order/note`, { event: "ship" }).catch(() => null);
      if (after.stage === STAGE_DONE) setFinished(after);
      setTick((t) => t + 1);
      return true;
    } catch (e) {
      setErr((e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-4 text-[12.5px]">
      <div>
        <h2 className="text-[15px] font-semibold">출하 대기</h2>
        <p className="text-foreground-muted">거래명세서를 발급하고 아직 출하하지 않은 건입니다(입금 확인과 상관없이 출하할 수 있음). [출하 기록]으로 제품별 수량·사진과 배송 정보를 남기면 단계가 &apos;출하 후 입금 대기&apos;(완납이면 &apos;완료 (출하·완납)&apos;)로 바뀝니다.</p>
      </div>
      <ErrorBox error={err} />
      {rows === null ? <Muted>불러오는 중…</Muted> : rows.length === 0 ? (
        <p className="rounded-lg border border-dashed border-border px-3 py-6 text-center text-foreground-subtle">지금 출하할 건이 없습니다.</p>
      ) : (
        <table className="w-full border-collapse text-[13px]">
          <thead>
            <tr className="border-b border-border text-left text-[12px] text-foreground-muted">
              {["건번호", "고객사", "건명", "결제 조건", "확인한 입금(공급가액)", ""].map((h) => <th key={h} className="px-2 py-2 font-semibold">{h}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="border-b border-border/70">
                <td className="px-2 py-2 font-mono text-[12px]">{r.display_no ?? r.deal_no ?? "번호 미부여"}</td>
                <td className="px-2 py-2 font-medium">{r.customer ?? "-"}</td>
                <td className="max-w-[200px] truncate px-2 py-2">{r.title}</td>
                <td className="px-2 py-2 text-foreground-muted">{r.flow?.pay_case_label ?? "-"}</td>
                <td className="px-2 py-2 tabular-nums">{won(r.flow?.paid)}원 / {won(r.flow?.total)}원</td>
                <td className="whitespace-nowrap px-2 py-2 text-right">
                  <button type="button" className="mr-2 text-[12px] text-accent hover:underline" onClick={() => onOpen(r.id)}>건 열기</button>
                  <button type="button" className={primaryBtn} disabled={busy} onClick={() => void start(r.id)}>출하 기록</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {open && <ShipDialog deal={open} busy={busy} act={act} onClose={() => setOpen(null)} />}
      {finished && <CompletionDialog deals={[finished]} onClose={() => setFinished(null)} />}
    </section>
  );
}

/** 수주 확인됐고 아직 끝나지 않은 건. */
function OrderList({ onOpen }: { onOpen: (id: number) => void }) {
  const [rows, setRows] = useState<DealRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    getJson<DealListData>("/api/sales-deals")
      // 새 절차로 진행 중인 건(엑셀에서 옮긴 예전 건은 명세서·결제 조건을 시작한 경우만 — backend flow.steps)
      .then((d) => { if (alive) setRows(d.rows.filter((r) => !!r.flow?.steps.length && ![STAGE_DONE, "드랍", STAGE_SHIP_ONLY].includes(r.stage))); })
      .catch((e: Error) => { if (alive) setErr(e.message); });
    return () => { alive = false; };
  }, []);
  return (
    <section className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-4">
      <div>
        <h2 className="text-[15px] font-semibold text-foreground">수주 진행</h2>
        <p className="text-[12.5px] text-foreground-muted">
          수주 확인된 건을 AI 와 대화하며 진행합니다 — 거래명세서 발급 → 결제 조건 → 회차별 입금 확인 → 출하.
          영업 건에서 [수주 확정] 뒤 [수주 진행 →]으로도 들어옵니다.
        </p>
      </div>
      <ErrorBox error={err} />
      {rows === null ? <Muted>불러오는 중…</Muted> : rows.length === 0 ? (
        <p className="rounded-lg border border-dashed border-border px-3 py-6 text-center text-[12.5px] text-foreground-subtle">
          진행 중인 수주 건이 없습니다. [제품 영업 건 관리]에서 건을 열고 [수주 확정]을 누르면 여기에 나옵니다.
        </p>
      ) : (
        <table className="w-full border-collapse text-[13px]">
          <thead>
            <tr className="border-b border-border text-left text-[12px] text-foreground-muted">
              {["건번호", "고객사", "건명", "단계", "다음 할 일", ""].map((h) => <th key={h} className="px-2 py-2 font-semibold">{h}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="cursor-pointer border-b border-border/70 hover:bg-accent/5" onClick={() => onOpen(r.id)}>
                <td className="px-2 py-2 font-mono text-[12px]">{r.display_no ?? r.deal_no ?? "번호 미부여"}</td>
                <td className="px-2 py-2 font-medium">{r.customer ?? "-"}</td>
                <td className="max-w-[220px] truncate px-2 py-2">{r.title}</td>
                <td className="px-2 py-2"><span className={cn("rounded-full px-2 py-0.5 text-[12px] font-semibold", STAGE_TONE[r.stage])}>{r.stage}</span></td>
                <td className="px-2 py-2 text-foreground-muted">{r.flow?.steps.find((s) => s.ready)?.label ?? "-"}</td>
                <td className="px-2 py-2 text-right text-accent">열기 →</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

export function OrderWorkspace({ id, onBack }: { id: number; onBack: () => void }) {
  const [o, setO] = useState<Order | null>(null);
  const [draft, setDraft] = useState<DealStatement | null>(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [live, setLive] = useState<{ user: string; think: string; steps: AgentStep[]; answer: string } | null>(null);
  const [saving, setSaving] = useState(false);
  // 출하 작성은 왼쪽 출하 칸 안에서(대화와 같이 쓰도록 — 사용자 2026-10-07). shipRef = 화면에서 고친 최신 내용
  const [shipOpen, setShipOpen] = useState(false);
  const shipRef = useRef<ShipDraft | null>(null);
  const [preview, setPreview] = useState<number | null>(null);
  const [finished, setFinished] = useState<DealRow | null>(null);
  const stageRef = useRef<string | null>(null);
  const chatEnd = useRef<HTMLDivElement>(null);
  const [admin, setAdmin] = useState(false);
  const [payDate, setPayDate] = useState(todayIso());

  function load(x: Order) {
    // 이 화면에서 마지막 절차를 마쳐 '완료'가 되는 순간 완납 팝업(처음 열 때 이미 완료인 건은 띄우지 않음)
    if (stageRef.current && stageRef.current !== STAGE_DONE && x.deal.stage === STAGE_DONE) setFinished(x.deal);
    stageRef.current = x.deal.stage;
    setO(x);
    setDraft(x.state.draft);
    setDirty(false);
  }
  useEffect(() => {
    let alive = true;
    getJson<Order>(`/api/sales-deals/${id}/order`).then((x) => { if (alive) { setAdmin(!!x.admin); load(x); } }).catch((e: Error) => { if (alive) setErr(e.message); });
    return () => { alive = false; };
  }, [id]);
  useEffect(() => { chatEnd.current?.scrollIntoView({ block: "end", behavior: "smooth" }); }, [o?.state.chat.length, live?.answer, live?.think]);

  async function run<T>(label: string, f: () => Promise<T>): Promise<T | null> {
    setBusy(label);
    setErr(null);
    try {
      return await f();
    } catch (e) {
      setErr((e as Error).message);
      return null;
    } finally {
      setBusy(null);
    }
  }
  /** 버튼으로 확정 → 영업 건 저장 → 대화에 '한 일 + 다음 할 일'. */
  async function confirm(path: string, body: unknown, note: NoteEvent): Promise<boolean> {
    const ok = await run("저장 중", async () => {
      await dealRequest(`/${id}/${path}`, body);
      load(await getJson<Order>(`/api/sales-deals/${id}/order/note`, { event: note }));
      return true;
    });
    return !!ok;
  }
  const act: Act = (path, body) => confirm(path, body, path === "shipments" || path === "shipment-edit" ? "ship" : path === "confirm-term" ? "payment" : "case");
  /** 회차 입금 확인 — 회사가 따로 확인한 뒤 버튼으로(사용자 2026-10-08). 금액 = 그 회차 공급가액. */
  const confirmTerm = (index: number) => confirm("confirm-term", { index, date: payDate || todayIso() }, "payment");
  /** 입금 확인 취소 — 관리자만. 대화에는 남기지 않고 화면만 다시 받는다. */
  const unconfirmTerm = (index: number) => run("저장 중", async () => {
    await dealRequest(`/${id}/unconfirm-term`, { index });
    load(await getJson<Order>(`/api/sales-deals/${id}/order`));
  });

  async function saveDraft(): Promise<boolean> {
    if (!draft) return false;
    const x = await run("저장 중", () => getJson<Order>(`/api/sales-deals/${id}/order/draft`, draft));
    if (x) load(x);
    return !!x;
  }
  /** 왼쪽에서 고친 초안은 대화·미리보기·발급 전에 알아서 저장한다(사용자 2026-10-07: [변경 저장]을 먼저 눌러야 하는 게 불편). */
  async function ensureSaved(): Promise<boolean> {
    if (dirty && !(await saveDraft())) return false;
    // 출하 칸에서 고친 내용도 대화 전에 저장 — AI 가 출하 정보를 채울 때 화면에서 고친 것을 덮어쓰지 않게
    const sh = shipRef.current;
    if (shipOpen && sh && o && JSON.stringify(sh) !== JSON.stringify(o.state.ship)) {
      const x = await run("저장 중", () => getJson<Order>(`/api/sales-deals/${id}/order/ship`, { ship: sh }));
      if (!x) return false;
      load(x);
    }
    return true;
  }
  async function issue() {
    if (!draft || !(await ensureSaved())) return;
    const items = draft.items.filter((it) => it.name.trim());
    if (await confirm("statement", { ...draft, items }, "issue")) setSaving(true);
  }
  async function openPreview() {
    if (await ensureSaved()) setPreview(Date.now());
  }
  async function send() {
    const m = text.trim();
    if (!m || busy) return;
    if (!(await ensureSaved())) return;
    setText("");
    setErr(null);
    setBusy("AI 가 생각하는 중");
    setLive({ user: m, think: "", steps: [], answer: "" });
    let failed: string | null = null;
    try {
      await streamNdjson<OrderEvent>(`/api/sales-deals/${id}/order/agent`, { message: m }, (ev) => {
        if (ev.t === "think") setLive((l) => (l ? { ...l, think: l.think + ev.text } : l));
        else if (ev.t === "answer") setLive((l) => (l ? { ...l, answer: l.answer + ev.text } : l));
        else if (ev.t === "answer_set") setLive((l) => (l ? { ...l, answer: ev.text } : l));
        else if (ev.t === "step") setLive((l) => (l ? { ...l, steps: [...l.steps, { ok: ev.ok, text: ev.text }] } : l));
        else if (ev.t === "error") failed = ev.text;
        else if (ev.t === "done") {
          load({ deal: ev.deal, state: ev.state });
          if (ev.actions.some((a) => a.type === "ship")) setShipOpen(true);      // AI 가 출하 정보를 채우면 출하 칸을 연다
        }
      });
    } catch (e) {
      failed = (e as Error).message;
    } finally {
      setLive(null);
      setBusy(null);
    }
    if (failed) {
      setErr(failed);
      setText(m);
    }
  }

  if (!o || !draft) {
    return (
      <section className="flex flex-col gap-3">
        <button type="button" className={cn(secondaryBtn, "self-start")} onClick={onBack}><Icon name="back" className="h-4 w-4" /> 수주 진행 목록</button>
        {err ? <ErrorBox error={err} /> : <Muted>불러오는 중…</Muted>}
      </section>
    );
  }
  const { deal, state } = o;
  const fl = deal.flow;
  const st = deal.statement;
  const edit = (d: DealStatement) => { setDraft(d); setDirty(true); };
  const stepDone = (k: string) => !!fl?.steps.find((s) => s.key === k)?.done;
  const ready = (k: string) => !!fl?.steps.find((s) => s.key === k)?.ready;
  const terms = fl?.terms ?? [];
  const late = new Set((fl?.overdue ?? []).map((t) => t.index));
  const payReady = terms.some((t) => ready(`pay:${t.index}`));
  const payDone = terms.length > 0 && terms.every((t) => t.confirmed);
  // 출하 강조('지금 할 차례')는 결제 조건을 정한 뒤에만 — 정하는 동안엔 결제 조건 칸만 강조(사용자 2026-10-08). 버튼은 그대로 누를 수 있다
  const shipNow = ready("ship") && stepDone("case");

  return (
    <div className="grid items-start gap-4 lg:grid-cols-2">
      <div className="flex min-w-0 flex-col gap-4">
        {/* 머리 · 진행 */}
        <section className="flex flex-col gap-2 rounded-xl border border-accent/30 bg-surface p-4">
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" className={secondaryBtn} onClick={onBack}><Icon name="back" className="h-4 w-4" /> 목록</button>
            <h2 className="text-[15px] font-semibold">{deal.display_no ?? deal.deal_no ?? "번호 미부여"} · {deal.customer ?? "-"}</h2>
            <span className={cn("rounded-full px-2 py-0.5 text-[12px] font-semibold", STAGE_TONE[deal.stage])}>{deal.stage}</span>
            {fl?.total != null && <span className="ml-auto text-[12.5px] text-foreground-muted">공급가액 {won(fl.total)}원 · 입금 확인 {won(fl.paid)}원</span>}
          </div>
          <ol className="flex flex-wrap gap-1.5 text-[12px]">
            {fl?.steps.map((s, i) => (
              <li key={s.key} className={cn("rounded-md px-2 py-1 font-medium",
                s.done ? "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300" : s.ready && (s.key !== "ship" || stepDone("case")) ? "bg-accent text-white" : "bg-foreground/5 text-foreground-subtle")}>
                {s.done ? "✓ " : `${i + 1}. `}{s.label}
              </li>
            ))}
          </ol>
        </section>

        {/* 거래명세서 */}
        <section className={cn("flex flex-col gap-2 rounded-xl border bg-surface p-4 text-[12.5px]", !st ? "border-accent/50" : "border-border")}>
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-[14px] font-semibold">거래명세서</h3>
            {st ? <span className="text-emerald-700 dark:text-emerald-300">✓ 발급 {st.date} · 합계 {won(st.totals?.total)}원</span>
              : <span className="text-foreground-subtle">발급 전 초안</span>}
            {st && (
              <button type="button" onClick={() => setSaving(true)} className="ml-auto inline-flex items-center gap-1 font-semibold text-accent hover:underline"
                      title="지금 발급되어 있는 거래명세서를 PDF·엑셀로 받습니다(고친 초안은 다시 발급해야 반영)">
                <Icon name="download" className="h-3.5 w-3.5" /> 다시 다운로드 (현재 발급본)
              </button>
            )}
          </div>
          <div className="grid gap-2 md:grid-cols-4">
            <Lab label="발행번호"><input value={draft.no} onChange={(e) => edit({ ...draft, no: e.target.value })} className={inputCls} /></Lab>
            <Lab label="발행일자"><input type="date" value={draft.date} onChange={(e) => edit({ ...draft, date: e.target.value })} className={inputCls} /></Lab>
          </div>
          {/* 주소는 한 줄 통째로 — 긴 주소도 끝까지 보며 오타 확인 */}
          <div className="grid gap-2 md:grid-cols-3">
            {([["reg_no", "등록번호 *"], ["name", "상호 *"], ["ceo", "성명(대표)"], ["address", "주소 *"]] as const).map(([k, lab]) => (
              <Lab key={k} label={`공급받는자 ${lab}`} wide={k === "address"}>
                <input value={draft.buyer[k] ?? ""} onChange={(e) => edit({ ...draft, buyer: { ...draft.buyer, [k]: e.target.value } })} className={inputCls} />
              </Lab>
            ))}
          </div>
          <StatementItems deal={deal} st={draft} onChange={edit} />
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" className={secondaryBtn} disabled={!dirty || !!busy} onClick={() => void saveDraft()}>
              <Icon name="check" className="h-4 w-4" /> 변경 저장
            </button>
            {dirty && <button type="button" className="text-[12px] text-foreground-subtle hover:underline" onClick={() => load(o)}>되돌리기</button>}
            <button type="button" className={cn(secondaryBtn, "ml-auto")} disabled={!!busy} onClick={() => void openPreview()}
                    title="파일로 만들기 전에 지금 초안 그대로 봅니다(빈 칸은 빈 채로)">
              <Icon name="eye" className="h-4 w-4" /> 미리보기
            </button>
            <button type="button" className={primaryBtn} disabled={!!busy} onClick={() => void issue()}
                    title={st ? "고친 초안으로 발급본을 바꿉니다(덮어쓰기)" : ""}>
              <Icon name="file-spreadsheet" className="h-4 w-4" /> {st ? "고쳐서 다시 발급 (덮어쓰기)" : "거래명세서 발급"}
            </button>
          </div>
        </section>

        {/* 결제 조건 — 정할 차례면 눈에 띄게. 선금·중도금·잔금 % 수기(사용자 2026-10-08) */}
        <section className={cn("flex flex-col gap-2 rounded-xl border bg-surface p-4 text-[12.5px]", ready("case") ? HL : "border-border")}>
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-[14px] font-semibold">결제 조건 {fl?.pay_case_label && <span className="font-normal text-foreground-muted">· {fl.pay_case_label}</span>}</h3>
            {ready("case") && <NowBadge>지금 정할 차례</NowBadge>}
          </div>
          {!st ? <Muted>거래명세서를 발급하면 정할 수 있습니다.</Muted> : (
            <TermsEditor key={JSON.stringify(deal.pay_terms) + deal.payments.length} deal={deal} busy={!!busy}
                         onSave={(t) => void confirm("terms", { terms: t }, "case")} />
          )}
        </section>

        {/* 입금 확인·출하 — 서로 순서 없음(출하는 입금 확인 없이도, 사용자 2026-10-08), 출하 칸을 먼저 */}
        {(["ship", "pay"] as const).map((k) => k === "pay" ? (
          <section key="pay" className={cn("flex flex-col gap-2 rounded-xl border bg-surface p-4 text-[12.5px]",
            payReady ? HL : late.size ? "border-red-400" : "border-border", !payReady && !payDone && !late.size && "opacity-60")}>
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-[14px] font-semibold">입금 확인</h3>
              {payReady && <NowBadge>지금 할 차례</NowBadge>}
              {payDone && <span className="text-emerald-700 dark:text-emerald-300">✓ 완납</span>}
              {payReady && (
                <label className="ml-auto flex items-center gap-1.5 text-foreground-muted">입금일
                  <input type="date" value={payDate} onChange={(e) => setPayDate(e.target.value)} className={cn(INPUT, "h-8 w-[140px]")} />
                </label>
              )}
            </div>
            {!terms.length ? <Muted>결제 조건을 정하면 회차별로 확인합니다.</Muted> : (
              <ul className="flex flex-col gap-1">
                {terms.map((t) => {
                  const now = ready(`pay:${t.index}`);
                  return (
                    <li key={t.index} className={cn("flex flex-wrap items-center gap-2 rounded-md px-2 py-1.5",
                      t.confirmed ? "bg-emerald-500/8" : late.has(t.index) ? "bg-red-500/[0.08]" : now ? "bg-accent/8" : "bg-foreground/[0.03]")}>
                      <b>{t.label} {t.pct}%</b>
                      {t.when && <span className="text-foreground-subtle">{t.when}</span>}
                      <b className="tabular-nums">{won(t.amount)}원</b>
                      {t.expected_date && (
                        <span className={cn(late.has(t.index) ? "font-semibold text-red-600 dark:text-red-400" : "text-foreground-muted")}>
                          예정 {shortDate(t.expected_date)}{late.has(t.index) ? ` · ${fl?.overdue.find((x) => x.index === t.index)?.days}일 지남` : ""}
                        </span>
                      )}
                      <span className="ml-auto flex items-center gap-2">
                        {t.confirmed ? (
                          <>
                            <span className="text-emerald-700 dark:text-emerald-300">✓ 입금 확인 {shortDate(t.paid_date)}</span>
                            {admin && <button type="button" disabled={!!busy} onClick={() => void unconfirmTerm(t.index)}
                                              className="text-[11.5px] text-foreground-subtle hover:text-red-600 hover:underline">확인 취소</button>}
                          </>
                        ) : (
                          <button type="button" disabled={!!busy || !now && !late.has(t.index)}
                                  onClick={() => void confirmTerm(t.index)} className={cn(now ? primaryBtn : secondaryBtn, "h-8")}>
                            입금 확인
                          </button>
                        )}
                      </span>
                    </li>
                  );
                })}
              </ul>
            )}
            {!payReady && !payDone && terms.length > 0 && (
              <Muted>결제 조건을 정하면 확인합니다.</Muted>
            )}
          </section>
        ) : (
          <section key="ship" className={cn("flex flex-col gap-2 rounded-xl border bg-surface p-4 text-[12.5px]",
            shipNow ? HL : "border-border", !shipNow && !stepDone("ship") && "opacity-60")}>
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-[14px] font-semibold">출하</h3>
              {shipNow && <NowBadge>지금 할 차례</NowBadge>}
              {stepDone("ship") && <span className="text-emerald-700 dark:text-emerald-300">✓ {deal.shipments.length}회 기록</span>}
              {!shipOpen && (
                <button type="button" className={cn(shipNow ? primaryBtn : secondaryBtn, "ml-auto")} disabled={!st || !!busy}
                        onClick={() => setShipOpen(true)}>
                  {stepDone("ship") ? "출하 기록 수정" : "출하 기록 작성"}
                </button>
              )}
            </div>
            {shipOpen && stepDone("ship") && deal.shipments.length > 0 && (
              // 이미 출하했으면 방금 남긴 기록을 불러와 고친다(사용자 2026-10-07 — 새 출하 입력 창이 아니라)
              <ShipForm key={`edit-${deal.shipments.length}`} deal={deal} busy={!!busy} act={act}
                        initial={shipmentDraft(deal.shipments[deal.shipments.length - 1])} editIndex={deal.shipments.length - 1}
                        onSaved={() => setShipOpen(false)} onCancel={() => setShipOpen(false)} />
            )}
            {shipOpen && !stepDone("ship") && (
              <>
                <p className="text-[12px] text-foreground-subtle">대화창에 &quot;오늘 경동택배로 1세트 보냈어, 운송장 1234&quot;처럼 말하면 이 칸에 채워집니다. 사진은 여기서 올려 주세요.</p>
                <ShipForm key={JSON.stringify(state.ship)} deal={deal} busy={!!busy} act={act} initial={state.ship}
                          onChange={(d) => { shipRef.current = d; }}
                          onSaved={() => { shipRef.current = null; setShipOpen(false); }}
                          onCancel={() => { shipRef.current = null; setShipOpen(false); }} />
              </>
            )}
            {!st && <Muted>거래명세서를 발급하면 출하를 기록할 수 있습니다.</Muted>}
          </section>
        ))}
        <ErrorBox error={err} />
      </div>

      {/* 오른쪽: AI 와 대화 */}
      <aside className="flex flex-col overflow-hidden rounded-xl border border-border bg-surface lg:sticky lg:top-4 lg:h-[calc(100svh-2rem)]">
        <div className="flex items-center gap-2 border-b border-border px-4 py-3">
          <span className="und-grad grid h-7 w-7 place-items-center rounded-full text-[11px] font-bold text-white" aria-hidden>AI</span>
          <div className="min-w-0">
            <h2 className="text-[14px] font-semibold leading-tight">AI 와 같이 수주 진행</h2>
            <p className="truncate text-[11.5px] text-foreground-subtle">말로 명세서·결제 조건·출하 정보를 채웁니다 · 발급·입금 확인·출하 저장은 버튼으로 확정</p>
          </div>
        </div>
        <div className="scroll-thin flex min-h-[320px] flex-1 flex-col gap-3 overflow-y-auto px-4 py-3" aria-live="polite">
          {state.chat.map((m, i) => m.role === "user" ? <UserBubble key={i} text={m.text} /> : (
            <div key={i} className="flex flex-col gap-1.5">
              <AgentBubble m={{ think: m.think, steps: m.steps, answer: m.answer, live: false }} />
              {i === state.chat.length - 1 && m.actions?.length ? (
                <div className="ml-8 flex flex-wrap gap-1.5">
                  {m.actions.map((a, j) => a.type === "issue" ? (
                    <ActBtn key={j} disabled={!!busy} onClick={() => void issue()}>거래명세서 발급</ActBtn>
                  ) : a.type === "ship" ? (
                    <ActBtn key={j} disabled={!!busy || !st || !deal.pay_case} onClick={() => setShipOpen(true)}>출하 칸 열기</ActBtn>
                  ) : (
                    <ActBtn key={j} disabled={!!busy || !!terms[a.index]?.confirmed}
                            onClick={() => void confirmTerm(a.index)}>
                      {terms[a.index]?.confirmed ? "확인됨" : `입금 확인 — ${a.label} ${won(a.amount)}원`}
                    </ActBtn>
                  ))}
                </div>
              ) : null}
            </div>
          ))}
          {live && (
            <>
              <UserBubble text={live.user} />
              <AgentBubble m={{ think: live.think, steps: live.steps, answer: live.answer, live: true }} />
            </>
          )}
          {busy && !live && <p className="flex items-center gap-2 text-[12.5px] text-foreground-subtle"><Icon name="refresh" className="h-4 w-4 animate-spin text-accent" /> {busy}…</p>}
          <div ref={chatEnd} />
        </div>
        <div className="border-t border-border p-3">
          <div className="flex flex-col rounded-xl border border-foreground/15 bg-background focus-within:ring-1 focus-within:ring-accent/40">
            <textarea rows={3} maxLength={1000} value={text} disabled={!!busy} onChange={(e) => setText(e.target.value)}
                      onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(); } }}
                      aria-label="AI 에게 보내기"
                      placeholder="예: 등록번호 000-00-00000 주소 경기도 포천시… / PPM 3개로 / 선금 30% 발주 시 10월 20일, 잔금 나머지 납품 후 / 선금 들어왔어 / 오늘 경동택배로 보냈어 운송장 1234 (Enter 보내기 · 왼쪽에서 고친 초안은 보낼 때 같이 저장)"
                      className="w-full resize-none bg-transparent px-3 pt-2.5 text-[13px] leading-relaxed placeholder:text-foreground-subtle/70 focus:outline-none" />
            <div className="flex items-center px-2 pb-2">
              <button type="button" aria-label="보내기" disabled={!text.trim() || !!busy} onClick={() => void send()}
                      className="und-grad ml-auto grid h-8 w-8 place-items-center rounded-full text-white transition hover:brightness-105 disabled:opacity-40">
                <Icon name="send" className="h-4 w-4" />
              </button>
            </div>
          </div>
        </div>
      </aside>

      {saving && st && (
        <DownloadDialog urlFor={(fmt) => `/api/sales-deals/${deal.id}/statement/file?fmt=${fmt}`}
                        fileName={`(주)유엔디로보틱스_거래명세서_${st.buyer.name}_${st.no}`} title="거래명세서 내려받기"
                        onClose={() => setSaving(false)} />
      )}
      {preview && (
        <PreviewDialog key={preview} url={`/api/sales-deals/${id}/order/preview?t=${preview}`} title="거래명세서 미리보기"
                       sub="파일로 만들기 전, 저장된 초안 그대로 · 빈 칸은 빈 채로" onClose={() => setPreview(null)} />
      )}
      {finished && <CompletionDialog deals={[finished]} onClose={() => setFinished(null)} />}
    </div>
  );

}

const HL = "border-accent ring-2 ring-accent/25 shadow-sm";

type TermDraft = { label: string; pct: string; when: string; expected_date: string; fixed: boolean };
/** 항상 나오는 회차(사용자 2026-10-08: 회차 추가 대신 선금·중도금·잔금 고정, % 와 시점은 수기 — 시점은 비워 두어도 됨) */
const FIXED = ["선금", "중도금", "잔금"];

/** 저장된 결제 조건 → 고정 세 줄(+ 예전에 다른 이름으로 저장한 회차가 있으면 그 줄도) */
function termRows(saved: DealRow["pay_terms"]): TermDraft[] {
  const rows: TermDraft[] = FIXED.map((label) => {
    const t = saved.find((x) => x.label === label);
    return { label, pct: t ? String(t.pct) : "", when: t?.when ?? "", expected_date: t?.expected_date ?? "", fixed: true };
  });
  for (const t of saved) {
    if (!FIXED.includes(t.label)) rows.push({ label: t.label, pct: String(t.pct), when: t.when ?? "", expected_date: t.expected_date ?? "", fixed: false });
  }
  return rows;
}

/** 결제 조건 편집 — 선금·중도금·잔금 세 줄이 항상 있고, 줄마다 %·시점(수기, 비워도 됨)·예정 입금일. 0% 나 빈 줄은 저장하지 않는다.
 *  입금 확인한 회차는 % 를 못 바꾼다(예정일은 됨). */
function TermsEditor({ deal, busy, onSave }: { deal: DealRow; busy: boolean; onSave: (t: { label: string; pct: number; when: string; expected_date: string | null }[]) => void }) {
  const saved = deal.pay_case ? deal.pay_terms : [];
  const doneIdx = new Set(deal.payments.map((p) => p.term).filter((x): x is number => x != null));
  const doneLabels = new Set(saved.filter((_, i) => doneIdx.has(i)).map((t) => t.label));
  const [rows, setRows] = useState<TermDraft[]>(() => termRows(saved));
  const [editing, setEditing] = useState(!saved.length);
  const total = deal.flow?.total ?? 0;
  const used = rows.filter((r) => Number(r.pct) > 0);
  const sum = used.reduce((s, r) => s + Number(r.pct), 0);
  const okSum = Math.abs(sum - 100) < 0.01;
  const setRow = (i: number, patch: Partial<TermDraft>) => setRows((p) => p.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  // backend 와 같게: 금액은 저장되는 회차(0% 아닌 줄) 기준, 마지막 회차가 끝전
  const amountOf = (r: TermDraft) => {
    const k = used.indexOf(r);
    if (k < 0) return null;
    if (k < used.length - 1) return Math.round(total * Number(r.pct) / 100);
    return Math.round(total - used.slice(0, -1).reduce((s, x) => s + Math.round(total * Number(x.pct) / 100), 0));
  };

  if (!editing) {
    return (
      <div className="flex flex-wrap items-center gap-2">
        {saved.map((t, i) => (
          <span key={i} className="rounded-md bg-foreground/[0.04] px-2 py-1">
            <b>{t.label} {t.pct}%</b> <span className="text-foreground-subtle">{[t.when, t.expected_date ? `예정 ${shortDate(t.expected_date)}` : "예정일 미정"].filter(Boolean).join(" · ")}</span>
          </span>
        ))}
        <button type="button" className="ml-auto text-[12px] font-semibold text-accent hover:underline" disabled={busy} onClick={() => setEditing(true)}>고치기</button>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-2">
      <table className="w-full border-collapse">
        <thead>
          <tr className="border-b border-border text-left text-foreground-muted">
            {["구분", "비율(%)", "시점", "예정 입금일", "금액(공급가액)"].map((h) => <th key={h} className="py-1 pr-1.5 font-semibold">{h}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => {
            const locked = doneLabels.has(r.label);
            const off = !(Number(r.pct) > 0);
            const amt = amountOf(r);
            return (
              <tr key={r.label} className={cn("border-b border-border/60", off && "text-foreground-subtle")}>
                <td className="w-24 py-1 pr-1.5 font-semibold">{r.label}{!r.fixed && <span className="ml-1 text-[11px] font-normal text-foreground-subtle">(예전)</span>}</td>
                <td className="w-24 py-1 pr-1.5">
                  <input value={r.pct} disabled={locked} inputMode="decimal" placeholder="0" aria-label={`${r.label} 비율`}
                         onChange={(e) => setRow(i, { pct: e.target.value.replace(/[^\d.]/g, "") })} className={cn(INPUT, "h-8 text-right")} />
                </td>
                <td className="w-32 py-1 pr-1.5">
                  <input value={r.when} disabled={off} maxLength={30} placeholder="예: 발주 시" aria-label={`${r.label} 시점`}
                         onChange={(e) => setRow(i, { when: e.target.value })} className={cn(INPUT, "h-8")} />
                </td>
                <td className="w-36 py-1 pr-1.5">
                  <input type="date" value={r.expected_date} disabled={off} aria-label={`${r.label} 예정 입금일`}
                         onChange={(e) => setRow(i, { expected_date: e.target.value })} className={cn(INPUT, "h-8")} />
                </td>
                <td className="py-1 pr-1.5 text-right tabular-nums">
                  {locked ? <span className="mr-2 text-[11px] text-emerald-700 dark:text-emerald-300">입금 확인됨</span> : null}
                  {amt != null ? won(amt) : <Muted>-</Muted>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[12px] text-foreground-subtle">쓰지 않는 회차는 비워 두세요(0% 는 저장하지 않음).</span>
        <span className={cn("ml-auto", okSum ? "text-foreground-muted" : "font-semibold text-red-600")}>합계 {sum}%{okSum ? "" : " — 100% 가 되게"}</span>
        {saved.length > 0 && <button type="button" className={secondaryBtn} disabled={busy} onClick={() => setEditing(false)}>취소</button>}
        <button type="button" className={primaryBtn} disabled={busy || !used.length || !okSum}
                onClick={() => onSave(used.map((r) => ({ label: r.label, pct: Number(r.pct), when: r.when.trim(), expected_date: r.expected_date || null })))}>
          <Icon name="check" className="h-4 w-4" /> 결제 조건 저장
        </button>
      </div>
    </div>
  );
}

function NowBadge({ children }: { children: React.ReactNode }) {
  return <span className="rounded-full bg-accent px-2 py-0.5 text-[11px] font-bold text-white">{children}</span>;
}

function nowText(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** 출하·입금까지 모든 절차가 끝났을 때 — 완납 안내와 한 건 요약(사용자 2026-10-07). 여러 건이 한꺼번에 끝나면 모두. */
export function CompletionDialog({ deals, onClose }: { deals: DealRow[]; onClose: () => void }) {
  const at = nowText();
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4" onClick={onClose}>
      <div role="dialog" aria-label="수주 진행 완료" onClick={(e) => e.stopPropagation()}
           className="scroll-thin flex max-h-[90svh] w-full max-w-lg flex-col gap-3 overflow-y-auto rounded-2xl border border-border bg-surface p-5 shadow-2xl">
        <div className="flex items-center gap-3">
          <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-emerald-500 text-[20px] font-bold text-white">✓</span>
          <div>
            <h3 className="text-[16px] font-bold">{deals.length > 1 ? `${deals.length}건 완납 · 출하 완료` : "완납 · 출하 완료"}</h3>
            <p className="text-[12px] text-foreground-muted">{at} · 모든 절차가 끝났습니다</p>
          </div>
        </div>
        {deals.map((d) => {
          const items = d.statement?.items ?? [];
          const ship = d.shipments[d.shipments.length - 1];
          return (
            <dl key={d.id} className="grid grid-cols-[84px_1fr] gap-x-3 gap-y-1 rounded-xl bg-foreground/[0.03] p-3 text-[12.5px]">
              <dt className="text-foreground-muted">건번호</dt><dd className="font-mono">{d.display_no ?? d.deal_no ?? "번호 미부여"}</dd>
              <dt className="text-foreground-muted">고객사</dt><dd className="font-semibold">{d.customer ?? "-"}</dd>
              <dt className="text-foreground-muted">제품</dt>
              <dd>{items.length ? items.slice(0, 4).map((it) => `${it.name} × ${it.qty ?? "-"}`).join(", ") + (items.length > 4 ? ` 외 ${items.length - 4}` : "") : d.title}</dd>
              <dt className="text-foreground-muted">공급가액</dt><dd className="font-semibold">{won(d.flow?.total)}원</dd>
              <dt className="text-foreground-muted">결제 조건</dt><dd>{d.flow?.pay_case_label ?? "-"}</dd>
              <dt className="text-foreground-muted">입금</dt>
              <dd>{d.payments.map((p) => `${p.date} ${won(p.amount)}원`).join(" · ") || "-"}</dd>
              <dt className="text-foreground-muted">출하</dt>
              <dd>{ship ? [ship.date, ship.carrier, ship.tracking.length ? `운송장 ${ship.tracking.join(", ")}` : null].filter(Boolean).join(" · ") : "-"}</dd>
            </dl>
          );
        })}
        <button type="button" className={cn(primaryBtn, "self-end")} onClick={onClose}>확인</button>
      </div>
    </div>
  );
}

function Lab({ label, children, wide }: { label: string; children: React.ReactNode; wide?: boolean }) {
  return <label className={cn("flex flex-col gap-1", wide && "md:col-span-3")}><span className="text-[11.5px] text-foreground-muted">{label}</span>{children}</label>;
}

function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <div className="bubble-user max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md px-3.5 py-2 text-[13px] leading-relaxed text-white">{text}</div>
    </div>
  );
}

function ActBtn({ children, ...p }: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button type="button" {...p} className="rounded-full border border-accent/50 px-3 py-1 text-[12px] font-semibold text-accent hover:bg-accent/5 disabled:opacity-40">{children}</button>;
}
