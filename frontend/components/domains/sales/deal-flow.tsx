// 수주 뒤 절차의 공용 창·칸(사용자 2026-10-07) — 거래명세서 발급 창, 출하 작성(ShipForm·ShipDialog).
// 절차는 수주 진행 탭(order-workspace)에서 진행하고, 영업 건 상세에는 [AI 와 수주 진행 →] 안내만 둔다.
// 단계·출하 조건·받을 금액은 backend(sales_deals/flow.py)가 계산해 deal.flow 로 준다.
"use client";

import { useEffect, useRef, useState } from "react";
import { Icon } from "@/components/shared/ui/icon";
import { Field, INPUT, Muted, todayIso, won } from "@/components/domains/sales/deal-format";
import { SnButton, SnPanel, flatName, loadSnView, usableUnits, type SnLine } from "@/components/domains/sales/unit-register";
import { ITEM_BTN, primaryBtn, secondaryBtn } from "@/components/domains/sales/proposal-actions";
import { AMOUNT_BOX, HEAD_FIELD, ROW_BTN, ROW_CARD, ROW_FIELD, ROW_ICON_BTN, ROW_LABEL } from "@/components/domains/sales/quote-styles";
import type { DealRow, DealShipment, DealStatement, DealStatementItem, IssueUnits } from "@/lib/shared/sales-deals";
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
/** 거래명세서 공급받는자 칸 안 예시(사용자 2026-10-12) — 영업 건 상세 발급 창·수주 진행 화면이 같이 쓴다. */
export const BUYER_HINT = {
  reg_no: "예: 123-45-67890", name: "예: (주)가나테크", ceo: "예: 홍길동", address: "예: 경기도 포천시 ○○로 12, 2동",
} as const;

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
            <Field label="발행자 이니셜 *"><input value={st.initials ?? ""} maxLength={4} placeholder="예: LU"
                                         onChange={(e) => setSt({ ...st, initials: e.target.value.toUpperCase() })} className={INPUT} /></Field>
            <Field label="발행일자"><input type="date" value={st.date} onChange={(e) => setSt({ ...st, date: e.target.value })} className={INPUT} /></Field>
          </div>
          <p className="text-[11.5px] text-foreground-subtle">
            발행번호는 발급할 때 자동으로 붙습니다 — TS-{(st.initials || "LU").toUpperCase()}{st.date.replaceAll("-", "")}-01 (그 발행자의 그날 몇 번째 명세서인지).
            {deal.statement_no && ` 지금 발급본: ${deal.statement_no}`}
          </p>
          <p className="font-semibold">공급받는자 <span className="font-normal text-foreground-subtle">· 한 번 적으면 같은 고객사는 다음부터 채워 둡니다</span></p>
          <div className="grid gap-2 md:grid-cols-3">
            <Field label="등록번호 *"><input value={st.buyer.reg_no} onChange={(e) => setBuyer("reg_no", e.target.value)} className={INPUT} placeholder={BUYER_HINT.reg_no} /></Field>
            <Field label="상호 *"><input value={st.buyer.name} onChange={(e) => setBuyer("name", e.target.value)} className={INPUT} placeholder={BUYER_HINT.name} /></Field>
            <Field label="성명(대표)"><input value={st.buyer.ceo} onChange={(e) => setBuyer("ceo", e.target.value)} className={INPUT} placeholder={BUYER_HINT.ceo} /></Field>
          </div>
          <Field label="주소 *"><input value={st.buyer.address} onChange={(e) => setBuyer("address", e.target.value)} className={INPUT} placeholder={BUYER_HINT.address} /></Field>
          <StatementItems deal={deal} st={st} onChange={setSt} withNote />
          <div className="flex justify-end gap-2">
            <SmallBtn onClick={() => onClose(false)}>취소</SmallBtn>
            <PrimarySmall onClick={() => void save()} disabled={busy}>{deal.statement ? "다시 발급 (새 번호)" : "발급"}</PrimarySmall>
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
      {/* 품목 줄 — 견적서 작성과 같은 두 단 카드(좁은 칸에서도 품명·규격이 다 보이게, 사용자 2026-10-23) */}
      <ul className="flex flex-col gap-2.5">
        {st.items.map((it, i) => (
          <li key={i} className={ROW_CARD}>
            <div className="grid gap-2.5 sm:grid-cols-2">
              <label className={ROW_LABEL}>품명
                <input value={it.name} onChange={(e) => setItem(i, { name: e.target.value })} placeholder="예: TCV1 MASTER T.C" className={HEAD_FIELD} />
              </label>
              <label className={ROW_LABEL}>규격
                <input value={it.spec} onChange={(e) => setItem(i, { spec: e.target.value })} placeholder="예: Payload 10Kg" className={HEAD_FIELD} />
              </label>
            </div>
            <div className="mt-2.5 flex flex-wrap items-end gap-2.5">
              <label className={ROW_LABEL}>수량
                <span className="flex items-center gap-1.5">
                  <input value={it.qty ?? ""} inputMode="numeric" className={cn(ROW_FIELD, "w-[72px] text-right")}
                         onChange={(e) => {
                           const qty = toNum(e.target.value);
                           // 세트 수량을 바꾸면 단가 = 구성 합계 ÷ 수량(공급가액은 그대로)
                           setItem(i, isSet && qty ? { qty, unit_price: Math.round(partsSum / qty) } : { qty });
                         }} />
                  {it.unit && <span className="shrink-0 text-[12.5px] font-semibold text-foreground-muted">{it.unit}</span>}
                </span>
              </label>
              <label className={ROW_LABEL}>단가
                <input value={it.unit_price ?? ""} inputMode="numeric" onChange={(e) => setItem(i, { unit_price: toNum(e.target.value) })}
                       className={cn(ROW_FIELD, "w-[130px] text-right")} />
              </label>
              <div className={cn(ROW_LABEL, "ml-auto flex-1 items-end")}>
                <span>금액</span>
                <span className={AMOUNT_BOX}>{won(lineAmount(it))}</span>
              </div>
              {!isSet && (
                <button type="button" className={cn(ROW_ICON_BTN, "hover:border-red-400/60 hover:bg-red-500/5 hover:text-red-600")} aria-label="줄 빼기" title="이 줄 빼기"
                        onClick={() => onChange({ ...st, items: st.items.filter((_, j) => j !== i) })}><Icon name="trash" className="h-4 w-4" /></button>
              )}
            </div>
            {withNote && (
              <label className={cn(ROW_LABEL, "mt-2.5")}>비고
                <input value={it.note} onChange={(e) => setItem(i, { note: e.target.value })} placeholder="예: 납품 후 설치" className={HEAD_FIELD} />
              </label>
            )}
          </li>
        ))}
      </ul>
      {isSet && (
        <div className="flex flex-col gap-1 text-[11.5px] text-foreground-subtle">
          <p>구성(명세서에는 세트 한 줄로만 나옵니다): {parts.map((x) => `${x.name}${x.spec ? ` ${x.spec}` : ""} × ${x.qty ?? "-"}`).join(", ")} · 합계 {won(partsSum)}원</p>
          {supply !== partsSum && (
            <p className="text-amber-700 dark:text-amber-300">세트 금액 {won(supply)}원이 구성 품목 합계 {won(partsSum)}원과 다릅니다 — 맞는지 확인해 주세요.</p>
          )}
        </div>
      )}
      {!isSet && (
        <button type="button" className={cn(ITEM_BTN, "self-start")} onClick={() => onChange({ ...st, items: [...st.items, { name: "", spec: "", qty: 1, unit_price: null, note: "" }] })}>
          <Icon name="plus" className="h-3.5 w-3.5 text-blue-600 dark:text-blue-400" /> 품목 추가
        </button>
      )}
      {/* 합계 막대 — 견적서 작성과 같은 모양(사용자 2026-10-23) */}
      <div className="flex flex-wrap items-end gap-x-6 gap-y-1 rounded-xl border border-blue-500/20 bg-gradient-to-b from-blue-500/[0.06] to-blue-500/[0.02] px-4 py-3">
        <span className="flex flex-col"><span className="text-[12px] font-semibold text-foreground-muted">공급가액</span><b className="text-[14px]">{won(supply)}원</b></span>
        <span className="flex flex-col"><span className="text-[12px] font-semibold text-foreground-muted">부가세</span><b className="text-[14px]">{won(Math.round(supply * 0.1))}원</b></span>
        <span className="ml-auto flex flex-col items-end"><span className="text-[12px] font-semibold text-foreground-muted">합계</span>
          <b className="text-[22px] font-extrabold tracking-tight">{won(supply + Math.round(supply * 0.1))}원</b></span>
      </div>
    </>
  );
}

