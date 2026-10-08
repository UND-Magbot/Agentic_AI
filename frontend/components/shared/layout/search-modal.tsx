"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import type { Conversation, DomainKey, DomainMeta } from "@/lib/shared/types";
import { cn, formatRelativeTime } from "@/lib/shared/utils";
import { Icon } from "@/components/shared/ui/icon";

type Props = {
  open: boolean;
  onClose: () => void;
  recent: Conversation[];
  domainMap: Record<DomainKey, DomainMeta>;
};

type ConversationDTO = {
  id: number;
  title: string;
  domain_key: DomainKey;
  updated_at: string;
  preview?: string | null;
};

/** 매치된 부분에 <mark> 강조. 케이스 무관, 첫 1회만. */
function highlightMatch(text: string, query: string) {
  const q = query.trim();
  if (!q) return text;
  const idx = text.toLowerCase().indexOf(q.toLowerCase());
  if (idx < 0) return text;
  return (
    <>
      {text.slice(0, idx)}
      <mark className="rounded-sm bg-yellow-200 px-0.5 text-foreground dark:bg-yellow-500/30">
        {text.slice(idx, idx + q.length)}
      </mark>
      {text.slice(idx + q.length)}
    </>
  );
}

/**
 * 검색 모달.
 *  - 빈 입력: 사이드바 layout 이 전달한 최근 항목(`recent`) 그대로 노출.
 *  - 입력값 있음: 300ms 디바운스 후 `/api/conversations?q=...` 호출.
 *    - 백엔드는 conversation.title + message.content ILIKE 매치를 모두 본다.
 *    - 권한 가드는 백엔드(_load_owned/list_conversations)가 이미 처리.
 */
export function SearchModal({ open, onClose, recent, domainMap }: Props) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Conversation[]>([]);
  const [searching, setSearching] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  // 열릴 때 입력 포커스 + 쿼리 초기화. 닫힐 때도 다음 오픈을 깨끗하게 하기 위해 초기화.
  useEffect(() => {
    if (open) {
      setQuery("");
      setResults([]);
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  // ESC 닫기 + 배경 스크롤 잠금.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [open, onClose]);

  // 입력 변경 → 300ms 디바운스 → 서버 검색. 입력 비어있으면 결과 즉시 비움.
  useEffect(() => {
    const q = query.trim();
    if (!q) {
      setResults([]);
      setSearching(false);
      return;
    }
    setSearching(true);
    const ac = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const r = await fetch(
          `/api/conversations?q=${encodeURIComponent(q)}&limit=50`,
          { cache: "no-store", signal: ac.signal },
        );
        if (!r.ok) {
          setResults([]);
          return;
        }
        const items = (await r.json()) as ConversationDTO[];
        setResults(
          (items ?? []).map((it) => ({
            id: String(it.id),
            domain: it.domain_key,
            title: it.title,
            updatedAt: it.updated_at,
            preview: it.preview ?? undefined,
          })),
        );
      } catch (err) {
        if ((err as { name?: string })?.name !== "AbortError") setResults([]);
      } finally {
        setSearching(false);
      }
    }, 300);
    return () => {
      clearTimeout(timer);
      ac.abort();
    };
  }, [query]);

  const queryActive = query.trim().length > 0;
  const filtered = queryActive ? results : recent;

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="채팅 및 프로젝트 검색"
      className="fixed inset-0 z-50 flex items-start justify-center bg-foreground/30 px-4 pt-[12vh] backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="flex w-full max-w-xl flex-col overflow-hidden rounded-2xl border border-border bg-surface-raised text-foreground shadow-[0_30px_70px_-28px_rgba(15,30,70,0.6)]"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 입력 영역 */}
        <div className="flex items-center gap-3 border-b border-foreground/10 px-4 py-3">
          <Icon name="search" className="h-4 w-4 text-foreground-subtle" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="채팅 및 프로젝트 검색"
            aria-label="채팅 및 프로젝트 검색"
            className="flex-1 bg-transparent text-[14px] text-foreground placeholder:text-foreground-subtle/70 focus:outline-none"
          />
          <button
            type="button"
            onClick={onClose}
            aria-label="닫기 (ESC)"
            title="닫기 (ESC)"
            className="grid h-7 w-7 place-items-center rounded-md text-foreground-subtle hover:bg-foreground/10 hover:text-foreground"
          >
            <span className="text-[15px] leading-none">×</span>
          </button>
        </div>

        {/* 결과 영역 */}
        <div className="scroll-thin max-h-[60vh] overflow-y-auto">
          <div className="font-sans px-4 pt-3 text-[12px] font-semibold tracking-tight text-foreground-subtle">
            {queryActive
              ? searching
                ? "검색 중…"
                : `검색 결과 · ${filtered.length}건`
              : "최근 항목"}
          </div>

          {filtered.length === 0 ? (
            <div className="px-4 py-6 text-[13px] text-foreground-subtle">
              {queryActive
                ? searching
                  ? "검색 중…"
                  : "일치하는 채팅이 없습니다."
                : "최근 채팅이 없습니다."}
            </div>
          ) : (
            <ul className="space-y-1 px-2 py-2">
              {filtered.map((c) => {
                const d = domainMap[c.domain];
                return (
                  <li key={c.id}>
                    <Link
                      href={`/?c=${c.id}`}
                      onClick={onClose}
                      className="group relative flex flex-col gap-1 overflow-hidden rounded-lg px-4 py-2.5 ring-1 ring-transparent hover:bg-foreground/5 hover:ring-foreground/10"
                    >
                      <span
                        className={cn("absolute inset-y-1 left-1.5 w-0.5 rounded-full", d.accent.bg)}
                        aria-hidden
                      />
                      <span className="truncate pl-2 text-[13px] font-medium">
                        {queryActive ? highlightMatch(c.title, query) : c.title}
                      </span>
                      {/* 메시지 본문 매치 발췌 — 백엔드가 매치 위치 ±50자 잘라서 보내줌. */}
                      {queryActive && c.preview && (
                        <span className="line-clamp-2 pl-2 text-[12px] leading-snug text-foreground-subtle">
                          {highlightMatch(c.preview, query)}
                        </span>
                      )}
                      <span className="flex items-center justify-between gap-2 pl-2">
                        <span
                          className={cn(
                            "inline-flex items-center gap-1 rounded-sm px-1.5 py-0.5 text-[10px] font-medium ring-1 ring-inset",
                            d.accent.soft,
                            d.accent.softText,
                            d.accent.ring,
                          )}
                        >
                          <Icon name={d.icon} className="h-2.5 w-2.5" />
                          {d.shortLabel}
                        </span>
                        <span className="text-[10px] text-foreground-subtle">
                          {formatRelativeTime(c.updatedAt)}
                        </span>
                      </span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        {/* 하단 힌트 */}
        <div className="flex items-center justify-end gap-3 border-t border-foreground/10 bg-foreground/[0.02] px-4 py-2 text-[11px] text-foreground-subtle">
          <span>
            <kbd className="mr-1 rounded border border-foreground/15 bg-surface-raised px-1.5 py-0.5 text-[10px] text-foreground/80">Esc</kbd>
            닫기
          </span>
        </div>
      </div>
    </div>
  );
}
