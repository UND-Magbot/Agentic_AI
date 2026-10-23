// [제품·A/S] 탭(사용자 2026-10-19, 영업팀 요청 20번) — 시리얼로 고객사·출하·무상 기간·A/S 이력을 찾고, A/S 를 기록한다.
// 유상 A/S 견적은 영업팀이 프로그램 밖에서 수기로 — 여기는 진행 상태만 쌓는다. 무상 기간 기본값(제품마다)은 관리자가 고친다.
"use client";

import { useCallback, useEffect, useState } from "react";
import { Icon } from "@/components/shared/ui/icon";
import { GroupHead } from "@/components/domains/sales/deal-flow";
import { INPUT, Muted, todayIso, UnitStateBadge } from "@/components/domains/sales/deal-format";
import { primaryBtn, secondaryBtn } from "@/components/domains/sales/proposal-actions";
import { HEAD_AREA, HEAD_FIELD, ROW_BTN, ROW_LABEL } from "@/components/domains/sales/quote-styles";
import type { AsCase, DealBiz, ProductUnit, UnitDetail, WarrantyPolicy } from "@/lib/shared/sales-deals";
import { cn } from "@/lib/shared/utils";

const CAUSES: AsCase["cause"][] = ["확인 중", "제품 결함", "고객 과실", "기타"];
const CHARGES: AsCase["charge"][] = ["미정", "무상", "유상"];
const STATUSES: AsCase["status"][] = ["접수", "진행 중", "완료"];
const SMALL = "h-8 rounded-md border border-border bg-surface px-2 text-[12.5px] outline-none focus:border-accent";

async function call<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(`/api/sales-deals${path}`, body === undefined ? { cache: "no-store" }
    : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof d?.detail === "string" ? d.detail : Array.isArray(d?.detail) ? "입력값을 확인해 주세요." : "처리하지 못했습니다.");
  return d as T;
}

function WarrantyChip({ state }: { state?: string }) {
  if (!state) return null;
  const tone = state.startsWith("무상 기간 (") ? "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300"
    : state === "무상 기간 지남" ? "bg-red-500/10 text-red-700 dark:text-red-300" : "bg-foreground/8 text-foreground-muted";
  return <span className={cn("whitespace-nowrap rounded px-1.5 py-0.5 text-[11.5px] font-semibold", tone)}>{state}</span>;
}


/** 프로젝트(영업 건) 구분 — 판매 건 / 대여 건 */
function BizChip({ biz }: { biz?: DealBiz }) {
  if (!biz) return null;
  return <span className={cn("rounded px-1.5 py-0.5 text-[11px] font-semibold",
    biz === "rental" ? "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300" : "bg-blue-500/12 text-blue-700 dark:text-blue-300")}>
    {biz === "rental" ? "대여 건" : "판매 건"}</span>;
}

function ChargeChip({ charge }: { charge: AsCase["charge"] }) {
  const tone = charge === "무상" ? "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300"
    : charge === "유상" ? "bg-amber-500/12 text-amber-700 dark:text-amber-300" : "bg-foreground/8 text-foreground-muted";
  return <span className={cn("rounded px-1.5 py-0.5 text-[11.5px] font-semibold", tone)}>{charge}</span>;
}

