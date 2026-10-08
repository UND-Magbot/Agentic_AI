"use client";

import { useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import type { DomainAccent } from "@/lib/shared/types";
import { cn } from "@/lib/shared/utils";
import { Icon } from "@/components/shared/ui/icon";

type Props = {
  accent: DomainAccent;
  placeholder: string;
  disabled?: boolean;
  /** 대화가 시작된 뒤에는 하단에 가깝게 붙이기 위해 bottom padding 을 축소한다. */
  compact?: boolean;
  /** 모드(자동/도메인 잠금) 라벨. 모델 라벨 옆에 함께 표시. */
  modelLabel?: string;
  /** 응답 스트리밍 중인지. true 면 전송 자리에 정지 버튼이 노출된다. */
  streaming?: boolean;
  /** 메시지 본문 + 첨부 메타 배열을 함께 전달. 첨부 없으면 빈 배열. */
  onSubmit: (text: string, attachments: ComposerAttachment[]) => void;
  /** 정지 버튼 클릭 — 진행 중인 fetch 를 abort 한다. streaming===true 일 때만 의미. */
  onStop?: () => void;
};

export type ComposerAttachment = {
  id: number;
  filename: string;
  mime: string;
  size_bytes: number;
};

// 한 번에 선택 가능한 최대 파일 수. 너무 많으면 서버 부하 + UX 혼란.
// expense 영수증 슬롯 한도(16) 보다 약간 여유 있게 두어 다른 도메인 사용도 흡수.
const MAX_FILES_PER_SELECT = 20;
// 한 파일 최대 크기 (백엔드 ATTACHMENT_MAX_BYTES 와 동기 — 75MB).
// 1시간 내외 회의 녹음(m4a @128kbps mono ≈ 60MB) 까지 수용.
// 클라이언트에서 사전 차단해 큰 파일 업로드 라운드트립 절약.
const MAX_FILE_BYTES = 75 * 1024 * 1024;

function formatErrorDetail(detail: unknown): string | null {
  if (detail == null) return null;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    // FastAPI ValidationError: [{loc, msg, type}, ...]
    const parts = detail
      .map((d) => {
        if (typeof d === "string") return d;
        if (d && typeof d === "object" && "msg" in (d as Record<string, unknown>)) {
          return String((d as Record<string, unknown>).msg);
        }
        return JSON.stringify(d);
      })
      .filter(Boolean);
    return parts.join("; ") || null;
  }
  if (typeof detail === "object") {
    const obj = detail as Record<string, unknown>;
    if (typeof obj.msg === "string") return obj.msg;
    if (typeof obj.message === "string") return obj.message;
    try {
      return JSON.stringify(detail);
    } catch {
      return null;
    }
  }
  return String(detail);
}

