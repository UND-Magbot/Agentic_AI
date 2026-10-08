// 승인 대기 회사 지식 — 영업 관리자가 [승인]/[반려] 한다(사용자 결정 2026-09-30).
// 승인해야 회상에 쓰인다. 반려는 사유를 남긴다.
"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { KnowledgeReview } from "@/lib/shared/proposal-projects";
import { Icon } from "@/components/shared/ui/icon";
import { ErrorBox, inputCls, primaryBtn, secondaryBtn } from "./proposal-actions";

function ReviewItem({ item }: { item: KnowledgeReview }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rejecting, setRejecting] = useState(false);
  const [note, setNote] = useState("");
  const k = item.card;

  async function review(action: "approve" | "reject") {
    setBusy(true);
    setError(null);
    try {
      const r = await fetch(`/api/knowledge-reviews/${item.id}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, note }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof d?.detail === "string" ? d.detail : "요청을 처리하지 못했습니다.");
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "요청을 처리하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="flex flex-col gap-2 rounded-xl border border-border bg-surface p-4">
      <div className="flex flex-wrap items-center gap-1.5 text-[12px]">
        <span className="rounded-full bg-sky-500/12 px-2 py-0.5 font-medium text-sky-700 dark:text-sky-300">{item.evidence}</span>
        <span className="text-[14px] font-semibold text-foreground">{k.title}</span>
      </div>
      <p className="text-[12px] text-foreground-subtle">
        E{item.id} · {item.title} · 제출 {item.submitted_by || "알 수 없음"} · {new Date(item.submitted_at).toLocaleString("ko-KR")}
      </p>
      <dl className="grid gap-1 text-[13px] leading-relaxed text-foreground sm:grid-cols-[88px_1fr]">
        <dt className="text-foreground-muted">공정</dt><dd>{k.process || "-"}</dd>
        <dt className="text-foreground-muted">해법</dt><dd>{k.solution || "-"}</dd>
        {k.key_ideas.length > 0 && (<><dt className="text-foreground-muted">핵심 아이디어</dt>
          <dd><ul className="list-disc pl-4">{k.key_ideas.map((x) => <li key={x}>{x}</li>)}</ul></dd></>)}
        {k.applies_when && (<><dt className="text-foreground-muted">맞는 조건</dt><dd>{k.applies_when}</dd></>)}
        {k.cautions.length > 0 && (<><dt className="text-foreground-muted">주의</dt><dd>{k.cautions.join(" · ")}</dd></>)}
      </dl>
      <ErrorBox error={error} />
      {rejecting ? (
        <div className="flex flex-col gap-2">
          <input className={inputCls} value={note} maxLength={300} placeholder="반려 사유(필수) — 예: 공정 조건이 달라 일반화 어려움"
                 onChange={(e) => setNote(e.target.value)} />
          <div className="flex flex-wrap gap-2">
            <button type="button" className={primaryBtn} disabled={busy || !note.trim()} onClick={() => void review("reject")}>
              반려 확정
            </button>
            <button type="button" className={secondaryBtn} disabled={busy} onClick={() => setRejecting(false)}>취소</button>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          <button type="button" className={primaryBtn} disabled={busy} onClick={() => void review("approve")}>
            <Icon name="check" className="h-4 w-4" /> 승인 — 회상에 쓰기
          </button>
          <button type="button" className={secondaryBtn} disabled={busy} onClick={() => setRejecting(true)}>반려</button>
        </div>
      )}
    </li>
  );
}

export function KnowledgeReviewList({ items }: { items: KnowledgeReview[] | null }) {
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-4 py-6">
      <div>
        <h1 className="text-[18px] font-semibold text-foreground">AI가 배울 내용 · 승인 대기</h1>
        <p className="text-[13px] text-foreground-muted">
          제안서 작업을 마치며 [지식 저장]을 누른 컨셉입니다. 승인하면 다음 제안서의 공정 컨셉을 짤 때 AI 가 회사 경험으로
          참고하고, 반려하면 쓰지 않습니다.
        </p>
      </div>
      {items === null ? (
        <p className="rounded-lg border border-border px-4 py-3 text-[13px] text-foreground-muted">
          승인은 영업 관리자만 할 수 있습니다.
        </p>
      ) : items.length === 0 ? (
        <p className="rounded-lg border border-dashed border-border px-4 py-6 text-center text-[13px] text-foreground-subtle">
          승인을 기다리는 내용이 없습니다. 제안서를 완성하고 [지식 저장]을 누르면 여기에 모입니다.
        </p>
      ) : (
        <ul className="flex flex-col gap-3">{items.map((it) => <ReviewItem key={it.id} item={it} />)}</ul>
      )}
    </div>
  );
}
