"use client";

// 버그 등록 팝업 — 프로필 메뉴에서 누구나(사용자 2026-10-08). 제목 / 사진 첨부 / 상세 내용, 번호(UND-00001…)는 서버가 매긴다.
import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/shared/utils";

type Props = { onClose: () => void };

const MAX_PHOTOS = 5;
const MAX_MB = 10;
const ACCEPT = ".png,.jpg,.jpeg,.webp,.gif";
const inputClass =
  "w-full rounded-lg border border-border bg-surface px-3 py-2 text-[13.5px] text-foreground outline-none transition focus:border-accent disabled:opacity-60";

type Photo = { file: File; url: string };

/** 버그 등록 팝업 — 열 때만 렌더한다({open && <BugReportModal …/>}), 그래서 열 때마다 빈 칸으로 시작. */
export function BugReportModal({ onClose }: Props) {
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [photos, setPhotos] = useState<Photo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [doneNo, setDoneNo] = useState<string | null>(null);
  const titleRef = useRef<HTMLInputElement>(null);

  // 열 때마다 새로 그려진다(부모가 열 때만 렌더) — 제목에 포커스
  useEffect(() => { requestAnimationFrame(() => titleRef.current?.focus()); }, []);

  // 미리보기 주소 정리 — 뺀 사진은 바로, 남은 것은 닫힐 때
  const urls = useRef<string[]>([]);
  useEffect(() => { urls.current = photos.map((p) => p.url); }, [photos]);
  useEffect(() => () => urls.current.forEach((u) => URL.revokeObjectURL(u)), []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !submitting) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, submitting]);

  function addPhotos(list: FileList) {
    setError(null);
    const picked = Array.from(list);
    const big = picked.find((f) => f.size > MAX_MB * 1024 * 1024);
    if (big) {
      setError(`'${big.name}' — 사진은 장당 ${MAX_MB}MB까지입니다.`);
      return;
    }
    if (photos.length + picked.length > MAX_PHOTOS) {
      setError(`사진은 ${MAX_PHOTOS}장까지 올릴 수 있습니다.`);
      return;
    }
    setPhotos((xs) => [...xs, ...picked.map((file) => ({ file, url: URL.createObjectURL(file) }))]);
  }

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (submitting) return;
    if (!title.trim() || !content.trim()) {
      setError("제목과 상세 내용을 적어 주세요.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const fd = new FormData();
      fd.append("title", title.trim());
      fd.append("content", content.trim());
      photos.forEach((p) => fd.append("files", p.file, p.file.name));
      const r = await fetch("/api/bug-reports", { method: "POST", body: fd });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof d?.detail === "string" ? d.detail : "등록하지 못했습니다.");
      setDoneNo(d.report_no as string);
    } catch (err) {
      setError(err instanceof Error ? err.message : "네트워크 오류가 발생했습니다.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div role="dialog" aria-modal="true" aria-labelledby="bug-title"
         className="anim-fade-in fixed inset-0 z-50 grid place-items-center bg-black/40 px-4"
         onMouseDown={(e) => { if (e.target === e.currentTarget && !submitting) onClose(); }}>
      <div className="anim-fade-in-up max-h-[90vh] w-full max-w-[560px] overflow-y-auto rounded-2xl border border-border bg-surface-raised p-6 shadow-[0_30px_70px_-30px_rgba(15,30,70,0.55)]">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 id="bug-title" className="text-[15px] font-semibold tracking-tight text-foreground">버그 등록</h2>
            <p className="mt-1 text-[12.5px] text-foreground-muted">무엇이 잘못됐는지 적어 주세요. 등록하면 번호가 자동으로 붙습니다.</p>
          </div>
          <button type="button" onClick={onClose} disabled={submitting} aria-label="닫기"
                  className="grid h-7 w-7 shrink-0 place-items-center rounded-sm text-foreground-subtle hover:bg-foreground/5 hover:text-foreground">
            <span aria-hidden className="text-[15px] leading-none">×</span>
          </button>
        </div>

        {doneNo ? (
          <div className="mt-6 flex flex-col items-center gap-2 py-4 text-center">
            <span className="grid h-11 w-11 place-items-center rounded-full bg-emerald-500 text-[20px] font-bold text-white">✓</span>
            <p className="text-[15px] font-semibold"><span className="font-mono">{doneNo}</span> 로 등록되었습니다.</p>
            <p className="text-[12.5px] text-foreground-muted">문의할 때 이 번호를 알려 주세요.</p>
            <button type="button" onClick={onClose}
                    className="mt-3 h-9 rounded-lg bg-accent px-5 text-[13px] font-semibold text-white hover:brightness-105">닫기</button>
          </div>
        ) : (
          <form onSubmit={onSubmit} className="mt-5 flex flex-col gap-4" noValidate>
            <section className="flex flex-col gap-1.5">
              <label htmlFor="bug-title-input" className="text-[12.5px] font-semibold text-foreground">제목 *</label>
              <input id="bug-title-input" ref={titleRef} value={title} maxLength={120} disabled={submitting}
                     onChange={(e) => setTitle(e.target.value)} placeholder="예: 견적서 PDF 다운로드가 안 됩니다" className={inputClass} />
            </section>

            <section className="flex flex-col gap-1.5">
              <div className="flex items-center justify-between">
                <span className="text-[12.5px] font-semibold text-foreground">사진 첨부 <span className="font-normal text-foreground-subtle">· 화면 캡처 등 {MAX_PHOTOS}장까지, 장당 {MAX_MB}MB</span></span>
                <label className={cn("cursor-pointer rounded-lg border border-border px-2.5 py-1 text-[12px] font-semibold text-accent hover:bg-accent/5",
                  (submitting || photos.length >= MAX_PHOTOS) && "pointer-events-none opacity-50")}>
                  + 사진
                  <input type="file" accept={ACCEPT} multiple className="hidden"
                         onChange={(e) => { if (e.target.files?.length) addPhotos(e.target.files); e.target.value = ""; }} />
                </label>
              </div>
              {photos.length > 0 ? (
                <div className="flex flex-wrap gap-2 rounded-lg border border-border p-2">
                  {photos.map((p, i) => (
                    <span key={p.url} className="relative">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={p.url} alt={`첨부 사진 ${i + 1}`} className="h-20 w-20 rounded-md object-cover ring-1 ring-border" />
                      <button type="button" aria-label="사진 빼기" disabled={submitting}
                              onClick={() => { URL.revokeObjectURL(p.url); setPhotos((xs) => xs.filter((x) => x.url !== p.url)); }}
                              className="absolute -right-1.5 -top-1.5 grid h-5 w-5 place-items-center rounded-full bg-black/65 text-[11px] text-white">✕</button>
                    </span>
                  ))}
                </div>
              ) : (
                <p className="rounded-lg border border-dashed border-border px-3 py-3 text-center text-[12px] text-foreground-subtle">첨부한 사진이 없습니다(선택)</p>
              )}
            </section>

            <section className="flex flex-col gap-1.5">
              <label htmlFor="bug-content" className="text-[12.5px] font-semibold text-foreground">상세 내용 *</label>
              <textarea id="bug-content" value={content} maxLength={5000} rows={7} disabled={submitting}
                        onChange={(e) => setContent(e.target.value)}
                        placeholder={"어느 화면에서 무엇을 했을 때 어떻게 됐는지 적어 주세요.\n예: 영업 건 관리 → 상세 → [견적서 다운로드] 누르면 오류 창이 뜹니다."}
                        className={cn(inputClass, "resize-y leading-relaxed")} />
            </section>

            {error && <p className="text-[12.5px] text-red-600">{error}</p>}
            <div className="flex justify-end gap-2">
              <button type="button" onClick={onClose} disabled={submitting}
                      className="h-9 rounded-lg border border-border px-4 text-[13px]">취소</button>
              <button type="submit" disabled={submitting}
                      className="h-9 rounded-lg bg-accent px-5 text-[13px] font-semibold text-white hover:brightness-105 disabled:opacity-50">
                {submitting ? "등록 중…" : "등록"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
