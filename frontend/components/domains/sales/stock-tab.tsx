// [제품 재고 관리] 탭(사용자 2026-11-19) — 제품 구매 건의 입고 기록에서 제품 한 대 = 한 줄로 자동으로 채워지는 재고 목록.
// 아래 탭 두 개: 판매용 / 보유 후 자체 개발용. 입고 기록에서 오는 칸(제품명·고객사·담당자·S/N·S/W 담당자·입고일)은 입고 기록에서 고치고,
// 구분·상태(+메모)·자체 개발용 영업팀 담당자·표시 이름은 여기서 정한다. 상태는 처음 '대기 중' — 눌러서 바꾸고 메모는 상태 아랫줄에 보인다.
// 판매용은 S/N 을 일부러 안 받으므로(출하 때 등록) S/N 칸·미등록 경고가 없다. 알림 칸은 아직 비워 둔다(사용자 2026-11-19).
// 묶기(사용자 2026-11-19, 저장은 한 대씩 그대로):
//  · 기본 — 입고 기록에 적은 줄 그대로(같은 품목을 [줄 나누기]로 나눴으면 나눈 줄마다 한 줄, 판매용·자체 개발용 같게).
//    여러 대 줄은 수량·상태별 개수, 펼치면 한 대씩(S/N 도 펼쳐서), '100개 중 30개를 사용 중으로'.
//  · [제품별 묶기] — 같은 제품(표시 이름, 대·소문자·띄어쓰기 무시)을 한 줄로 '총 6대'. 펼치면 한 대씩(S/N 없는 품목은 입고 건 묶음).
// 표시 이름 — 발주서 품목명(name)은 그대로 두고 재고에 보일 이름만 바꾼다(추적은 발주서 이름으로도 됨, 검색은 둘 다).
// 제조사 — 처음엔 구매 공급사, 대리점에서 산 것 등은 ✎ 로 고친다(사용자 2026-11-19). 제품별 묶기는 제조사 + 표시 이름이 같을 때.
"use client";

