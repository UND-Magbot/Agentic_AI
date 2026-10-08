"use client";

import { useEffect, useState } from "react";

import { Icon } from "@/components/shared/ui/icon";
import type { ProjectSummaryDTO } from "@/lib/shared/projects";
import { cn } from "@/lib/shared/utils";

type Props = {
  open: boolean;
  conversationId: string;
  conversationTitle: string;
  onClose: () => void;
  onAdded: (project: { id: string; name: string }) => void;
};

/**
 * 대화를 프로젝트에 추가하는 모달.
 *  - 마운트 시 GET /api/projects 로 본인 소유 프로젝트 목록을 받아 목록 렌더.
 *  - 항목 클릭 → POST /api/projects/{pid}/conversations { conversation_id }.
 */
export function AddToProjectModal({
  open,
  conversationId,
  conversationTitle,
  onClose,
  onAdded,
}: Props) {
  const [projects, setProjects] = useState<ProjectSummaryDTO[]>([]);
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    if (!open) return;
    setError(null);
    setQuery("");
    setBusyId(null);
    setLoading(true);
    let cancelled = false;
    void (async () => {
      try {
        const r = await fetch("/api/projects?sort_by=activity&limit=200", { cache: "no-store" });
        if (!r.ok) throw new Error(`목록 로드 실패 (${r.status})`);
        const data = (await r.json()) as ProjectSummaryDTO[];
        if (!cancelled) setProjects(Array.isArray(data) ? data : []);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  async function add(p: ProjectSummaryDTO) {
    if (busyId) return;
    setBusyId(String(p.id));
    setError(null);
    try {
      const r = await fetch(`/api/projects/${encodeURIComponent(String(p.id))}/conversations`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversation_id: Number(conversationId) }),
      });
      if (!r.ok) {
        const data = await r.json().catch(() => ({}));
        throw new Error(data?.detail ?? `추가 실패 (${r.status})`);
      }
      onAdded({ id: String(p.id), name: p.name });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId(null);
    }
  }

  const filtered = query.trim()
    ? projects.filter((p) => p.name.toLowerCase().includes(query.trim().toLowerCase()))
    : projects;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="add-to-project-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/30 p-4 backdrop-blur-sm"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="anim-fade-in-up w-full max-w-md rounded-2xl border border-border bg-surface-raised p-6 shadow-[0_30px_70px_-30px_rgba(15,30,70,0.55)]">
        <div className="flex items-center justify-between">
          <h2
            id="add-to-project-title"
            className="text-[16px] font-semibold tracking-tight text-foreground"
          >
            프로젝트에 추가
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="닫기"
            className="grid h-7 w-7 place-items-center rounded-md text-foreground-subtle hover:bg-foreground/5 hover:text-foreground"
          >
            <span className="text-[16px] leading-none">×</span>
          </button>
        </div>
        <p className="mt-1.5 truncate text-[13px] text-foreground-subtle">
          <span className="font-medium text-foreground/80">{conversationTitle}</span>
        </p>

        <div className="relative mt-4">
          <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-foreground-subtle">
            <Icon name="search" className="h-4 w-4" />
          </span>
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="프로젝트 검색..."
            className="w-full rounded-md border border-foreground/15 bg-background py-2 pl-9 pr-3 text-[13px] text-foreground placeholder:text-foreground-subtle/70 focus:outline-none focus:ring-1 focus:ring-foreground/25"
          />
        </div>

        <div className="mt-3 max-h-[320px] overflow-y-auto rounded-md border border-foreground/10 bg-background/40">
          {loading ? (
            <div className="flex items-center justify-center gap-2 px-4 py-10 text-[13px] text-foreground-subtle">
              <Icon name="refresh" className="h-3.5 w-3.5 animate-spin" />
              불러오는 중...
            </div>
          ) : filtered.length === 0 ? (
            <div className="px-4 py-10 text-center text-[13px] text-foreground-subtle">
              {query.trim() ? "일치하는 프로젝트가 없습니다." : "프로젝트가 아직 없습니다."}
            </div>
          ) : (
            <ul className="flex flex-col gap-0.5 p-1">
              {filtered.map((p) => {
                const isBusy = busyId === String(p.id);
                return (
                  <li key={p.id}>
                    <button
                      type="button"
                      onClick={() => add(p)}
                      disabled={busyId !== null}
                      className={cn(
                        "flex w-full items-center gap-2 rounded-sm px-2.5 py-2 text-left text-[13px] transition",
                        "hover:bg-foreground/5 disabled:opacity-50",
                      )}
                    >
                      <Icon name="folder" className="h-4 w-4 text-foreground-subtle" />
                      <span className="min-w-0 flex-1 truncate text-foreground">{p.name}</span>
                      {isBusy ? (
                        <Icon name="refresh" className="h-3.5 w-3.5 shrink-0 animate-spin text-foreground-subtle" />
                      ) : (
                        <span className="shrink-0 text-[10px] text-foreground-subtle">
                          {p.conversation_count}개 대화
                        </span>
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        {error && (
          <div className="mt-3 rounded-md border border-red-500/30 bg-red-500/5 px-3 py-2 text-[12px] text-red-600 dark:text-red-400">
            {error}
          </div>
        )}
      </div>
    </div>
  );
}
