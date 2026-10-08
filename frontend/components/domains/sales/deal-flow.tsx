// 수주 뒤 절차의 공용 창·칸(사용자 2026-10-07) — 거래명세서 발급 창, 출하 작성(ShipForm·ShipDialog).
// 절차는 수주 진행 탭(order-workspace)에서 진행하고, 영업 건 상세에는 [AI 와 수주 진행 →] 안내만 둔다.
// 단계·출하 조건·받을 금액은 backend(sales_deals/flow.py)가 계산해 deal.flow 로 준다.
"use client";

import { useEffect, useRef, useState } from "react";
import { Field, INPUT, Muted, todayIso, won } from "@/components/domains/sales/deal-format";
import type { DealRow, DealShipment, DealStatement, DealStatementItem } from "@/lib/shared/sales-deals";
import { cn } from "@/lib/shared/utils";

export type Act = (path: string, body: unknown) => Promise<boolean>;

const CARRIERS = ["로젠택배", "경동택배", "직납", "배차", "기타"];

/** 파일 올리기(multipart) — 성공하면 JSON, 실패하면 화면 문구로 Error. */
async function upload<T>(path: string, file: File): Promise<T> {
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch(`/api/sales-deals${path}`, { method: "POST", body: fd });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof d?.detail === "string" ? d.detail : "파일을 올리지 못했습니다.");
  return d as T;
}

const SET_UNIT = "SET";

/** 세트 이름 기본값 — 건명 첫 모델(예: 'TCC1 외 6' → 'TCC1 툴체인저(ATC)'). backend order_agent.default_set_name 과 같게. */
function defaultSetName(deal: DealRow): string {
  const model = (deal.title || "").split(/\s+외\s+|\s/)[0];
  return model ? `${model} 툴체인저(ATC)` : "툴체인저(ATC)";
}

// ── 거래명세서 발급 ────────────────────────────────────────────────────────

/** 거래명세서 발급 창 — 영업 건 상세·수주 진행 [거래명세서 발급] 목록이 같이 쓴다. onClose(true) = 발급함. */
export function StatementDialog({ deal, busy, act, onClose }: { deal: DealRow; busy: boolean; act: Act; onClose: (issued: boolean) => void }) {
  const [st, setSt] = useState<DealStatement | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    fetch(`/api/sales-deals/${deal.id}/statement/draft`, { cache: "no-store" })
      .then(async (r) => {
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(typeof d?.detail === "string" ? d.detail : "불러오지 못했습니다.");
        if (alive) setSt(d as DealStatement);
      })
      .catch((e: Error) => { if (alive) setErr(e.message); });
    return () => { alive = false; };
  }, [deal.id]);
  const setBuyer = (k: keyof DealStatement["buyer"], v: string) => setSt((x) => (x ? { ...x, buyer: { ...x.buyer, [k]: v } } : x));

  async function save() {
    if (!st) return;
    if (await act("statement", { ...st, items: st.items.filter((it) => it.name.trim()) })) onClose(true);
  }

  return (
    <Modal title="거래명세서 발급" onClose={() => onClose(false)} busy={busy}>
      {err && <p className="text-[12.5px] text-red-600">{err}</p>}
      {!st ? <Muted>불러오는 중…</Muted> : (
        <div className="flex flex-col gap-3 text-[12.5px]">
          <div className="grid gap-2 md:grid-cols-4">
            <Field label="발행번호"><input value={st.no} onChange={(e) => setSt({ ...st, no: e.target.value })} className={INPUT} /></Field>
            <Field label="발행일자"><input type="date" value={st.date} onChange={(e) => setSt({ ...st, date: e.target.value })} className={INPUT} /></Field>
          </div>
          <p className="font-semibold">공급받는자 <span className="font-normal text-foreground-subtle">· 한 번 적으면 같은 고객사는 다음부터 채워 둡니다</span></p>
          <div className="grid gap-2 md:grid-cols-3">
            <Field label="등록번호 *"><input value={st.buyer.reg_no} onChange={(e) => setBuyer("reg_no", e.target.value)} className={INPUT} placeholder="000-00-00000" /></Field>
            <Field label="상호 *"><input value={st.buyer.name} onChange={(e) => setBuyer("name", e.target.value)} className={INPUT} /></Field>
            <Field label="성명(대표)"><input value={st.buyer.ceo} onChange={(e) => setBuyer("ceo", e.target.value)} className={INPUT} /></Field>
          </div>
          <Field label="주소 *"><input value={st.buyer.address} onChange={(e) => setBuyer("address", e.target.value)} className={INPUT} /></Field>
          <StatementItems deal={deal} st={st} onChange={setSt} withNote />
          <div className="flex justify-end gap-2">
            <SmallBtn onClick={() => onClose(false)}>취소</SmallBtn>
            <PrimarySmall onClick={() => void save()} disabled={busy}>{deal.statement ? "다시 발급(덮어쓰기)" : "발급"}</PrimarySmall>
          </div>
        </div>
      )}
    </Modal>
  );
}

