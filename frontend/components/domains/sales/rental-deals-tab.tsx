// [대여 제품 목록] 탭(사용자 2026-11-10) — 대여 건(유상·무상)의 제품을 품목 줄마다 한 줄씩. 줄을 누르면 오른쪽 패널(화면 절반)에
// 그 제품의 S/N 과 거꾸로 고객사·대여 건 정보(담당자·견적서·대여 기간·회수)가 열린다.
// 상태는 수주·출하 단계 없이 간단하게: 견적(유상 수주 전) → 대여 예정 → 대여 중(대여 시작일부터 자동) → 회수 중 → 회수 완료,
// 반납일이 지났는데 회수 완료 전이면 '반납일 지남'. 금액은 보이지 않는다.
// 무상 대여 건은 영업 건 관리에 나오지 않고 여기서만 — [+ 무상 대여 건 등록]으로 무상 대여 견적서를 써서 발행하면 건이 생긴다.
"use client";

import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { Icon } from "@/components/shared/ui/icon";
import { Muted, RegionBadge } from "@/components/domains/sales/deal-format";
import { primaryBtn, secondaryBtn } from "@/components/domains/sales/proposal-actions";
import { PreviewDialog } from "@/components/domains/sales/quote-workspace";
import { SnPanel } from "@/components/domains/sales/unit-register";
import type { IssueUnits, RentalDetail, RentalPhase, RentalProductList, RentalProductRow, SerialLoadResult } from "@/lib/shared/sales-deals";
import { cn } from "@/lib/shared/utils";

// sales-deals.ts 는 서버 전용(next/headers)이라 값은 여기 — 타입만 가져온다
const RENTAL_PHASES: RentalPhase[] = ["견적", "대여 예정", "대여 중", "회수 중", "반납일 지남", "회수 완료"];

const PHASE_TONE: Record<RentalPhase, string> = {
  "견적": "bg-violet-500/12 text-violet-700 dark:text-violet-300",
  "대여 예정": "bg-sky-500/12 text-sky-700 dark:text-sky-300",
  "대여 중": "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300",
  "회수 중": "bg-amber-500/12 text-amber-700 dark:text-amber-300",
  "반납일 지남": "bg-red-500/12 text-red-700 dark:text-red-300",
  "회수 완료": "bg-foreground/5 text-foreground-subtle",
};
type View = "" | "soon" | RentalPhase;
type Fee = "" | "paid" | "free";
/** 반납일·상태 칸이 쓰는 건 정보 */
type RentalWhen = Pick<RentalProductRow, "phase" | "end" | "start" | "days_left" | "returned" | "return_started">;
const isLate = (r: RentalWhen) => r.phase === "반납일 지남";
const isSoon = (r: RentalWhen, soon: number) => r.days_left != null && r.days_left >= 0 && r.days_left <= soon && r.phase !== "회수 완료";

/** 오늘(한국 날짜) YYYY-MM-DD */
const kstToday = () => new Date().toLocaleDateString("sv-SE", { timeZone: "Asia/Seoul" });

async function call<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(`/api/sales-deals${path}`, body === undefined ? { cache: "no-store" }
    : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof d?.detail === "string" ? d.detail : "요청을 처리하지 못했습니다.");
  return d as T;
}

/** 연 패널 — 제품 줄(line 있음) 또는 건(채팅 이동 버튼처럼 건만 알 때) */
type Opened = { deal: number; line: number | null };

