"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Icon } from "@/components/shared/ui/icon";
import { AddToProjectModal } from "@/components/shared/layout/add-to-project-modal";
import { RecentItemMenu } from "@/components/shared/layout/recent-item-menu";
import { RenameConversationModal } from "@/components/shared/layout/rename-conversation-modal";
import { listDomainMetas } from "@/lib/agents/registry";
import type { Conversation, DomainKey, DomainMeta } from "@/lib/shared/types";
import { cn, formatRelativeTime, groupRecentByTime } from "@/lib/shared/utils";

type ConversationSummaryDTO = {
  id: number;
  title: string;
  domain_key: DomainKey;
  starred?: boolean;
  updated_at: string;
};

type RowProps = {
  c: Conversation;
  d: DomainMeta;
  onRequestRename: () => void;
  onRequestAddToProject: () => void;
  onUpdate: (next: Conversation) => void;
  onDelete: () => void;
};

function ChatRow({ c, d, onRequestRename, onRequestAddToProject, onUpdate, onDelete }: RowProps) {
  return (
    <li className="group/row relative">
      <Link
        href={`/?c=${c.id}`}
        className="relative flex items-center gap-3 overflow-hidden rounded-xl border border-border bg-surface-raised py-3 pl-5 pr-12 transition hover:-translate-y-0.5 hover:border-accent/30 hover:shadow-[0_14px_30px_-18px_rgba(15,30,70,0.45)]"
      >
        <span className={cn("absolute inset-y-2 left-0 w-[3px] rounded-full", d.accent.bg)} aria-hidden />
        {c.starred && <Icon name="star" className="h-3.5 w-3.5 shrink-0 text-amber-500" />}
        <span className="min-w-0 flex-1 truncate text-[14px] font-medium text-foreground">
          {c.title}
        </span>
        <span
          className={cn(
            "inline-flex shrink-0 items-center gap-1 rounded-sm px-1.5 py-0.5 text-[10px] font-medium ring-1 ring-inset",
            d.accent.soft,
            d.accent.softText,
            d.accent.ring,
          )}
        >
          <Icon name={d.icon} className="h-2.5 w-2.5" />
          {d.shortLabel}
        </span>
        <span className="shrink-0 text-[11px] text-foreground-subtle">
          {formatRelativeTime(c.updatedAt)}
        </span>
      </Link>
      {/* 우측 끝 `…` 메뉴 — hover 시 노출. */}
      <div className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 opacity-0 transition group-hover/row:pointer-events-auto group-hover/row:opacity-100 focus-within:pointer-events-auto focus-within:opacity-100">
        <RecentItemMenu
          conversation={c}
          align="right"
          onRequestRename={onRequestRename}
          onRequestAddToProject={onRequestAddToProject}
          onAction={(action) => {
            if (action.type === "deleted") onDelete();
            else if (action.type === "starred") onUpdate({ ...c, starred: action.starred });
            else if (action.type === "renamed") onUpdate({ ...c, title: action.title });
          }}
        />
      </div>
    </li>
  );
}

/**
 * 채팅 목록 페이지.
 *  - 메인 영역만 전환되는 라우트(`/chats`). 사이드바는 layout 에서 그대로 유지.
 *  - 각 항목 hover 시 `…` 메뉴 → Star / Rename / Add to project / Delete.
 *  - 즐겨찾기는 항상 위로 분리, 그 아래는 시간대 그룹(오늘/어제/이번 주/이전).
 */
