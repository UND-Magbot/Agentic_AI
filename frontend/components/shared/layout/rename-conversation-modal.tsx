"use client";

import { useEffect, useRef, useState } from "react";

import { Icon } from "@/components/shared/ui/icon";

type Props = {
  open: boolean;
  conversationId: string;
  initialTitle: string;
  onClose: () => void;
  onRenamed: (newTitle: string) => void;
};

/** 대화 이름 변경 모달. */
export function RenameConversationModal({
  open,
  conversationId,
  initialTitle,
  onClose,
  onRenamed,
}: Props) {
  const [value, setValue] = useState(initialTitle);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ref = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    setValue(initialTitle);
    setError(null);
    setSubmitting(false);
    const t = setTimeout(() => {
      ref.current?.focus();
      ref.current?.select();
    }, 50);
    return () => clearTimeout(t);
  }, [open, initialTitle]);

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
    const trimmed = value.trim();
    if (!trimmed) {
      setError("이름을 입력하세요.");
      return;
    }
    if (trimmed === initialTitle) {
      onClose();
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const r = await fetch(`/api/conversations/${encodeURIComponent(conversationId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: trimmed }),
      });
      if (!r.ok) {
        const data = await r.json().catch(() => ({}));
        throw new Error(data?.detail ?? `요청 실패 (${r.status})`);
      }
      onRenamed(trimmed);
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
      aria-labelledby="rename-conv-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/30 p-4 backdrop-blur-sm"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <form
        onSubmit={submit}
        className="anim-fade-in-up w-full max-w-md rounded-2xl border border-border bg-surface-raised p-6 shadow-[0_30px_70px_-30px_rgba(15,30,70,0.55)]"
      >
        <div className="flex items-center justify-between">
          <h2 id="rename-conv-title" className="text-[16px] font-semibold tracking-tight text-foreground">
            대화 이름 변경
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
        <input
          ref={ref}
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          maxLength={200}
          placeholder="대화 이름"
          className="mt-4 w-full rounded-md border border-foreground/15 bg-background px-3 py-2 text-[14px] text-foreground placeholder:text-foreground-subtle/70 focus:outline-none focus:ring-1 focus:ring-foreground/25"
        />
        {error && (
          <div className="mt-2 rounded-md border border-red-500/30 bg-red-500/5 px-3 py-2 text-[12px] text-red-600 dark:text-red-400">
            {error}
          </div>
        )}
        <div className="mt-5 flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="rounded-md px-3 py-1.5 text-[13px] text-foreground hover:bg-foreground/5 disabled:opacity-50"
          >
            취소
          </button>
          <button
            type="submit"
            disabled={submitting || !value.trim()}
            className="und-grad inline-flex items-center gap-2 rounded-lg px-4 py-2 text-[13px] font-semibold text-white shadow-[0_8px_18px_-8px_rgba(37,99,235,0.6),inset_0_1px_0_rgba(255,255,255,0.35)] transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {submitting && <Icon name="refresh" className="h-3.5 w-3.5 animate-spin" />}
            <span>{submitting ? "저장 중..." : "저장"}</span>
          </button>
        </div>
      </form>
    </div>
  );
}
