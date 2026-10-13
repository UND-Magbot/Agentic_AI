"use client";

// 내 정보 팝업 — 프로필 메뉴에서 누구나 본인 것만(사용자 2026-10-12). 이니셜·이름·직함·휴대폰·이메일.
// 견적서·거래명세서 입력 폼이 이 값으로 미리 채워진다(폼에서 고칠 수 있음). 이니셜은 발행번호(QT-LU20261012-01)에 들어간다.
import { useEffect, useState } from "react";

type Props = { onClose: () => void };
type Profile = { initials: string; name_ko: string; title: string; mobile: string; email: string };

const EMPTY: Profile = { initials: "", name_ko: "", title: "", mobile: "", email: "" };
const inputClass =
  "w-full rounded-lg border border-border bg-surface px-3 py-2 text-[13.5px] text-foreground outline-none transition focus:border-accent disabled:opacity-60";
const FIELDS: { key: keyof Profile; label: string; placeholder: string; max: number; hint?: string }[] = [
  { key: "initials", label: "이니셜", placeholder: "예: LU", max: 4, hint: "영문 2~4자 — 발행번호 QT-LU20261012-01 에 들어갑니다" },
  { key: "name_ko", label: "이름", placeholder: "예: 홍길동", max: 40 },
  { key: "title", label: "직함", placeholder: "예: 이사", max: 40 },
  { key: "mobile", label: "휴대폰", placeholder: "예: 010-1234-5678", max: 40 },
  { key: "email", label: "이메일", placeholder: "예: name@unde.co.kr", max: 255 },
];

/** 내 정보 팝업 — 열 때만 렌더한다({open && <MyProfileModal …/>}). 열면 지금 값을 불러온다. */
export function MyProfileModal({ onClose }: Props) {
  const [p, setP] = useState<Profile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let alive = true;
    fetch("/api/my-profile", { cache: "no-store" })
      .then(async (r) => {
        const d = await r.json().catch(() => ({}));
        if (!alive) return;
        if (!r.ok) throw new Error(typeof d?.detail === "string" ? d.detail : "내 정보를 불러오지 못했습니다.");
        setP({ ...EMPTY, ...d });
      })
      .catch((e: Error) => { if (alive) { setError(e.message); setP(EMPTY); } });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !saving) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, saving]);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!p || saving) return;
    if (p.initials && !/^[A-Za-z]{2,4}$/.test(p.initials.trim())) {
      setError("이니셜은 영문 2~4자로 적어 주세요(예: LU).");
      return;
    }
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const r = await fetch("/api/my-profile", {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...p, initials: p.initials.trim().toUpperCase() }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) {
        const msg = typeof d?.detail === "string" ? d.detail
          : Array.isArray(d?.detail) ? String(d.detail[0]?.msg ?? "").replace(/^Value error, /, "") || "입력값을 확인해 주세요." : "저장하지 못했습니다.";
        throw new Error(msg);
      }
      setP({ ...EMPTY, ...d });
      setSaved(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "네트워크 오류가 발생했습니다.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div role="dialog" aria-modal="true" aria-labelledby="profile-title"
         className="anim-fade-in fixed inset-0 z-50 grid place-items-center bg-black/40 px-4"
         onMouseDown={(e) => { if (e.target === e.currentTarget && !saving) onClose(); }}>
      <div className="anim-fade-in-up max-h-[90vh] w-full max-w-[460px] overflow-y-auto rounded-2xl border border-border bg-surface-raised p-6 shadow-[0_30px_70px_-30px_rgba(15,30,70,0.55)]">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 id="profile-title" className="text-[15px] font-semibold tracking-tight text-foreground">내 정보</h2>
            <p className="mt-1 text-[12.5px] text-foreground-muted">견적서·거래명세서를 작성할 때 담당자 칸이 이 값으로 미리 채워집니다. 폼에서 고칠 수 있어요.</p>
          </div>
          <button type="button" onClick={onClose} disabled={saving} aria-label="닫기"
                  className="grid h-7 w-7 shrink-0 place-items-center rounded-sm text-foreground-subtle hover:bg-foreground/5 hover:text-foreground">
            <span aria-hidden className="text-[15px] leading-none">×</span>
          </button>
        </div>

        {!p ? (
          <p className="mt-6 py-6 text-center text-[13px] text-foreground-muted">불러오는 중…</p>
        ) : (
          <form onSubmit={onSubmit} className="mt-5 flex flex-col gap-3.5" noValidate>
            {FIELDS.map((f) => (
              <label key={f.key} className="flex flex-col gap-1.5">
                <span className="text-[12.5px] font-semibold text-foreground">{f.label}</span>
                <input value={p[f.key]} maxLength={f.max} disabled={saving} placeholder={f.placeholder}
                       onChange={(e) => { setSaved(false); setP({ ...p, [f.key]: e.target.value }); }}
                       className={inputClass + (f.key === "initials" ? " uppercase" : "")} />
                {f.hint && <span className="text-[11.5px] text-foreground-subtle">{f.hint}</span>}
              </label>
            ))}
            {error && <p className="text-[12.5px] text-red-600">{error}</p>}
            {saved && !error && <p className="text-[12.5px] text-emerald-700 dark:text-emerald-300">저장했습니다.</p>}
            <div className="flex justify-end gap-2">
              <button type="button" onClick={onClose} disabled={saving} className="h-9 rounded-lg border border-border px-4 text-[13px]">닫기</button>
              <button type="submit" disabled={saving}
                      className="h-9 rounded-lg bg-accent px-5 text-[13px] font-semibold text-white hover:brightness-105 disabled:opacity-50">
                {saving ? "저장 중…" : "저장"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
