// [대여 제품 목록] 탭(사용자 2026-10-30) — 대여 건을 건이 아니라 나간 제품 한 줄씩 본다. 핵심은 언제 반납되는지와 지금 상태.
// 금액은 보이지 않는다. 줄을 누르면 [제품 영업 건 관리]에서 그 건 상세가 열린다(회수 시작·완료는 거기서).
"use client";

import { useEffect, useMemo, useState } from "react";
import { Muted, UnitStateBadge } from "@/components/domains/sales/deal-format";
import { cn } from "@/lib/shared/utils";

type Phase = "대여 중" | "회수 중" | "출하 전" | "S/N 미등록" | "회수 완료";

interface RentalUnitRow {
  unit_id: number | null;
  deal_id: number;
  /** 대여 제품(견적 품목 줄 첫 줄) · 부품명 */
  product: string;
  part: string;
  serial: string;
  no_serial: boolean;
  /** 제품 상태 코드(R 대여 · A A/S 중 …), S/N 미등록 줄은 null */
  state: string | null;
  phase: Phase;
  customer: string;
  contact: string;
  owner: string;
  deal_no: string;
  title: string;
  start: string | null;
  /** 반납 예정일(대여 끝나는 날) */
  end: string | null;
  months: number | null;
  /** 회수 완료일 */
  returned: string | null;
  return_started: string | null;
  /** 반납 예정일까지 남은 날(지났으면 음수) — 대여 중·회수 중만 */
  days_left: number | null;
  free: boolean;
  qty?: number | null;
}

interface RentalUnitsData { today: string; soon_days: number; rows: RentalUnitRow[] }

const PHASE_TONE: Record<Phase, string> = {
  "대여 중": "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300",
  "회수 중": "bg-amber-500/12 text-amber-700 dark:text-amber-300",
  "출하 전": "bg-sky-500/12 text-sky-700 dark:text-sky-300",
  "S/N 미등록": "bg-foreground/8 text-foreground-muted",
  "회수 완료": "bg-foreground/5 text-foreground-subtle",
};
type View = "" | "late" | "soon" | Phase;
const isLate = (r: RentalUnitRow) => r.days_left != null && r.days_left < 0;
const isSoon = (r: RentalUnitRow, soon: number) => r.days_left != null && r.days_left >= 0 && r.days_left <= soon;

