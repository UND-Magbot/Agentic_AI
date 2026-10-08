"use client";

import { useEffect, useRef, useState } from "react";

import { Icon } from "@/components/shared/ui/icon";

type Props = {
  open: boolean;
  initial: string;
  projectName: string;
  onClose: () => void;
  onSave: (next: string) => void | Promise<void>;
};

/** Instructions(시스템 프롬프트) 편집 모달. */
export function InstructionsModal({ open, initial, projectName, onClose, onSave }: Props) {
  const [value, setValue] = useState(initial);
  const [saving, setSaving] = useState(false);
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!open) return;
    setValue(initial);
    setSaving(false);
    const t = setTimeout(() => ref.current?.focus(), 50);
    return () => clearTimeout(t);
  }, [open, initial]);

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
    if (saving) return;
    setSaving(true);
    try {
      await onSave(value);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="instructions-modal-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/30 p-4 backdrop-blur-sm"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <form
        onSubmit={submit}
        className="anim-fade-in-up w-full max-w-2xl rounded-2xl border border-border bg-surface-raised p-6 shadow-[0_30px_70px_-30px_rgba(15,30,70,0.55)]"
      >
        <div className="flex items-center justify-between">
          <h2
            id="instructions-modal-title"
            className="text-[18px] font-semibold tracking-tight text-foreground"
          >
            Instructions 편집
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
          <span className="font-medium text-foreground/80">{projectName}</span> 안의 모든 대화 시작 시 이
          내용이 시스템 프롬프트에 부착됩니다.
        </p>

        <textarea
          ref={ref}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          rows={12}
          placeholder={"예: 당신은 사내 영업 카피라이터다.\n- 정중한 비즈니스 톤(존칭).\n- 핵심은 첫 두 문장에 배치."}
          className="mt-4 w-full resize-y rounded-md border border-foreground/15 bg-background px-3 py-2.5 text-[13px] leading-relaxed text-foreground placeholder:text-foreground-subtle/70 focus:outline-none focus:ring-1 focus:ring-foreground/25"
        />

        <div className="mt-5 flex items-center justify-between gap-2">
          <button
            type="button"
            onClick={() => setValue("")}
            disabled={saving}
            className="text-[12px] text-foreground-subtle hover:text-foreground disabled:opacity-50"
          >
            모두 지우기
          </button>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={onClose}
              disabled={saving}
              className="rounded-md px-4 py-2 text-[13px] text-foreground hover:bg-foreground/5 disabled:opacity-50"
            >
              취소
            </button>
            <button
              type="submit"
              disabled={saving}
              className="und-grad inline-flex items-center gap-2 rounded-xl px-4 py-2 text-[13px] font-semibold text-white shadow-[0_8px_18px_-8px_rgba(37,99,235,0.6),inset_0_1px_0_rgba(255,255,255,0.35)] transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {saving ? (
                <>
                  <Icon name="refresh" className="h-3.5 w-3.5 animate-spin" />
                  <span>저장 중...</span>
                </>
              ) : (
                <span>저장</span>
              )}
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}
