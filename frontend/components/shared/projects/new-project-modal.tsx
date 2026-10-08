"use client";

import { useEffect, useRef, useState } from "react";

import { Icon } from "@/components/shared/ui/icon";
import type { ProjectSummaryDTO } from "@/lib/shared/projects";
import type { DomainKey, DomainMeta } from "@/lib/shared/types";
import { cn } from "@/lib/shared/utils";

type Props = {
  open: boolean;
  onClose: () => void;
  onCreated: (project: ProjectSummaryDTO) => void;
  domains: DomainMeta[];
};

/**
 * 새 프로젝트 생성 모달.
 * - 이름은 필수, 그 외(설명/Instructions/도메인)는 선택.
 * - 생성 성공 시 onCreated 로 전체 detail(목록 갱신용)을 부모에 전달.
 */
export function NewProjectModal({ open, onClose, onCreated, domains }: Props) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [domain, setDomain] = useState<DomainKey | "">("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    setName("");
    setDescription("");
    setSystemPrompt("");
    setDomain("");
    setError(null);
    // 다음 tick에 포커스 — transition 완료 후 자연스럽게.
    const t = setTimeout(() => inputRef.current?.focus(), 50);
    return () => clearTimeout(t);
  }, [open]);

  // ESC 닫기.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting) return;
    const trimmed = name.trim();
    if (!trimmed) {
      setError("프로젝트 이름을 입력하세요.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const r = await fetch("/api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: trimmed,
          description: description.trim() || null,
          system_prompt: systemPrompt.trim() || null,
          domain_key: domain || null,
        }),
      });
      if (!r.ok) {
        const data = await r.json().catch(() => ({}));
        throw new Error(data?.detail ?? `요청 실패 (${r.status})`);
      }
      const created = (await r.json()) as ProjectSummaryDTO;
      onCreated(created);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="new-project-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/30 p-4 backdrop-blur-sm"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <form
        onSubmit={submit}
        className="anim-fade-in-up w-full max-w-lg rounded-2xl border border-border bg-surface-raised p-6 shadow-[0_30px_70px_-30px_rgba(15,30,70,0.55)]"
      >
        <div className="flex items-center justify-between">
          <h2
            id="new-project-title"
            className="text-[18px] font-semibold tracking-tight text-foreground"
          >
            새 프로젝트
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="닫기"
            className="grid h-8 w-8 place-items-center rounded-md text-foreground-subtle hover:bg-foreground/5 hover:text-foreground"
          >
            <span className="text-[18px] leading-none">×</span>
          </button>
        </div>

        <p className="mt-1.5 text-[13px] text-foreground-subtle">
          관련된 대화를 한 곳에 묶고, 그룹 단위 Instructions(시스템 프롬프트)로 톤을 고정할 수 있습니다.
        </p>

        <div className="mt-5 flex flex-col gap-4">
          <label className="flex flex-col gap-1.5">
            <span className="text-[12px] font-medium text-foreground">이름 *</span>
            <input
              ref={inputRef}
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="예: Q2 영업 캠페인"
              maxLength={120}
              className="rounded-md border border-foreground/15 bg-background px-3 py-2 text-[14px] text-foreground placeholder:text-foreground-subtle/70 focus:outline-none focus:ring-1 focus:ring-foreground/25"
            />
          </label>

          <label className="flex flex-col gap-1.5">
            <span className="text-[12px] font-medium text-foreground">설명 (선택)</span>
            <input
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="이 프로젝트가 다루는 일을 한 줄로"
              maxLength={500}
              className="rounded-md border border-foreground/15 bg-background px-3 py-2 text-[14px] text-foreground placeholder:text-foreground-subtle/70 focus:outline-none focus:ring-1 focus:ring-foreground/25"
            />
          </label>

          <label className="flex flex-col gap-1.5">
            <span className="text-[12px] font-medium text-foreground">
              Instructions (선택)
            </span>
            <textarea
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              rows={4}
              placeholder="이 프로젝트 안의 모든 대화에 적용할 톤·규칙·스타일 가이드"
              className="resize-y rounded-md border border-foreground/15 bg-background px-3 py-2 text-[13px] text-foreground placeholder:text-foreground-subtle/70 focus:outline-none focus:ring-1 focus:ring-foreground/25"
            />
          </label>

          <div className="flex flex-col gap-1.5">
            <span className="text-[12px] font-medium text-foreground">
              권장 도메인 (선택)
            </span>
            <div className="flex flex-wrap gap-2">
              <DomainPill
                active={domain === ""}
                onClick={() => setDomain("")}
                label="없음"
              />
              {domains.map((d) => (
                <DomainPill
                  key={d.key}
                  active={domain === d.key}
                  onClick={() => setDomain(d.key)}
                  label={d.shortLabel}
                  dotClass={d.accent.dot}
                />
              ))}
            </div>
          </div>

          {error && (
            <div className="rounded-md border border-red-500/30 bg-red-500/5 px-3 py-2 text-[12px] text-red-600 dark:text-red-400">
              {error}
            </div>
          )}
        </div>

        <div className="mt-6 flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="rounded-md px-4 py-2 text-[13px] text-foreground hover:bg-foreground/5 disabled:opacity-50"
          >
            취소
          </button>
          <button
            type="submit"
            disabled={submitting || !name.trim()}
            className="und-grad inline-flex items-center gap-2 rounded-xl px-4 py-2 text-[13px] font-semibold text-white shadow-[0_8px_18px_-8px_rgba(37,99,235,0.6),inset_0_1px_0_rgba(255,255,255,0.35)] transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {submitting ? (
              <>
                <Icon name="refresh" className="h-3.5 w-3.5 animate-spin" />
                <span>생성 중...</span>
              </>
            ) : (
              <>
                <Icon name="plus" className="h-4 w-4" />
                <span>만들기</span>
              </>
            )}
          </button>
        </div>
      </form>
    </div>
  );
}

function DomainPill({
  active,
  onClick,
  label,
  dotClass,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  dotClass?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-[12px] transition",
        active
          ? "border-foreground/40 bg-foreground/5 text-foreground"
          : "border-foreground/15 bg-background text-foreground-muted hover:bg-foreground/5",
      )}
    >
      {dotClass && <span className={cn("h-1.5 w-1.5 rounded-full", dotClass)} />}
      <span>{label}</span>
    </button>
  );
}
