"use client";

// 견적서 수기 작성 — 목적 고르기(사용자 2026-10-22): 제품 판매 / 제품 대여. 대여는 방역 로봇 3년 렌탈 견적서처럼
// 계약 개월 수 · Total 옆 월 납입금 · 중도 해지 주석이 들어가고, 발행하면 [제품 대여 관리]로 이어진다.
import { useEffect } from "react";

import type { QuotePurpose } from "@/lib/shared/product-recommend";

const CHOICES: { key: QuotePurpose; title: string; desc: string; tone: string }[] = [
  { key: "sale", title: "제품 판매", desc: "지금까지의 견적서 — 발행하면 [제품 영업 건 관리]로 이어집니다.",
    tone: "hover:border-violet-500/60 hover:bg-violet-500/5" },
  { key: "rental", title: "제품 대여",
    desc: "계약 개월 수를 적으면 Total 옆에 월 납입금이 들어가고, 중도 해지 문구가 붙습니다. 발행하면 [제품 대여 관리]로 이어집니다.",
    tone: "hover:border-emerald-500/60 hover:bg-emerald-500/5" },
];

export function QuotePurposeDialog({ busy, onPick, onClose }: {
  busy: boolean; onPick: (purpose: QuotePurpose) => void; onClose: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !busy) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/30 p-4" onClick={() => !busy && onClose()}>
      <div role="dialog" aria-label="견적서 목적 선택" onClick={(e) => e.stopPropagation()}
           className="w-full max-w-[520px] rounded-2xl bg-background p-5 shadow-2xl">
        <h2 className="text-[16px] font-bold">어떤 견적서를 쓸까요?</h2>
        <p className="mt-1 text-[12.5px] text-foreground-muted">양식은 거의 같고, 대여는 대여 조건이 더 들어갑니다. 작성 중에도 바꿀 수 있습니다.</p>
        <div className="mt-4 grid gap-2 sm:grid-cols-2">
          {CHOICES.map((c) => (
            <button key={c.key} type="button" disabled={busy} onClick={() => onPick(c.key)}
                    className={`flex flex-col gap-1 rounded-xl border border-border p-4 text-left transition-colors disabled:opacity-60 ${c.tone}`}>
              <span className="text-[15px] font-semibold">{c.title}</span>
              <span className="text-[12px] leading-relaxed text-foreground-muted">{c.desc}</span>
            </button>
          ))}
        </div>
        <div className="mt-4 flex justify-end">
          <button type="button" disabled={busy} onClick={onClose}
                  className="h-9 rounded-lg border border-border px-4 text-[13px] hover:bg-foreground/5">{busy ? "만드는 중…" : "취소"}</button>
        </div>
      </div>
    </div>
  );
}
