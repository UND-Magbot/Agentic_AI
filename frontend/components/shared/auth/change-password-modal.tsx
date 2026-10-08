"use client";

import { useEffect, useRef, useState } from "react";
import { Icon } from "@/components/shared/ui/icon";
import { cn } from "@/lib/shared/utils";

type Props = {
  open: boolean;
  onClose: () => void;
};

export function ChangePasswordModal({ open, onClose }: Props) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [showCurrent, setShowCurrent] = useState(false);
  const [showNext, setShowNext] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const firstFieldRef = useRef<HTMLInputElement>(null);

  // 모달이 열릴 때 상태 초기화 + 첫 입력 포커스. 닫힐 때도 정리.
  useEffect(() => {
    if (open) {
      setCurrent("");
      setNext("");
      setConfirm("");
      setError(null);
      setSuccess(false);
      setSubmitting(false);
      // 다음 틱에 포커스(렌더 후)
      requestAnimationFrame(() => firstFieldRef.current?.focus());
    }
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

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (submitting) return;
    if (!current || !next || !confirm) {
      setError("모든 항목을 입력해주세요.");
      return;
    }
    if (next.length < 8) {
      setError("새 비밀번호는 8자 이상이어야 합니다.");
      return;
    }
    if (next !== confirm) {
      setError("새 비밀번호 확인이 일치하지 않습니다.");
      return;
    }
    if (next === current) {
      setError("새 비밀번호는 현재 비밀번호와 달라야 합니다.");
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      const r = await fetch("/api/auth/change-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ current_password: current, new_password: next }),
      });
      const data = (await r.json().catch(() => ({}))) as { ok?: boolean; detail?: string };
      if (!r.ok || !data.ok) {
        setError(data.detail ?? "비밀번호 변경에 실패했습니다.");
        setSubmitting(false);
        return;
      }
      setSuccess(true);
      setSubmitting(false);
      // 2초 후 자동 닫기.
      window.setTimeout(() => onClose(), 1400);
    } catch (err) {
      setError(err instanceof Error ? err.message : "네트워크 오류가 발생했습니다.");
      setSubmitting(false);
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="cpw-title"
      className="anim-fade-in fixed inset-0 z-50 grid place-items-center bg-black/40 px-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="anim-fade-in-up w-full max-w-[420px] rounded-2xl border border-border bg-surface-raised p-6 shadow-[0_30px_70px_-30px_rgba(15,30,70,0.55)]">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 id="cpw-title" className="text-[15px] font-semibold tracking-tight text-foreground">
              비밀번호 재설정
            </h2>
            <p className="mt-1 text-[12.5px] text-foreground-muted">
              현재 비밀번호와 새 비밀번호(8자 이상)를 입력해주세요.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="닫기"
            className="grid h-7 w-7 shrink-0 place-items-center rounded-sm text-foreground-subtle hover:bg-foreground/5 hover:text-foreground"
          >
            <span aria-hidden className="text-[15px] leading-none">×</span>
          </button>
        </div>

        <form onSubmit={onSubmit} className="mt-5 flex flex-col gap-3.5" noValidate>
          <Field label="현재 비밀번호" htmlFor="cpw-current">
            <div className="relative">
              <input
                ref={firstFieldRef}
                id="cpw-current"
                type={showCurrent ? "text" : "password"}
                autoComplete="current-password"
                value={current}
                onChange={(e) => setCurrent(e.target.value)}
                disabled={submitting || success}
                className={cn(inputClass, "pr-10")}
              />
              <ToggleEye on={showCurrent} setOn={setShowCurrent} />
            </div>
          </Field>

          <Field label="새 비밀번호" htmlFor="cpw-next">
            <div className="relative">
              <input
                id="cpw-next"
                type={showNext ? "text" : "password"}
                autoComplete="new-password"
                value={next}
                onChange={(e) => setNext(e.target.value)}
                disabled={submitting || success}
                className={cn(inputClass, "pr-10")}
              />
              <ToggleEye on={showNext} setOn={setShowNext} />
            </div>
          </Field>

          <Field label="새 비밀번호 확인" htmlFor="cpw-confirm">
            <input
              id="cpw-confirm"
              type={showNext ? "text" : "password"}
              autoComplete="new-password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              disabled={submitting || success}
              className={inputClass}
            />
          </Field>

          {error && (
            <div
              role="alert"
              className="rounded-sm border border-red-500/30 bg-red-500/10 px-3 py-2 text-[12.5px] text-red-700 dark:text-red-300"
            >
              {error}
            </div>
          )}
          {success && (
            <div
              role="status"
              className="rounded-sm border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-[12.5px] text-emerald-700 dark:text-emerald-300"
            >
              비밀번호가 변경되었습니다.
            </div>
          )}

          <div className="mt-2 flex items-center justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              disabled={submitting}
              className="h-9 rounded-md px-4 text-[13px] text-foreground-muted hover:bg-foreground/5 hover:text-foreground disabled:opacity-60"
            >
              취소
            </button>
            <button
              type="submit"
              disabled={submitting || success}
              className={cn(
                "inline-flex h-10 items-center justify-center rounded-xl px-4 text-[13px] font-semibold",
                "und-grad text-white shadow-[0_8px_18px_-8px_rgba(37,99,235,0.6),inset_0_1px_0_rgba(255,255,255,0.35)]",
                "transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-60",
              )}
            >
              {submitting ? "변경 중..." : "변경하기"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

const inputClass = cn(
  "h-10 w-full rounded-md bg-surface px-3 text-[14px] text-foreground placeholder:text-foreground-subtle/70",
  "border border-border focus:border-foreground/40 focus:outline-none",
  "focus:ring-2 focus:ring-[color:var(--accent-ring)]",
  "disabled:cursor-not-allowed disabled:opacity-60",
);

function Field({
  label,
  htmlFor,
  children,
}: {
  label: string;
  htmlFor: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label
        htmlFor={htmlFor}
        className="font-sans text-[12.5px] font-medium tracking-tight text-foreground"
      >
        {label}
      </label>
      {children}
    </div>
  );
}

function ToggleEye({ on, setOn }: { on: boolean; setOn: (v: boolean) => void }) {
  return (
    <button
      type="button"
      onClick={() => setOn(!on)}
      tabIndex={-1}
      aria-label={on ? "비밀번호 숨기기" : "비밀번호 표시"}
      className="absolute right-2 top-1/2 grid h-7 w-7 -translate-y-1/2 place-items-center rounded-sm text-foreground-subtle hover:bg-foreground/5 hover:text-foreground"
    >
      <Icon name={on ? "eye-off" : "eye"} className="h-4 w-4" />
    </button>
  );
}
