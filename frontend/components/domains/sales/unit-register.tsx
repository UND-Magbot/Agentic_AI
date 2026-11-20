// 출하 품목의 S/N 등록(사용자 2026-10-19 → 2026-10-23 변경) — 출하 기록을 쓰며 수주 판의 품목 줄마다 [S/N 등록]을 눌러
// 한 개씩 확인하며 적는다. 견적서 카드에서 따로 등록하던 창(경고창·수주 진행 전 안내)은 없앴다.
// 빈 칸 = 미등록. S/N이 없는 품목은 '특주'처럼 적는다(숫자 없는 글 = S/N 없음).
// 여러 S/N을 줄바꿈으로 복사해 한 칸에 붙여 넣으면 그 칸부터 아래 칸으로 나눠 들어간다(모자라면 칸을 더 만든다).
"use client";

import { useState } from "react";
import { askConfirm } from "@/components/shared/ui/confirm-host";
import { Icon } from "@/components/shared/ui/icon";
import { SnPhotoCell, type SnPhotoApi, uploadPhoto } from "@/components/domains/sales/sn-photos";
import { ITEM_BTN, primaryBtn, secondaryBtn } from "@/components/domains/sales/proposal-actions";
import { HEAD_FIELD, ROW_ICON_BTN, ROW_LABEL } from "@/components/domains/sales/quote-styles";
import type { IssueUnits, ProductUnit, UnitSummary } from "@/lib/shared/sales-deals";
import { UNIT_STATES } from "@/lib/shared/unit-states";
import { cn } from "@/lib/shared/utils";

export type SnLine = IssueUnits["lines"][number];

const MAKERS = ["UND", "Dobot", "Realman", "Doosan", "Universal Robots"];

/** 이 출하에 묶을 수 있는 부품 — 아직 안 나갔거나, 고치는 출하에 묶여 있는 것 */
export function usableUnits(ln: SnLine, editIndex?: number): ProductUnit[] {
  return ln.units.filter((u) => u.shipment_index == null || u.shipment_index === editIndex);
}

/** 품목 줄 이름을 한 줄로('ATC_CORE\n- TCV1 MASTER' → 'ATC_CORE TCV1 MASTER') — 출하 줄 이름·짝 찾기에 같이 쓴다 */
export function flatName(name: string): string {
  return name.replace(/\s*\n\s*-?\s*/g, " ").trim();
}

/** 수주 판의 S/N 칸 값 */
export async function loadSnView(dealId: number, src: Pick<UnitSummary, "quote_id" | "revision">): Promise<IssueUnits | null> {
  const r = await fetch(`/api/sales-deals/${dealId}/units?quote_id=${src.quote_id}&revision=${src.revision}`, { cache: "no-store" });
  return r.ok ? ((await r.json()) as IssueUnits) : null;
}

/** 이미 남긴 출하 기록 하나에서 S/N 을 적을 줄과 개수 — 출하 줄을 품목 줄에 맞추고(줄 번호, 없으면 이름),
 *  맞는 줄이 없으면(세트로 묶은 출하·예전 기록) 품목 줄 전부를 앞선 출하로 안 나간 만큼. */
export function shipmentSnLines(view: IssueUnits, lines: { name: string; qty: number | null; line_no?: number }[], index: number) {
  const need = new Map<number, number>();
  for (const l of lines) {
    const ln = view.lines.find((x) => (l.line_no != null ? x.line_no === l.line_no : flatName(x.name) === flatName(l.name)));
    if (ln && (l.qty ?? 0) > 0) need.set(ln.line_no, (need.get(ln.line_no) ?? 0) + Math.ceil(l.qty ?? 0));
  }
  if (need.size === 0) {
    for (const ln of view.lines) {
      const elsewhere = ln.units.filter((u) => u.shipment_index != null && u.shipment_index !== index).length;
      if (ln.qty - elsewhere > 0) need.set(ln.line_no, ln.qty - elsewhere);
    }
  }
  return [...need].map(([no, n]) => ({ line: view.lines.find((x) => x.line_no === no)!, need: n }));
}

