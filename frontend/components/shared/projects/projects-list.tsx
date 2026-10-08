"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { Icon } from "@/components/shared/ui/icon";
import { NewProjectModal } from "@/components/shared/projects/new-project-modal";
import { NEUTRAL_ACCENT } from "@/lib/shared/neutral";
import type { ProjectSortBy, ProjectSummaryDTO } from "@/lib/shared/projects";
import type { DomainKey, DomainMeta } from "@/lib/shared/types";
import { cn, formatRelativeTime } from "@/lib/shared/utils";

type Props = {
  initial: ProjectSummaryDTO[];
  domains: DomainMeta[];
};

const SORT_OPTIONS: { value: ProjectSortBy; label: string }[] = [
  { value: "activity", label: "최근 활동순" },
  { value: "name", label: "이름순" },
  { value: "created", label: "생성일순" },
];

export function ProjectsList({ initial, domains }: Props) {
  const domainMap = useMemo(
    () =>
      Object.fromEntries(domains.map((d) => [d.key, d])) as Record<DomainKey, DomainMeta>,
    [domains],
  );

  const [items, setItems] = useState<ProjectSummaryDTO[]>(initial);
  const [query, setQuery] = useState("");
  const [sortBy, setSortBy] = useState<ProjectSortBy>("activity");
  const [creating, setCreating] = useState(false);
  const [loading, setLoading] = useState(false);

  // 정렬 변경 → 서버 재조회. 검색은 클라이언트 측 in-memory 필터로 즉응.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      setLoading(true);
      try {
        const params = new URLSearchParams();
        params.set("sort_by", sortBy);
        params.set("limit", "200");
        const r = await fetch(`/api/projects?${params.toString()}`, { cache: "no-store" });
        if (!r.ok) return;
        const data = (await r.json()) as ProjectSummaryDTO[];
        if (!cancelled && Array.isArray(data)) setItems(data);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sortBy]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter(
      (p) =>
        p.name.toLowerCase().includes(q) ||
        (p.description ?? "").toLowerCase().includes(q),
    );
  }, [items, query]);

  function handleCreated(p: ProjectSummaryDTO) {
    setItems((prev) => [p, ...prev]);
    setCreating(false);
  }

  return (
    <div className="scroll-thin flex h-full flex-col overflow-y-auto">
      <div className="mx-auto flex w-full max-w-5xl flex-col px-6 pb-12">
        {/* 페이지 제목 + 새 프로젝트 버튼 */}
        <div className="mt-12 mb-6 flex items-center justify-between gap-4">
          <h1 className="text-[32px] font-semibold tracking-tight text-foreground">
            프로젝트
          </h1>
          <button
            type="button"
            onClick={() => setCreating(true)}
            className="und-grad inline-flex items-center gap-2 rounded-xl px-4 py-2.5 text-[13px] font-semibold text-white shadow-[0_8px_20px_-8px_rgba(37,99,235,0.6),inset_0_1px_0_rgba(255,255,255,0.35)] transition hover:brightness-105"
          >
            <Icon name="plus" className="h-4 w-4" />
            <span>새 프로젝트</span>
          </button>
        </div>

        {/* 검색 입력 */}
        <div className="relative">
          <span className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-foreground-subtle">
            <Icon name="search" className="h-4 w-4" />
          </span>
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="프로젝트 검색..."
            aria-label="프로젝트 검색"
            className="w-full rounded-xl border border-border bg-surface-raised py-3 pl-11 pr-10 text-[14px] text-foreground placeholder:text-foreground-subtle/70 focus:outline-none focus:border-accent/40 focus:ring-2 focus:ring-accent-soft"
          />
          {query && (
            <button
              type="button"
              onClick={() => setQuery("")}
              aria-label="검색어 지우기"
              className="absolute right-3 top-1/2 grid h-6 w-6 -translate-y-1/2 place-items-center rounded-sm text-foreground-subtle hover:bg-foreground/10 hover:text-foreground"
            >
              <span className="text-[15px] leading-none">×</span>
            </button>
          )}
        </div>

        {/* 정렬 셀렉트 */}
        <div className="mt-5 flex items-center justify-end gap-2 text-[13px]">
          <span className="text-foreground-subtle">정렬</span>
          <div className="relative">
            <select
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value as ProjectSortBy)}
              aria-label="정렬 기준"
              className="appearance-none rounded-md border border-foreground/15 bg-surface-raised py-1.5 pl-3 pr-8 text-foreground focus:outline-none focus:ring-1 focus:ring-foreground/25"
            >
              {SORT_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            <span className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-foreground-subtle">
              <Icon name="chevron-down" className="h-3.5 w-3.5" />
            </span>
          </div>
        </div>

        {/* 카드 그리드 */}
        <div className="mt-6">
          {filtered.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-border bg-surface-raised/50 py-16 text-center text-[13px] text-foreground-subtle">
              {query.trim()
                ? "일치하는 프로젝트가 없습니다."
                : loading
                  ? "프로젝트 목록을 불러오는 중..."
                  : "아직 프로젝트가 없습니다. 새 프로젝트를 만들어 시작하세요."}
            </div>
          ) : (
            <ul className="grid grid-cols-1 gap-4 md:grid-cols-2">
              {filtered.map((p) => {
                const accent = p.domain_key ? domainMap[p.domain_key]?.accent : undefined;
                const a = accent ?? NEUTRAL_ACCENT;
                return (
                  <li key={p.id}>
                    <Link
                      href={`/projects/${p.id}`}
                      className="group relative block overflow-hidden rounded-2xl border border-border bg-surface-raised p-5 transition hover:-translate-y-0.5 hover:border-accent/30 hover:shadow-[0_18px_40px_-22px_rgba(15,30,70,0.45)]"
                    >
                      <span
                        className={cn("absolute inset-y-3 left-0 w-[3px] rounded-full", a.bg)}
                        aria-hidden
                      />
                      <div className="flex items-start justify-between gap-2 pl-2">
                        <h3 className="min-w-0 truncate text-[16px] font-semibold tracking-tight text-foreground">
                          {p.name}
                        </h3>
                        {p.starred && (
                          <Icon
                            name="star"
                            className="h-4 w-4 shrink-0 text-amber-500"
                          />
                        )}
                      </div>
                      {p.description && (
                        <p className="mt-2 line-clamp-2 pl-2 text-[13px] text-foreground-muted">
                          {p.description}
                        </p>
                      )}
                      <div className="mt-5 flex items-center justify-between gap-2 pl-2 text-[11px] text-foreground-subtle">
                        <span>{formatRelativeTime(p.updated_at)} 활동</span>
                        <span className="inline-flex items-center gap-1">
                          <Icon name="chat" className="h-3 w-3" />
                          {p.conversation_count}개 대화
                        </span>
                      </div>
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>

      <NewProjectModal
        open={creating}
        onClose={() => setCreating(false)}
        domains={domains}
        onCreated={handleCreated}
      />
    </div>
  );
}
