// 제안서 화면 위쪽 탭 — [제안서 만들기] / [AI 학습 내용 관리](사용자 2026-10-01: 첫 화면 가운데 끼어 있던
// '승인 대기 회사 지식'을 회사 제품 추천과 같은 탭 방식으로). 주소로 나뉜 두 화면을 같은 탭 줄로 잇는다.
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { cn } from "@/lib/shared/utils";

const TABS = [
  { href: "/proposals/new", label: "제안서 만들기" },
  { href: "/proposals/knowledge", label: "AI 학습 내용 관리" },
] as const;

export function ProposalTabs() {
  const path = usePathname();
  // 승인 대기 수(영업 관리자만 — 그 외는 backend 가 403 이라 숫자를 안 붙인다)
  const [pending, setPending] = useState<number | null>(null);
  useEffect(() => {
    let alive = true;
    fetch("/api/knowledge-reviews", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (alive && Array.isArray(d)) setPending(d.length); })
      .catch(() => {});
    return () => { alive = false; };
  }, []);
  return (
    <div className="shrink-0 border-b border-border bg-background">
      <div role="tablist" aria-label="제안서 화면" className="mx-auto flex max-w-6xl gap-1 px-4 pt-3 md:px-6">
        {TABS.map((t) => {
          const on = path?.startsWith(t.href);
          return (
            <Link key={t.href} href={t.href} role="tab" aria-selected={on}
                  className={cn("-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-[13.5px] font-semibold transition-colors",
                    on ? "border-accent text-accent" : "border-transparent text-foreground-muted hover:text-foreground")}>
              {t.label}
              {t.href === "/proposals/knowledge" && pending !== null && (
                <span className={cn("ml-1.5 rounded-full px-1.5 py-0.5 text-[11px] font-medium",
                  on ? "bg-accent/15 text-accent" : pending > 0 ? "bg-amber-500/15 text-amber-700 dark:text-amber-300" : "bg-foreground/8 text-foreground-subtle")}>
                  {pending}
                </span>
              )}
            </Link>
          );
        })}
      </div>
    </div>
  );
}