// ── 출하: 제품별 수량·사진 + 배송 정보 ────────────────────────────────────────

/** line_no = 수주 판의 몇 번째 품목 줄인지 — 있으면 그 줄 오른쪽에 [S/N 등록](사용자 2026-10-23) */
type ShipLine = { name: string; qty: number | null; photos: string[]; line_no?: number };
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
      : { lines: lines.map((l) => ({ name: l.name, qty: l.qty, photos: l.photos, line_no: l.line_no })) }),
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
  // S/N(사용자 2026-10-23) — 출하 기록을 쓰며 품목마다 등록한다. 품목은 수주 판의 품목 줄(설치비·물류비·서술형 줄 빼고)에서 딸려 온다.
  const [view, setView] = useState<IssueUnits | null>(null);
  const [snOpen, setSnOpen] = useState<number | null>(null);
  const src = deal.units ? { quote_id: deal.units.quote_id, revision: deal.units.revision } : null;
  useEffect(() => {
    if (!src) return;
    let alive = true;
    loadSnView(deal.id, src)
      .then((v) => {
        if (!alive || !v) return;
        setView(v);
        // 새 출하는 수주 판 품목으로 채우고, 남긴 출하(고치기)는 이름이 같은 품목 줄에 짝을 지어 준다
        setLines((xs) => (initial?.lines?.length
          ? xs.map((l) => (l.line_no != null ? l : { ...l, line_no: v.lines.find((ln) => flatName(ln.name) === flatName(l.name))?.line_no }))
          : v.lines.length ? v.lines.map((ln) => ({ name: flatName(ln.name), qty: ln.qty, photos: [], line_no: ln.line_no })) : xs));
      })
      .catch(() => { /* 품목은 기본 줄 그대로 */ });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deal.id, !!src, src?.quote_id, src?.revision]);
  const snLine = (no: number | undefined): SnLine | undefined => (no == null ? undefined : view?.lines.find((ln) => ln.line_no === no));
  // 이 출하에서 품목 줄마다 적어야 할 S/N 개수 — 품목별: 출하 수량, 세트: 수주 수량 중 앞선 출하로 안 나간 만큼
  const need = new Map<number, number>();
  if (view) {
    if (mode === "set") {
      for (const ln of view.lines) need.set(ln.line_no, Math.max(0, ln.qty - (ln.units.length - usableUnits(ln, editIndex).length)));
    } else {
      for (const l of lines) {
        if (snLine(l.line_no) && (l.qty ?? 0) > 0) need.set(l.line_no!, (need.get(l.line_no!) ?? 0) + Math.ceil(l.qty ?? 0));
      }
    }
  }
  const snCount = (no: number) => usableUnits(snLine(no)!, editIndex).length;
  const snShort = view ? [...need].filter(([no, n]) => n > 0 && snCount(no) < n).map(([no]) => flatName(snLine(no)!.name)) : [];
  // 품목마다 출하 수량만큼만 묶는다 — 이 출하에 이미 묶인 것 먼저, 그다음 안 나간 것(검증 2026-10-26: 다음 출하 몫까지 묶이던 문제)
  const pick = (no: number) => {
    const us = usableUnits(snLine(no)!, editIndex);
    const mine = editIndex == null ? [] : us.filter((u) => u.shipment_index === editIndex);
    return [...mine, ...us.filter((u) => !mine.includes(u))].slice(0, need.get(no) ?? 0).map((u) => u.id);
  };
  // 고칠 때 이번 품목 칸에 없는 줄(이름으로 짝을 못 찾은 예전 줄)에 묶인 S/N 은 그대로 둔다
  const bound = view && editIndex != null
    ? view.lines.filter((ln) => !need.has(ln.line_no)).flatMap((ln) => ln.units.filter((u) => u.shipment_index === editIndex).map((u) => u.id)) : [];
  const unitIds = view ? [...new Set([...[...need.keys()].flatMap(pick), ...bound])] : null;

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
    if (unitIds) Object.assign(body, { unit_ids: unitIds });
    const ok = editIndex != null ? await act("shipment-edit", { ...body, index: editIndex, expect: deal.shipments[editIndex] })
      : await act("shipments", body);
    if (ok) onSaved?.();
  }
  const noPhoto = mode === "set" ? (setPhotos.length ? 0 : 1) : items.filter((l) => !l.photos.length).length;
  // 순서를 건너뛴 출하 안내(사용자 2026-10-23) — 정상 순서는 거래명세서 → 입금 확인 → 출하. 막지는 않는다(출하는 언제든, 2026-10-08)
  const skipped = editIndex != null || !deal.flow?.steps.length ? [] : [
    ...(deal.statement ? [] : ["거래명세서 발급"]),
    ...(deal.flow.terms ?? []).filter((t) => !t.confirmed && (/^(선금|전액)$/.test(t.label) || /발주|계약|출하\s*전|납품\s*전/.test(t.when)))
      .map((t) => `${t.label} ${t.pct}% 입금 확인`),
  ];
  const canSave = !busy && up === null && (editIndex != null || snShort.length === 0) && (mode === "set" ? !!setName.trim() && !!setQty.trim() : items.length > 0);

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
    <label className={cn(ROW_BTN, "cursor-pointer text-accent", up !== null && "pointer-events-none opacity-50")}>
      <Icon name="plus" className="h-3.5 w-3.5" /> {up === target ? "올리는 중…" : "사진"}
      <input type="file" accept=".jpg,.jpeg,.png,.webp" multiple className="hidden"
             onChange={(e) => { const fs = e.target.files; if (fs?.length) void addPhotos(target, fs); e.target.value = ""; }} />
    </label>
  );

  const snBtn = (no: number) => (
    <SnButton tall count={snCount(no)} need={need.get(no) ?? 0} open={snOpen === no} disabled={busy || !need.get(no)}
              onClick={() => setSnOpen(snOpen === no ? null : no)} />
  );
  const snBox = (no: number) => view && snOpen === no && (
    <SnPanel key={no} dealId={deal.id} view={view} line={snLine(no)!} need={need.get(no) ?? 0} editIndex={editIndex} bindIndex={editIndex}
             bindExpect={editIndex != null ? deal.shipments[editIndex] : undefined}
             onSaved={setView} onClose={() => setSnOpen(null)} />
  );
  const field = (label: string, input: React.ReactNode) => <label className={ROW_LABEL}><span>{label}</span>{input}</label>;

  // 견적서·거래명세서 작성과 같은 짜임(사용자 2026-10-23) — ① 출하 품목 ② 배송, 흰 40px 칸·테두리 버튼
  return (
    <div className="flex flex-col gap-3 text-[12.5px]">
      {skipped.length > 0 && (
        <p className="rounded-xl border border-amber-500/30 bg-amber-500/10 px-3.5 py-2.5 text-amber-800 dark:text-amber-200">
          <b>정상 순서: 거래명세서 → 입금 확인 → 출하</b> · 지금은 {skipped.join(", ")} 전입니다. 그래도 출하 기록은 남길 수 있습니다.
        </p>
      )}
      {editIndex != null && (
        <p className="rounded-xl bg-sky-500/10 px-3.5 py-2.5 text-sky-800 dark:text-sky-200">이미 남긴 출하 기록을 고칩니다 — 저장하면 이 기록이 바뀝니다(새 출하가 늘어나지 않음).</p>
      )}
      <div className="flex flex-col gap-3 rounded-xl border border-border p-4">
        <div className="flex flex-wrap items-center gap-2">
          <GroupHead n={1} title="출하 품목" tone="bg-cyan-600/10 text-cyan-800 dark:text-cyan-300" />
          <div className="ml-auto flex gap-1.5">
            {([["items", "품목별"], ["set", "세트로 묶어서 (예: 1 SET)"]] as const).map(([k, label]) => (
              <button key={k} type="button" onClick={() => setMode(k)} aria-pressed={mode === k}
                      className={cn("h-8 rounded-full border px-3 text-[12px] font-semibold",
                        mode === k ? "border-accent bg-accent/10 text-accent" : "border-border-strong bg-surface-raised text-foreground-muted hover:bg-foreground/5")}>
                {label}
              </button>
            ))}
          </div>
        </div>
        {mode === "set" ? (
          <div className={ROW_CARD}>
            <div className="flex flex-wrap items-end gap-2.5">
              <label className={cn(ROW_LABEL, "min-w-[180px] flex-1")}><span>세트 이름</span>
                <input value={setName} onChange={(e) => setSetName(e.target.value)} aria-label="세트 이름" placeholder="예: TCV1 툴체인저(ATC)" className={HEAD_FIELD} />
              </label>
              <label className={ROW_LABEL}><span>수량</span>
                <input value={setQty} onChange={(e) => setSetQty(e.target.value)} aria-label="세트 수량" placeholder="예: 1 SET" className={cn(ROW_FIELD, "w-[96px] text-right")} />
              </label>
              {photoBtn("set")}
            </div>
            {photoStrip(setPhotos, (p) => setSetPhotos((xs) => xs.filter((q) => q !== p)), `${setName} 출하 사진`)}
            {items.length > 0 && (
              <p className="mt-2 text-[11.5px] text-foreground-subtle">구성(참고로 함께 남김): {items.map((l) => `${l.name} × ${l.qty}`).join(", ")}</p>
            )}
            {view && view.lines.length > 0 && (
              <ul className="mt-3 flex flex-col gap-2 border-t border-border pt-3">
                <li className="text-[12px] font-semibold text-foreground-muted">구성 품목 S/N</li>
                {view.lines.map((ln) => (
                  <li key={ln.line_no}>
                    <div className="flex items-center gap-2.5">
                      <span className="min-w-0 flex-1 truncate text-[13.5px] font-semibold">{flatName(ln.name)}</span>
                      <span className="text-foreground-muted">× {need.get(ln.line_no) ?? 0}</span>
                      {snBtn(ln.line_no)}
                    </div>
                    {snBox(ln.line_no)}
                  </li>
                ))}
              </ul>
            )}
          </div>
        ) : (
          <>
            <ul className="flex flex-col gap-2.5">
              {lines.map((l, i) => (
                <li key={i} className={ROW_CARD}>
                  {/* 한 줄 = 제품 · 수량 · 사진 · [S/N 등록] — 좁은 상세 패널에서는 버튼이 아랫줄로 */}
                  <div className="flex flex-wrap items-end gap-2.5">
                    <label className={cn(ROW_LABEL, "min-w-[180px] flex-1")}><span>제품</span>
                      <input value={l.name} onChange={(e) => setLines((xs) => xs.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))}
                             aria-label="제품" placeholder="예: TCV1 MASTER T.C" className={HEAD_FIELD} />
                    </label>
                    <label className={ROW_LABEL}><span>수량</span>
                      <input value={l.qty ?? ""} inputMode="numeric" aria-label="수량"
                             onChange={(e) => setLines((xs) => xs.map((x, j) => (j === i ? { ...x, qty: Number(e.target.value) || 0 } : x)))}
                             className={cn(ROW_FIELD, "w-[72px] text-right")} />
                    </label>
                    <div className="ml-auto flex gap-2">
                      {photoBtn(i)}
                      {snLine(l.line_no) && snBtn(l.line_no!)}
                      {/* [제품 줄 추가]로 넣은 줄은 지울 수 있다(사용자 2026-10-23) — 수주 견적서에서 딸려 온 품목 줄은 그대로 */}
                      {!snLine(l.line_no) && (
                        <button type="button" aria-label={`${l.name || "새"} 줄 빼기`} title="빼기" disabled={up !== null}
                                onClick={() => { setLines((xs) => xs.filter((_, j) => j !== i)); setSnOpen(null); }}
                                className={cn(ROW_ICON_BTN, "hover:text-red-600")}>
                          <Icon name="trash" className="h-4 w-4" />
                        </button>
                      )}
                    </div>
                  </div>
                  {snLine(l.line_no) && lines.findIndex((x) => x.line_no === l.line_no) === i && snBox(l.line_no!)}
                  {photoStrip(l.photos, (p) => setLines((xs) => xs.map((x, j) => (j === i ? { ...x, photos: x.photos.filter((q) => q !== p) } : x))),
                              `${l.name} 출하 사진`)}
                </li>
              ))}
            </ul>
            <div>
              <button type="button" className={ITEM_BTN} onClick={() => setLines((xs) => [...xs, { name: "", qty: 1, photos: [] }])}>
                <Icon name="plus" className="h-3.5 w-3.5 text-accent" /> 제품 줄 추가
              </button>
            </div>
          </>
        )}
      </div>
      <div className="rounded-xl border border-border p-4">
        <GroupHead n={2} title="배송" tone="bg-blue-500/10 text-blue-700 dark:text-blue-300" />
        {/* 좁은 칸(수주 진행·상세 패널)에서도 라벨이 줄바꿈되지 않게 두 줄로 */}
        <div className="mt-3 grid gap-3 sm:grid-cols-[150px_130px_minmax(0,1fr)]">
          {field("출하일", <input type="date" value={f.date} onChange={(e) => setF({ ...f, date: e.target.value })} className={HEAD_FIELD} />)}
          {field("배송", (
            <select value={f.carrier} onChange={(e) => setF({ ...f, carrier: e.target.value })} className={HEAD_FIELD}>
              {/* 예전 기록의 배송(예: '택배')이 목록에 없어도 고칠 때 바뀌지 않게 */}
              {(f.carrier && !CARRIERS.includes(f.carrier) ? [f.carrier, ...CARRIERS] : CARRIERS).map((c) => <option key={c}>{c}</option>)}
            </select>
          ))}
          {field("운송장 번호", <input value={f.tracking} onChange={(e) => setF({ ...f, tracking: e.target.value })} className={HEAD_FIELD}
                                   title="여러 개는 쉼표로" placeholder="예: 1234-5678, 2345-6789 (쉼표로 여러 개)" />)}
        </div>
        <div className="mt-3 grid gap-3 sm:grid-cols-[220px_minmax(0,1fr)]">
          {field("받는 사람·연락처", <input value={f.receiver} onChange={(e) => setF({ ...f, receiver: e.target.value })} className={HEAD_FIELD}
                                          placeholder="예: 김철수 과장 010-1234-5678" />)}
          {field("주소", <input value={f.address} onChange={(e) => setF({ ...f, address: e.target.value })} className={HEAD_FIELD}
                               placeholder="예: 경기도 포천시 ○○로 12, 2동 자재 입고장" />)}
        </div>
      </div>
      {snShort.length > 0 && (
        <p className="rounded-xl border border-red-500/25 bg-red-500/[0.06] px-3.5 py-2.5 text-red-700 dark:text-red-300">
          <b>출하 전에 S/N을 등록해 주세요</b> — {snShort.join(", ")}. 품목 오른쪽 [S/N 등록]을 눌러 한 개씩 적습니다(출하일부터 무상 기간 시작).
        </p>
      )}
      {err && <p className="text-red-600">{err}</p>}
      <div className="flex flex-wrap items-center gap-2">
        {noPhoto > 0 && (
          <p className="text-[12px] text-amber-700 dark:text-amber-300">
            {mode === "set" ? "세트 사진이 없습니다" : `사진이 없는 제품이 ${noPhoto}개 있습니다`} — 그대로 저장할 수도 있습니다.
          </p>
        )}
        <div className="ml-auto flex gap-2">
          {onCancel && <button type="button" className={secondaryBtn} onClick={onCancel}>취소</button>}
          <button type="button" className={primaryBtn} onClick={() => void save()} disabled={!canSave}>
            <Icon name="check" className="h-4 w-4" /> {editIndex != null ? "고친 내용 저장" : "출하 기록 저장"}
          </button>
        </div>
      </div>
    </div>
  );
}

/** 단계 묶음 제목 — 번호 배지 + 제목(거래명세서·출하 작성 칸이 같이 쓴다) */
export function GroupHead({ n, title, tone }: { n: number; title: string; tone: string }) {
  return (
    <div className="flex items-center gap-2.5">
      <span className={cn("grid h-[26px] w-[26px] place-items-center rounded-lg text-[12.5px] font-extrabold", tone)}>{n}</span>
      <h4 className="text-[14px] font-extrabold text-foreground">{title}</h4>
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
