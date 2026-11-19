// 재고 [제품 등록](사용자 2026-11-19) — 구매 없이 이미 있는 제품을 제품 재고 관리에 넣는 창. 판매용 / 보유 후 자체 개발용을 고르면
// 입고 기록 작성 칸([품목 모두 비우기] 한 것과 같은 꼴)이 뜨고, 저장하면 화면에 안 보이는 '직접 등록' 건(purchases.direct)에 입고 기록처럼
// 담긴다 — 재고 줄·상태·제품 S/N 관리·사진이 입고한 제품과 같게 동작. 사진을 먼저 올리려고 창을 열 때 빈 작성 건을 잡는다(같은 사람은 다시 씀).
"use client";

import { useEffect, useState } from "react";
import { ReceiptForm, type ReceiptBody } from "@/components/domains/sales/purchase-detail";
import { askConfirm } from "@/components/shared/ui/confirm-host";
import type { PurchaseRow, ReceiptPurpose } from "@/lib/shared/purchases";

const PURPOSE_NAME: Record<ReceiptPurpose, string> = { sale: "판매용", dev: "보유 후 자체 개발용" };

async function call<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(`/api/stock${path}`, body === undefined ? { cache: "no-store" } : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof d?.detail === "string" ? d.detail : Array.isArray(d?.detail) ? "입력값을 확인해 주세요." : "처리하지 못했습니다.");
  return d as T;
}

/** pid 가 있으면 그 직접 등록 건 고치기, 없으면 새 등록(빈 작성 건을 잡고 시작) */
export function StockDirect({ purpose: want, pid, admin, onDone, onClose }: {
  purpose: ReceiptPurpose; pid: number | null; admin: boolean; onDone: () => void; onClose: () => void;
}) {
  const [id, setId] = useState<number | null>(pid);
  const [p, setP] = useState<PurchaseRow | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const got = pid ?? (await call<{ id: number }>("/direct/draft", {})).id;
        const row = await call<PurchaseRow>(`/direct/${got}`);
        if (alive) { setId(got); setP(row); }
      } catch (e) {
        if (alive) setErr(e instanceof Error ? e.message : "열지 못했습니다.");
      }
    })();
    return () => { alive = false; };
  }, [pid]);
  const rec = p?.receipt.records[0];
  const purpose: ReceiptPurpose = rec?.purpose ?? want;
  const save = async (b: ReceiptBody) => {
    if (!id) return;
    setBusy(true);
    setErr(null);
    try {
      await call(`/direct/${id}`, { ...b, purpose });
      onDone();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "저장하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  };
  const remove = async () => {
    if (!id || !(await askConfirm({ title: "등록한 제품을 지울까요?", message: "이 등록의 재고 줄과 제품 S/N 관리 줄이 함께 지워지고 되돌릴 수 없습니다.",
                                    confirmLabel: "지우기", danger: true }))) return;
    try {
      await call(`/direct/${id}/delete`, {});
      onDone();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "지우지 못했습니다.");
    }
  };
  return (
    <div role="dialog" aria-modal="true" aria-label="제품 등록" className="fixed inset-0 z-40 flex items-start justify-center overflow-y-auto bg-black/40 p-6"
         onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); onClose(); } }}>
      <div className="w-full max-w-[900px] rounded-2xl bg-background p-5 shadow-2xl">
        <div className="mb-3 flex items-center gap-2">
          <h2 className="text-[16px] font-bold">제품 등록 — {PURPOSE_NAME[purpose]}</h2>
          <span className="text-[12px] text-foreground-muted">구매 건 없이 이미 있는 제품을 재고에 넣습니다{rec ? " · 고치기" : ""}</span>
          {rec && admin && <button type="button" onClick={() => void remove()} className="ml-auto text-[12px] text-red-600 hover:underline">이 등록 지우기</button>}
          <button type="button" onClick={onClose} aria-label="닫기" className={rec && admin ? "rounded px-2 text-foreground-muted hover:bg-foreground/[0.06]" : "ml-auto rounded px-2 text-foreground-muted hover:bg-foreground/[0.06]"}>✕</button>
        </div>
        {err && <p className="mb-2 text-[12.5px] font-semibold text-red-600">{err}</p>}
        {!p || !id ? <p className="py-10 text-center text-[13px] text-foreground-subtle">{err ? "" : "여는 중…"}</p> : (
          <ReceiptForm id={id} p={p} purpose={purpose} initial={rec} picked={[]} leftOf={() => 0} remaining={[]} busy={busy} direct
                       onDraft={() => undefined} onSave={(b) => void save(b)} onCancel={onClose} />
        )}
      </div>
    </div>
  );
}