/** 품목 줄 오른쪽 [S/N 등록] 버튼 — 적은 개수/출하 수량, 다 적으면 초록 */
export function SnButton({ count, need, open, disabled, onClick, tall }: {
  count: number; need: number; open: boolean; disabled?: boolean; onClick: () => void;
  /** 입력칸과 같은 40px 높이(출하 작성 품목 줄) */
  tall?: boolean;
}) {
  const done = need > 0 && count >= need;
  return (
    <button type="button" onClick={onClick} disabled={disabled} aria-expanded={open}
            className={cn("inline-flex shrink-0 items-center gap-1 whitespace-nowrap border font-semibold disabled:opacity-40",
              tall ? "h-10 rounded-[10px] bg-surface-raised px-3 text-[12.5px]" : "rounded-lg px-2 py-1 text-[12px]",
              done ? "border-emerald-500/40 text-emerald-700 hover:bg-emerald-500/10 dark:text-emerald-300"
                : "border-red-500/40 text-red-700 hover:bg-red-500/10 dark:text-red-300")}>
      {done ? "✓ " : ""}S/N 등록 <span className="font-mono">{Math.min(count, need)}/{need}</span>
    </button>
  );
}

// S/N 두 줄(사용자 2026-11-18): serial = 제조사 S/N, und_serial = UND S/N, photos = S/N 사진(여러 장)
// item_photos = 낱개 품목 사진(사용자 2026-11-19 — 구매 '보유 후 자체 개발용 입고'와 같은 두 칸)
type Draft = { key: string; id?: number; part_name: string; serial: string; und_serial: string; photos: string[]; item_photos: string[]; maker: string;
               purpose: string; note: string; locked: boolean };

let seq = 0;
const blank = (ln: SnLine, purpose: string): Draft => ({ key: `n${++seq}`, part_name: ln.part, serial: "", und_serial: "", photos: [], item_photos: [],
                                                        maker: "UND", purpose,
                                                        note: "",
                                                        locked: false });
const fromUnit = (u: ProductUnit): Draft => ({ key: `u${u.id}`, id: u.id, part_name: u.part_name, serial: u.serial,
                                               und_serial: u.und_serial ?? "", photos: u.photos ?? [], item_photos: u.item_photos ?? [], maker: u.maker,
                                               purpose: u.purpose,
                                               note: u.note, locked: u.shipment_index != null });