const lineAmount = (it: DealStatementItem) => (it.qty ?? 0) * (it.unit_price ?? 0);
const lineSum = (xs: DealStatementItem[]) => xs.reduce((s, it) => s + lineAmount(it), 0);
const toNum = (v: string) => (v.trim() === "" ? null : Number(v.replaceAll(",", "")) || 0);

/** 거래명세서 품목 — [품목별 / 세트로 묶어서](출하처럼 'TCC1 툴체인저(ATC) 1 SET', 사용자 2026-10-08).
 *  세트 = 한 줄 + 단위 SET(단가 = 원래 품목 합계 ÷ 세트 수량), 원래 품목은 parts 로 같이 둬서 되돌릴 수 있게.
 *  발급 창과 수주 진행 화면이 같이 쓴다. */
export function StatementItems({ deal, st, onChange, withNote = false }: {
  deal: DealRow; st: DealStatement; onChange: (st: DealStatement) => void; withNote?: boolean;
}) {
  const parts = st.parts ?? [];
  const isSet = parts.length > 0;
  const partsSum = lineSum(parts);
  const supply = lineSum(st.items);
  const setItem = (i: number, patch: Partial<DealStatementItem>) =>
    onChange({ ...st, items: st.items.map((it, j) => (j === i ? { ...it, ...patch } : it)) });
  function toSet() {
    const xs = st.items.filter((it) => it.name.trim());
    if (!xs.length) return;
    onChange({ ...st, parts: xs, items: [{ name: defaultSetName(deal), spec: "", qty: 1, unit: SET_UNIT, unit_price: lineSum(xs), note: "" }] });
  }
  function toItems() {
    const { parts: back, ...rest } = st;
    onChange({ ...rest, items: back ?? [] });
  }
  const heads = ["품명", "규격", "수량", "단가", "금액", ...(withNote ? ["비고"] : []), ""];

  return (
    <>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="font-semibold">품목</span>
        {([[false, "품목별"], [true, "세트로 묶어서 (예: 1 SET)"]] as const).map(([k, label]) => (
          <button key={label} type="button" onClick={() => (k ? !isSet && toSet() : isSet && toItems())} aria-pressed={isSet === k}
                  className={cn("rounded-full border px-2.5 py-0.5 text-[12px] font-semibold",
                    isSet === k ? "border-accent bg-accent/10 text-accent" : "border-border text-foreground-muted hover:bg-foreground/5")}>
            {label}
          </button>
        ))}
      </div>
      <table className="w-full border-collapse">
        <thead>
          <tr className="border-b border-border text-left text-foreground-muted">
            {heads.map((h, k) => <th key={k} className="py-1 pr-1.5 font-semibold">{h}</th>)}
          </tr>
        </thead>
        <tbody>
          {st.items.map((it, i) => (
            <tr key={i} className="border-b border-border/60">
              <td className="py-1 pr-1.5"><input value={it.name} onChange={(e) => setItem(i, { name: e.target.value })} aria-label="품명" className={cn(INPUT, "h-8")} /></td>
              <td className="py-1 pr-1.5"><input value={it.spec} onChange={(e) => setItem(i, { spec: e.target.value })} aria-label="규격" className={cn(INPUT, "h-8")} /></td>
              <td className={cn("py-1 pr-1.5", isSet ? "w-24" : "w-16")}>
                <span className="flex items-center gap-1">
                  <input value={it.qty ?? ""} inputMode="numeric" aria-label="수량" className={cn(INPUT, "h-8 text-right")}
                         onChange={(e) => {
                           const qty = toNum(e.target.value);
                           // 세트 수량을 바꾸면 단가 = 구성 합계 ÷ 수량(공급가액은 그대로)
                           setItem(i, isSet && qty ? { qty, unit_price: Math.round(partsSum / qty) } : { qty });
                         }} />
                  {it.unit && <span className="shrink-0 font-semibold text-foreground-muted">{it.unit}</span>}
                </span>
              </td>
              <td className="w-28 py-1 pr-1.5"><input value={it.unit_price ?? ""} inputMode="numeric" aria-label="단가" onChange={(e) => setItem(i, { unit_price: toNum(e.target.value) })} className={cn(INPUT, "h-8 text-right")} /></td>
              <td className="w-28 py-1 pr-1.5 text-right tabular-nums">{won(lineAmount(it))}</td>
              {withNote && <td className="w-28 py-1 pr-1.5"><input value={it.note} onChange={(e) => setItem(i, { note: e.target.value })} aria-label="비고" className={cn(INPUT, "h-8")} /></td>}
              <td className="py-1">
                {!isSet && <button type="button" className="text-foreground-subtle hover:text-red-600" aria-label="줄 빼기"
                                   onClick={() => onChange({ ...st, items: st.items.filter((_, j) => j !== i) })}>✕</button>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {isSet && (
        <div className="flex flex-col gap-1 text-[11.5px] text-foreground-subtle">
          <p>구성(명세서에는 세트 한 줄로만 나옵니다): {parts.map((x) => `${x.name}${x.spec ? ` ${x.spec}` : ""} × ${x.qty ?? "-"}`).join(", ")} · 합계 {won(partsSum)}원</p>
          {supply !== partsSum && (
            <p className="text-amber-700 dark:text-amber-300">세트 금액 {won(supply)}원이 구성 품목 합계 {won(partsSum)}원과 다릅니다 — 맞는지 확인해 주세요.</p>
          )}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-3">
        {!isSet && <SmallBtn onClick={() => onChange({ ...st, items: [...st.items, { name: "", spec: "", qty: 1, unit_price: null, note: "" }] })}>+ 품목</SmallBtn>}
        <span className="ml-auto text-foreground-muted">공급가액 {won(supply)} · 부가세 {won(Math.round(supply * 0.1))} ·</span>
        <b>합계 {won(supply + Math.round(supply * 0.1))}원</b>
      </div>
    </>
  );
}

// ── 출하: 제품별 수량·사진 + 배송 정보 ────────────────────────────────────────

type ShipLine = { name: string; qty: number | null; photos: string[] };
/** 출하 작성 내용 — 수주 진행 대화(AI)가 채우고, 화면에서 고친 것은 대화 전에 저장(order_state.ship). */
export type ShipDraft = Partial<{
  mode: "items" | "set"; date: string; carrier: string; tracking: string; receiver: string; address: string;
  set_name: string; set_qty: string; set_photos: string[]; lines: ShipLine[];
}>;

/** 건의 기본 품목 줄(거래명세서 — 세트로 묶었으면 그 구성, 없으면 견적). */
function baseLines(deal: DealRow): ShipLine[] {
  const st = deal.statement;
  const src = st?.parts?.length ? st.parts : st?.items;
  return src?.length
    ? src.map((it) => ({ name: [it.name, it.spec].filter(Boolean).join(" "), qty: it.qty, photos: [] }))
    : (deal.quote?.items ?? []).map((it) => ({ name: it.name.replace(/\s*\n\s*-?\s*/g, " "), qty: it.qty, photos: [] }));
}

/** 거래명세서를 세트로 발급했으면 그 세트 줄 — 출하도 같은 세트로 채운다. */
function statementSet(deal: DealRow): DealStatementItem | null {
  const st = deal.statement;
  return st?.parts?.length && st.items.length === 1 ? st.items[0] : null;
}

/** 출하 기록 작성 — 품목별(제품마다 수량·사진) 또는 세트로 묶어서(예: 'TCC1 툴체인저(ATC) 1 SET', 예전 출하 시트처럼).
 *  수주 진행 화면에서는 왼쪽 칸에 바로(대화와 같이 쓰도록), 그 밖에서는 창(ShipDialog)으로 쓴다(사용자 2026-10-07). */
/** 이미 남긴 출하 기록 → 고치기 창에 채울 값(세트 한 줄이면 세트로). */
export function shipmentDraft(s: DealShipment): ShipDraft {
  const lines = s.lines ?? [];
  const set = lines.length === 1 && !!lines[0].qty_text;
  return {
    mode: set ? "set" : "items", date: s.date ?? undefined, carrier: s.carrier ?? undefined, tracking: s.tracking.join(", "),
    receiver: s.receiver ?? "", address: s.address ?? "",
    ...(set ? { set_name: lines[0].name, set_qty: lines[0].qty_text ?? "1 SET", set_photos: lines[0].photos }
      : { lines: lines.map((l) => ({ name: l.name, qty: l.qty, photos: l.photos })) }),
  };
}

export function ShipForm({ deal, busy, act, initial, onSaved, onCancel, onChange, editIndex }: {
  deal: DealRow; busy: boolean; act: Act; initial?: ShipDraft;
  onSaved?: () => void; onCancel?: () => void; onChange?: (d: ShipDraft) => void;
  /** 이미 남긴 출하 기록(번호)을 고칠 때 */
  editIndex?: number;
}) {
  const last = deal.shipments[deal.shipments.length - 1];
  const stSet = statementSet(deal);
  const [mode, setMode] = useState<"items" | "set">(initial?.mode ?? (stSet ? "set" : "items"));
  const [lines, setLines] = useState<ShipLine[]>(initial?.lines?.length ? initial.lines : baseLines(deal));
  const [setName, setSetName] = useState(initial?.set_name ?? stSet?.name ?? defaultSetName(deal));
  const [setQty, setSetQty] = useState(initial?.set_qty ?? `${stSet?.qty ?? 1} SET`);
  const [setPhotos, setSetPhotos] = useState<string[]>(initial?.set_photos ?? []);
  const [f, setF] = useState({ date: initial?.date ?? todayIso(), carrier: initial?.carrier ?? last?.carrier ?? CARRIERS[0],
    tracking: initial?.tracking ?? "", receiver: initial?.receiver ?? last?.receiver ?? "", address: initial?.address ?? last?.address ?? "" });
  const [up, setUp] = useState<number | "set" | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // 화면에서 고친 내용을 위로 알린다(수주 진행: 대화 보내기 전에 저장)
  const report = useRef(onChange);
  useEffect(() => { report.current = onChange; }, [onChange]);
  useEffect(() => {
    report.current?.({ mode, lines, set_name: setName, set_qty: setQty, set_photos: setPhotos, ...f });
  }, [mode, lines, setName, setQty, setPhotos, f]);

  async function addPhotos(target: number | "set", files: FileList) {
    setUp(target);
    setErr(null);
    try {
      for (const file of Array.from(files)) {
        const { id } = await upload<{ id: string }>(`/${deal.id}/ship-photos`, file);
        if (target === "set") setSetPhotos((xs) => [...xs, id]);
        else setLines((xs) => xs.map((x, j) => (j === target ? { ...x, photos: [...x.photos, id] } : x)));
      }
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setUp(null);
    }
  }
  const items = lines.filter((l) => l.name.trim() && (l.qty ?? 0) > 0);
  async function save() {
    const body = mode === "set"
      ? { ...f, purpose: "판매", qty_text: setQty.trim(),
          // 예전 출하 시트처럼 '세트 이름 수량' + 참고로 구성품
          items: `${setName.trim()} ${setQty.trim()}` + (items.length ? `\n(구성: ${items.map((l) => `${l.name} × ${l.qty}`).join(", ")})` : "").slice(0, 900),
          lines: [{ name: setName.trim(), qty: null, qty_text: setQty.trim(), photos: setPhotos }] }
      : { ...f, purpose: "판매", lines: items };
    const ok = editIndex != null ? await act("shipment-edit", { ...body, index: editIndex }) : await act("shipments", body);
    if (ok) onSaved?.();
  }
  const noPhoto = mode === "set" ? (setPhotos.length ? 0 : 1) : items.filter((l) => !l.photos.length).length;
  const canSave = !busy && up === null && (mode === "set" ? !!setName.trim() && !!setQty.trim() : items.length > 0);

  const photoStrip = (photos: string[], remove: (p: string) => void, alt: string) => photos.length > 0 && (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {photos.map((p) => (
        <span key={p} className="relative">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={`/api/sales-deals/${deal.id}/ship-photos/${p}`} alt={alt} className="h-16 w-16 rounded-md object-cover ring-1 ring-border" />
          <button type="button" aria-label="사진 빼기" onClick={() => remove(p)}
                  className="absolute -right-1 -top-1 grid h-4 w-4 place-items-center rounded-full bg-black/60 text-[10px] text-white">✕</button>
        </span>
      ))}
    </div>
  );
  const photoBtn = (target: number | "set") => (
    <label className={cn("cursor-pointer rounded-lg border border-border px-2 py-1 font-semibold text-accent hover:bg-accent/5",
      up !== null && "pointer-events-none opacity-50")}>
      {up === target ? "올리는 중…" : "+ 사진"}
      <input type="file" accept=".jpg,.jpeg,.png,.webp" multiple className="hidden"
             onChange={(e) => { const fs = e.target.files; if (fs?.length) void addPhotos(target, fs); e.target.value = ""; }} />
    </label>
  );

  return (
    <div className="flex flex-col gap-3 text-[12.5px]">
      {editIndex != null && (
        <p className="rounded-lg bg-sky-500/10 px-3 py-2 text-sky-800 dark:text-sky-200">이미 남긴 출하 기록을 고칩니다 — 저장하면 이 기록이 바뀝니다(새 출하가 늘어나지 않음).</p>
      )}
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="font-semibold">출하 품목</span>
        {([["items", "품목별"], ["set", "세트로 묶어서 (예: 1 SET)"]] as const).map(([k, label]) => (
          <button key={k} type="button" onClick={() => setMode(k)} aria-pressed={mode === k}
                  className={cn("rounded-full border px-2.5 py-0.5 text-[12px] font-semibold",
                    mode === k ? "border-accent bg-accent/10 text-accent" : "border-border text-foreground-muted hover:bg-foreground/5")}>
            {label}
          </button>
        ))}
      </div>
      {mode === "set" ? (
        <div className="rounded-lg border border-border p-2">
          <div className="flex flex-wrap items-center gap-2">
            <input value={setName} onChange={(e) => setSetName(e.target.value)} aria-label="세트 이름" className={cn(INPUT, "h-8 min-w-0 flex-1")} />
            <input value={setQty} onChange={(e) => setSetQty(e.target.value)} aria-label="세트 수량" className={cn(INPUT, "h-8 w-24 text-right")} />
            {photoBtn("set")}
          </div>
          {photoStrip(setPhotos, (p) => setSetPhotos((xs) => xs.filter((q) => q !== p)), `${setName} 출하 사진`)}
          {items.length > 0 && (
            <p className="mt-1.5 text-[11.5px] text-foreground-subtle">구성(참고로 함께 남김): {items.map((l) => `${l.name} × ${l.qty}`).join(", ")}</p>
          )}
        </div>
      ) : (
        <>
          <ul className="flex flex-col gap-2">
            {lines.map((l, i) => (
              <li key={i} className="rounded-lg border border-border p-2">
                <div className="flex flex-wrap items-center gap-2">
                  <input value={l.name} onChange={(e) => setLines((xs) => xs.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))}
                         className={cn(INPUT, "h-8 min-w-0 flex-1")} />
                  <input value={l.qty ?? ""} inputMode="numeric" aria-label="수량"
                         onChange={(e) => setLines((xs) => xs.map((x, j) => (j === i ? { ...x, qty: Number(e.target.value) || 0 } : x)))}
                         className={cn(INPUT, "h-8 w-20 text-right")} />
                  {photoBtn(i)}
                </div>
                {photoStrip(l.photos, (p) => setLines((xs) => xs.map((x, j) => (j === i ? { ...x, photos: x.photos.filter((q) => q !== p) } : x))),
                            `${l.name} 출하 사진`)}
              </li>
            ))}
          </ul>
          <SmallBtn onClick={() => setLines((xs) => [...xs, { name: "", qty: 1, photos: [] }])}>+ 제품 줄</SmallBtn>
        </>
      )}
      <div className="grid gap-2 md:grid-cols-3">
        <Field label="출하일"><input type="date" value={f.date} onChange={(e) => setF({ ...f, date: e.target.value })} className={INPUT} /></Field>
        <Field label="배송">
          <select value={f.carrier} onChange={(e) => setF({ ...f, carrier: e.target.value })} className={INPUT}>
            {CARRIERS.map((c) => <option key={c}>{c}</option>)}
          </select>
        </Field>
        <Field label="운송장 번호 (여러 개는 쉼표로)"><input value={f.tracking} onChange={(e) => setF({ ...f, tracking: e.target.value })} className={INPUT} /></Field>
        <Field label="받는 사람·연락처"><input value={f.receiver} onChange={(e) => setF({ ...f, receiver: e.target.value })} className={INPUT} /></Field>
        <Field label="주소" wide><input value={f.address} onChange={(e) => setF({ ...f, address: e.target.value })} className={INPUT} /></Field>
      </div>
      {err && <p className="text-red-600">{err}</p>}
      {noPhoto > 0 && (
        <p className="text-[12px] text-amber-700 dark:text-amber-300">
          {mode === "set" ? "세트 사진이 없습니다" : `사진이 없는 제품이 ${noPhoto}개 있습니다`} — 그대로 저장할 수도 있습니다.
        </p>
      )}
      <div className="flex justify-end gap-2">
        {onCancel && <SmallBtn onClick={onCancel}>취소</SmallBtn>}
        <PrimarySmall onClick={() => void save()} disabled={!canSave}>{editIndex != null ? "고친 내용 저장" : "출하 기록 저장"}</PrimarySmall>
      </div>
    </div>
  );
}

/** 출하 기록 창 — 출하 목록·영업 건 상세에서. 수주 진행 화면은 ShipForm 을 칸 안에 바로 쓴다. */
export function ShipDialog({ deal, busy, act, onClose, initial, onSaved }: {
  deal: DealRow; busy: boolean; act: Act; onClose: () => void; initial?: ShipDraft; onSaved?: () => void;
}) {
  return (
    <Modal title="출하 기록" onClose={onClose} busy={busy}>
      <ShipForm deal={deal} busy={busy} act={act} initial={initial}
                onSaved={() => { onSaved?.(); onClose(); }} onCancel={onClose} />
    </Modal>
  );
}

// ── 조각 ─────────────────────────────────────────────────────────────────

function Modal({ title, children, onClose, busy }: { title: string; children: React.ReactNode; onClose: () => void; busy: boolean }) {
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/30 p-4" onClick={(e) => { e.stopPropagation(); if (!busy) onClose(); }}>
      <div role="dialog" aria-label={title} onClick={(e) => e.stopPropagation()}
           className="scroll-thin max-h-[92svh] w-full max-w-3xl overflow-y-auto rounded-xl border border-border bg-surface p-5 shadow-xl">
        <h3 className="mb-3 text-[15px] font-semibold">{title}</h3>
        {children}
      </div>
    </div>
  );
}

function SmallBtn({ children, ...p }: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button type="button" {...p} className="rounded-lg border border-border px-2.5 py-1 text-[12px] font-semibold hover:bg-foreground/5 disabled:opacity-40">{children}</button>;
}

function PrimarySmall({ children, ...p }: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button type="button" {...p} className="rounded-lg bg-accent px-3 py-1 text-[12.5px] font-semibold text-white disabled:opacity-40">{children}</button>;
}
