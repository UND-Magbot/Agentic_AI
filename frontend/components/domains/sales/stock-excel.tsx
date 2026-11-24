"use client";

// 공장 제품 재고 엑셀(사용자 2026-11-20) — [엑셀 내려받기] · [재고 엑셀 적용].
// 적용: 고친 엑셀을 올리면 바뀌는 줄을 먼저 보여 준다(저장 안 함). 짝은 재고 번호 → S/N → AI 추정 순.
// AI 추정 줄은 처음엔 체크하지 않는다 — 사람이 보고 맞으면 체크. [n줄 반영]으로 고른 줄만 상태·메모를 바꾼다.

import { useRef, useState } from "react";
import { askConfirm } from "@/components/shared/ui/confirm-host";
import { cn } from "@/lib/shared/utils";

interface Change {
  id: number;
  purpose: "sale" | "dev";
  where: string;
  how: "재고 번호" | "S/N" | "AI 추정";
  why: string;
  name: string;
  maker: string;
  sn: string;
  who: string;
  from_status: string;
  to_status: string;
  from_note: string;
  to_note: string;
}
interface Preview {
  changes: Change[];
  problems: { where: string; name: string; why: string }[];
  same: number;
  rows: number;
}

const BTN = "h-9 rounded-lg border border-border px-3 text-[12.5px] font-semibold text-foreground hover:border-accent hover:text-accent disabled:opacity-50";

async function readError(r: Response, fallback: string): Promise<string> {
  const d = await r.json().catch(() => ({}));
  return typeof d?.detail === "string" ? d.detail : fallback;
}

export function StockExcelButtons({ onApplied, readOnly = false }: { onApplied: () => void; readOnly?: boolean }) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState<"down" | "read" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pv, setPv] = useState<Preview | null>(null);

  const download = async () => {
    setBusy("down");
    setError(null);
    try {
      const r = await fetch("/api/stock/export", { cache: "no-store" });
      if (!r.ok) throw new Error(await readError(r, "엑셀을 만들지 못했습니다."));
      const blob = await r.blob();
      const cd = r.headers.get("content-disposition") ?? "";
      const name = decodeURIComponent(/filename\*=UTF-8''([^;]+)/i.exec(cd)?.[1] ?? "공장제품재고.xlsx");
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "엑셀을 만들지 못했습니다.");
    } finally {
      setBusy(null);
    }
  };

  const upload = async (file: File) => {
    setBusy("read");
    setError(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await fetch("/api/stock/import/preview", { method: "POST", body: fd });
      if (!r.ok) throw new Error(await readError(r, "엑셀을 읽지 못했습니다."));
      setPv((await r.json()) as Preview);
    } catch (e) {
      setError(e instanceof Error ? e.message : "엑셀을 읽지 못했습니다.");
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <button type="button" className={BTN} disabled={!!busy} onClick={() => void download()}
              title="판매용·보유 후 자체 개발용 두 시트 — 상태·상태 메모 칸을 고쳐 다시 올립니다">
        {busy === "down" ? "만드는 중…" : "엑셀 내려받기"}</button>
      {!readOnly && (
      <button type="button" className={BTN} disabled={!!busy} onClick={() => fileRef.current?.click()}
              title="고친 재고 엑셀을 올리면 바뀌는 줄을 먼저 보여 줍니다">
        {busy === "read" ? "대조하는 중…" : "재고 엑셀 적용"}</button>
      )}
      <input ref={fileRef} type="file" accept=".xlsx" className="hidden" aria-label="재고 엑셀 파일"
             onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) void upload(f); }} />
      {error && <span role="alert" className="basis-full text-right text-[12px] font-semibold text-red-600 dark:text-red-400">{error}</span>}
      {pv && <ExcelPreview pv={pv} onClose={() => setPv(null)} onDone={() => { setPv(null); onApplied(); }} />}
    </>
  );
}

const HOW_TONE: Record<Change["how"], string> = {
  "재고 번호": "bg-foreground/8 text-foreground-muted",
  "S/N": "bg-sky-500/12 text-sky-700 dark:text-sky-300",
  "AI 추정": "bg-amber-500/15 text-amber-800 dark:text-amber-300",
};

