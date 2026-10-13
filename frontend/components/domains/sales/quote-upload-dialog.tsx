"use client";

// 밖에서 고친 견적서 파일 첨부(사용자 2026-10-12, 영업팀 요청) — 영업 건 상세의 견적서 칸·견적서 탭이 같이 쓴다.
// ① 파일(엑셀·PDF) + 발행자 이니셜(기본 = 로그인 계정) → 올리면 AI 가 품목·금액을 읽는다
// ② 읽은 품목·발행번호를 확인·수정 → [이 내용으로 발행] = 견적서 발행 한 번(판 +1, 영업 건 금액 갱신).
//    발행번호는 견적서에 적힌 번호를 따른다(담당자가 고치면서 정한 번호). 적힌 번호가 없을 때만 새 번호 QT-이니셜+오늘-NN.
import { useEffect, useState } from "react";

import { Icon } from "@/components/shared/ui/icon";
import { cn } from "@/lib/shared/utils";

type Item = { name: string; spec: string; qty: number | null; unit_price: number | null };
type Upload = { key: string; filename: string; mime: string; size: number };
type ReadResult = { status: "ok" | "unreadable" | "failed"; items: Item[]; supply_total?: number | null;
                    vat_included?: boolean | null; doc_no?: string | null; summary: string };

const ACCEPT = ".xlsx,.pdf";
const inputCls = "h-8 w-full min-w-0 rounded-md border border-border bg-surface px-2 text-[12.5px] outline-none focus:border-accent";
const won = (n: number) => n.toLocaleString("ko-KR");

function detail(d: unknown, fallback: string): string {
  const x = (d as { detail?: unknown })?.detail;
  return typeof x === "string" ? x : Array.isArray(x) ? "입력값을 확인해 주세요." : fallback;
}

