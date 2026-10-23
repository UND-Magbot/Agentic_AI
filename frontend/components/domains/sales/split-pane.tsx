// 작업 칸(왼쪽) ↔ AI 대화(오른쪽) 폭 조절 — 견적서 작성(사용자 2026-10-22)·수주 진행(2026-10-23)이 같이 쓴다.
"use client";

import { useState } from "react";

const SPLIT_MIN = 35;
const SPLIT_MAX = 75;               // AI 대화창이 너무 좁아지지 않게

export interface Split {
  pct: number;
  start: (e: React.PointerEvent<HTMLDivElement>) => void;
  key: (e: React.KeyboardEvent) => void;
  reset: () => void;
}

/** 왼쪽 폭 % — 막대를 끌거나 화살표 키로 바꾸고, 화면마다(storageKey) 이 브라우저에 기억한다. */
export function useSplit(storageKey: string, fallback: number): Split {
  // 기억한 폭 — 내용을 받은 뒤(브라우저에서만) 그려지므로 처음 값으로 바로 읽는다
  const [pct, setPct] = useState(() => {
    if (typeof window === "undefined") return fallback;
    try {
      const v = Number(window.localStorage.getItem(storageKey));
      return v >= SPLIT_MIN && v <= SPLIT_MAX ? v : fallback;
    } catch {
      return fallback;
    }
  });
  const keep = (v: number) => {
    const c = Math.min(SPLIT_MAX, Math.max(SPLIT_MIN, v));
    setPct(c);
    try { window.localStorage.setItem(storageKey, String(Math.round(c * 10) / 10)); } catch { /* 기억 못 해도 동작 */ }
  };
  const start = (e: React.PointerEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    const box = el.parentElement?.getBoundingClientRect();         // 막대의 부모 = 작업 칸·대화를 담은 칸
    if (!box) return;
    e.preventDefault();
    el.setPointerCapture(e.pointerId);
    const move = (ev: PointerEvent) => keep(((ev.clientX - box.left) / box.width) * 100);
    const up = () => {
      el.removeEventListener("pointermove", move);
      el.removeEventListener("pointerup", up);
      el.removeEventListener("pointercancel", up);
    };
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerup", up);
    el.addEventListener("pointercancel", up);
  };
  const key = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      e.preventDefault();
      keep(pct + (e.key === "ArrowLeft" ? -2 : 2));
    }
  };
  return { pct, start, key, reset: () => keep(fallback) };
}

/** 부모 grid 의 열 — 왼쪽 var(--qs) · 막대 12px · 오른쪽 나머지(넓은 화면에서만) */
export const SPLIT_GRID = "grid items-start gap-4 lg:grid-cols-[minmax(0,var(--qs))_12px_minmax(0,1fr)] lg:gap-x-1.5";
export const splitStyle = (s: Split) => ({ "--qs": `${s.pct}%` }) as React.CSSProperties;

/** 폭 조절 막대 — 끌어서 조절, 두 번 누르면 기본 폭, 화살표 키로도 */
export function SplitBar({ split, label }: { split: Split; label: string }) {
  return (
    <div role="separator" aria-orientation="vertical" aria-label={label} aria-valuenow={Math.round(split.pct)}
         aria-valuemin={SPLIT_MIN} aria-valuemax={SPLIT_MAX} tabIndex={0} title="끌어서 폭 조절 · 두 번 누르면 기본 폭"
         onPointerDown={split.start} onDoubleClick={split.reset} onKeyDown={split.key}
         className="group hidden cursor-col-resize touch-none select-none items-center justify-center outline-none lg:sticky lg:top-4 lg:flex lg:h-[calc(100svh-2rem)]">
      <span className="h-16 w-1.5 rounded-full bg-border transition-colors group-hover:bg-accent group-focus-visible:bg-accent group-active:bg-accent" />
    </div>
  );
}