export default function ChatsPage() {
  const domains = listDomainMetas();
  const domainMap = Object.fromEntries(domains.map((d) => [d.key, d])) as Record<
    DomainKey,
    DomainMeta
  >;
  // 시간 의존 렌더(상대시간/그룹) → SSR mismatch 회피 위해 mount 이후에만 데이터 채움.
  const [recent, setRecent] = useState<Conversation[]>([]);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch("/api/conversations?limit=200", { cache: "no-store" });
        if (!r.ok) return;
        const items = (await r.json()) as ConversationSummaryDTO[];
        if (cancelled || !Array.isArray(items)) return;
        setRecent(
          items.map((it) => ({
            id: String(it.id),
            domain: it.domain_key,
            title: it.title,
            updatedAt: it.updated_at,
            starred: Boolean(it.starred),
          })),
        );
      } catch {
        /* 백엔드 미가용 시엔 빈 상태로 둔다. */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const [query, setQuery] = useState("");
  const [renameTarget, setRenameTarget] = useState<Conversation | null>(null);
  const [projectTarget, setProjectTarget] = useState<Conversation | null>(null);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return recent;
    return recent.filter((c) => c.title.toLowerCase().includes(q));
  }, [query, recent]);

  const starredItems = useMemo(() => filtered.filter((c) => c.starred), [filtered]);
  const unstarredItems = useMemo(() => filtered.filter((c) => !c.starred), [filtered]);

  const handleUpdate = (next: Conversation) => {
    setRecent((prev) => prev.map((p) => (p.id === next.id ? next : p)));
  };
  const handleDelete = (id: string) => {
    setRecent((prev) => prev.filter((p) => p.id !== id));
  };

  return (
    <div className="scroll-thin flex h-full flex-col overflow-y-auto">
      <div className="mx-auto flex w-full max-w-3xl flex-col px-6">
        <h1 className="mt-16 mb-8 text-[32px] font-semibold tracking-tight text-foreground">
          채팅
        </h1>

        {/* 헤더 — 검색창 + 우상단 새 채팅. 스크롤 시 상단 고정. */}
        <div className="sticky top-0 z-10 flex items-center gap-3 border-b border-foreground/10 bg-background py-4">
          <div className="relative flex-1">
            <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-foreground-subtle">
              <Icon name="search" className="h-4 w-4" />
            </span>
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="대화내용 검색"
              aria-label="대화내용 검색"
              className="w-full rounded-xl border border-border bg-surface-raised py-2.5 pl-9 pr-8 text-[14px] text-foreground placeholder:text-foreground-subtle/70 focus:outline-none focus:border-accent/40 focus:ring-2 focus:ring-accent-soft"
            />
            {query && (
              <button
                type="button"
                onClick={() => setQuery("")}
                aria-label="검색어 지우기"
                title="검색어 지우기"
                className="absolute right-2 top-1/2 grid h-5 w-5 -translate-y-1/2 place-items-center rounded-sm text-foreground-subtle hover:bg-foreground/10 hover:text-foreground"
              >
                <span className="text-[14px] leading-none">×</span>
              </button>
            )}
          </div>
          <Link
            href="/"
            onClick={(e) => {
              if (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) return;
              if (typeof window !== "undefined") {
                window.dispatchEvent(new CustomEvent("und_cortex:new_chat"));
              }
            }}
            aria-label="새 채팅"
            title="새 채팅"
            className="und-grad flex shrink-0 items-center gap-2 rounded-xl px-3.5 py-2.5 text-sm font-semibold text-white shadow-[0_8px_20px_-8px_rgba(37,99,235,0.6),inset_0_1px_0_rgba(255,255,255,0.35)] transition hover:brightness-105"
          >
            <Icon name="plus" className="h-4 w-4" />
            <span>새 채팅</span>
          </Link>
        </div>

        {/* 본문 */}
        <div className="py-5">
          <div className="mb-3 px-1 text-[11px] font-semibold uppercase tracking-wider text-foreground-subtle">
            {query.trim() ? `검색 결과 · ${filtered.length}건` : "전체 채팅"}
          </div>

          {filtered.length === 0 ? (
            <div className="px-1 py-8 text-[13px] text-foreground-subtle">
              {query.trim() ? "일치하는 채팅이 없습니다." : "아직 채팅이 없습니다."}
            </div>
          ) : query.trim() ? (
            <ul className="flex flex-col gap-1.5">
              {filtered.map((c) => (
                <ChatRow
                  key={c.id}
                  c={c}
                  d={domainMap[c.domain]}
                  onRequestRename={() => setRenameTarget(c)}
                  onRequestAddToProject={() => setProjectTarget(c)}
                  onUpdate={handleUpdate}
                  onDelete={() => handleDelete(c.id)}
                />
              ))}
            </ul>
          ) : (
            <div className="space-y-5">
              {/* 즐겨찾기 그룹 — 별도 헤더로 위로 분리. */}
              {starredItems.length > 0 && (
                <div>
                  <div className="mb-2 flex items-center gap-1 px-1 text-[11px] font-medium tracking-tight text-foreground-subtle">
                    <Icon name="star" className="h-3 w-3 text-amber-500" />
                    즐겨찾기
                  </div>
                  <ul className="flex flex-col gap-1.5">
                    {starredItems.map((c) => (
                      <ChatRow
                        key={c.id}
                        c={c}
                        d={domainMap[c.domain]}
                        onRequestRename={() => setRenameTarget(c)}
                        onRequestAddToProject={() => setProjectTarget(c)}
                        onUpdate={handleUpdate}
                        onDelete={() => handleDelete(c.id)}
                      />
                    ))}
                  </ul>
                </div>
              )}

              {/* 시간대 그룹 (즐겨찾기 외 항목들). */}
              {groupRecentByTime(unstarredItems).map((group) => (
                <div key={group.key}>
                  <div className="mb-2 px-1 text-[11px] font-medium tracking-tight text-foreground-subtle">
                    {group.label}
                  </div>
                  <ul className="flex flex-col gap-1.5">
                    {group.items.map((c) => (
                      <ChatRow
                        key={c.id}
                        c={c}
                        d={domainMap[c.domain]}
                        onRequestRename={() => setRenameTarget(c)}
                        onRequestAddToProject={() => setProjectTarget(c)}
                        onUpdate={handleUpdate}
                        onDelete={() => handleDelete(c.id)}
                      />
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* 액션 모달 */}
      {renameTarget && (
        <RenameConversationModal
          open
          conversationId={renameTarget.id}
          initialTitle={renameTarget.title}
          onClose={() => setRenameTarget(null)}
          onRenamed={(t) => {
            handleUpdate({ ...renameTarget, title: t });
            setRenameTarget(null);
          }}
        />
      )}
      {projectTarget && (
        <AddToProjectModal
          open
          conversationId={projectTarget.id}
          conversationTitle={projectTarget.title}
          onClose={() => setProjectTarget(null)}
          onAdded={() => setProjectTarget(null)}
        />
      )}
    </div>
  );
}