/** 열 때만 렌더({open && <QuoteUploadDialog …/>}). uploadUrl = multipart 올리기, confirmUrl = JSON 발행. onDone 에 발행 응답. */
export function QuoteUploadDialog({ title, uploadUrl, confirmUrl, onDone, onClose }: {
  title: string; uploadUrl: string; confirmUrl: string; onDone: (data: unknown) => void; onClose: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [initials, setInitials] = useState("");
  const [upload, setUpload] = useState<Upload | null>(null);
  const [read, setRead] = useState<ReadResult | null>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [docNo, setDocNo] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // 이미 있는 발행번호로 발행하려 할 때 팝업(사용자 2026-10-13) — [확인]이면 발행번호 칸으로 돌아가 고친다
  const [dupMsg, setDupMsg] = useState<string | null>(null);

  // 발행자 이니셜 기본값 = 로그인 계정(내 정보) — 고칠 수 있음
  useEffect(() => {
    let alive = true;
    fetch("/api/my-profile", { cache: "no-store" }).then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (alive && d?.initials) setInitials((v) => v || String(d.initials)); }).catch(() => {});
    return () => { alive = false; };
  }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !busy && !dupMsg) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, dupMsg, onClose]);

  function closeDup() {
    setDupMsg(null);
    requestAnimationFrame(() => {
      const el = document.getElementById("qup-docno") as HTMLInputElement | null;
      el?.focus();
      el?.select();
    });
  }

  async function send() {
    if (!file) return;
    setBusy("올리고 AI 가 견적서를 읽는 중…");
    setError(null);
    try {
      const fd = new FormData();
      fd.append("file", file, file.name);
      const r = await fetch(uploadUrl, { method: "POST", body: fd });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(detail(d, "견적서를 올리지 못했습니다."));
      setUpload(d.upload as Upload);
      const rd = d.read as ReadResult;
      setRead(rd);
      setItems(rd.items.length ? rd.items : [{ name: "", spec: "", qty: 1, unit_price: null }]);
      setDocNo(rd.doc_no ?? "");
    } catch (e) {
      setError(e instanceof Error ? e.message : "견적서를 올리지 못했습니다.");
    } finally {
      setBusy(null);
    }
  }

  async function issue() {
    if (!upload) return;
    if (!docNo.trim() && !/^[A-Za-z]{2,4}$/.test(initials.trim())) {
      setError("견적서에 발행번호가 없으면 발행자 이니셜을 영문 2~4자로 적어 주세요(예: LU) — 새 번호를 매깁니다.");
      return;
    }
    setBusy("발행하는 중…");
    setError(null);
    try {
      const r = await fetch(confirmUrl, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ upload, doc_no: docNo.trim(), initials: initials.trim().toUpperCase(), items }),
      });
      const d = await r.json().catch(() => ({}));
      if (r.status === 409) {
        setDupMsg(detail(d, "이미 존재하는 발행번호입니다. 견적서의 발행번호를 새 번호로 고쳐 주세요."));
        setBusy(null);
        return;
      }
      if (!r.ok) throw new Error(detail(d, "발행하지 못했습니다."));
      onDone(d);
    } catch (e) {
      setError(e instanceof Error ? e.message : "발행하지 못했습니다.");
      setBusy(null);
    }
  }

  const setItem = (i: number, patch: Partial<Item>) => setItems((xs) => xs.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  const num = (s: string) => (s.trim() === "" ? null : Number(s.replace(/,/g, "")));
  const total = items.reduce((a, x) => a + (x.qty ?? 0) * (x.unit_price ?? 0), 0);
  const filled = items.length > 0 && items.every((x) => x.name.trim() && x.qty && x.qty > 0 && x.unit_price != null);
  const today = new Date();
  const ymd = `${today.getFullYear()}${String(today.getMonth() + 1).padStart(2, "0")}${String(today.getDate()).padStart(2, "0")}`;

  return (
    <div role="dialog" aria-modal="true" aria-labelledby="qup-title"
         className="anim-fade-in fixed inset-0 z-50 grid place-items-center bg-black/40 px-4"
         onMouseDown={(e) => { if (e.target === e.currentTarget && !busy && !dupMsg) onClose(); }}>
      <div className="anim-fade-in-up max-h-[90vh] w-full max-w-[760px] overflow-y-auto rounded-2xl border border-border bg-surface-raised p-5 text-[13px] shadow-[0_30px_70px_-30px_rgba(15,30,70,0.55)]">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 id="qup-title" className="text-[15px] font-semibold text-foreground">{title}</h2>
            <p className="mt-1 text-[12.5px] text-foreground-muted">
              AI 가 만든 견적서를 밖에서 고쳤거나 따로 만든 견적서를, 고객에게 보낸 파일 그대로 올립니다. 견적서에 적힌 발행번호로 발행되고 영업 건 금액도 바뀝니다.
            </p>
          </div>
          <button type="button" onClick={onClose} disabled={!!busy} aria-label="닫기"
                  className="grid h-7 w-7 shrink-0 place-items-center rounded-sm text-foreground-subtle hover:bg-foreground/5 hover:text-foreground">
            <span aria-hidden className="text-[15px] leading-none">×</span>
          </button>
        </div>

        {!upload ? (
          <div className="mt-4 flex flex-col gap-3">
            <label className={cn("flex cursor-pointer flex-col items-center gap-1.5 rounded-xl border-2 border-dashed px-4 py-7 text-center",
              file ? "border-accent/60 bg-accent/[0.04]" : "border-border hover:border-foreground/25")}>
              <input type="file" accept={ACCEPT} className="sr-only" disabled={!!busy}
                     onChange={(e) => { setFile(e.target.files?.[0] ?? null); setError(null); }} />
              <Icon name="paperclip" className="h-6 w-6 text-foreground-subtle" />
              {file ? <><b className="text-[14px] text-foreground">{file.name}</b><span className="text-[12px] text-foreground-subtle">{(file.size / 1024).toFixed(0)}KB · 다른 파일을 고르면 바꿉니다</span></>
                : <><b className="text-[14px] text-foreground">견적서 파일을 고르세요</b><span className="text-[12px] text-foreground-subtle">엑셀(xlsx) · PDF, 20MB 까지</span></>}
            </label>
            <label className="flex flex-col gap-1 text-[12.5px]">
              <span className="font-medium text-foreground">발행자 이니셜</span>
              <input value={initials} maxLength={4} placeholder="예: LU" disabled={!!busy} id="qup-initials"
                     onChange={(e) => setInitials(e.target.value.toUpperCase())} className={cn(inputCls, "h-9 w-32 uppercase")} />
              <span className="text-[11.5px] text-foreground-subtle">
                발행번호는 견적서에 적힌 번호를 그대로 씁니다. 견적서에 번호가 없을 때만 이 이니셜로 새 번호 QT-{(initials || "LU").toUpperCase()}{ymd}-NN 을 매깁니다.
              </span>
            </label>
          </div>
        ) : (
          <div className="mt-4 flex flex-col gap-3">
            <div className={cn("rounded-lg border px-3 py-2 text-[12.5px]",
              read?.status === "ok" ? "border-border bg-surface" : "border-amber-500/50 bg-amber-500/[0.06] text-amber-800 dark:text-amber-200")}>
              <b>{upload.filename}</b> — {read?.summary}
              {read?.vat_included && <span className="block text-amber-700 dark:text-amber-300">단가가 부가세 포함으로 적혀 있습니다 — 공급가액(부가세 제외)으로 고쳐 적어 주세요.</span>}
            </div>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[620px] text-[12.5px]">
                <thead>
                  <tr className="text-left text-[11.5px] text-foreground-subtle">
                    <th className="px-1 py-1 font-medium">품명 *</th><th className="px-1 py-1 font-medium">규격</th>
                    <th className="w-[80px] px-1 py-1 font-medium">수량 *</th><th className="w-[120px] px-1 py-1 font-medium">단가(공급가) *</th>
                    <th className="w-[110px] px-1 py-1 text-right font-medium">금액</th><th className="w-6" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {items.map((x, i) => (
                    <tr key={i}>
                      <td className="px-1 py-1"><input className={inputCls} value={x.name} aria-label={`${i + 1}번 품명`} placeholder="예: Magbot ATC"
                                                       onChange={(e) => setItem(i, { name: e.target.value })} /></td>
                      <td className="px-1 py-1"><input className={inputCls} value={x.spec} aria-label={`${i + 1}번 규격`} placeholder="예: TCV1 - MASTER T.C"
                                                       onChange={(e) => setItem(i, { spec: e.target.value })} /></td>
                      <td className="px-1 py-1"><input className={cn(inputCls, x.qty == null && "border-amber-500")} inputMode="decimal" value={x.qty ?? ""}
                                                       aria-label={`${i + 1}번 수량`} onChange={(e) => setItem(i, { qty: num(e.target.value) })} /></td>
                      <td className="px-1 py-1"><input className={cn(inputCls, x.unit_price == null && "border-amber-500")} inputMode="numeric" value={x.unit_price ?? ""}
                                                       aria-label={`${i + 1}번 단가`} onChange={(e) => setItem(i, { unit_price: num(e.target.value) })} /></td>
                      <td className="whitespace-nowrap px-1 py-1 text-right tabular-nums">{won((x.qty ?? 0) * (x.unit_price ?? 0))}</td>
                      <td className="px-1 py-1">
                        <button type="button" aria-label={`${i + 1}번 품목 빼기`} className="text-foreground-subtle hover:text-red-600"
                                onClick={() => setItems((xs) => xs.filter((_, j) => j !== i))}><Icon name="trash" className="h-4 w-4" /></button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <button type="button" className="inline-flex items-center gap-1 rounded border border-border px-2 py-0.5 text-[12px] text-foreground-muted hover:text-foreground"
                      onClick={() => setItems((xs) => [...xs, { name: "", spec: "", qty: 1, unit_price: null }])}>
                <Icon name="plus" className="h-3.5 w-3.5" /> 품목 추가
              </button>
              <span className="text-[13px]">
                합계(공급가액) <b className="tabular-nums">{won(total)}원</b>
                {read?.supply_total != null && Math.abs(read.supply_total - total) > 1 && (
                  <span className="ml-1.5 text-[12px] text-amber-700 dark:text-amber-300">· 견적서 공급가액 {won(read.supply_total)}원과 다름</span>
                )}
              </span>
            </div>
            <div className="flex flex-col gap-1.5 rounded-lg border border-border bg-surface px-3 py-2 text-[12.5px]">
              <label className="flex flex-wrap items-center gap-2">
                <span className="font-medium">발행번호</span>
                <input value={docNo} maxLength={40} disabled={!!busy} onChange={(e) => setDocNo(e.target.value)} id="qup-docno"
                       placeholder="견적서에 적힌 번호(예: QT-LU20261012-03)" className={cn(inputCls, "w-64 font-mono")} aria-label="발행번호" />
                <span className="text-[11.5px] text-foreground-subtle">
                  {read?.doc_no ? "견적서에서 읽은 번호 — 다르면 고쳐 주세요" : "견적서에서 번호를 찾지 못했습니다 — 적거나 비워 두면 새 번호"}
                </span>
              </label>
              {!docNo.trim() && (
                <label className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">발행자 이니셜 *</span>
                  <input value={initials} maxLength={4} disabled={!!busy} onChange={(e) => setInitials(e.target.value.toUpperCase())}
                         className={cn(inputCls, "w-24 uppercase")} aria-label="발행자 이니셜" />
                  <span className="text-[11.5px] text-foreground-subtle">→ 새 번호 QT-{(initials || "??").toUpperCase()}{ymd}-NN</span>
                </label>
              )}
            </div>
          </div>
        )}

        {error && <p className="mt-3 text-[12.5px] text-red-600">{error}</p>}
        <div className="mt-4 flex flex-wrap items-center justify-end gap-2">
          {busy && <span className="mr-auto text-[12.5px] text-foreground-muted">{busy}</span>}
          <button type="button" onClick={onClose} disabled={!!busy} className="h-9 rounded-lg border border-border px-4 text-[13px]">취소</button>
          {!upload ? (
            <button type="button" disabled={!file || !!busy} onClick={() => void send()}
                    className="h-9 rounded-lg bg-accent px-5 text-[13px] font-semibold text-white hover:brightness-105 disabled:opacity-50">
              올리고 읽기
            </button>
          ) : (
            <button type="button" disabled={!filled || !!busy} onClick={() => void issue()}
                    title={filled ? "" : "품명·수량·단가를 모두 채워 주세요"}
                    className="h-9 rounded-lg bg-accent px-5 text-[13px] font-semibold text-white hover:brightness-105 disabled:opacity-50">
              {docNo.trim() ? `${docNo.trim()} 로 발행` : "이 내용으로 발행 (새 번호)"}
            </button>
          )}
        </div>
      </div>
      {dupMsg && (
        <div role="alertdialog" aria-modal="true" aria-labelledby="qup-dup-title" aria-describedby="qup-dup-msg"
             className="anim-fade-in fixed inset-0 z-[60] grid place-items-center bg-black/30 px-4">
          <div className="anim-fade-in-up w-full max-w-[420px] rounded-2xl border border-border bg-surface-raised p-5 text-[13px] shadow-[0_30px_70px_-30px_rgba(15,30,70,0.55)]">
            <h3 id="qup-dup-title" className="text-[15px] font-semibold text-red-600">이미 존재하는 발행번호</h3>
            <p id="qup-dup-msg" className="mt-2 text-[13px] leading-relaxed text-foreground">{dupMsg}</p>
            <p className="mt-1 text-[12px] text-foreground-muted">같은 번호로는 다시 발행할 수 없습니다. 고친 견적서는 새 발행번호로 발행해 주세요(비워 두면 이니셜로 새 번호를 매깁니다).</p>
            <div className="mt-4 flex justify-end">
              <button type="button" autoFocus onClick={closeDup}
                      className="h-9 rounded-lg bg-accent px-5 text-[13px] font-semibold text-white hover:brightness-105">확인</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
