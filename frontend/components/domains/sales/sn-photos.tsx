// S/N 사진 칸(사용자 2026-11-18) — 제조사 S/N · UND S/N 두 줄 옆, 그 제품 한 대의 사진(명판·S/N). 장수 제한 없이 여러 장.
// 여러 대를 한 번에 찍은 사진(박스 상태 등)은 품목의 '전체 사진'에 올린다(사용자 2026-11-19 — 같은 사진이 낱개마다 겹치지 않게,
// 예전 '한꺼번에/이것만' 팝업은 없앰).
// 영업 건 S/N 등록(unit-register)과 구매 '보유 후 자체 개발용 입고'가 같이 쓴다 — 올리기·보기 주소만 넘겨받는다.
"use client";

import { useRef, useState } from "react";

import { Icon } from "@/components/shared/ui/icon";
import { cn } from "@/lib/shared/utils";

export interface SnPhotoApi {
  /** 사진 한 장 올리기 → 저장된 사진 이름 */
  upload: (file: File) => Promise<string>;
  /** 사진 이름 → 보기 주소 */
  urlOf: (id: string) => string;
}

/** 파일 고르는 창을 열어 고른 사진(여러 장)을 모두 올리고 이름을 돌려준다. 고르지 않으면 [] */
export function pickPhotos(api: SnPhotoApi): Promise<string[]> {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".jpg,.jpeg,.png,.webp";
    input.multiple = true;
    input.onchange = async () => {
      const files = Array.from(input.files ?? []);
      const ids: string[] = [];
      for (const f of files) ids.push(await api.upload(f));
      resolve(ids);
    };
    input.addEventListener("cancel", () => resolve([]));
    input.click();
  });
}

/** 사진 칸 하나(낱개 S/N 사진 · 품목 전체 사진 공용). label = 칸 이름 */
export function SnPhotoCell({ api, photos, disabled, onAdd, onRemove, label = "사진" }: {
  api: SnPhotoApi; photos: string[]; disabled?: boolean; label?: string;
  onAdd: (ids: string[]) => void; onRemove: (id: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const alive = useRef(true);
  const add = async () => {
    setBusy(true);
    setErr(null);
    try {
      const ids = await pickPhotos(api);
      if (ids.length && alive.current) onAdd(ids);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "사진을 올리지 못했습니다.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-[11.5px] font-semibold text-foreground-muted">{label}</span>
      {photos.map((ph) => (
        <span key={ph} className="relative">
          <a href={api.urlOf(ph)} target="_blank" rel="noreferrer">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={api.urlOf(ph)} alt={label} className="h-9 w-9 rounded object-cover" />
          </a>
          {!disabled && (
            <button type="button" aria-label="사진 빼기" onClick={() => onRemove(ph)}
                    className="absolute -right-1 -top-1 grid h-4 w-4 place-items-center rounded-full bg-black/70 text-[10px] text-white">×</button>
          )}
        </span>
      ))}
      {!disabled && (
        <button type="button" disabled={busy} onClick={() => void add()}
                className={cn("inline-flex h-8 items-center gap-1 rounded-lg border border-dashed border-border px-2 text-[12px] text-foreground-muted hover:border-accent hover:text-accent disabled:opacity-50")}>
          <Icon name="plus" className="h-3.5 w-3.5" /> {busy ? "올리는 중…" : label}
        </button>
      )}
      {err && <span className="text-[11.5px] text-red-600">{err}</span>}
    </div>
  );
}

/** 사진 올리기 공통 — multipart 'file' → {id} */
export async function uploadPhoto(url: string, file: File): Promise<string> {
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch(url, { method: "POST", body: fd });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof d?.detail === "string" ? d.detail : "사진을 올리지 못했습니다.");
  return String(d.id);
}