/** 품목 줄 하나의 S/N 칸 — 출하 수량만큼. 저장하면 이 줄만 바뀐다(다른 줄 S/N은 그대로). */
export function SnPanel({ dealId, view, line, need, editIndex, bindIndex, bindExpect, scope = "이 출하", onSaved, onClose }: {
  dealId: number; view: IssueUnits; line: SnLine; need: number; editIndex?: number;
  /** 이미 남긴 출하 기록에 적을 때 — 저장하면 그 출하로 바로 묶인다(그 출하일부터 무상 기간) */
  bindIndex?: number;
  /** 그 출하 기록(화면에서 본 것) — 그사이 다른 사람이 출하를 지워 번호가 밀렸으면 서버가 막는다 */
  bindExpect?: unknown;
  /** 머리 문구의 '이 출하' 자리 — 대여 제품 목록에서는 '이 품목'(사용자 2026-11-10) */
  scope?: string;
  onSaved: (v: IssueUnits) => void; onClose: () => void;
}) {
  const purpose = view.biz === "rental" ? "R" : "S";      // 대여 건은 R(대여), 판매 건은 S(판매)(사용자 2026-10-22)
  // 앞선 출하로 이미 나간 부품 — 보기만(지울 수 없음)
  const earlier = line.units.filter((u) => u.shipment_index != null && u.shipment_index !== editIndex);
  // 남긴 출하에 적을 때는 그 출하에 묶인 것만 칸에 — 다른 출하 몫으로 적어 둔(안 나간) S/N 은 그대로 두고 보기만(검증 2026-10-26)
  const waiting = bindIndex != null ? line.units.filter((u) => u.shipment_index == null) : [];
  const [rows, setRows] = useState<Draft[]>(() => {
    const have = usableUnits(line, editIndex).filter((u) => !waiting.includes(u)).map(fromUnit);
    return [...have, ...Array.from({ length: Math.max(0, need - have.length) }, () => blank(line, purpose))];
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const filled = rows.filter((d) => d.serial.trim() || d.und_serial.trim()).length;

  // 우리 제품(제조사 UND)은 제조사 S/N 이 곧 UND S/N — 제조사 S/N 을 적으면 UND S/N 칸도 같은 번호로 따라간다(고칠 수 있음)
  const follow = (d: Draft, serial: string): Draft =>
    (d.maker.trim().toUpperCase() === "UND" && (!d.und_serial || d.und_serial === d.serial) ? { ...d, serial, und_serial: serial } : { ...d, serial });
  const setRow = (key: string, patch: Partial<Draft>) => setRows((r) => r.map((d) => (d.key !== key ? d
    : patch.serial !== undefined ? { ...follow(d, patch.serial), ...Object.fromEntries(Object.entries(patch).filter(([k]) => k !== "serial")) }
      : { ...d, ...patch })));
  const photoApi: SnPhotoApi = {
    upload: (f) => uploadPhoto(`/api/sales-deals/${dealId}/ship-photos`, f),
    urlOf: (id) => `/api/sales-deals/${dealId}/ship-photos/${id}`,
  };
  const addPhotos = (key: string, kind: "photos" | "item_photos", ids: string[]) => setRows((r) => r.map((d) =>
    d.key === key ? { ...d, [kind]: [...d[kind], ...ids.filter((x) => !d[kind].includes(x))] } : d));
  // [S/N 없는 품목](소모품 등, 사용자 2026-11-19) — 낱개 칸을 숨기고 'S/N 없음'으로 채운다(숫자 없는 글 = 시리얼 없음, 서버 규칙 그대로)
  const NO_SN = "S/N 없음";
  const isNoSn = (v: string) => !!v.trim() && !/\d/.test(v);
  // 처음엔 체크 안 함(S/N 있는 품목이 기본, 사용자 2026-11-19). 고칠 수 있는 칸이 있고 그 칸이 모두 'S/N 없음'으로 저장돼 있을 때만 체크 —
  // 예전엔 출하 [수정]에서 칸이 모두 잠겨(이미 등록) 고칠 칸이 0개면 every([]) = 참이라 체크된 채로 열렸다
  const [noSn, setNoSn] = useState(() => {
    const open = rows.filter((d) => !d.locked);
    return open.length > 0 && open.every((d) => isNoSn(d.serial));
  });
  const toggleNoSn = (on: boolean) => {
    setNoSn(on);
    setRows((r) => r.map((d) => (d.locked ? d : on ? { ...d, serial: NO_SN, und_serial: "" } : isNoSn(d.serial) ? { ...d, serial: "", und_serial: "" } : d)));
  };
  const photoN = rows.filter((d) => d.photos.length > 0 || d.item_photos.length > 0).length;

  /** 여러 줄을 붙여 넣으면 그 칸부터 아래로 하나씩(출하한 칸은 건너뛰고, 모자라면 칸을 더 만든다) */
  const pasteMany = (key: string, text: string): boolean => {
    const vals = text.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
    if (vals.length < 2) return false;
    setRows((r) => {
      const list = [...r];
      let i = list.findIndex((d) => d.key === key);
      for (const v of vals) {
        while (i < list.length && list[i].locked) i++;
        if (i >= list.length) list.push(blank(line, purpose));
        list[i] = follow(list[i], v);
        i++;
      }
      return list;
    });
    return true;
  };

  /** UND S/N 을 적으면 이미 등록된 제품(재고 입고·다른 건)의 제조사 S/N·제조사를 불러온다(사용자 2026-11-20). 제조사 S/N 을 이미 적었으면 그대로 */
  const lookupUnd = async (key: string, und: string) => {
    const v = und.trim();
    if (!v) return;
    try {
      const r = await fetch(`/api/sales-deals/units/search?q=${encodeURIComponent(v)}`, { cache: "no-store" });
      if (!r.ok) return;
      const d = (await r.json()) as { items?: ProductUnit[] };
      const hit = (d.items ?? []).find((u) => (u.und_serial ?? "").trim().toLowerCase() === v.toLowerCase() && !u.no_serial);
      if (!hit) return;
      setRows((rs) => rs.map((x) => (x.key !== key || x.locked || x.serial.trim() || x.und_serial.trim().toLowerCase() !== v.toLowerCase() ? x
        : { ...x, serial: hit.serial, maker: hit.maker || x.maker })));
    } catch { /* 못 불러오면 손으로 적는다 */ }
  };

  const save = async (takeStock = false): Promise<void> => {
    setBusy(true);
    setError(null);
    try {
      const toUnit = (d: Draft) => ({ id: d.id ?? null, part_name: d.part_name, serial: d.serial, und_serial: d.und_serial,
                                       photos: d.photos, item_photos: d.item_photos, maker: d.maker, purpose: d.purpose, note: d.note });
      const units = [
        ...[...earlier, ...waiting].map((u) => toUnit(fromUnit(u))),
        ...rows.filter((d) => d.serial.trim() || d.und_serial.trim()).map(toUnit),
      ];
      const r = await fetch(`/api/sales-deals/${dealId}/units`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ quote_id: view.quote_id, revision: view.revision, partial: true, lines: [{ line_no: line.line_no, units }],
                               shipment_index: bindIndex ?? null, shipment_expect: bindIndex != null ? bindExpect ?? null : null,
                               take_stock: takeStock }),
      });
      const d = await r.json().catch(() => ({}));
      // 재고(판매용·자체 개발용 입고) 제품과 같은 S/N — '판매하시겠습니까?/대여하시겠습니까?'(사용자 2026-11-20)
      if (r.status === 409 && d?.code === "STOCK_MATCH" && typeof d.detail === "string") {
        const [title, ...rest] = d.detail.split("\n");
        setBusy(false);
        if (await askConfirm({ title, message: rest.join("\n"), confirmLabel: title.includes("대여하시겠") ? "대여" : "판매", cancelLabel: "취소" })) await save(true);
        return;
      }
      if (!r.ok) throw new Error(typeof d?.detail === "string" ? d.detail : Array.isArray(d?.detail) ? "입력값을 확인해 주세요." : "저장하지 못했습니다.");
      onSaved(d as IssueUnits);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "저장하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  };

  // 견적서 품목 줄과 같은 두 단 카드(사용자 2026-10-23) — 좁은 칸에서 S/N·부품명이 잘리지 않게
  return (
    <div className="mt-3 rounded-xl border border-accent/30 bg-accent/[0.03] p-3.5" onKeyDown={(e) => { if (e.key === "Escape" && !busy) { e.stopPropagation(); onClose(); } }}>
      <div className="mb-3 flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <b className="text-[13.5px] font-extrabold">S/N 등록</b>
        <span className="text-[12px] text-foreground-muted">{scope} {need}개 · S/N <b className={filled >= need ? "text-emerald-700 dark:text-emerald-300" : "text-amber-700 dark:text-amber-300"}>{Math.min(filled, need)}/{need}</b>
          {!noSn && <> · 사진 <b>{photoN}/{rows.length}</b></>}</span>
        {/* 무상 기간은 모든 제품 출하일부터 1년 고정(사용자 2026-10-30) */}
        <span className="text-[11.5px] text-foreground-subtle">무상 기간 출하일부터 1년</span>
        <span className="ml-auto text-[11.5px] text-foreground-subtle">제조사 S/N·UND S/N 을 적습니다(우리 제품은 UND S/N 만 · UND S/N 을 적으면 등록된 제조사 S/N 을 불러옴) · S/N이 없는 품목은 &lsquo;특주&rsquo;</span>
      </div>
      {earlier.length > 0 && (
        <p className="mb-2 text-[11.5px] text-foreground-subtle">
          앞선 출하로 나간 S/N: {earlier.map((u) => u.serial || u.und_serial || "").join(", ")}
        </p>
      )}
      {waiting.length > 0 && (
        <p className="mb-2 text-[11.5px] text-foreground-subtle">
          아직 출하 전으로 적어 둔 S/N(이 출하에 묶지 않음): {waiting.map((u) => u.serial || u.und_serial || "").join(", ")}
        </p>
      )}
      <label className="mb-2 inline-flex items-center gap-1.5 text-[12.5px]">
        <input type="checkbox" checked={noSn} disabled={busy} onChange={(e) => toggleNoSn(e.target.checked)} />
        S/N 없는 품목(소모품 등) — 낱개 칸 없이 &lsquo;S/N 없음&rsquo;으로 기록
      </label>
      {noSn ? (
        <p className="rounded-lg bg-foreground/[0.04] px-3 py-2 text-[12px] text-foreground-muted">
          S/N 없는 품목으로 {rows.length}개 기록합니다. 박스·제품 사진은 출하 품목 줄의 [+ 품목 사진]에 올려 주세요.
        </p>
      ) : (
      <ul className="flex flex-col gap-2">
        {rows.map((d, i) => (
          <li key={d.key} className="@container rounded-xl border border-border bg-surface-raised p-3">
            {/* 윗줄: 번호 · S/N · 부품명 · 빼기 — 아랫줄은 칸이 넓으면 한 줄, 좁으면 메모만 따로(상세 패널·수주 진행 폭 둘 다) */}
            <div className="grid grid-cols-[24px_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1.1fr)_40px] items-end gap-2.5">
              <span className="grid h-10 place-items-center font-mono text-[12px] font-bold text-foreground-subtle">{i + 1}</span>
              <label className={ROW_LABEL}><span>제조사 S/N</span>
                <input className={cn(HEAD_FIELD, "font-mono")} value={d.serial} maxLength={80} placeholder="제조사 S/N 또는 특주" aria-label={`${i + 1}번 제조사 S/N`}
                       autoFocus={i === rows.findIndex((x) => !x.serial.trim())} disabled={busy}
                       onPaste={(e) => { if (pasteMany(d.key, e.clipboardData.getData("text"))) e.preventDefault(); }}
                       onChange={(e) => setRow(d.key, { serial: e.target.value })} />
              </label>
              <label className={ROW_LABEL}><span>UND S/N</span>
                <input className={cn(HEAD_FIELD, "font-mono")} value={d.und_serial} maxLength={80} placeholder="UND S/N" aria-label={`${i + 1}번 UND S/N`}
                       disabled={busy} onChange={(e) => setRow(d.key, { und_serial: e.target.value })}
                       onBlur={(e) => { if (!d.serial.trim()) void lookupUnd(d.key, e.target.value); }} />
              </label>
              <label className={ROW_LABEL}><span>부품명</span>
                <input className={HEAD_FIELD} value={d.part_name} maxLength={120} aria-label={`${i + 1}번 부품명`} disabled={busy}
                       onChange={(e) => setRow(d.key, { part_name: e.target.value })} />
              </label>
              <button type="button" disabled={busy || d.locked} aria-label={`${i + 1}번 칸 빼기`}
                      title={d.locked ? "출하한 부품은 뺄 수 없습니다" : "빼기"}
                      onClick={() => setRows((r) => r.filter((x) => x.key !== d.key))}
                      className={cn(ROW_ICON_BTN, "hover:text-red-600")}>
                <Icon name="trash" className="h-4 w-4" />
              </button>
            </div>
            <div className="ml-[34px] mt-2.5 grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-2.5 @md:mr-[50px] @md:grid-cols-[100px_104px_minmax(0,1fr)]">
              <label className={ROW_LABEL}><span>제조사</span>
                <input className={HEAD_FIELD} value={d.maker} maxLength={40} list="sn-makers" aria-label={`${i + 1}번 제조사`} disabled={busy}
                       onChange={(e) => setRow(d.key, { maker: e.target.value })} />
              </label>
              <label className={ROW_LABEL}><span>상태</span>
                <select className={cn(HEAD_FIELD, "px-2")} value={d.purpose} aria-label={`${i + 1}번 상태`} disabled={busy}
                        onChange={(e) => setRow(d.key, { purpose: e.target.value })}>
                  {UNIT_STATES.map((s) => <option key={s.code} value={s.code} title={s.en}>{s.code} · {s.label}</option>)}
                </select>
              </label>
              <label className={cn(ROW_LABEL, "col-span-2 @md:col-span-1")}><span>메모</span>
                <input className={HEAD_FIELD} value={d.note} maxLength={300} aria-label={`${i + 1}번 메모`} disabled={busy}
                       onChange={(e) => setRow(d.key, { note: e.target.value })} />
              </label>
            </div>
            {/* 사진 두 가지 — 낱개 품목 사진 · S/N 사진(여러 장), 입고와 같게 */}
            <div className="ml-[34px] mt-2.5 flex flex-wrap gap-x-5 gap-y-2">
              <SnPhotoCell api={photoApi} photos={d.item_photos} disabled={busy} label="낱개 품목 사진"
                           onAdd={(ids) => addPhotos(d.key, "item_photos", ids)}
                           onRemove={(ph) => setRow(d.key, { item_photos: d.item_photos.filter((x) => x !== ph) })} />
              <SnPhotoCell api={photoApi} photos={d.photos} disabled={busy} label="S/N 사진"
                           onAdd={(ids) => addPhotos(d.key, "photos", ids)}
                           onRemove={(ph) => setRow(d.key, { photos: d.photos.filter((x) => x !== ph) })} />
            </div>
          </li>
        ))}
      </ul>
      )}
      <datalist id="sn-makers">{MAKERS.map((m) => <option key={m} value={m} />)}</datalist>
      {error && <p className="mt-2 rounded-lg bg-red-500/10 px-3 py-1.5 text-[12px] text-red-700 dark:text-red-300">{error}</p>}
      <div className="mt-2.5">
        <button type="button" disabled={busy || noSn} onClick={() => setRows((r) => [...r, blank(line, purpose)])} className={ITEM_BTN}>
          <Icon name="plus" className="h-3.5 w-3.5 text-accent" /> 칸 추가 <span className="font-normal text-foreground-muted">(한 품목을 부품 여러 개로 나눌 때)</span>
        </button>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <span className="text-[11.5px] text-foreground-subtle">여러 S/N을 줄바꿈으로 복사해 한 칸에 붙여 넣으면 아래 칸으로 나눠 들어갑니다.</span>
        <span className="ml-auto flex gap-2">
          <button type="button" onClick={onClose} disabled={busy} className={secondaryBtn}>닫기</button>
          <button type="button" onClick={() => void save(false)} disabled={busy} className={primaryBtn}>
            <Icon name="check" className="h-4 w-4" /> {busy ? "저장 중…" : "S/N 저장"}
          </button>
        </span>
      </div>
    </div>
  );
}
