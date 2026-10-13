"use client";

// 관리자 화면 전체에 붙는 승인 요청 알림(사용자 2026-10-13) — 일반 직원이 나를 받는 사람으로 골라 요청을 보내면
// 팝업을 한 번 띄우고 [검토·승인 > 승인 요청]으로 안내한다. 로그인 중이면 30초 안에(창으로 돌아오면 바로),
// 로그아웃 중에 온 요청은 다음 로그인 때 한 번. 본 기록은 DB(approval_requests.alerted_to) — 어느 기기에서 봤든 다시 안 뜬다.
import { useCallback, useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";

type Brief = { id: number; kind: string; customer: string | null; deal_no: string | null; term_label: string | null;
               requested_due: string | null; requested_by: string | null; reason: string; created_at: string };

const POLL_MS = 30_000;
const KIND_LABEL: Record<string, string> = { pay_due_extend: "입금기한 연장" };
export const APPROVALS_URL = "/proposals/recommend?tab=memory&sub=approvals";
/** 같은 화면(제품 영업)에 이미 있을 때 탭만 바꾸라는 신호 */
export const OPEN_APPROVALS_EVENT = "und:open-approvals";

/** 팝업을 본 요청을 서버에 기록 — 실패하면 다음 확인 때 한 번 더 뜰 수 있다 */
async function markSeen(ids: number[]) {
  if (!ids.length) return;
  try {
    await fetch("/api/sales-deals/approvals/seen", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids }) });
  } catch { /* 무시 */ }
}

export function ApprovalAlert() {
  const router = useRouter();
  const pathname = usePathname();
  const [items, setItems] = useState<Brief[]>([]);

  const check = useCallback(async () => {
    try {
      const r = await fetch("/api/sales-deals/approvals/count", { cache: "no-store" });
      if (!r.ok) return;
      const d = await r.json() as { admin: boolean; items: Brief[] };
      if (!d.admin) return;
      const fresh = d.items ?? [];                        // 서버가 아직 안 본 요청만 준다
      if (fresh.length) setItems((cur) => (cur.length ? [...cur, ...fresh.filter((x) => !cur.some((c) => c.id === x.id))] : fresh));
    } catch { /* 다음 확인 때 다시 */ }
  }, []);

  useEffect(() => {
    const first = window.setTimeout(() => void check(), 0);       // 로그인 직후·화면을 연 직후 한 번
    const t = window.setInterval(() => void check(), POLL_MS);
    const onFocus = () => { if (document.visibilityState === "visible") void check(); };
    document.addEventListener("visibilitychange", onFocus);
    window.addEventListener("focus", onFocus);
    return () => { window.clearTimeout(first); window.clearInterval(t); document.removeEventListener("visibilitychange", onFocus); window.removeEventListener("focus", onFocus); };
  }, [check]);

  const close = (go: boolean) => {
    void markSeen(items.map((x) => x.id));
    setItems([]);
    if (go) {
      if (pathname === "/proposals/recommend") window.dispatchEvent(new Event(OPEN_APPROVALS_EVENT));
      router.push(APPROVALS_URL);
    }
  };

  if (!items.length) return null;
  const shown = items.slice(0, 3);
  return (
    <div role="alertdialog" aria-modal="true" aria-labelledby="appr-alert-title"
         className="anim-fade-in fixed inset-0 z-[70] grid place-items-center bg-black/40 px-4">
      <div className="anim-fade-in-up w-full max-w-[460px] rounded-2xl border border-border bg-surface-raised p-5 text-[13px] shadow-[0_30px_70px_-30px_rgba(15,30,70,0.55)]">
        <div className="flex items-start justify-between gap-3">
          <h2 id="appr-alert-title" className="text-[15px] font-semibold text-foreground">
            새 승인 요청 <span className="rounded-full bg-red-500 px-2 py-0.5 text-[12px] text-white">{items.length}</span>
          </h2>
          <button type="button" aria-label="닫기" onClick={() => close(false)}
                  className="grid h-7 w-7 place-items-center rounded-sm text-foreground-subtle hover:bg-foreground/5 hover:text-foreground">×</button>
        </div>
        <p className="mt-1 text-[12.5px] text-foreground-muted">나를 받는 사람으로 고른 승인 요청이 왔습니다. [검토·승인] 탭에서 확인해 주세요.</p>
        <ul className="mt-3 flex flex-col gap-1.5">
          {shown.map((x) => (
            <li key={x.id} className="rounded-lg border border-border px-3 py-2">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="rounded bg-foreground/[0.07] px-1.5 py-0.5 text-[11px] font-semibold">{KIND_LABEL[x.kind] ?? "승인 요청"}</span>
                <b className="text-foreground">{x.customer}</b>
                {x.term_label && <span className="text-foreground-muted">{x.term_label}</span>}
                {x.requested_due && <span className="text-foreground-muted">→ {x.requested_due}</span>}
              </div>
              <p className="mt-0.5 truncate text-[12px] text-foreground-subtle">{x.requested_by} · {x.reason}</p>
            </li>
          ))}
          {items.length > shown.length && <li className="text-[12px] text-foreground-subtle">외 {items.length - shown.length}건</li>}
        </ul>
        <div className="mt-4 flex flex-wrap justify-end gap-2">
          <button type="button" onClick={() => close(false)} className="h-9 rounded-lg border border-border px-4 text-[13px]">나중에</button>
          <button type="button" autoFocus onClick={() => close(true)}
                  className="h-9 rounded-lg bg-accent px-5 text-[13px] font-semibold text-white hover:brightness-105">검토·승인에서 확인하기</button>
        </div>
      </div>
    </div>
  );
}