export function Composer({
  accent,
  placeholder,
  disabled,
  compact,
  modelLabel,
  streaming,
  onSubmit,
  onStop,
}: Props) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // 첨부 — 업로드 즉시 attachment 메타 누적. 메시지 전송 시 id 들 함께 onSubmit 으로 흘림.
  const [attachments, setAttachments] = useState<ComposerAttachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  function submit() {
    const text = ref.current?.value.trim();
    // 본문 또는 첨부 중 하나만 있어도 전송 가능.
    if ((!text && attachments.length === 0) || disabled) return;
    onSubmit(text ?? "", attachments);
    if (ref.current) ref.current.value = "";
    setAttachments([]);
    setUploadError(null);
  }

  async function handleFileSelect(e: React.ChangeEvent<HTMLInputElement>) {
    // multiple 활성 — FileList 를 배열로 변환해 각 파일을 병렬 업로드.
    // 사용자가 한 파일을 다시 골라도 onChange 가 다시 발화되도록 value reset 도 유지.
    const picked = Array.from(e.target.files ?? []);
    e.target.value = "";
    if (picked.length === 0) return;

    setUploadError(null);

    // 사전 가드 — 한 번에 너무 많이/큰 파일 차단. 서버 라운드트립 절약.
    const prePassed: File[] = [];
    const preFailed: { name: string; reason: string }[] = [];
    let droppedByCap = 0;
    for (const f of picked) {
      if (prePassed.length >= MAX_FILES_PER_SELECT) {
        droppedByCap += 1;
        continue;
      }
      if (f.size <= 0) {
        preFailed.push({ name: f.name, reason: "빈 파일" });
        continue;
      }
      if (f.size > MAX_FILE_BYTES) {
        const mb = Math.round(MAX_FILE_BYTES / (1024 * 1024));
        preFailed.push({ name: f.name, reason: `최대 ${mb}MB 초과` });
        continue;
      }
      prePassed.push(f);
    }
    if (droppedByCap > 0) {
      preFailed.push({
        name: `(외 ${droppedByCap}개)`,
        reason: `한 번에 최대 ${MAX_FILES_PER_SELECT}개까지 첨부 가능`,
      });
    }
    if (prePassed.length === 0) {
      // 모두 사전 차단 — 첫 사유 한 줄만 노출.
      const first = preFailed[0];
      if (first) setUploadError(`${first.name}: ${first.reason}`);
      return;
    }

    const files = prePassed;
    setUploading(true);

    // 각 파일을 개별 POST 로 보낸다(backend 는 1요청=1파일). 병렬 실행으로 전체 대기 시간 단축.
    // 한 파일이 실패해도 나머지 성공분은 그대로 첨부 — 부분 성공 허용(사용자 경험 우선).
    type Resolution =
      | { ok: true; data: { id: number; filename: string; mime: string; size_bytes: number }; filename: string }
      | { ok: false; filename: string; error: string };

    const uploads: Promise<Resolution>[] = files.map(async (file): Promise<Resolution> => {
      try {
        const fd = new FormData();
        fd.append("file", file);
        const r = await fetch("/api/attachments", { method: "POST", body: fd });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) {
          return {
            ok: false,
            filename: file.name,
            // FastAPI 422 는 detail 이 객체 배열이라 단순 캐스팅하면 "[object Object]" 로 표시된다.
            // string/object/array 모두 안전하게 사람이 읽을 수 있는 메시지로 평탄화.
            error: formatErrorDetail(data?.detail) ?? `업로드 실패 (HTTP ${r.status})`,
          };
        }
        return { ok: true, data, filename: file.name };
      } catch (err) {
        return {
          ok: false,
          filename: file.name,
          error: err instanceof Error ? err.message : "업로드 오류",
        };
      }
    });

    try {
      const results = await Promise.all(uploads);
      const successes = results.filter(
        (r): r is Extract<Resolution, { ok: true }> => r.ok,
      );
      const failures = results.filter(
        (r): r is Extract<Resolution, { ok: false }> => !r.ok,
      );

      if (successes.length > 0) {
        // 사용자가 선택한 순서(files 배열 순서) 그대로 보존되도록 results 순서대로 append.
        const toAppend = successes.map((s) => ({
          id: s.data.id,
          filename: s.data.filename,
          mime: s.data.mime,
          size_bytes: s.data.size_bytes,
        }));
        setAttachments((prev) => [...prev, ...toAppend]);
      }

      // 사전 가드 실패 + 업로드 실패를 합쳐 한 줄 메시지로.
      const allFails = [
        ...preFailed.map((p) => ({ filename: p.name, error: p.reason })),
        ...failures.map((f) => ({ filename: f.filename, error: f.error })),
      ];
      if (allFails.length > 0) {
        const first = allFails[0];
        const more = allFails.length > 1 ? ` 외 ${allFails.length - 1}개` : "";
        setUploadError(`${first.filename}${more} 업로드 실패: ${first.error}`);
      }
    } finally {
      setUploading(false);
    }
  }

  async function removeAttachment(id: number) {
    // 낙관적 제거 + 서버 삭제 호출 (실패해도 UI 는 이미 빠진 상태).
    setAttachments((prev) => prev.filter((a) => a.id !== id));
    try {
      await fetch(`/api/attachments/${id}`, { method: "DELETE" });
    } catch {
      /* silent */
    }
  }

  function formatBytes(n: number): string {
    if (n < 1024) return `${n}B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)}KB`;
    return `${(n / (1024 * 1024)).toFixed(1)}MB`;
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  }

  function onForm(e: FormEvent) {
    e.preventDefault();
    submit();
  }

  function notImplemented(feature: string) {
    if (typeof window !== "undefined") {
      console.info(`[und_cortex] ${feature}: 곧 지원 예정`);
    }
  }

  // 모델 이름은 화면에 드러내지 않는다(사용자 결정 2026-09-30) — "사내 AI · {모드}" 만.
  const modePart = (modelLabel ?? "").split(" · ")[1] ?? "자동";
  const displayLabel = `사내 AI · ${modePart}`;

  return (
    <form onSubmit={onForm} className={cn("px-4", compact ? "pb-6" : "pb-[160px]")}>
      <div className="mx-auto max-w-3xl rounded-[20px] bg-[linear-gradient(135deg,rgba(37,99,235,0.45),rgba(6,182,212,0.32))] p-px shadow-[0_20px_50px_-26px_rgba(15,30,70,0.45)]">
        <div
          className={cn(
            "flex flex-col gap-1 rounded-[19px] und-glass p-2 transition focus-within:ring-2",
            accent.focusRing,
          )}
        >
        {/* Row 0 — 첨부 카드 영역 (있을 때만 노출) */}
        {(attachments.length > 0 || uploading || uploadError) && (
          <div className="flex flex-wrap gap-1.5 px-1 pt-1">
            {attachments.map((a) => (
              <div
                key={a.id}
                className="group inline-flex max-w-full items-center gap-2 rounded-md bg-surface-raised px-2 py-1 ring-1 ring-foreground/10"
                title={`${a.filename} · ${a.mime} · ${formatBytes(a.size_bytes)}`}
              >
                <Icon name="paperclip" className="h-3.5 w-3.5 shrink-0 text-foreground-subtle" />
                <span className="max-w-[180px] truncate text-[12px] text-foreground">
                  {a.filename}
                </span>
                <span className="text-[10px] text-foreground-subtle">
                  {formatBytes(a.size_bytes)}
                </span>
                <button
                  type="button"
                  onClick={() => removeAttachment(a.id)}
                  aria-label={`${a.filename} 첨부 제거`}
                  title="첨부 제거"
                  className="grid h-4 w-4 place-items-center rounded-sm text-foreground-subtle hover:bg-foreground/10 hover:text-foreground"
                >
                  <span className="text-[12px] leading-none">×</span>
                </button>
              </div>
            ))}
            {uploading && (
              <div className="inline-flex items-center gap-2 rounded-md bg-surface-raised px-2 py-1 text-[12px] text-foreground-subtle ring-1 ring-foreground/10">
                <span className="h-3 w-3 animate-spin rounded-full border-2 border-foreground/30 border-t-foreground/70" />
                <span>업로드 중…</span>
              </div>
            )}
            {uploadError && (
              <div className="inline-flex items-center gap-2 rounded-md bg-red-500/10 px-2 py-1 text-[12px] text-red-700 ring-1 ring-red-500/30 dark:text-red-300">
                <span>{uploadError}</span>
                <button
                  type="button"
                  onClick={() => setUploadError(null)}
                  aria-label="오류 닫기"
                  className="grid h-4 w-4 place-items-center rounded-sm hover:bg-red-500/10"
                >
                  <span className="text-[12px] leading-none">×</span>
                </button>
              </div>
            )}
          </div>
        )}

        {/* Row 1 — 입력 텍스트 */}
        <textarea
          ref={ref}
          rows={1}
          placeholder={placeholder}
          onKeyDown={onKeyDown}
          disabled={disabled}
          className="autosize w-full resize-none bg-transparent px-3 py-2 text-[15px] outline-none placeholder:text-foreground-subtle disabled:opacity-60"
        />

        {/* Row 2 — 컨트롤 */}
        <div className="flex items-center gap-1.5 px-1">
          {/* hidden file input — paperclip 버튼이 트리거 */}
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={handleFileSelect}
            accept="image/*,audio/*,application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-powerpoint,application/vnd.openxmlformats-officedocument.presentationml.presentation,text/plain,text/markdown,text/csv,application/json,application/zip"
          />
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading || disabled}
            aria-label="파일 첨부"
            title="파일 첨부"
            className="grid h-8 w-8 shrink-0 place-items-center rounded-md text-foreground-subtle hover:bg-foreground/5 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Icon name="paperclip" className="h-4 w-4" />
          </button>

          <div className="flex-1" />

          <span className="px-2 py-1 text-[12px] text-foreground-subtle">{displayLabel}</span>

          <button
            type="button"
            onClick={() => notImplemented("voice input")}
            aria-label="음성 입력"
            title="음성 입력 (곧 지원 예정)"
            className="grid h-8 w-8 shrink-0 place-items-center rounded-md text-foreground-subtle hover:bg-foreground/5 hover:text-foreground"
          >
            <Icon name="mic" className="h-4 w-4" />
          </button>

          {streaming ? (
            <button
              type="button"
              onClick={onStop}
              aria-label="응답 정지"
              title="응답 정지"
              className="grid h-8 w-8 shrink-0 place-items-center rounded-xl bg-zinc-800 text-zinc-50 shadow-sm transition hover:bg-zinc-700 dark:bg-zinc-200 dark:text-zinc-900 dark:hover:bg-zinc-100"
            >
              <Icon name="stop" className="h-3.5 w-3.5" />
            </button>
          ) : (
            <button
              type="submit"
              disabled={disabled}
              className={cn(
                "grid h-8 w-8 shrink-0 place-items-center rounded-xl text-white transition",
                "und-grad shadow-[0_10px_22px_-8px_rgba(37,99,235,0.7),inset_0_1px_0_rgba(255,255,255,0.4)]",
                "hover:brightness-105 active:translate-y-px disabled:cursor-not-allowed disabled:opacity-50",
              )}
              aria-label="전송"
            >
              <Icon name="send" className="h-4 w-4" />
            </button>
          )}
        </div>
        </div>
      </div>
    </form>
  );
}