export function RentalDealsTab({ openDealId = null, onOpenDeal, onQuote, onNewFree }: {
  /** 처음부터 열 건(채팅의 이동 버튼 ?tab=rentals&deal=) */
  openDealId?: number | null;
  /** 유상 대여 건 — [제품 영업 건 관리]에서 열기(수주 확정·거래명세서는 거기서) */
  onOpenDeal: (dealId: number) => void;
  /** [견적서 고치기 →] — 견적서 탭 작업 화면 */
  onQuote: (quoteId: number) => void;
  /** [+ 무상 대여 건 등록] — 무상 대여 견적서를 새로 만들어 연다 */
  onNewFree: () => Promise<void>;
}) {
  const [data, setData] = useState<RentalProductList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<View>("");
  const [fee, setFee] = useState<Fee>("");
  const [q, setQ] = useState("");
  const [opened, setOpened] = useState<Opened | null>(openDealId != null ? { deal: openDealId, line: null } : null);
  const [creating, setCreating] = useState(false);
  const [lateOpen, setLateOpen] = useState(true);

  const reload = useCallback(() => {
    call<RentalProductList>("/rental-products").then(setData).catch((e: Error) => setError(e.message));
  }, []);
  useEffect(reload, [reload]);

  const soon = data?.soon_days ?? 30;
  const rows = useMemo(() => {
    const t = q.trim().toLowerCase();
    const snHit = (r: RentalProductRow) => !!t && r.serials.some((x) => x.toLowerCase().includes(t));
    return (data?.rows ?? []).filter((r) => {
      if (fee && (fee === "free") !== r.free) return false;
      if (view === "soon" && !isSoon(r, soon)) return false;
      if (view && view !== "soon" && r.phase !== view) return false;
      // 회수 완료 건의 제품은 [회수 완료] 칩에서만(영업 건 관리의 완료 건과 같게) — S/N 으로 찾을 때는 보인다
      if (!view && r.phase === "회수 완료" && !snHit(r)) return false;
      return !t || [r.product, r.part, r.customer, r.contact, r.owner, r.no, r.title, ...r.serials].join(" ").toLowerCase().includes(t);
    });
  }, [data, view, fee, q, soon]);
  // S/N 으로 찾으면 그 S/N 이 든 제품 줄을 주황으로
  const snText = q.trim().toLowerCase();
  const snMatch = (r: RentalProductRow) => snText.length >= 3 && r.serials.some((x) => x.toLowerCase().includes(snText));

  // 상세는 오른쪽 패널(화면 절반, 영업 건 상세와 같은 방식) — Esc 로 닫는다
  const openRow = opened ? data?.rows.find((r) => r.deal_id === opened.deal && (opened.line == null || r.line_no === opened.line)) ?? null : null;
  useEffect(() => {
    if (!opened) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpened(null); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [opened]);

  // 반납일 지난 건(건마다 한 줄) — 하루 한 번 팝업
  const late = useMemo(() => {
    const seen = new Set<number>();
    return (data?.rows ?? []).filter((r) => {
      if (!isLate(r) || seen.has(r.deal_id)) return false;
      seen.add(r.deal_id);
      return true;
    });
  }, [data]);
  const seenToday = () => { try { return localStorage.getItem("rental-return-late-seen") === data?.today; } catch { return false; } };
  const closeLate = () => {
    setLateOpen(false);
    try { localStorage.setItem("rental-return-late-seen", data?.today ?? ""); } catch { /* 무시 */ }
  };

  const newFree = async () => {
    setCreating(true);
    setError(null);
    try { await onNewFree(); } catch (e) { setError(e instanceof Error ? e.message : "만들지 못했습니다."); setCreating(false); }
  };

  if (error && !data) return <p className="py-16 text-center text-[13px] text-foreground-muted">{error}</p>;
  if (!data) return <p className="py-16 text-center text-[13px] text-foreground-subtle">대여 제품을 불러오는 중…</p>;

  const all = data.rows;
  const count = (f: (r: RentalProductRow) => boolean) => all.filter(f).length;
  const chips: [View, string, number, string][] = [
    ["", "전체", count((r) => r.phase !== "회수 완료"), "bg-foreground/8 text-foreground"],
    ["soon", `반납 ${soon}일 안`, count((r) => isSoon(r, soon)), "bg-amber-500/12 text-amber-700 dark:text-amber-300"],
    ...RENTAL_PHASES.map((p) => [p, p === "반납일 지남" ? "⏰ 반납일 지남" : p, data.counts[p] ?? 0, PHASE_TONE[p]] as [View, string, number, string]),
  ];

  return (
    <div className="w-full pb-2">
      <header className="mb-3 flex flex-wrap items-end justify-between gap-3">
        <p className="max-w-[880px] text-[12.5px] text-foreground-muted">
          유상·무상 대여 제품을 품목마다 한 줄씩 봅니다 — 대여 시작일이 되면 자동으로 &lsquo;대여 중&rsquo;, 반납일이 지나면 &lsquo;반납일 지남&rsquo;.
          줄을 누르면 오른쪽에 그 제품의 S/N 과 고객사·대여 건 정보(대여 기간·회수·견적서)가 열립니다. 무상 대여 건은 [제품 영업 건 관리]에 나오지
          않고 여기서만 관리합니다. (기준일 {data.today})
        </p>
        <button type="button" onClick={() => void newFree()} disabled={creating}
                className="h-9 rounded-lg bg-accent px-4 text-[13px] font-semibold text-white hover:opacity-90 disabled:opacity-60">
          {creating ? "만드는 중…" : "+ 무상 대여 건 등록"}
        </button>
      </header>
      {error && <p className="mb-3 rounded-lg bg-red-500/10 px-3 py-2 text-[13px] text-red-700 dark:text-red-300">{error}</p>}
      <div className="mb-3 flex flex-wrap items-center gap-2">
        {/* 상태 칩은 0건이어도 늘 보인다 · 칩 수 = 제품 줄 수 */}
        {chips.map(([k, label, n, tone]) => (
          <button key={k || "all"} type="button" aria-pressed={view === k} onClick={() => setView(view === k ? "" : k)}
                  className={cn("rounded-full px-3 py-1 text-[12.5px] font-semibold", tone, view === k && "ring-2 ring-accent/60")}>
            {label} {n}
          </button>
        ))}
        <span className="mx-1 h-5 w-px bg-border" aria-hidden />
        {([["", "유상·무상"], ["paid", `유상 ${data.paid}`], ["free", `무상 ${data.free}`]] as [Fee, string][]).map(([k, label]) => (
          <button key={k || "both"} type="button" aria-pressed={fee === k} onClick={() => setFee(k)}
                  className={cn("rounded-full px-3 py-1 text-[12.5px] font-semibold", fee === k ? "bg-accent text-white" : "bg-foreground/6 text-foreground-muted hover:bg-foreground/10")}>
            {label}
          </button>
        ))}
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="제품·S/N·고객사·담당자·번호 검색" aria-label="대여 제품 검색"
               className="ml-auto h-9 w-64 rounded-lg border border-border bg-surface px-3 text-[13px] outline-none focus:border-accent" />
      </div>
      {rows.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border p-8 text-center text-[13px] text-foreground-muted">
          {all.length ? "조건에 맞는 대여 제품이 없습니다." : "아직 대여 제품이 없습니다 — 유상 대여는 견적서 수기 작성에서 [제품 대여]로, 무상 대여는 위의 [+ 무상 대여 건 등록]으로 만듭니다."}
        </div>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-border">
          <table className="w-full min-w-[1100px] border-collapse text-[13px]">
            <thead>
              <tr className="border-b border-border bg-foreground/[0.03] text-left text-[12px] text-foreground-muted">
                {["대여 제품", "수량 · S/N", "고객사 · 견적서", "구분", "대여 기간", "반납일", "상태"].map((h) => (
                  <th key={h} className={cn("whitespace-nowrap px-3 py-2 font-semibold", h === "반납일" && "text-foreground")}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => {
                const on = opened?.deal === r.deal_id && opened.line === r.line_no;
                return (
                  <tr key={`${r.deal_id}-${r.line_no ?? `i${i}`}`} onClick={() => setOpened({ deal: r.deal_id, line: r.line_no })}
                      className={cn("cursor-pointer border-b border-border/60 hover:bg-accent/[0.04]", r.phase === "회수 완료" && "text-foreground-muted",
                        snMatch(r) && "bg-amber-500/10", on && "bg-accent/[0.08]",
                        isLate(r) && "bg-red-500/[0.07] shadow-[inset_3px_0_0_#dc2626] hover:bg-red-500/[0.1]")}>
                    <td className="max-w-[300px] px-3 py-2">
                      <div className="truncate font-semibold" title={r.product}>{r.product || <Muted>-</Muted>}</div>
                      {r.part && r.part !== r.product && <div className="truncate text-[12px] text-foreground-muted" title={r.part}>{r.part}</div>}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 tabular-nums">
                      {r.qty}개
                      <span className={cn("ml-1.5 text-[11.5px]", r.sn >= r.qty ? "text-emerald-700 dark:text-emerald-300" : "text-foreground-subtle")}>
                        S/N {r.sn}/{r.qty}
                      </span>
                    </td>
                    <td className="max-w-[240px] px-3 py-2">
                      <div className="truncate" title={r.customer}>{r.customer || <Muted>고객사 미기재</Muted>}</div>
                      <div className="truncate font-mono text-[11.5px] text-foreground-subtle" title={r.title}>{r.no}</div>
                    </td>
                    <td className="whitespace-nowrap px-3 py-2">
                      <span className={cn("rounded px-1.5 py-0.5 text-[11.5px] font-semibold",
                        r.free ? "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300" : "bg-blue-500/10 text-blue-700 dark:text-blue-300")}>
                        {r.free ? "무상" : "유상"}
                      </span>
                      <RegionBadge overseas={r.overseas} className="ml-1 py-0.5" />
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 tabular-nums">
                      {r.start ? `${r.start} ~ ${r.end ?? "미정"}` : <span className="font-semibold text-amber-700 dark:text-amber-300">시작일 미정{r.months ? ` · ${r.months}개월` : ""}</span>}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 tabular-nums"><ReturnCell r={r} soon={soon} /></td>
                    <td className="whitespace-nowrap px-3 py-2">
                      <span className={cn("rounded-full px-2 py-0.5 text-[12px] font-semibold", PHASE_TONE[r.phase])}>{r.phase}</span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {opened && openRow && (
        <div className="fixed inset-0 z-40 flex justify-end bg-black/25" onClick={() => setOpened(null)}>
          <aside role="dialog" aria-label={`${opened.line != null ? openRow.product : openRow.customer} 상세`} onClick={(e) => e.stopPropagation()}
                 className="scroll-thin h-full w-full max-w-[max(680px,50vw)] overflow-y-auto bg-background shadow-2xl">
            <div className="sticky top-0 z-10 flex items-start justify-between gap-3 border-b border-border bg-background/95 px-5 py-4 backdrop-blur">
              <div className="min-w-0">
                <div className="font-mono text-[12px] text-foreground-subtle">{openRow.no}</div>
                <h2 className="mt-0.5 truncate text-[18px] font-bold">{opened.line != null ? openRow.product : openRow.customer || "고객사 미기재"}</h2>
                <p className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[13px] text-foreground-muted">
                  <span className={cn("rounded px-1.5 py-0.5 text-[11.5px] font-semibold",
                    openRow.free ? "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300" : "bg-blue-500/10 text-blue-700 dark:text-blue-300")}>
                    {openRow.free ? "무상 대여" : "유상 대여"}
                  </span>
                  <RegionBadge overseas={openRow.overseas} className="py-0.5" />
                  {opened.line != null ? openRow.customer : openRow.title}
                </p>
              </div>
              <button type="button" onClick={() => setOpened(null)} aria-label="닫기" title="닫기 (Esc)"
                      className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-foreground-muted hover:bg-foreground/8">
                <Icon name="x" className="h-4 w-4" />
              </button>
            </div>
            <div className="px-5 py-4">
              <RentalPanel key={`${opened.deal}-${opened.line}`} id={opened.deal} focusLine={opened.line} highlight={snMatch(openRow) ? q.trim() : ""}
                           onChanged={reload} onOpenDeal={onOpenDeal} onQuote={onQuote} />
            </div>
          </aside>
        </div>
      )}
      {lateOpen && late.length > 0 && !seenToday() && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-black/30 p-4" onClick={closeLate}>
          <div role="alertdialog" aria-label="반납일 지난 대여 건" onClick={(e) => e.stopPropagation()}
               className="w-full max-w-[600px] rounded-2xl bg-background p-5 shadow-2xl">
            <h2 className="text-[16px] font-bold text-red-700 dark:text-red-300">📦 반납일이 지났는데 회수 완료가 안 된 대여 건 {late.length}건</h2>
            <p className="mt-1 text-[12.5px] text-foreground-muted">
              돌려받았으면 건을 열어 [회수 완료]를 눌러 주세요. 아직이면 고객사에 반납 일정을 확인해 주세요.
            </p>
            <ul className="scroll-thin mt-3 max-h-72 overflow-y-auto rounded-lg border border-border text-[12.5px]">
              {late.map((r) => (
                <li key={r.deal_id}>
                  <button type="button" onClick={() => { closeLate(); setView(""); setOpened({ deal: r.deal_id, line: null }); }}
                          className="flex w-full items-center gap-2 border-b border-border/70 px-3 py-2 text-left last:border-0 hover:bg-red-500/5">
                    <span className="min-w-0 flex-1 truncate font-medium">{r.customer} · {r.no}</span>
                    <span className="whitespace-nowrap font-semibold text-red-600 dark:text-red-400">{r.end} ({-(r.days_left ?? 0)}일 지남)</span>
                  </button>
                </li>
              ))}
            </ul>
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" onClick={() => { closeLate(); setView("반납일 지남"); }} className="h-9 rounded-lg border border-border px-4 text-[13px]">목록에서 보기</button>
              <button type="button" onClick={closeLate} className="h-9 rounded-lg bg-accent px-4 text-[13px] font-semibold text-white">확인</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/** 반납일 — 회수 완료면 그날, 아니면 반납 예정일과 남은 날(지나면 빨갛게, 가까우면 주황) */
function ReturnCell({ r, soon }: { r: RentalWhen; soon: number }) {
  if (r.phase === "회수 완료") return <span>{r.returned ?? "-"} <span className="text-[11.5px]">반납</span></span>;
  if (!r.end) return <Muted>{r.start ? "-" : "시작일 정하면 계산"}</Muted>;
  const d = r.days_left;
  return (
    <span className={cn("font-semibold", d != null && d < 0 ? "text-red-600 dark:text-red-400" : d != null && d <= soon ? "text-amber-700 dark:text-amber-300" : "text-foreground")}>
      {r.end}
      {d != null && <span className="ml-1.5 text-[11.5px]">{d < 0 ? `${-d}일 지남` : d === 0 ? "오늘" : `D-${d}`}</span>}
      {r.return_started && <div className="text-[11.5px] font-normal text-foreground-subtle">회수 시작 {r.return_started}</div>}
    </span>
  );
}

// ── 상세(오른쪽 패널) — 제품 → 고객사·대여 건 → 대여 기간·회수 → 견적서 → 이 건의 제품 전체 ──────────────

const STEPS: RentalPhase[] = ["견적", "대여 예정", "대여 중", "회수 중", "회수 완료"];

function RentalPanel({ id, focusLine = null, highlight, onChanged, onOpenDeal, onQuote }: {
  id: number;
  /** 누른 제품 줄 — 맨 위에 그 제품과 S/N, 이 건의 제품 전체는 접어 둔다. null 이면 건 상세(제품 전체 펼침) */
  focusLine?: number | null;
  highlight: string; onChanged: () => void; onOpenDeal: (dealId: number) => void; onQuote: (quoteId: number) => void;
}) {
  const [det, setDet] = useState<RentalDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<SerialLoadResult | null>(null);
  const [startDay, setStartDay] = useState("");
  const [snLine, setSnLine] = useState<number | null>(null);
  const [snFocus, setSnFocus] = useState(false);
  const [viewing, setViewing] = useState(false);

  useEffect(() => {
    let alive = true;
    call<RentalDetail>(`/${id}/rental`).then((d) => { if (alive) { setDet(d); setStartDay(d.deal.rental?.start ?? ""); } })
      .catch((e: Error) => { if (alive) setErr(e.message); });
    return () => { alive = false; };
  }, [id]);

  const act = async (label: string, fn: () => Promise<void>) => {
    setBusy(label);
    setErr(null);
    try { await fn(); } catch (e) { setErr(e instanceof Error ? e.message : "처리하지 못했습니다."); } finally { setBusy(null); }
  };
  const refresh = async () => {
    const d = await call<RentalDetail>(`/${id}/rental`);
    setDet(d);
    setStartDay(d.deal.rental?.start ?? "");
    onChanged();
  };

  if (!det) return err ? <p className="text-[12.5px] text-red-600">{err}</p> : <p className="text-[12.5px] text-foreground-subtle">불러오는 중…</p>;
  const { deal, phase, issue } = det;
  const rent = deal.rental;
  const free = !!deal.flow?.free;
  const ret = rent?.return ?? {};
  const steps = free ? STEPS.slice(1) : STEPS;
  const at = phase === "반납일 지남" ? (ret.started ? "회수 중" : "대여 중") : phase;
  const lines = issue?.lines ?? [];
  const hl = highlight.toLowerCase();
  const focus = focusLine != null ? lines.find((l) => l.line_no === focusLine) ?? null : null;
  // S/N 저장 뒤 — Remark 에서 읽은 S/N(hints)은 저장 응답에 없어 그대로 붙여 둔다
  const savedSn = (v: IssueUnits) => {
    if (!issue) return;
    setDet({ ...det, issue: { ...v, lines: v.lines.map((x) => ({ ...x, hints: lines.find((y) => y.line_no === x.line_no)?.hints })), hint_total: issue.hint_total } });
    onChanged();
  };
  const info: [string, React.ReactNode][] = [
    ["고객사", deal.customer ?? <Muted>미기재</Muted>],
    ["고객사 담당자", deal.contact || <Muted>미기재</Muted>],
    ["우리 담당자", deal.owner || <Muted>-</Muted>],
    ["견적서", <span key="no" className="font-mono">{deal.display_no ?? "-"}</span>],
    ["구분", <span key="k" className="inline-flex items-center gap-1">{free ? "무상 대여" : "유상 대여"}<RegionBadge overseas={deal.quote?.currency === "USD"} className="py-0.5" /></span>],
    ["대여 기간", rent ? `${rent.months}개월${rent.start ? ` · ${rent.start} ~ ${rent.end}` : " · 시작일 미정"}` : <Muted>-</Muted>],
    ["상태", <span key="ph" className={cn("rounded-full px-2 py-0.5 text-[12px] font-semibold", PHASE_TONE[phase])}>{phase}</span>],
  ];

  return (
    <div className="flex flex-col gap-4">
      {/* 누른 제품 — 수량·S/N, 여기서 바로 S/N 입력 */}
      {focus && issue && (
        <section className="rounded-xl border border-accent/30 bg-accent/[0.03] p-3.5">
          <div className="flex flex-wrap items-start gap-2">
            <div className="min-w-0 flex-1">
              <h4 className="text-[14px] font-bold">{focus.name.split("\n")[0]}</h4>
              {focus.part && focus.part !== focus.name.split("\n")[0] && <p className="text-[12px] text-foreground-muted">{focus.part}</p>}
            </div>
            <span className="text-[12.5px] tabular-nums">수량 <b>{focus.qty}</b></span>
            <button type="button" onClick={() => setSnFocus(!snFocus)}
                    className={cn("rounded-lg border px-2.5 py-1 text-[12px] font-semibold",
                      focus.units.length >= focus.qty ? "border-emerald-500/40 text-emerald-700 hover:bg-emerald-500/10 dark:text-emerald-300"
                        : "border-accent/40 text-accent hover:bg-accent/5")}>
              {focus.units.length >= focus.qty ? "✓ S/N 고치기" : "S/N 입력"} <span className="font-mono">{Math.min(focus.units.length, focus.qty)}/{focus.qty}</span>
            </button>
          </div>
          <div className="mt-2 font-mono text-[12px]">
            {focus.units.length ? <span className="break-all">{focus.units.map((u) => u.serial).join(", ")}</span>
              : <span className="font-sans text-foreground-subtle">S/N 빈 칸</span>}
          </div>
          {snFocus && (
            <SnPanel dealId={id} view={issue} line={focus} need={focus.qty} scope="이 품목" onSaved={savedSn} onClose={() => setSnFocus(false)} />
          )}
        </section>
      )}

      {/* 고객사·대여 건 — 제품에서 거꾸로 어느 고객사·어느 건인지 */}
      <section className="rounded-xl border border-border bg-surface p-3.5">
        <h4 className="mb-2 text-[13px] font-bold">고객사 · 대여 건</h4>
        <dl className="grid grid-cols-[110px_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-[12.5px]">
          {info.map(([k, v]) => (
            <Fragment key={k}>
              <dt className="text-foreground-muted">{k}</dt>
              <dd className="min-w-0 truncate">{v}</dd>
            </Fragment>
          ))}
        </dl>
      </section>

      {/* 진행 — 지금 단계까지 채운다. 반납일이 지났으면 빨간 표시 */}
      <div className="flex flex-wrap items-center gap-1.5">
        {steps.map((s, i) => {
          const done = steps.indexOf(at) >= i;
          return (
            <Fragment key={s}>
              {i > 0 && <span className={cn("h-px w-6", done ? "bg-accent" : "bg-border")} aria-hidden />}
              <span className={cn("rounded-full px-2.5 py-1 text-[12px] font-semibold",
                s === at ? PHASE_TONE[s] + " ring-1 ring-current" : done ? "bg-accent/10 text-accent" : "bg-foreground/5 text-foreground-subtle")}>
                {s}
              </span>
            </Fragment>
          );
        })}
        {phase === "반납일 지남" && <span className="ml-2 rounded-full bg-red-500/12 px-2.5 py-1 text-[12px] font-bold text-red-700 dark:text-red-300">⏰ 반납일 지남</span>}
      </div>

      <div className="grid gap-3 min-[1700px]:grid-cols-[1.1fr_1fr]">
        {/* 대여 기간·회수 */}
        <section className="rounded-xl border border-border bg-surface p-3.5">
          <h4 className="mb-2 text-[13px] font-bold">대여 기간 · 회수</h4>
          {phase === "견적" ? (
            <p className="text-[12.5px] text-foreground-muted">
              유상 대여 견적 단계입니다 — 수주 확정은 [제품 영업 건 관리]에서 합니다. 수주하면 대여 시작일부터 자동으로 대여 중이 됩니다.
            </p>
          ) : (
            <>
              <div className="flex flex-wrap items-end gap-2 text-[12.5px]">
                <label className="flex flex-col gap-1">
                  <span className="text-[11.5px] text-foreground-muted">대여 시작일 {rent?.months ? `· ${rent.months}개월` : ""}</span>
                  <input type="date" value={startDay} onChange={(e) => setStartDay(e.target.value)} disabled={!!busy || !!ret.done}
                         className="h-9 rounded-lg border border-border bg-surface px-2 outline-none focus:border-accent" />
                </label>
                <button type="button" className={secondaryBtn} disabled={!!busy || !startDay || startDay === (rent?.start ?? "") || !!ret.done}
                        onClick={() => void act("시작일", async () => { await call(`/${id}/rental-start`, { date: startDay }); await refresh(); })}>
                  {busy === "시작일" ? "저장 중…" : rent?.start ? "시작일 바꾸기" : "시작일 정하기"}
                </button>
                <span className="pb-2 text-foreground-muted">
                  반납일 <b className="text-foreground">{rent?.end ?? "시작일을 정하면 계산"}</b>
                </span>
              </div>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                {/* [대여 시작] — 누른 날(한국 날짜)이 대여 시작일, 반납일도 바로 계산(사용자 2026-11-10). 이미 시작했으면 안 보인다 */}
                {phase === "대여 예정" && (
                  <button type="button" className={primaryBtn} disabled={!!busy}
                          title={`오늘(${kstToday()})을 대여 시작일로 — 반납일도 계산됩니다`}
                          onClick={() => void act("대여 시작", async () => { await call(`/${id}/rental-start`, { date: kstToday() }); await refresh(); })}>
                    {busy === "대여 시작" ? "처리 중…" : `대여 시작 (오늘 ${kstToday()})`}
                  </button>
                )}
                {!ret.started && !ret.done && (
                  <button type="button" className={secondaryBtn} disabled={!!busy || !det.started}
                          title={det.started ? "제품을 돌려받기 시작" : "대여 시작일이 지나야 회수할 수 있습니다"}
                          onClick={() => void act("회수 시작", async () => { await call(`/${id}/rental-return`, { step: "start" }); await refresh(); })}>
                    {busy === "회수 시작" ? "처리 중…" : "회수 시작"}
                  </button>
                )}
                {!ret.done && (
                  <button type="button" className={primaryBtn} disabled={!!busy || !det.started}
                          title={det.started ? "제품을 모두 돌려받음 — S/N 상태도 회수로" : "대여 시작일이 지나야 회수할 수 있습니다"}
                          onClick={() => void act("회수 완료", async () => { await call(`/${id}/rental-return`, { step: "done" }); await refresh(); })}>
                    {busy === "회수 완료" ? "처리 중…" : "회수 완료"}
                  </button>
                )}
                {ret.started && <span className="text-[12px] text-foreground-muted">회수 시작 {ret.started}</span>}
                {ret.done && <span className="text-[12px] font-semibold text-emerald-700 dark:text-emerald-300">회수 완료 {ret.done}</span>}
              </div>
            </>
          )}
        </section>

        {/* 견적서 */}
        <section className="rounded-xl border border-border bg-surface p-3.5">
          <h4 className="mb-2 text-[13px] font-bold">{free ? "견적서(무상 대여)" : "견적서(대여)"}</h4>
          {issue ? (
            <div className="flex flex-wrap items-center gap-2 text-[12.5px]">
              <span className="font-mono font-semibold">{issue.issue_no ?? "-"}</span>
              <span className="text-foreground-subtle">발행 {issue.issued_at?.slice(0, 10)}</span>
              <button type="button" onClick={() => setViewing(true)} className="inline-flex items-center gap-1 font-semibold text-accent hover:underline">
                <Icon name="eye" className="h-3.5 w-3.5" /> 보기
              </button>
              <button type="button" onClick={() => onQuote(issue.quote_id)} className="ml-auto rounded-lg border border-accent/50 px-2.5 py-1 text-[12px] font-semibold text-accent hover:bg-accent/5">
                견적서 고치기 →
              </button>
            </div>
          ) : <Muted>아직 발행한 견적서가 없습니다.</Muted>}
          {!free && (
            <button type="button" onClick={() => onOpenDeal(id)} className="mt-2 text-[12px] font-semibold text-accent hover:underline">
              [제품 영업 건 관리]에서 이 건 열기 — 수주·거래명세서·입금 →
            </button>
          )}
        </section>
      </div>

      {/* 이 건의 제품 전체 · S/N — 제품 하나를 눌러 열었으면 접어 둔다 */}
      <details open={!focus} className="group rounded-xl border border-border bg-surface p-3.5">
        <summary className="mb-2 flex cursor-pointer list-none flex-wrap items-center gap-2">
          <Icon name="chevron-down" className="h-3.5 w-3.5 -rotate-90 text-foreground-subtle transition-transform group-open:rotate-0" />
          <h4 className="text-[13px] font-bold">{focus ? "이 건의 제품 전체" : "제품 목록"}</h4>
          {issue && <span className="text-[12px] text-foreground-muted">
            {lines.length}종 · S/N {issue.summary.done}/{issue.summary.total}
            {issue.summary.missing > 0 && <span className="text-amber-700 dark:text-amber-300"> (빈 칸 {issue.summary.missing})</span>}
          </span>}
          {issue && (
            <button type="button" className={cn(secondaryBtn, "ml-auto")} disabled={!!busy || !issue.hint_total}
                    title={issue.hint_total ? `견적서 Remark 에 적힌 S/N ${issue.hint_total}개를 빈 칸에 넣습니다` : "견적서 Remark 에 'S/N: …'으로 적힌 번호가 없습니다"}
                    onClick={(e) => { e.preventDefault(); void act("불러오기", async () => {
                      const r = await call<SerialLoadResult>(`/${id}/rental/serials`, {});
                      setNotice(r);
                      setDet(r.detail);
                      onChanged();
                    }); }}>
              <Icon name="download" className="h-4 w-4" /> {busy === "불러오기" ? "불러오는 중…" : `S/N 불러오기${issue.hint_total ? ` (${issue.hint_total})` : ""}`}
            </button>
          )}
        </summary>
        {notice && (
          <div className="mb-2 rounded-lg bg-accent/[0.06] px-3 py-2 text-[12.5px]">
            <b>S/N {notice.filled}개를 넣었습니다.</b>
            {notice.skipped.length > 0 ? (
              <>
                {" "}짝짓기 어려운 {notice.skipped.length}개는 비워 두었습니다 — 아래 [S/N 입력]으로 직접 적어 주세요.
                <ul className="mt-1 max-h-32 list-disc overflow-y-auto pl-5 text-[12px] text-foreground-muted">
                  {notice.skipped.map((s, i) => <li key={i}><span className="font-mono">{s.serial}</span> · {s.line} — {s.why}</li>)}
                </ul>
              </>
            ) : notice.filled === 0 ? " 새로 넣을 S/N이 없습니다(이미 적었거나 Remark 에 없음)." : ""}
            <button type="button" onClick={() => setNotice(null)} className="ml-2 text-[12px] text-foreground-subtle hover:underline">닫기</button>
          </div>
        )}
        {err && <p className="mb-2 rounded-lg bg-red-500/10 px-3 py-1.5 text-[12px] text-red-700 dark:text-red-300">{err}</p>}
        {!issue ? <Muted>견적서를 발행하면 품목이 여기에 나옵니다.</Muted> : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] border-collapse text-[12.5px]">
              <thead>
                <tr className="border-b border-border text-left text-[11.5px] text-foreground-muted">
                  {["#", "제품", "수량", "S/N", ""].map((h, i) => <th key={i} className="whitespace-nowrap px-2 py-1.5 font-semibold">{h}</th>)}
                </tr>
              </thead>
              <tbody>
                {lines.map((ln, i) => {
                  const hit = !!hl && ln.units.some((u) => u.serial.toLowerCase().includes(hl));
                  return (
                    <Fragment key={ln.line_no}>
                      <tr className={cn("border-b border-border/50 align-top", hit && "bg-amber-500/10")}>
                        <td className="px-2 py-1.5 font-mono text-foreground-subtle">{i + 1}</td>
                        <td className="max-w-[320px] px-2 py-1.5">
                          <div className="truncate font-medium" title={ln.name}>{ln.name.split("\n")[0]}</div>
                          {ln.part && ln.part !== ln.name.split("\n")[0] && <div className="truncate text-[11.5px] text-foreground-muted">{ln.part}</div>}
                        </td>
                        <td className="whitespace-nowrap px-2 py-1.5 tabular-nums">{ln.qty}</td>
                        <td className="px-2 py-1.5 font-mono text-[12px]">
                          {ln.units.length
                            ? <span className="break-all">{ln.units.map((u) => u.serial).join(", ")}</span>
                            : <span className="font-sans text-foreground-subtle">빈 칸</span>}
                          {ln.units.length > 0 && ln.units.length < ln.qty && <span className="ml-1 font-sans text-[11.5px] text-amber-700 dark:text-amber-300">(빈 칸 {ln.qty - ln.units.length})</span>}
                        </td>
                        <td className="whitespace-nowrap px-2 py-1.5 text-right">
                          <button type="button" onClick={() => setSnLine(snLine === ln.line_no ? null : ln.line_no)}
                                  className={cn("rounded-lg border px-2 py-0.5 text-[12px] font-semibold",
                                    ln.units.length >= ln.qty ? "border-emerald-500/40 text-emerald-700 hover:bg-emerald-500/10 dark:text-emerald-300"
                                      : "border-accent/40 text-accent hover:bg-accent/5")}>
                            {ln.units.length >= ln.qty ? "✓ S/N 고치기" : "S/N 입력"} <span className="font-mono">{Math.min(ln.units.length, ln.qty)}/{ln.qty}</span>
                          </button>
                        </td>
                      </tr>
                      {snLine === ln.line_no && (
                        <tr><td colSpan={5} className="px-2 pb-3">
                          <SnPanel dealId={id} view={issue} line={ln} need={ln.qty} scope="이 품목" onSaved={savedSn} onClose={() => setSnLine(null)} />
                        </td></tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </details>
      {viewing && issue && (
        <PreviewDialog url={`/api/sales-deals/${id}/quote-file?quote=${issue.quote_id}&revision=${issue.revision}&fmt=pdf`}
                       title="견적서" sub={`${issue.issue_no ?? "견적서"} · 발행 ${issue.issued_at?.slice(0, 10) ?? ""}`} onClose={() => setViewing(false)} />
      )}
    </div>
  );
}