import { Fragment, useCallback, useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { askChoice, askConfirm, askText } from "@/components/shared/ui/confirm-host";
import { StockDirect } from "@/components/domains/sales/stock-direct";
import { StockExcelButtons } from "@/components/domains/sales/stock-excel";
import { pickPhotos, uploadPhoto } from "@/components/domains/sales/sn-photos";
import { INPUT, Muted, shortDate, stamp } from "@/components/domains/sales/deal-format";
import { productKey, STOCK_PURPOSE_LABEL, stockDetailText, stockLabel, stockRequest, type StockHistoryEntry, type StockListData,
         type StockPatch, type StockPurpose, type StockRow } from "@/lib/shared/stock";
import { REGION_LABEL } from "@/lib/shared/purchases";
import { cn } from "@/lib/shared/utils";

const STATUS_TONE: Record<string, string> = {
  "대기 중": "bg-slate-500/12 text-slate-700 dark:text-slate-300",
  "수리 필요": "bg-red-500/12 text-red-700 dark:text-red-300",
  "수리 중": "bg-amber-500/12 text-amber-800 dark:text-amber-300",
  "사용 중": "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300",
  "대여 중": "bg-violet-500/12 text-violet-700 dark:text-violet-300",
  "기타": "bg-foreground/8 text-foreground-muted",
};
/** 영업 건 대여로 나간 제품(사용자 2026-11-20) — 대여 S/N 등록으로만 들어가고 회수하면 자동으로 '대기 중'. 손으로 고르지 않는다 */
const RENTED = "대여 중";
/** 손으로 고를 수 있는 상태 — '대여 중' 빼고(지금 그 상태면 그대로 보임) */
const pickable = (states: string[], cur?: string) => states.filter((s) => s !== RENTED || s === cur);
/** 제조사 S/N · UND S/N 두 줄 칸 */
function SnLines({ r }: { r: StockRow }) {
  return r.no_sn ? <Muted>S/N 없는 품목</Muted> : (
    <>
      <div><span className="mr-1 font-sans text-[11px] text-foreground-subtle">제조사</span>{r.maker_sn || <Muted>-</Muted>}</div>
      <div><span className="mr-1 font-sans text-[11px] text-foreground-subtle">UND</span>{r.und_sn || <Muted>-</Muted>}</div>
    </>
  );
}
const SELECT = "h-8 rounded-lg border bg-surface px-2 text-[12.5px] outline-none focus:border-accent";
/** 한 대 줄의 사진 칸에 나란히 보일 사진 수 — 넘치면 +N */
const PHOTO_SHOW = 3;
const MODE_KEY = "und:stock:group-mode";
const MODE_EVENT = "und-stock-mode";
type GroupMode = "list" | "product";
// [제품별 묶기]는 브라우저에 기억(사람마다 편한 쪽으로). 서버 그림은 늘 기본(한 대씩), 브라우저에서 저장값으로 바뀐다.
// 저장소를 못 쓰는 브라우저(사생활 모드 등)는 이 화면을 여는 동안만 기억(memMode)
let memMode: GroupMode = "list";
const readMode = (): GroupMode => {
  try { return localStorage.getItem(MODE_KEY) === "product" ? "product" : "list"; } catch { return memMode; }
};
const writeMode = (m: GroupMode) => {
  memMode = m;
  try { localStorage.setItem(MODE_KEY, m); } catch { /* 저장소를 못 쓰면 memMode 로 */ }
  window.dispatchEvent(new Event(MODE_EVENT));
};
const subscribeMode = (cb: () => void) => {
  window.addEventListener("storage", cb);
  window.addEventListener(MODE_EVENT, cb);
  return () => { window.removeEventListener("storage", cb); window.removeEventListener(MODE_EVENT, cb); };
};

/** 같은 구매 건 · 같은 품목 줄 */
interface StockGroup {
  key: string;
  rows: StockRow[];
}
/** 같은 제품(표시 이름) — subs = 입고 건별 묶음 */
interface ProductGroup {
  key: string;
  rows: StockRow[];
  subs: StockGroup[];
}

/** S/N 이 없는 대 — [S/N 없는 품목]이거나 두 줄 모두 비어 있음 */
const noSn = (r: StockRow) => r.no_sn || (!r.maker_sn.trim() && !r.und_sn.trim());

/** 상태별 개수 — 목록 순서(states)대로 */
function countByStatus(rows: StockRow[], states: string[]): [string, number][] {
  return states.map((s) => [s, rows.filter((r) => r.status === s).length] as [string, number]).filter(([, n]) => n > 0);
}

/** 기본 줄 — 입고 기록에 적은 줄(나눈 줄) 그대로 한 줄(판매용·자체 개발용 같게, 사용자 2026-11-19). 2대 이상이면 펼쳐서 한 대씩 */
function receiptGroups(rows: StockRow[]): StockGroup[] {
  const by = new Map<string, StockRow[]>();
  for (const r of rows) {
    const k = `${r.purchase_id}|${r.line_key}|p${r.part}`;
    by.set(k, [...(by.get(k) ?? []), r]);
  }
  return [...by.entries()].map(([key, rs]) => ({ key, rows: [...rs].sort((a, b) => a.unit_index - b.unit_index) }));
}

/** 여러 줄의 한 칸 — 모두 같으면 그 값, 다르면 '여러 …' */
function oneOf(rows: StockRow[], pick: (r: StockRow) => string, many: string): React.ReactNode {
  const v = [...new Set(rows.map(pick))];
  return v.length > 1 ? <Muted>{many}</Muted> : v[0] || <Muted>-</Muted>;
}

export function StockTab({ onOpenPurchase }: { onOpenPurchase?: (purchaseId: number) => void }) {
  const [purpose, setPurpose] = useState<StockPurpose>("sale");
  const mode = useSyncExternalStore(subscribeMode, readMode, () => "list" as GroupMode);
  const [data, setData] = useState<StockListData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [cat, setCat] = useState("");
  const [name, setName] = useState("");
  const [maker, setMaker] = useState("");
  const [status, setStatus] = useState("");
  const [region, setRegion] = useState<"" | StockRow["region"]>("");   // 국내·해외(구매 건 구분)
  const [editing, setEditing] = useState<string | null>(null);   // 상태 바꾸는 줄('u:id' 한 대 · 'g:key' 입고 묶음 · 'p:key' 제품)
  const [openId, setOpenId] = useState<number | null>(null);     // 변경 이력 펼친 줄
  const [openGroups, setOpenGroups] = useState<Set<string>>(new Set());
  const [viewer, setViewer] = useState<{ items: PhotoItem[]; index: number } | null>(null);
  // [제품 등록](사용자 2026-11-19) — pid null = 새 등록, 아니면 그 직접 등록 고치기
  const [direct, setDirect] = useState<{ purpose: StockPurpose; pid: number | null } | null>(null);
  const startDirect = async () => {
    const c = await askChoice({ title: "어떤 제품을 등록할까요?", message: "구매 건 없이 이미 있는 제품을 재고에 넣습니다. 고른 쪽에 따라 적는 칸이 다릅니다.",
                                confirmLabel: "판매용", extraLabel: "보유 후 자체 개발용", cancelLabel: "취소" });
    if (c !== "cancel") setDirect({ purpose: c === "confirm" ? "sale" : "dev", pid: null });
  };
  /** 구매 건 링크 — 직접 등록 줄은 등록 고치기 창, 그 밖은 구매 건 */
  const openSource = (r: StockRow) => (r.direct ? setDirect({ purpose: r.purpose, pid: r.purchase_id }) : onOpenPurchase?.(r.purchase_id));

  const changeMode = (m: GroupMode) => {
    writeMode(m);
    setOpenGroups(new Set());
    setEditing(null);
  };

  const load = useCallback(async (p: StockPurpose) => {
    try {
      setData(await stockRequest<StockListData>(`?purpose=${p}`));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "재고를 불러오지 못했습니다.");
    }
  }, []);
  useEffect(() => {
    let alive = true;
    stockRequest<StockListData>(`?purpose=${purpose}`)
      .then((d) => { if (alive) { setData(d); setError(null); } })
      .catch((e) => { if (alive) setError(e instanceof Error ? e.message : "재고를 불러오지 못했습니다."); });
    return () => { alive = false; };
  }, [purpose]);

  const dev = purpose === "dev";
  const all = useMemo(() => (data?.rows ?? []).filter((r) => r.purpose === purpose), [data, purpose]);
  const names = useMemo(() => [...new Set(all.map(stockLabel))].sort((a, b) => a.localeCompare(b, "ko")), [all]);
  const makers = useMemo(() => [...new Set(all.map((r) => r.maker))].sort((a, b) => a.localeCompare(b, "ko")), [all]);
  const match = useCallback((r: StockRow) => {
    const k = q.trim().toLowerCase();
    return (!region || r.region === region) && (!cat || (cat === "-" ? !r.category : r.category === cat))
      && (!name || stockLabel(r) === name) && (!status || r.status === status) && (!maker || r.maker === maker)
      && (!k || [r.name, r.display_name, r.maker, r.maker_sn, r.und_sn, r.customer, r.owner, r.sw_owner, r.supplier, r.po_no ?? "", r.status_note,
                 r.category].some((v) => v.toLowerCase().includes(k)));
  }, [q, cat, name, status, region, maker]);
  // 보이는 묶음 — 그 중 하나라도 조건에 맞으면 묶음째(수량·상태 개수는 묶음 전체)
  const items = useMemo<(StockGroup | ProductGroup)[]>(() => {
    if (mode === "list") return receiptGroups(all).filter((g) => g.rows.some(match));
    const by = new Map<string, StockRow[]>();
    for (const r of all) by.set(productKey(r), [...(by.get(productKey(r)) ?? []), r]);
    return [...by.entries()].filter(([, rs]) => rs.some(match))
      .map(([key, rs]) => ({ key: `p${key}`, rows: rs, subs: receiptGroups(rs) }));
  }, [all, match, mode]);

  const switchTo = (p: StockPurpose) => {
    if (p === purpose) return;
    setData(null); setPurpose(p); setName(""); setEditing(null); setOpenId(null); setOpenGroups(new Set());
  };
  const save = async (ids: number[], patch: StockPatch) => {
    try {
      if (ids.length === 1) await stockRequest(`/${ids[0]}`, patch);
      else await stockRequest("/bulk", { ...patch, ids });
      await load(purpose);
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : "저장하지 못했습니다.");
      return false;
    }
  };
  /** 고객사·담당자 고치기(사용자 2026-11-19) — 이 줄의 대 전부. 그 입고 기록을 다시 저장하면 입고 기록 값으로 돌아간다 */
  const editText = async (rows: StockRow[], field: "customer" | "owner" | "sw_owner") => {
    const first = rows[0];
    const label = field === "customer" ? "판매 고객사" : field === "sw_owner" ? "S/W 담당자" : first.purpose === "dev" ? "영업팀 담당자" : "우리 담당자";
    const same = [...new Set(rows.map((r) => r[field]))];
    const v = await askText({
      title: label, confirmLabel: "저장", cancelLabel: "취소", initial: same.length === 1 ? same[0] : "",
      placeholder: field === "customer" ? "예: LG CNS" : "예: Lucas",
      message: `${stockLabel(first)}${rows.length > 1 ? ` ${rows.length}개` : ""}의 ${label}을 적어 주세요(비우면 지움).`,
    });
    if (v !== null) await save(rows.map((r) => r.id), { [field]: v });
  };
  const [dateEdit, setDateEdit] = useState<string | null>(null);     // 입고일 고치는 칸(줄 열쇠)
  // [수정] 모드(사용자 2026-11-19) — 켰을 때만 칸을 고칠 수 있고, 고칠 수 있는 칸에 점선·✎ 이 보인다(상태 바꾸기는 늘 됨)
  const [edit, setEdit] = useState(false);
  const EDITABLE = "rounded border border-dashed border-accent/50 bg-accent/[0.04] px-1 hover:bg-accent/10";
  const pen = <span className="ml-0.5 text-[11px] text-accent" aria-hidden>✎</span>;
  /** 고칠 수 있는 글 칸 — 누르면 입력 창 */
  const textCell = (rows: StockRow[], field: "customer" | "owner" | "sw_owner", many: string, cls = "") => (
    <td className={cn("px-2 py-2", cls)} onClick={(e) => edit && e.stopPropagation()}>
      {edit ? (
        <button type="button" onClick={() => void editText(rows, field)} title="눌러서 고치기"
                className={cn("inline-flex max-w-full items-center text-left", EDITABLE)}>
          <span className="truncate">{oneOf(rows, (r) => r[field], many)}</span>{pen}
        </button>
      ) : <span className="block truncate">{oneOf(rows, (r) => r[field], many)}</span>}
    </td>
  );
  /** 입고일 칸 — 누르면 달력(사용자 2026-11-19). prefix = '최근' 같은 앞 글자 */
  const dateCell = (key: string, rows: StockRow[], prefix?: React.ReactNode) => {
    const days = [...new Set(rows.map((r) => r.received_on ?? ""))].sort();
    const shown = days.at(-1) || null;
    return (
      <td className="whitespace-nowrap px-2 py-2 tabular-nums" onClick={(e) => edit && e.stopPropagation()}>
        {!edit ? <>{prefix}{shortDate(shown)}</> : dateEdit === key ? (
          <input type="date" autoFocus defaultValue={days.length === 1 ? days[0] : ""} aria-label="입고일"
                 onFocus={(e) => { try { e.currentTarget.showPicker?.(); } catch { /* 달력을 못 띄우는 브라우저 */ } }}
                 onBlur={() => setDateEdit(null)}
                 onChange={async (e) => { const v = e.target.value; if (v) { setDateEdit(null); await save(rows.map((r) => r.id), { received_on: v }); } }}
                 className="h-8 rounded-md border border-accent bg-surface px-1 text-[12.5px]" />
        ) : (
          <button type="button" onClick={() => setDateEdit(key)} title="눌러서 입고일 고치기"
                  className={cn("inline-flex items-center gap-1", EDITABLE)}>
            {prefix}{shortDate(shown)}
            <svg viewBox="0 0 24 24" className="h-3.5 w-3.5 text-accent" fill="none" stroke="currentColor" strokeWidth={1.8} aria-hidden>
              <rect x="3.5" y="5" width="17" height="15" rx="2" /><path d="M3.5 9.5h17M8 3v4M16 3v4" />
            </svg>
          </button>
        )}
      </td>
    );
  };
  /** 표시 이름 고치기 — 이 줄(묶음)의 대 전부, 발주서 이름이 같은 다른 대도 있으면 함께 바꿀지 묻는다 */
  const editName = async (rows: StockRow[]) => {
    const first = rows[0];
    const v = await askText({
      title: "재고 표시 이름",
      message: `재고 목록에 보일 이름입니다. 발주서·입고 기록의 품목명(${first.name})은 그대로 남고, 비우면 품목명으로 돌아갑니다.`,
      confirmLabel: "저장", cancelLabel: "취소", initial: stockLabel(first), placeholder: first.name,
    });
    if (v === null) return;
    const mine = new Set(rows.map((r) => r.id));
    const same = all.filter((r) => r.name === first.name && !mine.has(r.id));
    let ids = rows.map((r) => r.id);
    if (same.length) {
      const c = await askChoice({
        title: "어디까지 바꿀까요?",
        message: `발주서 품목명이 '${first.name}'인 재고가 다른 입고 건에도 ${same.length}개 있습니다.`,
        confirmLabel: `이 ${rows.length}개만`, extraLabel: `같은 품목명 ${rows.length + same.length}개 모두`, cancelLabel: "취소",
      });
      if (c === "cancel") return;
      if (c === "extra") ids = [...ids, ...same.map((r) => r.id)];
    }
    await save(ids, { display_name: v });
  };
  /** 제조사 고치기 — 이 줄(묶음) 전부, 발주서 품목명이 같은 다른 대도 있으면 함께 바꿀지 묻는다. 비우면 구매 공급사로 */
  const editMaker = async (rows: StockRow[]) => {
    const first = rows[0];
    const v = await askText({
      title: "제조사",
      message: `${stockLabel(first)}의 제조사를 적어 주세요. 처음엔 구매 공급사(${first.supplier})로 들어가 있고, 비우면 공급사로 돌아갑니다.`,
      confirmLabel: "저장", cancelLabel: "취소", initial: first.maker, placeholder: first.supplier,
    });
    if (v === null) return;
    const mine = new Set(rows.map((r) => r.id));
    const same = all.filter((r) => r.name === first.name && !mine.has(r.id));
    let ids = rows.map((r) => r.id);
    if (same.length) {
      const c = await askChoice({
        title: "어디까지 바꿀까요?",
        message: `발주서 품목명이 '${first.name}'인 재고가 다른 입고 건에도 ${same.length}개 있습니다.`,
        confirmLabel: `이 ${rows.length}개만`, extraLabel: `같은 품목명 ${rows.length + same.length}개 모두`, cancelLabel: "취소",
      });
      if (c === "cancel") return;
      if (c === "extra") ids = [...ids, ...same.map((r) => r.id)];
    }
    await save(ids, { maker: v });
  };
  const toggleGroup = (key: string) => setOpenGroups((s) => { const n = new Set(s); if (n.has(key)) n.delete(key); else n.add(key); return n; });

  const heads = dev
    ? ["구분", "제조사", "제품명", "사진", "제조사 S/N · UND S/N", "S/W 담당자", "영업팀 담당자", "입고일", "상태", "최근 변경"]
    : ["구분", "제조사", "제품명", "사진", "수량 · S/N", "판매 고객사", "우리 담당자", "입고일", "상태", "최근 변경"];
  const filtered = !!(q.trim() || cat || name || status || region || maker);
  const states = data?.states ?? [];
  const categories = data?.categories ?? [];

  const categoryCell = (rows: StockRow[], label: string) => {
    const cats = [...new Set(rows.map((r) => r.category))];
    const value = cats.length === 1 ? cats[0] : "__mixed";
    return (
      <td className="px-2 py-2" onClick={(e) => edit && e.stopPropagation()}>
        {!edit ? <span className={cn("text-[12.5px]", !value && "text-foreground-subtle")}>{value === "__mixed" ? "여러 구분" : value || "-"}</span> : (
        <select value={value} aria-label={`${label} 구분`}
                onChange={(e) => { if (e.target.value !== "__mixed") void save(rows.map((r) => r.id), { category: e.target.value }); }}
                className={cn(SELECT, "w-[84px] border-dashed border-accent/50 bg-accent/[0.04]")}>
          {value === "__mixed" && <option value="__mixed">여러 구분</option>}
          <option value="">고르기</option>
          {categories.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>)}
      </td>
    );
  };
  /** 사진 칸(사용자 2026-11-19 — 제품명 오른쪽, 알림 칸을 빼고 넓힘). 한 대 줄 = 최대 3장 나란히 + 나머지 +N(품목 사진 + 그 대 S/N 사진),
   *  여러 대 묶음 줄 = 사진 없이 카메라 그림 + 장수만(어느 대 사진인지 헷갈리지 않게). 누르면 그 줄의 사진을 넘겨 보는 창 */
  const photoCell = (rows: StockRow[]) => {
    const items = photoItems(rows, true, true);
    const many = rows.length > 1;
    if (edit) return photoEditCell(rows, items, many);
    return (
      <td className="min-w-[130px] px-2 py-2" onClick={(e) => e.stopPropagation()}>
        {items.length && many ? (
          <button type="button" onClick={() => setViewer({ items, index: 0 })} title={`사진 ${items.length}장 보기`}
                  className="inline-flex h-8 items-center gap-1 whitespace-nowrap rounded-md border border-border px-2 text-[12px] font-semibold tabular-nums text-foreground-muted hover:border-accent hover:text-accent">
            <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M4 8h3l1.5-2h7L17 8h3a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1z" /><circle cx="12" cy="13" r="3.5" />
            </svg>
            {items.length}
          </button>
        ) : items.length ? (
          <span className="flex items-center gap-1">
            {items.slice(0, PHOTO_SHOW).map((it, k) => (
              <button key={it.url} type="button" onClick={() => setViewer({ items, index: k })} title={`${PHOTO_KIND[it.kind].label} — 크게 보기`}
                      className="relative block h-10 w-10 shrink-0 rounded-md ring-1 ring-border hover:ring-2 hover:ring-accent">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={it.url} alt={PHOTO_KIND[it.kind].label} className="h-10 w-10 rounded-md object-cover" />
                <span className={cn("absolute bottom-0 left-0 rounded-tr-md rounded-bl-md px-1 text-[9px] font-semibold leading-tight", PHOTO_KIND[it.kind].tone)}>
                  {it.kind === "item" ? "품목" : it.kind === "sn" ? "S/N" : "낱개"}</span>
              </button>
            ))}
            {items.length > PHOTO_SHOW && (
              <button type="button" onClick={() => setViewer({ items, index: PHOTO_SHOW })} title={`사진 ${items.length}장 모두 보기`}
                      className="h-10 shrink-0 rounded-md border border-border px-1.5 text-[12px] font-semibold tabular-nums text-foreground-muted hover:border-accent hover:text-accent">
                +{items.length - PHOTO_SHOW}</button>
            )}
          </span>
        ) : <Muted>-</Muted>}
      </td>
    );
  };
  /** 수정 모드 사진 칸 — 사진마다 ×(빼기), [+ 사진](묶음 줄·판매용) · [+ 품목] [+ S/N](자체 개발용 한 대). 입고 기록에 저장 */
  const [photoBusy, setPhotoBusy] = useState<number | null>(null);
  const addPhotos = async (r: StockRow, scope: "line" | "unit", kind: "item" | "sn") => {
    setPhotoBusy(r.id);
    try {
      const ids = await pickPhotos({ upload: (f) => uploadPhoto(`/api/purchases/${r.purchase_id}/receipt-photos`, f),
                                     urlOf: (ph) => `/api/purchases/${r.purchase_id}/receipt-photos/${ph}` });
      if (ids.length) {
        await stockRequest(`/${r.id}/photos`, { add: ids, scope, kind });
        await load(purpose);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "사진을 올리지 못했습니다.");
    } finally {
      setPhotoBusy(null);
    }
  };
  const removePhoto = async (r: StockRow, it: PhotoItem) => {
    if (!(await askConfirm({ title: "이 사진을 뺄까요?", message: `${PHOTO_KIND[it.kind].label} — 입고 기록에서도 빠집니다.`, confirmLabel: "빼기", danger: true }))) return;
    try {
      await stockRequest(`/${r.id}/photos`, { remove: [it.url.split("/").pop() ?? ""] });
      await load(purpose);
    } catch (e) {
      setError(e instanceof Error ? e.message : "사진을 빼지 못했습니다.");
    }
  };
  const photoEditCell = (rows: StockRow[], items: PhotoItem[], many: boolean) => {
    const r = rows[0];
    const busy = photoBusy === r.id;
    const addBtn = (label: string, scope: "line" | "unit", kind: "item" | "sn") => (
      <button type="button" disabled={busy} onClick={() => void addPhotos(r, scope, kind)}
              className={cn("h-8 whitespace-nowrap text-[11.5px] font-semibold text-accent disabled:opacity-50", EDITABLE)}>
        {busy ? "올리는 중…" : `+ ${label}`}</button>
    );
    return (
      <td className="min-w-[130px] px-2 py-2" onClick={(e) => e.stopPropagation()}>
        <div className="flex flex-wrap items-center gap-1">
          {items.map((it, k) => (
            <span key={it.url} className="relative">
              <button type="button" onClick={() => setViewer({ items, index: k })} title={PHOTO_KIND[it.kind].label}
                      className="block h-9 w-9 rounded-md ring-1 ring-border">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={it.url} alt={PHOTO_KIND[it.kind].label} className="h-9 w-9 rounded-md object-cover" />
              </button>
              <button type="button" aria-label="사진 빼기" onClick={() => void removePhoto(r, it)}
                      className="absolute -right-1 -top-1 grid h-4 w-4 place-items-center rounded-full bg-black/70 text-[10px] text-white">×</button>
            </span>
          ))}
          {many || r.purpose === "sale" || r.no_sn ? addBtn("사진", "line", "item") : (
            <>{addBtn("품목", "unit", "item")}{addBtn("S/N", "unit", "sn")}</>
          )}
        </div>
      </td>
    );
  };
  /** 제조사 칸 — 고친 값이면 진하게, 공급사 그대로면 보통. ✎ 로 고치기 */
  // 제조사 — 자르지 않고 두 줄까지 줄바꿈(사용자 2026-11-19 — '[테스트] 입고 …'처럼 잘려 무슨 회사인지 안 보였음)
  const makerCell = (rows: StockRow[]) => (
    <td className="w-[130px] min-w-[110px] px-2 py-2">
      <div className="flex items-start gap-0.5">
        <span className={cn("line-clamp-2 break-keep", rows.some((r) => r.maker_custom) ? "font-semibold" : "text-foreground-muted")}>
          {oneOf(rows, (r) => r.maker, "여러 제조사")}
        </span>
        {edit && <button type="button" title="제조사 고치기(처음엔 구매 공급사)" aria-label="제조사 고치기"
                onClick={(e) => { e.stopPropagation(); void editMaker(rows); }}
                className={cn("shrink-0 text-[11.5px] text-accent", EDITABLE)}>✎</button>}
      </div>
    </td>
  );
  const renameBtn = (rows: StockRow[]) => edit && (
    <button type="button" title="재고 표시 이름 고치기(발주서 품목명은 그대로)" aria-label="표시 이름 고치기"
            onClick={(e) => { e.stopPropagation(); void editName(rows); }}
            className={cn("shrink-0 text-[11.5px] font-normal text-accent", EDITABLE)}>✎</button>
  );
  /** 제품명 칸 — 표시 이름(바꿨으면 아래에 발주서 품목명), 구매 건 링크 */
  const nameCell = (r: StockRow, rows: StockRow[], extra?: React.ReactNode) => (
    <td className="max-w-[180px] px-2 py-2">
      <div className="flex items-center gap-1 font-semibold">{extra}<span className="truncate">{stockLabel(r) || <Muted>-</Muted>}</span>{renameBtn(rows)}</div>
      {r.display_name && <div className="truncate text-[11px] text-foreground-subtle">발주서: {r.name}</div>}
      <button type="button" onClick={(e) => { e.stopPropagation(); openSource(r); }}
              className="truncate text-left text-[11.5px] text-foreground-muted hover:text-accent hover:underline" title="이 제품을 산 구매 건 열기">
        <span className={cn("mr-1 rounded px-1 text-[10.5px] font-semibold", r.region === "overseas" ? "bg-sky-500/12 text-sky-700 dark:text-sky-300" : "bg-foreground/8 text-foreground-muted")}>{REGION_LABEL[r.region]}</span>{r.direct ? "직접 등록" : <>{r.maker_custom ? `${r.supplier} · ` : ""}{r.po_no ?? "발주 전"}</>}
      </button>
    </td>
  );
  /** 최근 변경 — 언제 바뀌었는지만(사용자 2026-11-19). 무엇이 바뀌었는지는 줄을 눌러 변경 이력에서 */
  const lastCell = (rows: StockRow[]) => {
    const at = rows.map((x) => x.last?.at ?? "").sort().at(-1);
    return (
      <td className="px-2 py-2 text-[12px] tabular-nums text-foreground-muted">{at ? stamp(at) : <Muted>-</Muted>}</td>
    );
  };
  const statusChip = (s: string, n?: number) => (
    <span key={s} className={cn("whitespace-nowrap rounded-full px-2.5 py-0.5 text-[12px] font-semibold", STATUS_TONE[s] ?? STATUS_TONE["기타"])}>
      {s}{n !== undefined && <span className="ml-1 tabular-nums">{n}</span>}
    </span>
  );
  /** 여러 대 상태 칸 — 상태별 개수, 누르면 '몇 개를 → 상태'로 */
  const countStatusCell = (rows: StockRow[], ek: string) => {
    const notes = [...new Set(rows.map((r) => r.status_note).filter(Boolean))];
    return (
      <td className="min-w-[100px] max-w-[260px] px-2 py-2" onClick={(e) => e.stopPropagation()}>
        {editing === ek ? (
          <CountStatusEditor rows={rows} states={states} onCancel={() => setEditing(null)}
                             onSave={async (ids, st, note) => {
                               // 메모를 비우면 원래 메모는 그대로(여러 개를 바꿀 때 다른 메모가 지워지지 않게)
                               if (await save(ids, note ? { status: st, status_note: note } : { status: st })) setEditing(null);
                             }} />
        ) : (
          <button type="button" onClick={() => setEditing(ek)} title="눌러서 개수로 상태 바꾸기" className="flex flex-wrap items-center gap-1 text-left">
            {countByStatus(rows, states).map(([s, n]) => statusChip(s, n))}
            <span className="text-[11px] text-foreground-subtle">▾</span>
            {notes.length > 0 && <span className="block w-full whitespace-pre-wrap text-[11.5px] text-foreground-muted">{notes.join(" · ")}</span>}
          </button>
        )}
      </td>
    );
  };
  const indent = (depth: number) => (depth === 2 ? "pl-14" : depth === 1 ? "pl-8" : "");

  /** 한 대 줄 — depth 0 = 맨 위, 1·2 = 묶음을 펼친 안쪽 줄(이름·고객사 등은 위 줄에 있으므로 줄여 보인다) */
  const unitRow = (r: StockRow, depth = 0) => {
    const ek = `u:${r.id}`;
    const sub = depth > 0;
    return (
      <Fragment key={r.id}>
        <tr onClick={() => setOpenId(openId === r.id ? null : r.id)}
            className={cn("cursor-pointer border-b border-border/70 align-top hover:bg-accent/5", openId === r.id && "bg-accent/8",
                          sub && "bg-foreground/[0.015]")}>
          {categoryCell([r], stockLabel(r))}
          {makerCell([r])}
          {sub ? (
            <td className={cn("px-2 py-2 text-[12.5px] text-foreground-muted", indent(depth))}>
              {stockLabel(r)} <span className="tabular-nums">#{r.unit_index + 1}</span>
              {mode === "product" && <span className="ml-1.5 text-[11.5px]">· {shortDate(r.received_on)}{r.po_no ? ` · ${r.po_no}` : ""}</span>}
            </td>
          ) : nameCell(r, [r])}
          {photoCell([r])}
          {dev ? (
            <>
              <td className="whitespace-nowrap px-2 py-2 font-mono text-[12px] leading-5"><SnLines r={r} /></td>
              {textCell([r], "sw_owner", "", "whitespace-nowrap")}
              {textCell([r], "owner", "", "whitespace-nowrap")}
            </>
          ) : (
            <>
              <td className="whitespace-nowrap px-2 py-2 font-mono text-[12px] leading-5">{r.no_sn ? (sub ? "" : <span className="font-sans tabular-nums">1</span>) : <SnLines r={r} />}</td>
              {textCell([r], "customer", "", "max-w-[200px]")}
              {textCell([r], "owner", "", "whitespace-nowrap")}
            </>
          )}
          {dateCell(`u${r.id}`, [r])}
          <td className="min-w-[100px] max-w-[220px] px-2 py-2" onClick={(e) => e.stopPropagation()}>
            {editing === ek ? (
              <StatusEditor row={r} states={states} onCancel={() => setEditing(null)}
                            onSave={async (st, note) => { if (await save([r.id], { status: st, status_note: note })) setEditing(null); }} />
            ) : (
              <button type="button" onClick={() => setEditing(ek)} title="눌러서 상태 바꾸기·메모" className="text-left">
                {statusChip(r.status)}<span className="ml-0.5 text-[11px] text-foreground-subtle">▾</span>
                {r.status_note && <span className="mt-0.5 block whitespace-pre-wrap text-[11.5px] text-foreground-muted">{r.status_note}</span>}
              </button>
            )}
          </td>
          {lastCell([r])}
        </tr>
        {openId === r.id && (
          <tr className="border-b border-border/70 bg-foreground/[0.02]">
            <td colSpan={heads.length} className="px-4 py-3"><StockHistory id={r.id} /></td>
          </tr>
        )}
      </Fragment>
    );
  };

  /** 입고 건별 묶음 줄 — 같은 구매 건·같은 품목 줄 여러 대. nested = 제품별 줄을 펼친 안쪽 */
  const groupRow = (g: StockGroup, nested = false) => {
    const first = g.rows[0];
    const open = openGroups.has(g.key);
    const caret = <span className="w-3 text-[11px] text-foreground-subtle">{open ? "▾" : "▸"}</span>;
    return (
      <Fragment key={g.key}>
        <tr onClick={() => toggleGroup(g.key)} aria-expanded={open}
            className={cn("cursor-pointer border-b border-border/70 align-top hover:bg-accent/5", open && "bg-accent/[0.04]",
                          nested && "bg-foreground/[0.015]")}>
          {categoryCell(g.rows, stockLabel(first))}
          {makerCell(g.rows)}
          {nested ? (
            <td className="max-w-[180px] px-2 py-2 pl-8 text-[12.5px]">
              <div className="flex items-center gap-1">{caret}<span>입고 {shortDate(first.received_on)}</span></div>
              <button type="button" onClick={(e) => { e.stopPropagation(); openSource(first); }}
                      className="truncate text-left text-[11.5px] text-foreground-muted hover:text-accent hover:underline" title="이 제품을 산 구매 건 열기">
                <span className={cn("mr-1 rounded px-1 text-[10.5px] font-semibold", first.region === "overseas" ? "bg-sky-500/12 text-sky-700 dark:text-sky-300" : "bg-foreground/8 text-foreground-muted")}>{REGION_LABEL[first.region]}</span>{first.direct ? "직접 등록" : <>{first.maker_custom ? `${first.supplier} · ` : ""}{first.po_no ?? "발주 전"}</>}
              </button>
            </td>
          ) : nameCell(first, g.rows, caret)}
          {photoCell(g.rows)}
          {dev ? (
            <>
              <td className="whitespace-nowrap px-2 py-2 text-[12.5px]">
                <b className="tabular-nums">{g.rows.length}개</b>
                <span className="ml-1 text-foreground-muted">
                  {first.no_sn ? "S/N 없는 품목" : `S/N ${g.rows.filter((r) => !noSn(r)).length}/${g.rows.length}`}</span>
              </td>
              {textCell(g.rows, "sw_owner", "여러 명", "whitespace-nowrap")}
              {textCell(g.rows, "owner", "여러 명", "whitespace-nowrap")}
            </>
          ) : (
            <>
              <td className="whitespace-nowrap px-2 py-2 text-[12.5px]">
                <b className="tabular-nums">{g.rows.length}개</b>
                {!first.no_sn && <span className="ml-1 text-foreground-muted">S/N {g.rows.filter((r) => !noSn(r)).length}/{g.rows.length}</span>}
              </td>
              {textCell(g.rows, "customer", "여러 고객사", "max-w-[200px]")}
              {textCell(g.rows, "owner", "여러 명", "whitespace-nowrap")}
            </>
          )}
          {dateCell(`g${g.key}`, g.rows)}
          {countStatusCell(g.rows, `g:${g.key}`)}
          {lastCell(g.rows)}
        </tr>
        {open && g.rows.map((r) => unitRow(r, nested ? 2 : 1))}
      </Fragment>
    );
  };

  /** 제품별 줄 — 같은 제품의 모든 입고 건. 펼치면 입고 건별 줄 */
  const productRow = (p: ProductGroup) => {
    const first = p.rows[0];
    const open = openGroups.has(p.key);
    const latest = p.rows.map((r) => r.received_on ?? "").sort().at(-1) || null;
    const withSn = p.rows.filter((r) => !noSn(r)).length;
    return (
      <Fragment key={p.key}>
        <tr onClick={() => toggleGroup(p.key)} aria-expanded={open}
            className={cn("cursor-pointer border-b border-border/70 align-top hover:bg-accent/5", open && "bg-accent/[0.04]")}>
          {categoryCell(p.rows, stockLabel(first))}
          {makerCell(p.rows)}
          <td className="max-w-[180px] px-2 py-2">
            <div className="flex items-center gap-1 font-semibold">
              <span className="w-3 text-[11px] text-foreground-subtle">{open ? "▾" : "▸"}</span>
              <span className="truncate">{stockLabel(first)}</span>{renameBtn(p.rows)}
            </div>
            <div className="text-[11.5px] text-foreground-muted">입고 {new Set(p.rows.map((r) => `${r.purchase_id}|${r.line_key}`)).size}건
              {new Set(p.rows.map((r) => r.name)).size > 1 && " · 발주서 품목명 여러 개"}</div>
          </td>
          {photoCell(p.rows)}
          {dev ? (
            <>
              <td className="whitespace-nowrap px-2 py-2 text-[12.5px]">
                <b className="tabular-nums">{p.rows.length}대</b>
                <span className="ml-1 text-foreground-muted">S/N {withSn}대{withSn < p.rows.length ? ` · 없음 ${p.rows.length - withSn}` : ""}</span>
              </td>
              <td className="whitespace-nowrap px-2 py-2">{oneOf(p.rows, (r) => r.sw_owner, "여러 명")}</td>
              <td className="whitespace-nowrap px-2 py-2">{oneOf(p.rows, (r) => r.owner, "여러 명")}</td>
            </>
          ) : (
            <>
              <td className="px-2 py-2 font-semibold tabular-nums">{p.rows.length}</td>
              <td className="max-w-[200px] truncate px-2 py-2">{oneOf(p.rows, (r) => r.customer, "여러 고객사")}</td>
              <td className="whitespace-nowrap px-2 py-2">{oneOf(p.rows, (r) => r.owner, "여러 명")}</td>
            </>
          )}
          <td className="whitespace-nowrap px-2 py-2 tabular-nums">
            {p.subs.length > 1 && <span className="mr-1 text-[11px] text-foreground-subtle">최근</span>}{shortDate(latest)}
          </td>
          {countStatusCell(p.rows, `p:${p.key}`)}
          {lastCell(p.rows)}
        </tr>
        {open && p.subs.map((s) => (s.rows.length > 1 ? groupRow(s, true) : unitRow(s.rows[0], 1)))}
      </Fragment>
    );
  };

  /** 맨 위 한 줄 — 1대 = 한 대 줄, 입고 건 하나뿐인 제품 = 입고 묶음 줄, 그 밖 = 제품 줄 */
  const topRow = (it: StockGroup | ProductGroup) => {
    if (it.rows.length === 1) return unitRow(it.rows[0]);
    if (!("subs" in it)) return groupRow(it);
    if (it.subs.length === 1) return it.subs[0].rows.length > 1 ? groupRow(it.subs[0]) : unitRow(it.subs[0].rows[0]);
    return productRow(it);
  };

  return (
    <section>
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <div role="tablist" aria-label="재고 구분" className="inline-flex rounded-xl border border-border p-1 text-[13px]">
          {(["sale", "dev"] as const).map((p) => (
            <button key={p} type="button" role="tab" aria-selected={purpose === p} onClick={() => switchTo(p)}
                    className={cn("rounded-lg px-4 py-1.5 font-semibold", purpose === p ? "bg-accent text-white" : "text-foreground-muted hover:text-foreground")}>
              {STOCK_PURPOSE_LABEL[p]}
            </button>
          ))}
        </div>
        <div role="group" aria-label="국내·해외" className="inline-flex items-center rounded-lg border border-border p-0.5 text-[12.5px]">
          {([["", "전체"], ["domestic", "국내"], ["overseas", "해외"]] as const).map(([k, label]) => (
            <button key={label} type="button" aria-pressed={region === k} onClick={() => setRegion(k)}
                    className={cn("rounded-md px-2.5 py-1 font-semibold", region === k ? "bg-foreground/10 text-foreground" : "text-foreground-muted hover:text-foreground")}>
              {label}
            </button>
          ))}
        </div>
        {/* 엑셀 내려받기·적용(사용자 2026-11-20) */}
        <span className="ml-auto flex flex-wrap items-center justify-end gap-2"><StockExcelButtons onApplied={() => void load(purpose)} /></span>
        <button type="button" aria-pressed={edit} onClick={() => { setEdit((v) => !v); setDateEdit(null); }}
                className={cn("h-9 rounded-lg border px-3.5 text-[12.5px] font-semibold",
                  edit ? "border-accent bg-accent text-white" : "border-border text-foreground hover:border-accent hover:text-accent")}>
          {edit ? "수정 마치기" : "✎ 수정"}</button>
        <button type="button" onClick={() => void startDirect()}
                className="h-9 rounded-lg bg-accent px-3.5 text-[12.5px] font-semibold text-white hover:opacity-90">+ 제품 등록</button>
        {/* [제품별 묶기] — 켜면 같은 제품의 총수량, 끄면(기본) 한 대씩 */}
        <button type="button" aria-pressed={mode === "product"} onClick={() => changeMode(mode === "product" ? "list" : "product")}
                title="같은 제품을 한 줄로 묶어 총수량 보기"
                className={cn("inline-flex h-9 items-center gap-1.5 rounded-lg border px-3 text-[12.5px] font-semibold",
                  mode === "product" ? "border-accent bg-accent/10 text-accent" : "border-border text-foreground-muted hover:text-foreground")}>
          <span className={cn("grid h-4 w-4 place-items-center rounded border text-[10px]",
            mode === "product" ? "border-accent bg-accent text-white" : "border-border")}>{mode === "product" ? "✓" : ""}</span>
          제품별 묶기
        </button>
      </div>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <select value={cat} onChange={(e) => setCat(e.target.value)} aria-label="구분"
                className={cn(SELECT, cat ? "border-accent font-semibold text-accent" : "border-border text-foreground-muted")}>
          <option value="">구분: 전체</option>
          {categories.map((c) => <option key={c} value={c}>{c} ({all.filter((r) => r.category === c).length})</option>)}
          <option value="-">구분 미지정 ({all.filter((r) => !r.category).length})</option>
        </select>
        <select value={maker} onChange={(e) => setMaker(e.target.value)} aria-label="제조사"
                className={cn(SELECT, "max-w-[200px]", maker ? "border-accent font-semibold text-accent" : "border-border text-foreground-muted")}>
          <option value="">제조사: 전체</option>
          {makers.map((m) => <option key={m} value={m}>{m} ({all.filter((r) => r.maker === m).length})</option>)}
        </select>
        <select value={name} onChange={(e) => setName(e.target.value)} aria-label="제품명"
                className={cn(SELECT, "max-w-[240px]", name ? "border-accent font-semibold text-accent" : "border-border text-foreground-muted")}>
          <option value="">제품명: 전체</option>
          {names.map((n) => <option key={n} value={n}>{n} ({all.filter((r) => stockLabel(r) === n).length})</option>)}
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="상태"
                className={cn(SELECT, status ? "border-accent font-semibold text-accent" : "border-border text-foreground-muted")}>
          <option value="">상태: 전체</option>
          {states.map((s) => <option key={s} value={s}>{s} ({all.filter((r) => r.status === s).length})</option>)}
        </select>
        {filtered && (
          <button type="button" onClick={() => { setQ(""); setCat(""); setName(""); setStatus(""); setRegion(""); setMaker(""); }}
                  className="text-[12.5px] text-foreground-muted underline-offset-2 hover:underline">거르기 지우기</button>
        )}
        <input value={q} onChange={(e) => setQ(e.target.value)} aria-label="재고 검색"
               placeholder={dev ? "제품명·제조사 S/N·UND S/N·담당자 검색" : "제품명·고객사·담당자·발주번호 검색"}
               className="ml-auto h-9 w-72 rounded-lg border border-border bg-surface px-3 text-[13px] outline-none focus:border-accent" />
      </div>

      {edit && (
        <p className="mb-2 rounded-lg border border-dashed border-accent/50 bg-accent/[0.05] px-3 py-2 text-[12.5px] text-foreground">
          <b className="text-accent">수정 중</b> — 점선·✎ 표시된 칸(구분·제조사·제품명·사진·고객사·담당자·S/W 담당자·입고일)을 눌러 고칩니다.
          고객사·담당자·S/W 담당자·입고일은 그 입고 기록을 다시 저장하면 입고 기록 값으로 돌아갑니다. 다 고쳤으면 [수정 마치기].
        </p>
      )}
      {error && <p className="mb-2 text-[12.5px] text-red-600">{error}</p>}
      {!data ? (
        <p className="py-16 text-center text-[13px] text-foreground-subtle">재고를 불러오는 중…</p>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-border bg-surface">
          <table className="w-full min-w-[1080px] border-collapse text-[13px]">
            <thead>
              <tr className="border-b border-border bg-foreground/[0.03] text-left text-[12px] text-foreground-muted">
                {heads.map((h) => <th key={h} className="whitespace-nowrap px-2 py-2 font-semibold">{h}</th>)}
              </tr>
            </thead>
            <tbody>
              {items.map(topRow)}
              {items.length === 0 && (
                <tr><td colSpan={heads.length} className="px-3 py-12 text-center text-foreground-muted">
                  {all.length ? "조건에 맞는 제품이 없습니다."
                    : `${STOCK_PURPOSE_LABEL[purpose]} 재고가 없습니다. 제품 구매 건의 입고 단계에서 [${STOCK_PURPOSE_LABEL[purpose]} 입고 기록 작성]을 저장하면 여기에 들어옵니다.`}
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
      {viewer && <PhotoViewer items={viewer.items} start={viewer.index} onClose={() => setViewer(null)} />}
      {direct && <StockDirect purpose={direct.purpose} pid={direct.pid} admin={!!data?.me?.admin}
                              onClose={() => setDirect(null)} onDone={() => { setDirect(null); void load(purpose); }} />}
    </section>
  );
}

interface PhotoItem {
  url: string;
  /** 품목 사진 = 그 품목 줄 전체(박스 등) · S/N 사진 = 자체 개발용 그 대(명판·S/N) · 낱개 사진 = 판매용 그 대 */
  kind: "item" | "sn" | "unit";
  /** '26.11.19 · PO-…' / '#2 · 제조사 RB5-T002 · UND TT-UND-002' */
  detail: string;
}
const PHOTO_KIND: Record<PhotoItem["kind"], { label: string; tone: string }> = {
  item: { label: "품목 사진", tone: "bg-blue-600 text-white" },
  sn: { label: "S/N 사진", tone: "bg-emerald-600 text-white" },
  unit: { label: "낱개 사진", tone: "bg-violet-600 text-white" },
};

/** 여러 줄의 사진 — 품목 줄마다 품목 사진(한 번씩) 다음에 대마다 S/N 사진. 같은 사진은 한 번만 */
function photoItems(rows: StockRow[], line: boolean, unit: boolean): PhotoItem[] {
  const out: PhotoItem[] = [];
  const seen = new Set<string>();
  const url = (r: StockRow, ph: string) => `/api/purchases/${r.purchase_id}/receipt-photos/${ph}`;
  const push = (r: StockRow, ph: string, kind: PhotoItem["kind"], detail: string) => {
    const k = `${r.purchase_id}/${ph}`;
    if (seen.has(k)) return;
    seen.add(k);
    out.push({ url: url(r, ph), kind, detail });
  };
  if (line) {
    for (const r of rows) {
      for (const ph of r.line_photos) push(r, ph, "item", `${stockLabel(r)} · 입고 ${shortDate(r.received_on)}${r.po_no ? ` · ${r.po_no}` : ""}`);
    }
  }
  if (unit) {
    for (const r of rows) {
      const sn = [r.maker_sn && `제조사 ${r.maker_sn}`, r.und_sn && `UND ${r.und_sn}`].filter(Boolean).join(" · ");
      for (const ph of r.photos) push(r, ph, "sn", `${stockLabel(r)} #${r.unit_index + 1}${sn ? ` · ${sn}` : ""}`);
    }
  }
  return out;
}

/** 사진 보기 창 — 크게 한 장씩, ←/→ 로 넘기고 Esc·바깥 누름으로 닫는다. 원본은 새 창에서 */
function PhotoViewer({ items, start, onClose }: { items: PhotoItem[]; start: number; onClose: () => void }) {
  const [i, setI] = useState(start);
  const go = useCallback((d: number) => setI((x) => (x + d + items.length) % items.length), [items.length]);
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.stopPropagation(); onClose(); }
      else if (e.key === "ArrowLeft") go(-1);
      else if (e.key === "ArrowRight") go(1);
    };
    window.addEventListener("keydown", on, true);
    return () => window.removeEventListener("keydown", on, true);
  }, [go, onClose]);
  const cur = items[i];
  return (
    <div role="dialog" aria-modal="true" aria-label="입고 사진" onClick={onClose}
         className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6">
      <div onClick={(e) => e.stopPropagation()} className="flex max-h-full w-full max-w-4xl flex-col gap-2 rounded-xl bg-surface p-3 shadow-xl">
        <div className="flex items-center gap-2 text-[13px]">
          <span className={cn("shrink-0 rounded-md px-2 py-0.5 text-[13px] font-bold", PHOTO_KIND[cur.kind].tone)}>{PHOTO_KIND[cur.kind].label}</span>
          <b className="min-w-0 truncate">{cur.detail}</b>
          <span className="shrink-0 tabular-nums text-foreground-muted">{i + 1} / {items.length}</span>
          <a href={cur.url} target="_blank" rel="noreferrer" className="ml-auto text-[12px] text-accent hover:underline">원본 열기</a>
          <button type="button" onClick={onClose} aria-label="닫기" className="rounded px-2 py-0.5 text-foreground-muted hover:bg-foreground/[0.06]">✕</button>
        </div>
        <div className="relative flex min-h-[200px] items-center justify-center">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={cur.url} alt={`${PHOTO_KIND[cur.kind].label} ${cur.detail}`} className="max-h-[70vh] max-w-full rounded object-contain" />
          {items.length > 1 && (
            <>
              <button type="button" onClick={() => go(-1)} aria-label="이전 사진"
                      className="absolute left-1 top-1/2 -translate-y-1/2 rounded-full bg-black/50 px-3 py-2 text-white hover:bg-black/70">‹</button>
              <button type="button" onClick={() => go(1)} aria-label="다음 사진"
                      className="absolute right-1 top-1/2 -translate-y-1/2 rounded-full bg-black/50 px-3 py-2 text-white hover:bg-black/70">›</button>
            </>
          )}
        </div>
        {items.length > 1 && (
          <div className="flex gap-1.5 overflow-x-auto pb-1">
            {items.map((it, k) => (
              <button key={it.url} type="button" onClick={() => setI(k)} aria-label={`${k + 1}번째 사진 · ${PHOTO_KIND[it.kind].label}`}
                      className={cn("flex shrink-0 flex-col items-center gap-0.5 rounded p-0.5 ring-2", k === i ? "ring-accent" : "ring-transparent hover:ring-border")}>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={it.url} alt="" className="h-12 w-12 rounded object-cover" />
                <span className={cn("rounded px-1 text-[10px] font-semibold", PHOTO_KIND[it.kind].tone)}>{it.kind === "item" ? "품목" : it.kind === "sn" ? "S/N" : "낱개"}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/** 한 대 상태 바꾸기 — 상태 고르기 + 메모(상태 아랫줄에 보임) */
function StatusEditor({ row, states, onSave, onCancel }: {
  row: StockRow; states: string[]; onSave: (status: string, note: string) => Promise<void>; onCancel: () => void;
}) {
  const [st, setSt] = useState(row.status);
  const [note, setNote] = useState(row.status_note);
  const [busy, setBusy] = useState(false);
  const changed = st !== row.status || note.trim() !== row.status_note;
  const rented = row.status === RENTED;
  return (
    <div className="flex w-[220px] flex-col gap-1.5" onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); onCancel(); } }}>
      <select value={st} onChange={(e) => setSt(e.target.value)} aria-label="상태" autoFocus disabled={rented} className={cn(SELECT, "border-border")}>
        {pickable(states, row.status).map((s) => <option key={s} value={s}>{s}</option>)}
      </select>
      {rented && <span className="text-[11.5px] text-foreground-subtle">영업 건 대여로 나간 제품 — 회수하면 자동으로 &lsquo;대기 중&rsquo;이 됩니다. 메모만 고칠 수 있습니다.</span>}
      <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} maxLength={500} placeholder="메모(선택) — 상태 아랫줄에 보입니다"
                className={cn(INPUT, "h-auto py-1.5 text-[12.5px]")} />
      <div className="flex justify-end gap-1">
        <button type="button" onClick={onCancel} className="rounded-md px-2 py-1 text-[12px] text-foreground-muted hover:bg-foreground/[0.06]">취소</button>
        <button type="button" disabled={!changed || busy}
                onClick={async () => { setBusy(true); try { await onSave(st, note.trim()); } finally { setBusy(false); } }}
                className="rounded-md bg-accent px-2.5 py-1 text-[12px] font-semibold text-white disabled:opacity-40">{busy ? "저장 중…" : "저장"}</button>
      </div>
    </div>
  );
}

/** 묶음 줄 개수로 상태 바꾸기 — '[대기 중] 100개 중 [30]개를 → [사용 중]' + 메모. 앞 번호부터 고른다 */
function CountStatusEditor({ rows, states, onSave, onCancel }: {
  rows: StockRow[]; states: string[]; onSave: (ids: number[], status: string, note: string) => Promise<void>; onCancel: () => void;
}) {
  const have = countByStatus(rows, pickable(states));
  const [from, setFrom] = useState(have[0]?.[0] ?? states[0]);
  const fromRows = rows.filter((r) => r.status === from);
  const [n, setN] = useState(fromRows.length);
  const [to, setTo] = useState(pickable(states).find((s) => s !== from) ?? states[0]);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const count = Math.max(0, Math.min(Math.floor(n) || 0, fromRows.length));
  const ok = count > 0 && (to !== from || note.trim() !== "");
  return (
    <div className="flex w-[260px] flex-col gap-1.5 text-[12.5px]" onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); onCancel(); } }}>
      <div className="flex items-center gap-1.5">
        <select value={from} aria-label="지금 상태" autoFocus className={cn(SELECT, "border-border")}
                onChange={(e) => { setFrom(e.target.value); setN(rows.filter((r) => r.status === e.target.value).length); }}>
          {have.map(([s, k]) => <option key={s} value={s}>{s} {k}개</option>)}
        </select>
        <span className="text-foreground-muted">중</span>
        <input type="number" min={1} max={fromRows.length} value={n} aria-label="바꿀 개수" onChange={(e) => setN(Number(e.target.value))}
               className={cn(SELECT, "w-16 border-border text-right tabular-nums")} />
        <span className="text-foreground-muted">개를</span>
      </div>
      <div className="flex items-center gap-1.5">
        <span className="text-foreground-muted">→</span>
        <select value={to} onChange={(e) => setTo(e.target.value)} aria-label="바꿀 상태" className={cn(SELECT, "border-border")}>
          {pickable(states).map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <span className="text-foreground-muted">으로</span>
      </div>
      <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} maxLength={500} placeholder="메모(선택) — 바꾸는 제품의 상태 아랫줄에 보입니다"
                className={cn(INPUT, "h-auto py-1.5 text-[12.5px]")} />
      <div className="flex items-center justify-end gap-1">
        <span className="mr-auto text-[11.5px] text-foreground-subtle">한 대씩은 줄을 펼쳐서</span>
        <button type="button" onClick={onCancel} className="rounded-md px-2 py-1 text-[12px] text-foreground-muted hover:bg-foreground/[0.06]">취소</button>
        <button type="button" disabled={!ok || busy}
                onClick={async () => { setBusy(true); try { await onSave(fromRows.slice(0, count).map((r) => r.id), to, note.trim()); } finally { setBusy(false); } }}
                className="rounded-md bg-accent px-2.5 py-1 text-[12px] font-semibold text-white disabled:opacity-40">{busy ? "저장 중…" : `${count}개 저장`}</button>
      </div>
    </div>
  );
}

