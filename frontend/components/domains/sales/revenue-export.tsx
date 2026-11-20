"use client";

// [매출 내역 엑셀](사용자 2026-11-20, 재무 연계) — 잔금 세금계산서 발행일 기준. 연도별·분기별·월별로 고르고 내려받는다.
// 엑셀: 건마다 발행일·고객사·견적서 번호 | 품목(여러 줄)·수량·금액 | 건 공급가액·부가세·합계, 월마다 소계·총계, 요약 시트.

import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/shared/utils";

type Unit = "year" | "quarter" | "month";
const UNIT_LABEL: Record<Unit, string> = { year: "연도별", quarter: "분기별", month: "월별" };
const SELECT = "h-8 rounded-lg border border-border bg-surface px-2 text-[12.5px] outline-none focus:border-accent";

export function RevenueExportButton() {
  const now = new Date();
  const [open, setOpen] = useState(false);
  const [unit, setUnit] = useState<Unit>("month");
  const [year, setYear] = useState(now.getFullYear());
  const [quarter, setQuarter] = useState(Math.floor(now.getMonth() / 3) + 1);
  const [month, setMonth] = useState(now.getMonth() + 1);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const off = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", off);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", off); document.removeEventListener("keydown", esc); };
  }, [open]);
  const value = unit === "quarter" ? quarter : unit === "month" ? month : null;
  const href = `/api/sales-deals/revenue-export?year=${year}&unit=${unit}${value ? `&value=${value}` : ""}`;
  const label = unit === "year" ? `${year}년` : unit === "quarter" ? `${year}년 ${quarter}분기` : `${year}년 ${month}월`;
  return (
    <div ref={box} className="relative">
      <button type="button" aria-expanded={open} onClick={() => setOpen((v) => !v)}
              className="flex h-9 items-center rounded-lg border border-border px-4 text-[13px] font-semibold hover:bg-foreground/5">
        매출 내역 엑셀
      </button>
      {open && (
        <div role="dialog" aria-label="매출 내역 엑셀"
             className="absolute left-0 top-11 z-30 w-[300px] rounded-xl border border-border bg-surface p-3 text-[12.5px] shadow-xl">
          <p className="mb-2 text-[11.5px] text-foreground-muted">잔금 세금계산서 발행일 기준 · 공급가액(부가세·합계 함께). 건마다 고객사·품목·수량.</p>
          <div role="radiogroup" aria-label="범위" className="mb-2 inline-flex rounded-lg border border-border p-0.5">
            {(Object.keys(UNIT_LABEL) as Unit[]).map((u) => (
              <button key={u} type="button" role="radio" aria-checked={unit === u} onClick={() => setUnit(u)}
                      className={cn("rounded-md px-2.5 py-1 font-semibold", unit === u ? "bg-accent text-white" : "text-foreground-muted hover:text-foreground")}>
                {UNIT_LABEL[u]}</button>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <select className={SELECT} value={year} aria-label="연도" onChange={(e) => setYear(Number(e.target.value))}>
              {[0, 1, 2, 3].map((k) => now.getFullYear() - k).map((y) => <option key={y} value={y}>{y}년</option>)}
            </select>
            {unit === "quarter" && (
              <select className={SELECT} value={quarter} aria-label="분기" onChange={(e) => setQuarter(Number(e.target.value))}>
                {[1, 2, 3, 4].map((q) => <option key={q} value={q}>{q}분기</option>)}
              </select>
            )}
            {unit === "month" && (
              <select className={SELECT} value={month} aria-label="월" onChange={(e) => setMonth(Number(e.target.value))}>
                {Array.from({ length: 12 }, (_, i) => i + 1).map((m) => <option key={m} value={m}>{m}월</option>)}
              </select>
            )}
          </div>
          <a href={href} download onClick={() => setOpen(false)}
             className="mt-3 flex h-9 items-center justify-center rounded-lg bg-accent text-[13px] font-semibold text-white hover:opacity-90">
            {label} 내려받기</a>
        </div>
      )}
    </div>
  );
}