export function RentalUnitsTab({ onOpenDeal }: { onOpenDeal: (dealId: number) => void }) {
  const [data, setData] = useState<RentalUnitsData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<View>("");
  const [q, setQ] = useState("");
  // 반납일 지났는데 반납 확인 전인 제품 팝업 — 하루 한 번(닫으면 그날은 다시 안 뜸, 목록의 빨간 강조는 그대로, 사용자 2026-10-30)
  const [lateOpen, setLateOpen] = useState(true);

  useEffect(() => {
    let alive = true;
    fetch("/api/sales-deals/rental-units", { cache: "no-store" })
      .then(async (r) => {
        const d = await r.json().catch(() => ({}));
        if (!alive) return;
        if (!r.ok) setError(typeof d?.detail === "string" ? d.detail : "대여 제품 목록을 불러오지 못했습니다.");
        else setData(d as RentalUnitsData);
      })
      .catch(() => { if (alive) setError("서버에 연결하지 못했습니다."); });
    return () => { alive = false; };
  }, []);

  const soon = data?.soon_days ?? 30;
  const rows = useMemo(() => {
    const t = q.trim().toLowerCase();
    return (data?.rows ?? []).filter((r) => {
      if (view === "late" && !isLate(r)) return false;
      if (view === "soon" && !isSoon(r, soon)) return false;
      if (view && view !== "late" && view !== "soon" && r.phase !== view) return false;
      return !t || [r.product, r.part, r.serial, r.customer, r.contact, r.owner, r.deal_no, r.title].join(" ").toLowerCase().includes(t);
    });
  }, [data, view, q, soon]);

  const late = (data?.rows ?? []).filter(isLate);
  const seenToday = () => { try { return localStorage.getItem("rental-return-late-seen") === data?.today; } catch { return false; } };
  const closeLate = () => {
    setLateOpen(false);
    try { localStorage.setItem("rental-return-late-seen", data?.today ?? ""); } catch { /* 무시 */ }
  };

  if (error) return <p className="py-16 text-center text-[13px] text-foreground-muted">{error}</p>;
  if (!data) return <p className="py-16 text-center text-[13px] text-foreground-subtle">대여 제품 목록을 불러오는 중…</p>;

  const all = data.rows;
  const count = (f: (r: RentalUnitRow) => boolean) => all.filter(f).length;
  const chips: [View, string, number, string][] = [
    ["", "전체", all.length, "bg-foreground/8 text-foreground"],
    ["late", "⏰ 반납일 지남", count(isLate), "bg-red-500/12 text-red-700 dark:text-red-300"],
    ["soon", `반납 ${soon}일 안`, count((r) => isSoon(r, soon)), "bg-amber-500/12 text-amber-700 dark:text-amber-300"],
    ...(["대여 중", "회수 중", "출하 전", "S/N 미등록", "회수 완료"] as Phase[]).map((p) => [p, p, count((r) => r.phase === p), PHASE_TONE[p]] as [View, string, number, string]),
  ];

  return (
    <div className="w-full pb-2">
      <p className="mb-3 text-[12.5px] text-foreground-muted">
        대여 건의 제품을 한 줄씩 봅니다 — 언제 반납되는지·지금 어떤 상태인지가 먼저(반납일 가까운 순). 줄을 누르면 [제품 영업 건 관리]에서 그 건이
        열리고, 회수 시작·완료는 건 상세의 &lsquo;대여 · 회수&rsquo;에서 누릅니다. (기준일 {data.today})
      </p>
      <div className="mb-3 flex flex-wrap gap-2">
        {chips.filter(([k, , n]) => !k || n > 0 || view === k).map(([k, label, n, tone]) => (
          <button key={k || "all"} type="button" aria-pressed={view === k} onClick={() => setView(view === k ? "" : k)}
                  className={cn("rounded-full px-3 py-1 text-[12.5px] font-semibold", tone, view === k && "ring-2 ring-accent/60")}>
            {label} {n}
          </button>
        ))}
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="제품·S/N·고객사·담당자 검색" aria-label="대여 제품 검색"
               className="ml-auto h-9 w-60 rounded-lg border border-border bg-surface px-3 text-[13px] outline-none focus:border-accent" />
      </div>
      {rows.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border p-8 text-center text-[13px] text-foreground-muted">
          {all.length ? "조건에 맞는 대여 제품이 없습니다." : "아직 대여 건이 없습니다 — 견적서 수기 작성에서 [제품 대여]로 견적서를 내면 여기에 나옵니다."}
        </div>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-border">
          <table className="w-full min-w-[1160px] border-collapse text-[13px]">
            <thead>
              <tr className="border-b border-border bg-foreground/[0.03] text-left text-[12px] text-foreground-muted">
                {["대여 제품", "S/N", "상태 코드", "고객사", "고객사 담당자", "우리 담당자", "대여 기간", "반납일", "상태"].map((h) => (
                  <th key={h} className={cn("whitespace-nowrap px-3 py-2 font-semibold", h === "반납일" && "text-foreground")}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={`${r.deal_id}-${r.unit_id ?? `p${i}`}`} onClick={() => onOpenDeal(r.deal_id)} title="누르면 제품 영업 건 관리에서 이 건을 엽니다"
                    className={cn("cursor-pointer border-b border-border/60 hover:bg-accent/[0.04]", r.phase === "회수 완료" && "text-foreground-muted",
                      isLate(r) && "bg-red-500/[0.07] shadow-[inset_3px_0_0_#dc2626] hover:bg-red-500/[0.1]")}>
                  <td className="max-w-[280px] px-3 py-2">
                    <div className="truncate font-semibold" title={r.product}>{r.product || <Muted>-</Muted>}</div>
                    {r.part && r.part !== r.product && <div className="truncate text-[12px] text-foreground-muted" title={r.part}>{r.part}</div>}
                    {r.qty != null && !r.serial && <div className="text-[12px] text-foreground-subtle">수량 {r.qty}</div>}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 font-mono text-[12.5px]">
                    {r.serial ? (r.no_serial ? <span className="font-sans">{r.serial}</span> : r.serial) : <Muted>미등록</Muted>}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2">{r.state ? <UnitStateBadge code={r.state} /> : <Muted>-</Muted>}</td>
                  <td className="max-w-[200px] px-3 py-2">
                    <div className="truncate" title={r.customer}>{r.customer || <Muted>-</Muted>}</div>
                    <div className="truncate font-mono text-[11.5px] text-foreground-subtle" title={r.title}>{r.deal_no}</div>
                  </td>
                  {/* 고객사 담당자 = 견적서 받는 분(To), 우리 담당자 = 영업 담당(사용자 2026-10-30: 둘 다 이름으로) */}
                  <td className="max-w-[160px] truncate px-3 py-2" title={r.contact}>{r.contact || <Muted>미기재</Muted>}</td>
                  <td className="whitespace-nowrap px-3 py-2">{r.owner || <Muted>-</Muted>}</td>
                  <td className="whitespace-nowrap px-3 py-2 tabular-nums">
                    {r.start ? `${r.start} ~ ${r.end ?? "미정"}` : <Muted>시작일 미정{r.months ? ` · ${r.months}개월` : ""}</Muted>}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 tabular-nums">
                    <ReturnCell r={r} soon={soon} />
                  </td>
                  <td className="whitespace-nowrap px-3 py-2">
                    <span className={cn("rounded-full px-2 py-0.5 text-[12px] font-semibold", PHASE_TONE[r.phase])}>{r.phase}</span>
                    {r.free && <span className="ml-1 rounded bg-emerald-500/12 px-1.5 text-[11px] font-semibold text-emerald-700 dark:text-emerald-300">무상</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {lateOpen && late.length > 0 && !seenToday() && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-black/30 p-4" onClick={closeLate}>
          <div role="alertdialog" aria-label="반납일 지난 대여 제품" onClick={(e) => e.stopPropagation()}
               className="w-full max-w-[600px] rounded-2xl bg-background p-5 shadow-2xl">
            <h2 className="text-[16px] font-bold text-red-700 dark:text-red-300">📦 반납일이 지났는데 반납 확인이 안 된 제품 {late.length}개</h2>
            <p className="mt-1 text-[12.5px] text-foreground-muted">
              돌려받았으면 줄을 눌러 건을 열고 &lsquo;대여 · 회수&rsquo;의 [회수 완료]를 눌러 주세요. 아직이면 고객사에 반납 일정을 확인해 주세요.
            </p>
            <ul className="scroll-thin mt-3 max-h-72 overflow-y-auto rounded-lg border border-border text-[12.5px]">
              {late.map((r, i) => (
                <li key={`${r.deal_id}-${r.unit_id ?? i}`}>
                  <button type="button" onClick={() => { closeLate(); onOpenDeal(r.deal_id); }}
                          className="flex w-full items-center gap-2 border-b border-border/70 px-3 py-2 text-left last:border-0 hover:bg-red-500/5">
                    <span className="font-mono text-foreground-muted">{r.serial || "S/N 없음"}</span>
                    <span className="min-w-0 flex-1 truncate font-medium">{r.product} · {r.customer}</span>
                    <span className="whitespace-nowrap text-foreground-muted">{r.phase}</span>
                    <span className="whitespace-nowrap font-semibold text-red-600 dark:text-red-400">{r.end} ({-(r.days_left ?? 0)}일 지남)</span>
                  </button>
                </li>
              ))}
            </ul>
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" onClick={() => { closeLate(); setView("late"); }} className="h-9 rounded-lg border border-border px-4 text-[13px]">목록에서 보기</button>
              <button type="button" onClick={closeLate} className="h-9 rounded-lg bg-accent px-4 text-[13px] font-semibold text-white">확인</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/** 반납일 — 회수 완료면 그날, 아니면 반납 예정일과 남은 날(지나면 빨갛게, 가까우면 주황) */
function ReturnCell({ r, soon }: { r: RentalUnitRow; soon: number }) {
  if (r.phase === "회수 완료") return <span>{r.returned ?? "-"} <span className="text-[11.5px]">반납</span></span>;
  if (!r.end) return <Muted>{r.start ? "-" : "시작일 정하면 계산"}</Muted>;
  const d = r.days_left;
  return (
    <span className={cn("font-semibold", d != null && d < 0 ? "text-red-600 dark:text-red-400" : d != null && d <= soon ? "text-amber-700 dark:text-amber-300" : "text-foreground")}>
      {r.end}
      {d != null && <span className="ml-1.5 text-[11.5px]">{d < 0 ? `${-d}일 지남` : d === 0 ? "오늘" : `D-${d}`}</span>}
      {r.phase === "회수 중" && r.return_started && <div className="text-[11.5px] font-normal text-foreground-subtle">회수 시작 {r.return_started}</div>}
    </span>
  );
}