function StockHistory({ id }: { id: number }) {
  const [items, setItems] = useState<StockHistoryEntry[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    stockRequest<{ history: StockHistoryEntry[] }>(`/${id}/history`)
      .then((d) => { if (alive) setItems(d.history); })
      .catch((e) => { if (alive) setErr(e instanceof Error ? e.message : "이력을 불러오지 못했습니다."); });
    return () => { alive = false; };
  }, [id]);
  if (err) return <p className="text-[12px] text-red-600">{err}</p>;
  if (!items) return <p className="text-[12px] text-foreground-subtle">변경 이력을 불러오는 중…</p>;
  return (
    <div>
      <p className="mb-1.5 text-[12px] font-semibold text-foreground-muted">변경 이력 {items.length}</p>
      <ul className="space-y-1 text-[12.5px]">
        {items.map((h) => (
          <li key={h.id} className="flex gap-3">
            <span className="w-[110px] shrink-0 tabular-nums text-foreground-subtle">{stamp(h.at)}</span>
            <span className="w-[100px] shrink-0 font-semibold">{h.label}</span>
            <span className="min-w-0 flex-1 text-foreground-muted">{stockDetailText(h.detail) || "-"}</span>
            <span className="shrink-0 text-foreground-subtle">{h.by ?? "입고 기록"}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
