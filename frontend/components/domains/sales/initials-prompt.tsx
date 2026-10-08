// 건 번호 이니셜 묻기 — 새 영업 건 번호는 담당 이니셜 + 날짜(AL20261008, 사용자 2026-10-08).
"use client";

import { useState } from "react";

/** 건 번호 이니셜(AL·VT…)을 아직 안 정한 담당자 — 한 번 묻고 저장한다. name 이 없으면 로그인한 사람. */
export function InitialsPrompt({ name, onSaved, onCancel }: { name?: string; onSaved: () => void; onCancel: () => void }) {
  const [ini, setIni] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const ok = /^[A-Z]{2,4}$/.test(ini);
  async function save() {
    setBusy(true);
    setErr(null);
    try {
      const r = await fetch("/api/sales-deals/initials", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: name || null, initials: ini }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof d?.detail === "string" ? d.detail : "이니셜을 저장하지 못했습니다.");
      onSaved();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-amber-500/40 bg-amber-500/[0.07] px-3 py-2 text-[12.5px]">
      <span>{name ? <b>{name}</b> : "내"} 건 번호 이니셜을 정해 주세요 — 예: <b>AL</b>20261008</span>
      <input value={ini} maxLength={4} autoFocus aria-label="건 번호 이니셜" placeholder="AL"
             onChange={(e) => setIni(e.target.value.toUpperCase().replace(/[^A-Z]/g, ""))}
             onKeyDown={(e) => { if (e.key === "Enter" && ok && !busy) void save(); }}
             className="h-8 w-20 rounded-lg border border-border bg-surface px-2 font-mono outline-none focus:border-accent" />
      <button type="button" disabled={!ok || busy} onClick={() => void save()}
              className="h-8 rounded-lg bg-accent px-3 font-semibold text-white disabled:opacity-40">{busy ? "저장 중…" : "저장하고 계속"}</button>
      <button type="button" onClick={onCancel} className="h-8 px-1 text-foreground-subtle hover:underline">취소</button>
      {err && <span className="w-full text-red-600 dark:text-red-400">{err}</span>}
    </div>
  );
}