function ExcelPreview({ pv, onClose, onDone }: { pv: Preview; onClose: () => void; onDone: () => void }) {
  // AI 추정은 사람이 확인하고 체크(처음엔 빼 둠)
  const [picked, setPicked] = useState<Set<number>>(() => new Set(pv.changes.filter((c) => c.how !== "AI 추정").map((c) => c.id)));
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ applied: number; failed: { id: number; why: string }[] } | null>(null);
  const toggle = (id: number) => setPicked((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const ai = pv.changes.filter((c) => c.how === "AI 추정").length;

  const apply = async () => {
    const items = pv.changes.filter((c) => picked.has(c.id)).map((c) => ({ id: c.id, status: c.to_status, status_note: c.to_note }));
    if (!items.length) return;
    if (!(await askConfirm({ title: `${items.length}줄의 상태·메모를 바꿀까요?`, message: "변경 이력에 '엑셀'로 남습니다.", confirmLabel: "반영" }))) return;
    setBusy(true);
    try {
      const r = await fetch("/api/stock/import/apply", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ items }) });
      if (!r.ok) throw new Error(await readError(r, "반영하지 못했습니다."));
      const d = await r.json();
      if (!d.failed?.length) { onDone(); return; }
      setResult(d);
    } catch (e) {
      setResult({ applied: 0, failed: [{ id: 0, why: e instanceof Error ? e.message : "반영하지 못했습니다." }] });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4" role="dialog" aria-modal="true" aria-label="재고 엑셀 적용"
         onKeyDown={(e) => { if (e.key === "Escape" && !busy) onClose(); }}>
      <div className="flex max-h-[88vh] w-full max-w-[1100px] flex-col rounded-2xl bg-surface shadow-xl">
        <div className="border-b border-border px-5 py-3.5">
          <h3 className="text-[15px] font-bold">재고 엑셀 적용 — 바뀌는 줄 확인</h3>
          <p className="mt-0.5 text-[12px] text-foreground-muted">
            엑셀 {pv.rows}줄 중 바뀌는 줄 <b>{pv.changes.length}</b> · 그대로 {pv.same}
            {pv.problems.length > 0 && <> · 반영 못 하는 줄 <b className="text-red-600 dark:text-red-400">{pv.problems.length}</b></>}
            {ai > 0 && <> · <b className="text-amber-700 dark:text-amber-300">AI 추정 {ai}줄은 맞는지 보고 체크해 주세요</b></>}
          </p>
        </div>
        <div className="min-h-0 flex-1 overflow-auto px-5 py-3">
          {pv.changes.length === 0 ? (
            <p className="py-8 text-center text-[13px] text-foreground-muted">바뀌는 줄이 없습니다. 노란 칸(상태·상태 메모)을 고친 엑셀인지 확인해 주세요.</p>
          ) : (
            <table className="w-full border-collapse text-[12.5px]">
              <thead>
                <tr className="border-b border-border text-left text-[11.5px] text-foreground-muted">
                  <th className="w-8 px-1.5 py-1.5">
                    <input type="checkbox" aria-label="모두 고르기" checked={picked.size === pv.changes.length}
                           onChange={(e) => setPicked(e.target.checked ? new Set(pv.changes.map((c) => c.id)) : new Set())} />
                  </th>
                  <th className="px-1.5 py-1.5">엑셀</th><th className="px-1.5 py-1.5">짝</th><th className="px-1.5 py-1.5">제품</th>
                  <th className="px-1.5 py-1.5">S/N</th><th className="px-1.5 py-1.5">상태</th><th className="px-1.5 py-1.5">상태 메모</th>
                </tr>
              </thead>
              <tbody>
                {pv.changes.map((c) => (
                  <tr key={c.id} className={cn("border-b border-border/60 align-top", c.how === "AI 추정" && "bg-amber-500/[0.05]")}>
                    <td className="px-1.5 py-1.5"><input type="checkbox" aria-label={`${c.where} 반영`} checked={picked.has(c.id)} onChange={() => toggle(c.id)} /></td>
                    <td className="whitespace-nowrap px-1.5 py-1.5 text-foreground-muted">{c.where}</td>
                    <td className="px-1.5 py-1.5">
                      <span className={cn("whitespace-nowrap rounded px-1.5 py-0.5 text-[11px] font-semibold", HOW_TONE[c.how])}>{c.how}</span>
                      {c.why && <span className="mt-0.5 block text-[11px] text-foreground-subtle">{c.why}</span>}
                    </td>
                    <td className="px-1.5 py-1.5">{c.name}<span className="block text-[11px] text-foreground-muted">{[c.maker, c.who].filter(Boolean).join(" · ")}</span></td>
                    <td className="whitespace-nowrap px-1.5 py-1.5 font-mono text-[11.5px]">{c.sn || "-"}</td>
                    <td className="whitespace-nowrap px-1.5 py-1.5">
                      {c.from_status === c.to_status ? c.to_status : <><s className="text-foreground-subtle">{c.from_status}</s> → <b>{c.to_status}</b></>}
                    </td>
                    <td className="max-w-[280px] px-1.5 py-1.5">
                      {c.from_note === c.to_note ? <span className="text-foreground-muted">{c.to_note || "-"}</span> : (
                        <>{c.from_note && <s className="block text-foreground-subtle">{c.from_note}</s>}<b className="whitespace-pre-wrap">{c.to_note || "(지움)"}</b></>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {pv.problems.length > 0 && (
            <div className="mt-3 rounded-lg border border-red-500/25 bg-red-500/[0.05] px-3 py-2 text-[12px]">
              <b className="text-red-700 dark:text-red-300">반영 못 하는 줄</b>
              <ul className="mt-1 space-y-0.5 text-foreground-muted">
                {pv.problems.map((p, k) => <li key={k}><b className="text-foreground">{p.where}</b> {p.name && `${p.name} — `}{p.why}</li>)}
              </ul>
            </div>
          )}
          {result && (
            <div className="mt-3 rounded-lg border border-amber-500/30 bg-amber-500/[0.07] px-3 py-2 text-[12px]">
              <b>{result.applied}줄 반영 · {result.failed.length}줄 실패</b>
              <ul className="mt-1 text-foreground-muted">{result.failed.map((f, k) => <li key={k}>{f.why}</li>)}</ul>
            </div>
          )}
        </div>
        <div className="flex items-center justify-end gap-2 border-t border-border px-5 py-3">
          {result ? (
            <button type="button" className="h-9 rounded-lg bg-accent px-4 text-[12.5px] font-semibold text-white" onClick={onDone}>닫기</button>
          ) : (
            <>
              <button type="button" className={BTN} disabled={busy} onClick={onClose}>취소</button>
              <button type="button" disabled={busy || picked.size === 0} onClick={() => void apply()}
                      className="h-9 rounded-lg bg-accent px-4 text-[12.5px] font-semibold text-white disabled:opacity-40">
                {busy ? "반영 중…" : `${picked.size}줄 반영`}</button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