export function ProductUnitsTab({ onOpenDeal }: {
  /** [프로젝트 건 열기] — 그 건을 판매·대여 관리 탭의 상세로 연다(사용자 2026-10-22) */
  onOpenDeal?: (dealId: number, biz: DealBiz) => void;
} = {}) {
  const [view, setView] = useState<"units" | "cases" | "policies">("units");
  const [openId, setOpenId] = useState<number | null>(null);
  // A/S 기록을 지우면 오른쪽 시리얼 상세도 다시 읽는다
  const [panelVer, setPanelVer] = useState(0);
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-1.5">
        {([["units", "S/N 찾기"], ["cases", "A/S 기록"], ["policies", "무상 기간 기본값"]] as const).map(([k, label]) => (
          <button key={k} type="button" onClick={() => setView(k)} aria-pressed={view === k}
                  className={cn("rounded-full border px-3 py-1 text-[12.5px] font-semibold",
                    view === k ? "border-accent bg-accent/10 text-accent" : "border-border text-foreground-muted hover:bg-foreground/5")}>
            {label}
          </button>
        ))}
        <span className="ml-auto text-[12px] text-foreground-subtle">S/N은 영업 건의 출하 기록을 쓸 때 품목마다 [S/N 등록]으로 넣습니다.</span>
      </div>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
        <div className="min-w-0">
          {view === "units" && <UnitSearch openId={openId} onOpen={setOpenId} />}
          {view === "cases" && <CaseList onOpen={setOpenId} onChanged={() => setPanelVer((v) => v + 1)} />}
          {view === "policies" && <Policies />}
        </div>
        <div className="min-w-0">
          {openId ? <UnitPanel key={`${openId}-${panelVer}`} unitId={openId} onOpenDeal={onOpenDeal} /> : (
            view !== "policies" && <div className="rounded-xl border border-dashed border-border p-6 text-center text-[13px] text-foreground-muted">
              왼쪽에서 S/N을 고르면 고객사·출하·무상 기간·A/S 이력이 보입니다.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function UnitSearch({ openId, onOpen }: { openId: number | null; onOpen: (id: number) => void }) {
  const [q, setQ] = useState("");
  const [items, setItems] = useState<ProductUnit[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async (text: string) => {
    setError(null);
    try {
      setItems((await call<{ items: ProductUnit[] }>(`/units/search?q=${encodeURIComponent(text)}`)).items);
    } catch (e) {
      setError(e instanceof Error ? e.message : "불러오지 못했습니다.");
    }
  }, []);
  useEffect(() => {
    const t = setTimeout(() => void load(q), 250);
    return () => clearTimeout(t);
  }, [q, load]);
  return (
    <div className="flex flex-col gap-2">
      <div className="relative">
        <Icon name="search" className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-foreground-subtle" />
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="S/N·부품·고객사·발행번호로 찾기" aria-label="S/N 찾기"
               className={cn(INPUT, "pl-9")} />
      </div>
      {error && <p className="text-[12.5px] text-red-600">{error}</p>}
      {items === null ? <Muted>불러오는 중…</Muted> : items.length === 0 ? <Muted>{q ? "맞는 S/N이 없습니다." : "아직 등록한 S/N이 없습니다."}</Muted> : (
        <ul className="flex flex-col gap-1.5">
          {items.map((u) => (
            <li key={u.id}>
              <button type="button" onClick={() => onOpen(u.id)}
                      className={cn("w-full rounded-lg border px-3 py-2 text-left text-[12.5px] hover:bg-foreground/[0.03]",
                        openId === u.id ? "border-accent bg-accent/[0.05]" : "border-border")}>
                <div className="flex flex-wrap items-center gap-2">
                  <UnitStateBadge code={u.purpose} />
                  <b className={cn("font-mono", u.no_serial && "text-foreground-muted")}>{u.serial}</b>
                  {u.no_serial && <span className="text-[11px] text-foreground-subtle">(S/N 없음)</span>}
                  <span className="truncate">{u.part_name}</span>
                  <span className="ml-auto"><WarrantyChip state={u.warranty_state} /></span>
                </div>
                <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11.5px] text-foreground-muted">
                  <BizChip biz={u.deal_biz} />
                  <span>{u.customer ?? "고객사 없음"}{u.deal_title ? ` · ${u.deal_title}` : ""}</span>
                  {u.issue_no && <span className="font-mono">{u.issue_no}</span>}
                  <span>{u.maker}</span>
                  {!!u.as_count && <span className={cn(u.as_open ? "font-semibold text-amber-700 dark:text-amber-300" : "")}>
                    A/S {u.as_count}건{u.as_open ? ` (진행 ${u.as_open})` : ""}</span>}
                </div>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function CaseList({ onOpen, onChanged }: { onOpen: (unitId: number) => void; onChanged?: () => void }) {
  const [st, setSt] = useState<AsCase["status"] | "">("");
  const [items, setItems] = useState<AsCase[] | null>(null);
  const [admin, setAdmin] = useState(false);
  // 관리자 삭제(사용자 2026-10-22) — 골라서 지우면 보관함으로. 시리얼 목록은 지우지 않는다
  const [selecting, setSelecting] = useState(false);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ver, setVer] = useState(0);
  useEffect(() => {
    let alive = true;
    call<{ items: AsCase[]; admin?: boolean }>(`/as-cases${st ? `?case_status=${encodeURIComponent(st)}` : ""}`)
      .then((d) => { if (alive) { setItems(d.items); setAdmin(!!d.admin); } }).catch(() => { if (alive) setItems([]); });
    return () => { alive = false; };
  }, [st, ver]);
  const toggle = (id: number) => setPicked((p) => { const n = new Set(p); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const stop = () => { setSelecting(false); setPicked(new Set()); setConfirming(false); setError(null); };
  const remove = async () => {
    setDeleting(true);
    setError(null);
    try {
      await call("/as-cases/delete", { ids: [...picked] });
      stop();
      onChanged?.();
      setItems(null);
      setVer((v) => v + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : "지우지 못했습니다.");
    } finally {
      setDeleting(false);
    }
  };
  const chosen = (items ?? []).filter((c) => picked.has(c.id));
  return (
    <div className="flex flex-col gap-2">
      {admin && (
        <div className="flex flex-wrap items-center gap-2 text-[12.5px]">
          {!selecting ? (
            <button type="button" onClick={() => setSelecting(true)}
                    className="h-8 rounded-lg border border-red-500/40 px-3 font-semibold text-red-600 hover:bg-red-500/8 dark:text-red-400">삭제</button>
          ) : (
            <>
              <span className="text-foreground-muted">지울 기록을 고르세요 — {picked.size}건 선택</span>
              <button type="button" disabled={!picked.size} onClick={() => setConfirming(true)}
                      className="h-8 rounded-lg bg-red-600 px-3 font-semibold text-white disabled:opacity-40">선택한 {picked.size}건 삭제</button>
              <button type="button" onClick={stop} className="h-8 rounded-lg border border-border px-3 text-foreground-muted">취소</button>
            </>
          )}
        </div>
      )}
      {confirming && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-black/30 p-4" onClick={() => !deleting && setConfirming(false)}>
          <div role="alertdialog" aria-label="A/S 기록 삭제 확인" onClick={(e) => e.stopPropagation()}
               className="w-full max-w-[440px] rounded-2xl bg-background p-5 shadow-2xl">
            <h2 className="text-[16px] font-bold">A/S 기록 {chosen.length}건을 지울까요?</h2>
            <ul className="scroll-thin mt-3 max-h-48 overflow-y-auto rounded-lg border border-border text-[12.5px]">
              {chosen.map((c) => (
                <li key={c.id} className="flex gap-2 border-b border-border/70 px-3 py-1.5 last:border-0">
                  <span className="font-mono">{c.serial}</span><span className="truncate">{c.customer ?? ""}</span>
                  <span className="ml-auto shrink-0 text-foreground-muted">{c.received_on} · {c.status}</span>
                </li>
              ))}
            </ul>
            <p className="mt-2 text-[12px] text-foreground-muted">
              지운 기록은 보관함으로 옮겨져 되살릴 수 있습니다. S/N은 그대로 남고, 진행 중이던 A/S 면 제품 상태(A)를 원래대로 되돌립니다.
            </p>
            {error && <p className="mt-2 text-[12.5px] text-red-600">{error}</p>}
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" disabled={deleting} onClick={() => setConfirming(false)} className="h-9 rounded-lg border border-border px-4 text-[13px]">취소</button>
              <button type="button" disabled={deleting} onClick={() => void remove()}
                      className="h-9 rounded-lg bg-red-600 px-4 text-[13px] font-semibold text-white disabled:opacity-50">{deleting ? "지우는 중…" : "삭제"}</button>
            </div>
          </div>
        </div>
      )}
      <div className="flex flex-wrap gap-1.5">
        {(["", ...STATUSES] as const).map((k) => (
          <button key={k || "all"} type="button" onClick={() => { if (k !== st) { setItems(null); setSt(k); } }} aria-pressed={st === k}
                  className={cn("rounded-full border px-2.5 py-0.5 text-[12px] font-semibold",
                    st === k ? "border-accent bg-accent/10 text-accent" : "border-border text-foreground-muted hover:bg-foreground/5")}>
            {k || "전체"}
          </button>
        ))}
      </div>
      {items === null ? <Muted>불러오는 중…</Muted> : items.length === 0 ? <Muted>A/S 기록이 없습니다.</Muted> : (
        <ul className="flex flex-col gap-1.5">
          {items.map((c) => (
            <li key={c.id} className="flex items-start gap-2">
              {selecting && (
                <input type="checkbox" checked={picked.has(c.id)} onChange={() => toggle(c.id)} aria-label={`${c.serial} A/S 기록 선택`}
                       className="mt-3 h-4 w-4 shrink-0 cursor-pointer accent-red-600" />
              )}
              <button type="button" disabled={!selecting && !c.unit_id} onClick={() => (selecting ? toggle(c.id) : c.unit_id && onOpen(c.unit_id))}
                      className={cn("w-full rounded-lg border px-3 py-2 text-left text-[12.5px] hover:bg-foreground/[0.03]",
                        selecting && picked.has(c.id) ? "border-red-500/50 bg-red-500/[0.05]" : "border-border")}>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-foreground-muted">{c.received_on}</span>
                  <b className="font-mono">{c.serial}</b>
                  <span className="truncate">{c.part_name}</span>
                  <span className="ml-auto flex gap-1"><ChargeChip charge={c.charge} /><span className="rounded bg-foreground/8 px-1.5 py-0.5 text-[11.5px]">{c.status}</span></span>
                </div>
                <div className="mt-0.5 text-[11.5px] text-foreground-muted">{c.customer ?? "고객사 없음"} · {c.symptom || "증상 미기재"}</div>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function UnitPanel({ unitId, onOpenDeal }: { unitId: number; onOpenDeal?: (dealId: number, biz: DealBiz) => void }) {
  const [u, setU] = useState<UnitDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const load = useCallback(() => {
    call<UnitDetail>(`/units/${unitId}`).then(setU).catch((e) => setError(e instanceof Error ? e.message : "불러오지 못했습니다."));
  }, [unitId]);
  useEffect(() => { load(); }, [load]);
  if (error) return <p className="text-[12.5px] text-red-600">{error}</p>;
  if (!u) return <Muted>불러오는 중…</Muted>;
  const rows: [string, React.ReactNode][] = [
    ["S/N", <span key="s" className="font-mono">{u.serial}{u.no_serial ? " (S/N 없음)" : ""}</span>],
    ["부품", u.part_name],
    ["제조사", u.maker],
    ["상태", <UnitStateBadge key="st" code={u.purpose} />],
    ["프로젝트", u.deal_id ? (
      <span key="pj" className="flex flex-wrap items-center gap-1.5">
        <BizChip biz={u.deal_biz} />
        <span>{u.customer ?? "고객사 없음"}{u.deal_title ? ` · ${u.deal_title}` : ""}</span>
        {u.deal_display_no && <span className="font-mono text-[11.5px] text-foreground-muted">{u.deal_display_no}</span>}
      </span>) : "-"],
    ["견적서", u.issue_no ? `${u.issue_no}${u.quote_revision != null ? ` v${u.quote_revision + 1}` : ""}` : "-"],
    ["출하", u.shipment ? `${u.shipment.date ?? "-"}${u.shipment.carrier ? ` · ${u.shipment.carrier}` : ""}` : "출하 전"],
    ["무상 기간", u.warranty_months == null ? "미정" : `${u.warranty_months}개월${u.warranty_until ? ` (${u.warranty_start} ~ ${u.warranty_until})` : ""}`],
  ];
  return (
    <div className="flex flex-col gap-3 rounded-xl border border-border p-4 text-[13px]">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-[15px] font-semibold">{u.part_name}</h3>
        <UnitStateBadge code={u.purpose} />
        <WarrantyChip state={u.warranty_state} />
        {u.deal_id && onOpenDeal && (
          <button type="button" onClick={() => onOpenDeal(u.deal_id as number, u.deal_biz ?? "sale")}
                  className="ml-auto inline-flex items-center gap-1 rounded-lg bg-accent px-3 py-1.5 text-[12px] font-semibold text-white hover:bg-accent/90">
            프로젝트 건 열기 →
          </button>
        )}
      </div>
      <dl className="grid grid-cols-[110px_minmax(0,1fr)] gap-x-3 gap-y-1 text-[12.5px]">
        {rows.map(([k, v]) => <div key={k} className="contents"><dt className="text-foreground-muted">{k}</dt><dd className="min-w-0 break-words">{v}</dd></div>)}
      </dl>
      {u.note && <p className="text-[12px] text-foreground-muted">메모: {u.note}</p>}

      <div className="flex items-center gap-2 border-t border-border pt-3">
        <b>A/S 이력</b><span className="text-[12px] text-foreground-muted">{u.cases.length}건</span>
        {!adding && (
          <button type="button" onClick={() => setAdding(true)}
                  className="ml-auto inline-flex items-center gap-1 rounded-lg border border-accent/50 px-2.5 py-1 text-[12px] font-semibold text-accent hover:bg-accent/5">
            <Icon name="plus" className="h-3.5 w-3.5" /> A/S 접수서 작성
          </button>
        )}
      </div>
      {adding && <CaseForm unit={u} onDone={(changed) => { setAdding(false); if (changed) load(); }} />}
      {u.cases.length === 0 && !adding && <Muted>A/S 기록이 없습니다.</Muted>}
      {u.cases.map((c) => <CaseRow key={c.id} c={c} onSaved={load} />)}
    </div>
  );
}

/** A/S 사진 — 저장소에 한 장씩 올리고 id 를 모은다(접수·고치기 공용). */
function PhotoPicker({ photos, onChange, disabled }: { photos: string[]; onChange: (p: string[]) => void; disabled?: boolean }) {
  const [up, setUp] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const add = async (files: FileList) => {
    setUp(true);
    setErr(null);
    const ids: string[] = [];
    try {
      for (const file of Array.from(files).slice(0, Math.max(0, 10 - photos.length))) {
        const fd = new FormData();
        fd.append("file", file);
        const r = await fetch("/api/sales-deals/as-photos", { method: "POST", body: fd });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(typeof d?.detail === "string" ? d.detail : "사진을 올리지 못했습니다.");
        ids.push((d as { id: string }).id);
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : "사진을 올리지 못했습니다.");
    } finally {
      if (ids.length) onChange([...photos, ...ids]);
      setUp(false);
    }
  };
  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap items-center gap-1.5">
        {photos.map((p) => (
          <span key={p} className="relative">
            <a href={`/api/sales-deals/as-photos/${p}`} target="_blank" rel="noreferrer">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={`/api/sales-deals/as-photos/${p}`} alt="A/S 사진" className="h-16 w-16 rounded-md object-cover ring-1 ring-border" />
            </a>
            {!disabled && (
              <button type="button" aria-label="사진 빼기" onClick={() => onChange(photos.filter((x) => x !== p))}
                      className="absolute -right-1 -top-1 grid h-4 w-4 place-items-center rounded-full bg-black/60 text-[10px] text-white">✕</button>
            )}
          </span>
        ))}
        {!disabled && photos.length < 10 && (
          <label className={cn("grid h-16 w-16 cursor-pointer place-items-center rounded-[10px] border border-dashed border-border-strong bg-surface-raised text-[12px] font-semibold text-accent hover:bg-accent/5",
            up && "pointer-events-none opacity-50")}>
            {up ? "올리는 중…" : "+ 사진"}
            <input type="file" accept=".jpg,.jpeg,.png,.webp" multiple className="hidden"
                   onChange={(e) => { const fs = e.target.files; if (fs?.length) void add(fs); e.target.value = ""; }} />
          </label>
        )}
      </div>
      {err && <p className="text-[12px] text-red-600">{err}</p>}
    </div>
  );
}

/** 무상·유상 기본값의 근거 — 출하일 기준 무상 기간(사용자 2026-10-19: 출하일 기준으로 불러오고 바꿀 수 있게). */
function chargeBasis(unit: UnitDetail, received: string, cause: AsCase["cause"]): { suggested: AsCase["charge"]; text: string } {
  const ship = unit.shipment?.date ?? unit.warranty_start;
  if (!ship) return { suggested: cause === "고객 과실" ? "유상" : "미정", text: "출하 전 제품이라 무상 기간이 없습니다 — 직접 골라 주세요." };
  if (!unit.warranty_until) return { suggested: cause === "고객 과실" ? "유상" : "미정", text: `출하일 ${ship} — 무상 기간이 미정이라 직접 골라 주세요.` };
  const inside = received <= unit.warranty_until;
  const base = `출하일 ${ship} 기준 무상 ${unit.warranty_months}개월(~${unit.warranty_until}) — 접수일이 기간 ${inside ? "안" : "지남"}`;
  if (cause === "고객 과실") return { suggested: "유상", text: `${base}, 고객 과실이라 유상` };
  return { suggested: inside ? "무상" : "유상", text: base };
}

/** A/S 접수서 — 고객사 문의를 받아 우리가 작성(사용자 2026-10-19). 무상·유상은 출하일 기준 무상 기간으로 채우고 바꿀 수 있다. */
/** more — 같은 접수서로 함께 접수할 시리얼(영업 건 상세 [A/S 접수]에서 여러 개 고를 때, 사용자 2026-10-22). 시리얼마다 기록 한 건. */
/** step — 영업 건 상세에서 S/N 고르기(①) 다음 묶음 번호를 이어 붙일 때 */
function CaseForm({ unit, more = [], onDone, step = 1 }: { unit: UnitDetail; more?: UnitDetail[]; onDone: (changed: boolean) => void; step?: number }) {
  const [f, setF] = useState({ customer: unit.customer ?? "", contact: "", received_on: todayIso(), cause: "확인 중" as AsCase["cause"],
                               symptom: "", photos: [] as string[], status: "접수" as AsCase["status"], action: "" });
  const [picked, setPicked] = useState<AsCase["charge"] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const basis = chargeBasis(unit, f.received_on, f.cause);
  const all = [unit, ...more];
  // 여러 시리얼의 무상 기간이 서로 다르면 기본은 '시리얼마다' — 고르지 않으면 서버가 시리얼마다 제안한 값으로
  const mixed = new Set(all.map((u) => chargeBasis(u, f.received_on, f.cause).suggested)).size > 1;
  const charge = picked ?? basis.suggested;
  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      if (more.length) await call("/as-cases/batch", { unit_ids: all.map((u) => u.id), ...f, ...(picked || !mixed ? { charge } : {}) });
      else await call("/as-cases", { unit_id: unit.id, ...f, charge });
      onDone(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "저장하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  };
  // 견적서·출하 작성과 같은 짜임(사용자 2026-10-23) — 묶음 번호 + 흰 40px 칸 + 테두리 버튼
  return (
    <div className="flex flex-col gap-3 text-[12.5px]">
      <div className="flex flex-col gap-3 rounded-xl border border-border p-4">
        <div className="flex flex-wrap items-center gap-2">
          <GroupHead n={step} title="A/S 접수 내용" tone="bg-blue-500/10 text-blue-700 dark:text-blue-300" />
          {more.length > 0 && <span className="rounded-md bg-accent/10 px-2 py-0.5 text-[11.5px] font-bold text-accent">S/N {all.length}개 · S/N마다 한 건씩</span>}
          <span className="text-[11.5px] text-foreground-subtle">고객사 문의를 받아 작성합니다. 접수자는 로그인한 사람으로 남습니다.</span>
        </div>
        <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_160px]">
          <label className={ROW_LABEL}><span>고객사</span>
            <input value={f.customer} maxLength={100} onChange={(e) => setF({ ...f, customer: e.target.value })} className={HEAD_FIELD} /></label>
          <label className={ROW_LABEL}><span>고객사 담당자 (이름·연락처)</span>
            <input value={f.contact} maxLength={200} placeholder="예: 김철수 과장 010-1234-5678" onChange={(e) => setF({ ...f, contact: e.target.value })} className={HEAD_FIELD} /></label>
          <label className={ROW_LABEL}><span>접수일</span>
            <input type="date" value={f.received_on} onChange={(e) => setF({ ...f, received_on: e.target.value })} className={HEAD_FIELD} /></label>
        </div>
        <div className="grid gap-3 md:grid-cols-[160px_minmax(0,1fr)]">
          <label className={ROW_LABEL}><span>고장 원인 구분</span>
            <select value={f.cause} onChange={(e) => setF({ ...f, cause: e.target.value as AsCase["cause"] })} className={HEAD_FIELD}>
              {CAUSES.map((x) => <option key={x}>{x}</option>)}</select></label>
          <label className={ROW_LABEL}><span>고장 원인·증상</span>
            <textarea value={f.symptom} onChange={(e) => setF({ ...f, symptom: e.target.value })} rows={2} maxLength={1000}
                      placeholder="예: 툴 결합 후 신호 끊김, 포고핀 눌림 확인" className={HEAD_AREA} /></label>
        </div>
        <div className={ROW_LABEL}><span>사진</span>
          <PhotoPicker photos={f.photos} onChange={(photos) => setF((x) => ({ ...x, photos }))} disabled={busy} /></div>
      </div>
      <div className="flex flex-col gap-3 rounded-xl border border-border p-4">
        <GroupHead n={step + 1} title="무상·유상 · 진행" tone="bg-cyan-600/10 text-cyan-800 dark:text-cyan-300" />
        <div className="grid gap-3 md:grid-cols-[160px_160px_minmax(0,1fr)] md:items-end">
          <label className={ROW_LABEL}><span>무상 / 유상</span>
            <select value={mixed && !picked ? "" : charge} onChange={(e) => setPicked(e.target.value ? e.target.value as AsCase["charge"] : null)} className={HEAD_FIELD}>
              {mixed && <option value="">S/N마다 (기본)</option>}
              {CHARGES.map((x) => <option key={x} value={x}>{x}{!mixed && x === basis.suggested ? " (기본)" : ""}</option>)}</select></label>
          <label className={ROW_LABEL}><span>진행</span>
            <select value={f.status} onChange={(e) => setF({ ...f, status: e.target.value as AsCase["status"] })} className={HEAD_FIELD}>
              {STATUSES.map((x) => <option key={x}>{x}</option>)}</select></label>
          <p className="rounded-[10px] bg-foreground/[0.04] px-3 py-2 text-[12px] leading-relaxed text-foreground-muted">
            {mixed ? "고른 S/N의 무상 기간이 서로 달라 S/N마다 무상·유상을 정합니다" : basis.text}
            {picked && (mixed || picked !== basis.suggested) ? ` — ${mixed ? "모두" : `기본(${basis.suggested})에서`} ${picked}(으)로 바꿈` : ""}
            {picked && <button type="button" onClick={() => setPicked(null)} className="ml-1 font-semibold text-accent hover:underline">기본으로</button>}
          </p>
        </div>
        <label className={ROW_LABEL}><span>조치 내용</span>
          <textarea value={f.action} onChange={(e) => setF({ ...f, action: e.target.value })} rows={2} maxLength={1000} placeholder="나중에 적어도 됩니다"
                    className={HEAD_AREA} /></label>
      </div>
      {error && <p className="rounded-xl border border-red-500/25 bg-red-500/[0.06] px-3.5 py-2.5 text-red-700 dark:text-red-300">{error}</p>}
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-[11.5px] text-foreground-subtle">유상 A/S 견적서는 따로(수기) 작성합니다 — 여기는 접수와 진행 기록만 남깁니다.</p>
        <div className="ml-auto flex gap-2">
          <button type="button" onClick={() => onDone(false)} disabled={busy} className={secondaryBtn}>취소</button>
          <button type="button" onClick={() => void save()} disabled={busy} className={primaryBtn}>
            <Icon name="check" className="h-4 w-4" /> {busy ? "저장 중…" : more.length ? `${all.length}건 접수` : "접수"}
          </button>
        </div>
      </div>
    </div>
  );
}

/** 영업 건 상세 [출하] 옆 [A/S 접수](사용자 2026-10-22) — 이 건에서 출하한 시리얼을 찾아 체크박스로 하나 이상 골라
 *  같은 접수서(CaseForm)로 접수한다. 여러 개면 시리얼마다 A/S 기록이 한 건씩 생긴다. */
export function DealAsIntake({ units, onDone }: { units: ProductUnit[]; onDone: (changed: boolean) => void }) {
  const [q, setQ] = useState("");
  const [picked, setPicked] = useState<number[]>([]);
  const [loaded, setLoaded] = useState<Record<number, UnitDetail>>({});
  const [loading, setLoading] = useState<number[]>([]);
  const [error, setError] = useState<string | null>(null);
  const key = q.trim().toLowerCase();
  const shown = units.filter((u) => !key || `${u.serial} ${u.part_name}`.toLowerCase().includes(key));
  const load = async (ids: number[]) => {
    const need = ids.filter((id) => !loaded[id]);
    if (!need.length) return;
    setLoading((xs) => [...xs, ...need]);
    setError(null);
    try {
      const got = await Promise.all(need.map((id) => call<UnitDetail>(`/units/${id}`)));
      setLoaded((m) => ({ ...m, ...Object.fromEntries(got.map((u) => [u.id, u])) }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "S/N을 불러오지 못했습니다.");
      setPicked((xs) => xs.filter((id) => !need.includes(id)));
    } finally {
      setLoading((xs) => xs.filter((id) => !need.includes(id)));
    }
  };
  const toggle = (id: number, on: boolean) => {
    setPicked((xs) => (on ? [...xs, id] : xs.filter((x) => x !== id)));
    if (on) void load([id]);
  };
  const allShown = shown.length > 0 && shown.every((u) => picked.includes(u.id));
  const toggleShown = () => {
    if (allShown) setPicked((xs) => xs.filter((id) => !shown.some((u) => u.id === id)));
    else {
      const add = shown.map((u) => u.id).filter((id) => !picked.includes(id));
      setPicked((xs) => [...xs, ...add]);
      void load(add);
    }
  };
  const chosen = picked.map((id) => loaded[id]).filter(Boolean);
  return (
    <div className="mb-3 flex flex-col gap-3 text-[12.5px]">
      <div className="flex flex-col gap-3 rounded-xl border border-border p-4">
        <div className="flex flex-wrap items-center gap-2">
          <GroupHead n={1} title="A/S 받을 S/N 고르기" tone="bg-slate-500/12 text-slate-700 dark:text-slate-300" />
          <span className="text-[11.5px] text-foreground-subtle">이 건에서 출하한 제품 {units.length}개 · 여러 개 고를 수 있습니다</span>
          <button type="button" onClick={() => onDone(false)} className={cn(ROW_BTN, "ml-auto h-8")}>닫기</button>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="relative min-w-[200px] flex-1">
            <Icon name="search" className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-foreground-subtle" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="S/N·부품명으로 찾기" aria-label="A/S S/N 찾기" className={cn(HEAD_FIELD, "pl-9")} />
          </span>
          <button type="button" onClick={toggleShown} disabled={!shown.length} className={ROW_BTN}>
            <Icon name="check" className="h-3.5 w-3.5 text-accent" /> {allShown ? "보이는 것 모두 빼기" : "보이는 것 모두 고르기"}
          </button>
        </div>
        <ul className="scroll-thin flex max-h-64 flex-col gap-1.5 overflow-y-auto pr-1" aria-label="A/S 받을 S/N">
          {shown.length === 0 && <li><Muted>맞는 S/N이 없습니다.</Muted></li>}
          {shown.map((u) => {
            const on = picked.includes(u.id);
            return (
              <li key={u.id}>
                <label className={cn("flex min-w-0 cursor-pointer items-center gap-3 rounded-[10px] border px-3 py-2.5 transition-colors",
                                     on ? "border-accent/50 bg-accent/[0.06]" : "border-border bg-surface-raised hover:border-accent/35")}>
                  <input type="checkbox" checked={on} onChange={(e) => toggle(u.id, e.target.checked)}
                         aria-label={`${u.serial} A/S 접수 선택`} className="h-4 w-4 shrink-0 accent-accent" />
                  <span className="min-w-0 flex-1 truncate text-[13px] font-semibold">{u.part_name}</span>
                  <span className="whitespace-nowrap font-mono text-[12.5px] text-foreground-muted">{u.serial}</span>
                  <UnitStateBadge code={u.purpose} />
                  {loading.includes(u.id) && <span className="text-[11px] text-foreground-subtle">불러오는 중…</span>}
                </label>
              </li>
            );
          })}
        </ul>
        <div className="text-[12.5px] text-foreground-muted">고른 S/N <b className="text-[14px] text-foreground">{picked.length}</b>개</div>
        {error && <p className="text-red-600">{error}</p>}
      </div>
      {chosen.length > 0
        ? <CaseForm key="intake" unit={chosen[0]} more={chosen.slice(1)} onDone={onDone} step={2} />
        : <p className="text-[11.5px] text-foreground-subtle">S/N을 하나 이상 고르면 아래에 A/S 접수서가 열립니다.</p>}
    </div>
  );
}

function CaseRow({ c, onSaved }: { c: AsCase; onSaved: () => void }) {
  const [edit, setEdit] = useState(false);
  // 진행 상태는 버튼으로만 — 고치기는 내용만(사용자 2026-10-22)
  const [f, setF] = useState({ customer: c.customer ?? "", contact: c.contact, cause: c.cause, charge: c.charge,
                               symptom: c.symptom, photos: c.photos, action: c.action, note: c.note });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 진행은 버튼으로만(사용자 2026-10-22): 접수 → [A/S 들어감 확인] → 진행 중 → [A/S 완료] → 완료
  const [asking, setAsking] = useState<"진행 중" | "완료" | null>(null);
  const move = async (status: "진행 중" | "완료") => {
    setBusy(true);
    setError(null);
    try {
      await call(`/as-cases/${c.id}`, { status });
      setAsking(null);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "저장하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  };
  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await call(`/as-cases/${c.id}`, f);
      setEdit(false);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "저장하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="rounded-lg border border-border p-3 text-[12.5px]">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-foreground-muted">{c.received_on}</span>
        <ChargeChip charge={c.charge} />
        <span className="rounded bg-foreground/8 px-1.5 py-0.5 text-[11.5px]">{c.status}</span>
        <span className="text-foreground-muted">원인: {c.cause}</span>
        {c.in_warranty != null && <span className="text-[11.5px] text-foreground-subtle">{c.in_warranty ? "무상 기간 안 접수" : "무상 기간 지나 접수"}</span>}
        {c.closed_on && <span className="text-[11.5px] text-foreground-subtle">완료 {c.closed_on}</span>}
        {!edit && (
          <span className="ml-auto flex items-center gap-2">
            {c.status === "접수" && (
              <button type="button" disabled={busy} onClick={() => setAsking("진행 중")}
                      className="h-7 rounded-lg bg-accent px-3 text-[12px] font-semibold text-white disabled:opacity-60">A/S 들어감 확인</button>
            )}
            {c.status === "진행 중" && (
              <button type="button" disabled={busy} onClick={() => setAsking("완료")}
                      className="h-7 rounded-lg bg-emerald-600 px-3 text-[12px] font-semibold text-white disabled:opacity-60">A/S 완료</button>
            )}
            <button type="button" onClick={() => setEdit(true)} className="text-[12px] font-semibold text-foreground-muted hover:text-accent hover:underline">내용 고치기</button>
          </span>
        )}
      </div>
      {asking && (
        <div className="mt-2 rounded-lg border border-accent/40 bg-accent/[0.05] p-2.5">
          <p className="font-semibold">{asking === "진행 중" ? "이 제품이 A/S 에 들어갔나요?" : "A/S 가 끝났나요?"}</p>
          <p className="mt-0.5 text-[11.5px] text-foreground-muted">
            {asking === "진행 중"
              ? (c.charge === "유상"
                ? "유상 A/S 입니다 — 영업이 수기로 쓴 A/S 견적서에 고객이 동의하고(필요하면 입금까지) 확인한 뒤 누르세요. 누르면 'A/S 진행 중'이 되고, 제품 판매 관리에서 이 건의 줄이 연하게 보입니다."
                : "제품을 받아 A/S 를 시작했으면 누르세요. 누르면 'A/S 진행 중'이 되고, 제품 판매 관리에서 이 건의 줄이 연하게 보입니다.")
              : "누르면 '완료'가 되고 오늘이 완료일로 남습니다. 기록은 A/S 목록에 그대로 남습니다. 조치 내용은 [내용 고치기]로 적어 두세요."}
          </p>
          <div className="mt-2 flex justify-end gap-2">
            <button type="button" disabled={busy} onClick={() => setAsking(null)} className="h-7 rounded-lg border border-border px-3 text-[12px] font-semibold text-foreground-muted">취소</button>
            <button type="button" disabled={busy} onClick={() => void move(asking)}
                    className={cn("h-7 rounded-lg px-3 text-[12px] font-semibold text-white disabled:opacity-60", asking === "완료" ? "bg-emerald-600" : "bg-accent")}>
              {busy ? "저장 중…" : asking === "진행 중" ? "네, A/S 진행 중으로" : "네, 완료"}
            </button>
          </div>
        </div>
      )}
      {!edit && error && <p className="mt-1 text-red-600">{error}</p>}
      {!edit && (
        <>
          <p className="mt-1 text-[11.5px] text-foreground-muted">
            {c.customer ?? "고객사 없음"}{c.contact ? ` · ${c.contact}` : ""}{c.received_by ? ` · 접수 ${c.received_by}` : ""}
          </p>
          {c.symptom && <p className="mt-1 whitespace-pre-line">고장 원인·증상: {c.symptom}</p>}
          {c.photos.length > 0 && <div className="mt-1.5"><PhotoPicker photos={c.photos} onChange={() => {}} disabled /></div>}
          {c.action && <p className="mt-0.5 whitespace-pre-line text-foreground-muted">조치: {c.action}</p>}
          {c.note && <p className="mt-0.5 text-[11.5px] text-foreground-subtle">메모: {c.note}</p>}
        </>
      )}
      {edit && (
        <div className="mt-2 flex flex-col gap-2">
          <div className="grid gap-2 md:grid-cols-2">
            <input value={f.customer} onChange={(e) => setF({ ...f, customer: e.target.value })} maxLength={100} placeholder="고객사" aria-label="고객사" className={SMALL} />
            <input value={f.contact} onChange={(e) => setF({ ...f, contact: e.target.value })} maxLength={200} placeholder="고객사 담당자 (이름·연락처)" aria-label="고객사 담당자" className={SMALL} />
          </div>
          <div className="flex flex-wrap gap-2">
            <select value={f.charge} onChange={(e) => setF({ ...f, charge: e.target.value as AsCase["charge"] })} className={SMALL} aria-label="무상·유상">
              {CHARGES.map((x) => <option key={x}>{x}</option>)}</select>
            <select value={f.cause} onChange={(e) => setF({ ...f, cause: e.target.value as AsCase["cause"] })} className={SMALL} aria-label="고장 원인 구분">
              {CAUSES.map((x) => <option key={x}>{x}</option>)}</select>
          </div>
          <textarea value={f.symptom} onChange={(e) => setF({ ...f, symptom: e.target.value })} rows={2} maxLength={1000} placeholder="고장 원인·증상"
                    className="rounded-md border border-border bg-surface px-2 py-1.5 outline-none focus:border-accent" />
          <PhotoPicker photos={f.photos} onChange={(photos) => setF((x) => ({ ...x, photos }))} disabled={busy} />
          <textarea value={f.action} onChange={(e) => setF({ ...f, action: e.target.value })} rows={2} maxLength={1000} placeholder="조치 내용"
                    className="rounded-md border border-border bg-surface px-2 py-1.5 outline-none focus:border-accent" />
          <input value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} maxLength={300} placeholder="메모 (예: 유상 견적 수기 발송 10/20)"
                 className={SMALL} />
          {error && <p className="text-red-600">{error}</p>}
          <div className="flex justify-end gap-2">
            <button type="button" onClick={() => setEdit(false)} disabled={busy} className="h-8 rounded-lg border border-border px-3 font-semibold text-foreground-muted">취소</button>
            <button type="button" onClick={() => void save()} disabled={busy} className="h-8 rounded-lg bg-accent px-4 font-semibold text-white disabled:opacity-60">저장</button>
          </div>
        </div>
      )}
    </div>
  );
}

/** 무상 기간 기본값 — 제품마다(부품명·제조사에 이 이름이 들어 있으면 그 개월 수). 시리얼 등록 때 채워지고 건마다 고칠 수 있다. */
function Policies() {
  const [rows, setRows] = useState<WarrantyPolicy[] | null>(null);
  const [canEdit, setCanEdit] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => {
    call<{ rows: WarrantyPolicy[]; can_edit: boolean }>("/warranty-policies")
      .then((d) => { setRows(d.rows); setCanEdit(d.can_edit); }).catch(() => setRows([]));
  }, []);
  const save = async () => {
    if (!rows) return;
    setBusy(true);
    setMsg(null);
    try {
      const d = await call<{ rows: WarrantyPolicy[] }>("/warranty-policies", { rows: rows.map(({ keyword, months, note }) => ({ keyword, months, note })) });
      setRows(d.rows);
      setMsg({ ok: true, text: "저장했습니다. 이미 등록한 S/N의 무상 기간은 바뀌지 않습니다." });
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : "저장하지 못했습니다." });
    } finally {
      setBusy(false);
    }
  };
  if (rows === null) return <Muted>불러오는 중…</Muted>;
  return (
    <div className="flex flex-col gap-2 text-[12.5px]">
      <p className="text-foreground-muted">
        부품명·품목·제조사에 아래 이름이 들어 있으면 S/N 등록 때 그 개월 수가 채워집니다(여러 개 맞으면 긴 이름). 비우면 &lsquo;미정&rsquo;.
        {!canEdit && " 고치기는 관리자만 할 수 있습니다."}
      </p>
      <div className="grid grid-cols-[minmax(0,1.2fr)_90px_minmax(0,1.5fr)_28px] gap-1.5 text-[11.5px] text-foreground-muted">
        <span>제품 이름(예: TCV, Cable, Dobot)</span><span>무상(개월)</span><span>메모</span><span />
      </div>
      {rows.length === 0 && <Muted>아직 정한 기본값이 없습니다 — S/N마다 직접 적거나 &lsquo;미정&rsquo;으로 둡니다.</Muted>}
      {rows.map((r, i) => (
        <div key={i} className="grid grid-cols-[minmax(0,1.2fr)_90px_minmax(0,1.5fr)_28px] items-center gap-1.5">
          <input value={r.keyword} disabled={!canEdit} maxLength={60} aria-label={`${i + 1}번 제품 이름`} className={SMALL}
                 onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, keyword: e.target.value } : x)))} />
          <input value={r.months ?? ""} disabled={!canEdit} inputMode="numeric" placeholder="미정" aria-label={`${i + 1}번 무상 기간`} className={SMALL}
                 onChange={(e) => { const v = e.target.value.replace(/\D/g, "").slice(0, 3); setRows(rows.map((x, j) => (j === i ? { ...x, months: v === "" ? null : Number(v) } : x))); }} />
          <input value={r.note} disabled={!canEdit} maxLength={300} aria-label={`${i + 1}번 메모`} className={SMALL}
                 onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, note: e.target.value } : x)))} />
          {canEdit ? (
            <button type="button" aria-label={`${i + 1}번 지우기`} onClick={() => setRows(rows.filter((_, j) => j !== i))}
                    className="grid h-7 w-7 place-items-center rounded text-foreground-subtle hover:bg-foreground/5 hover:text-foreground">
              <Icon name="trash" className="h-3.5 w-3.5" />
            </button>
          ) : <span />}
        </div>
      ))}
      {canEdit && (
        <div className="flex items-center gap-2">
          <button type="button" onClick={() => setRows([...rows, { keyword: "", months: null, note: "" }])}
                  className="inline-flex items-center gap-1 rounded border border-border px-2 py-0.5 text-foreground-muted hover:text-foreground">
            <Icon name="plus" className="h-3.5 w-3.5" /> 줄 추가
          </button>
          <button type="button" onClick={() => void save()} disabled={busy}
                  className="ml-auto h-8 rounded-lg bg-accent px-4 font-semibold text-white disabled:opacity-60">{busy ? "저장 중…" : "저장"}</button>
        </div>
      )}
      {msg && <p className={msg.ok ? "text-emerald-700 dark:text-emerald-300" : "text-red-600"}>{msg.text}</p>}
    </div>
  );
}
