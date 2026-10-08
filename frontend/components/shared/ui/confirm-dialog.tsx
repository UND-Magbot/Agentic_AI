"use client";

import { useEffect, useRef, useState } from "react";

import { Icon } from "@/components/shared/ui/icon";
import type { IconName } from "@/lib/shared/types";
import { cn } from "@/lib/shared/utils";

type Variant = "danger" | "default";

type Props = {
  open: boolean;
  /** 헤더 좌측 아이콘. 위험 액션은 "trash", 그 외는 "help" 등. */
  icon?: IconName;
  /** 굵은 헤드라인 — 한 줄로 명확하게. */
  title: string;
  /** 본문 설명. 길어질 수 있음. children 으로도 받을 수 있다. */
  description?: React.ReactNode;
  /** 강조 인용(예: 삭제할 항목명). 본문 위에 별도 박스로 노출. */
  highlight?: string;
  /** 확인 버튼 라벨. default "삭제". */
  confirmLabel?: string;
  /** 취소 버튼 라벨. default "취소". */
  cancelLabel?: string;
  /** danger = 빨간 확인 버튼(파괴적 액션). default = 중립 톤. */
  variant?: Variant;
  /** 확인 시 실행. async 가능. throw 시 에러 메시지가 모달 안에 표시되고 모달은 열린 상태 유지. */
  onConfirm: () => void | Promise<void>;
  onClose: () => void;
};

/**
 * 위험·중요 액션 확인용 커스텀 모달.
 * 네이티브 `confirm()` 의 자리를 대체하며, 디자인 시스템과 일관된 톤을 갖는다.
 *
 *  - ESC / 배경 클릭 / 취소 버튼: 닫힘 (onConfirm 미호출).
 *  - 확인 버튼: onConfirm() 실행. async 면 spinner 표시, throw 시 본문에 에러 노출.
 *  - 첫 mount 시 취소 버튼에 자동 포커스(Enter 가 아닌 Esc 가 안전한 기본값을 갖도록).
 */
export function ConfirmDialog({
  open,
  icon = "trash",
  title,
  description,
  highlight,
  confirmLabel = "삭제",
  cancelLabel = "취소",
  variant = "danger",
  onConfirm,
  onClose,
}: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cancelBtnRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    setBusy(false);
    setError(null);
    const t = setTimeout(() => cancelBtnRef.current?.focus(), 50);
    return () => clearTimeout(t);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, busy, onClose]);

  if (!open) return null;

  async function handleConfirm() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await onConfirm();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
      return;
    }
    // 성공 시 모달 자체는 부모가 onClose 로 닫는다(또는 unmount).
    setBusy(false);
  }

  const isDanger = variant === "danger";

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-dialog-title"
      className="fixed inset-0 z-[60] flex items-center justify-center bg-foreground/40 p-4 backdrop-blur-sm"
      onMouseDown={(e) => {
        if (busy) return;
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="anim-fade-in-up w-full max-w-md overflow-hidden rounded-2xl border border-border bg-surface-raised shadow-2xl">
        {/* 본문 영역 */}
        <div className="flex gap-4 p-6">
          <div
            className={cn(
              "grid h-10 w-10 shrink-0 place-items-center rounded-full ring-1",
              isDanger
                ? "bg-red-500/10 text-red-600 ring-red-500/20 dark:bg-red-500/15 dark:text-red-400 dark:ring-red-500/30"
                : "bg-foreground/5 text-foreground ring-foreground/10",
            )}
            aria-hidden
          >
            <Icon name={icon} className="h-5 w-5" />
          </div>
          <div className="min-w-0 flex-1">
            <h2
              id="confirm-dialog-title"
              className="text-[16px] font-semibold tracking-tight text-foreground"
            >
              {title}
            </h2>
            {highlight && (
              <div className="mt-3 rounded-md border border-foreground/10 bg-background/60 px-3 py-2 text-[13px] font-medium text-foreground">
                <span className="line-clamp-2 break-all">{highlight}</span>
              </div>
            )}
            {description && (
              <div className="mt-3 text-[13px] leading-relaxed text-foreground-muted">
                {description}
              </div>
            )}
            {error && (
              <div className="mt-3 rounded-md border border-red-500/30 bg-red-500/5 px-3 py-2 text-[12px] text-red-600 dark:text-red-400">
                {error}
              </div>
            )}
          </div>
        </div>

        {/* 액션 영역 — 헤어라인 + 우측 정렬. */}
        <div className="flex items-center justify-end gap-2 border-t border-border bg-surface-raised/40 px-5 py-3">
          <button
            ref={cancelBtnRef}
            type="button"
            onClick={onClose}
            disabled={busy}
            className="rounded-md px-3.5 py-1.5 text-[13px] font-medium text-foreground transition hover:bg-foreground/5 disabled:opacity-50"
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            onClick={handleConfirm}
            disabled={busy}
            className={cn(
              "inline-flex items-center gap-2 rounded-lg px-4 py-2 text-[13px] font-semibold transition disabled:cursor-not-allowed disabled:opacity-60",
              isDanger
                ? "bg-red-600 text-white shadow-[0_8px_18px_-8px_rgba(220,38,38,0.6)] hover:bg-red-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/40"
                : "und-grad text-white shadow-[0_8px_18px_-8px_rgba(37,99,235,0.6),inset_0_1px_0_rgba(255,255,255,0.35)] hover:brightness-105",
            )}
          >
            {busy && <Icon name="refresh" className="h-3.5 w-3.5 animate-spin" />}
            <span>{busy ? "처리 중..." : confirmLabel}</span>
          </button>
        </div>
      </div>
    </div>
  );
}
