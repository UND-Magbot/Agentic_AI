// 제안서 작업 1·2단계 패널 — 자료 읽기 진행 표시, 추가 질문(첫 화면 뒤 합쳐서 최대 5개) 답변, 질문 마치기.
"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { ProposalCatalog, ProposalProject } from "@/lib/shared/proposal-projects";
import { Icon } from "@/components/shared/ui/icon";
import { cn } from "@/lib/shared/utils";

type Draft = { value: string; unknown: boolean };
const EMPTY_DRAFT: Draft = { value: "", unknown: false };

async function post(projectId: number, action: string, body?: unknown) {
  const r = await fetch(`/api/proposal-projects/${projectId}/${action}`, {
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof d?.detail === "string" ? d.detail : "요청을 처리하지 못했습니다.");
  return d;
}

const primaryBtn =
  "und-grad inline-flex items-center gap-2 rounded-xl px-4 py-2 text-[13px] font-semibold text-white disabled:cursor-not-allowed disabled:opacity-60";
const secondaryBtn =
  "inline-flex items-center gap-2 rounded-xl border border-border bg-surface px-4 py-2 text-[13px] font-medium text-foreground hover:bg-foreground/5 disabled:cursor-not-allowed disabled:opacity-60";

export function ProposalQuestionPanel({ project, catalog }: { project: ProposalProject; catalog: ProposalCatalog }) {
  const router = useRouter();
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // 'reading' 단계인데 작업이 없으면 서버 재시작 등으로 중단된 것 — 다시 읽기를 제안한다.
  const stuck = project.stage === "reading" && !project.reading;

  async function run(label: string, action: string, body?: unknown, after?: () => void) {
    setBusy(label);
    setError(null);
    try {
      await post(project.id, action, body);
      after?.();
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "요청을 처리하지 못했습니다.");
    } finally {
      setBusy(null);
    }
  }

  function setDraft(code: string, patch: Partial<Draft>) {
    setDrafts((d) => ({ ...d, [code]: { ...(d[code] ?? EMPTY_DRAFT), ...patch } }));
  }

  const errorBox = error && (
    <div role="alert" className="rounded-md border border-red-500/30 bg-red-500/5 px-3 py-2 text-[13px] text-red-600 dark:text-red-400">
      {error}
    </div>
  );

  if (project.reading) {
    return (
      <section className="flex items-center gap-3 rounded-xl border border-border bg-surface px-4 py-4" aria-live="polite">
        <Icon name="refresh" className="h-4 w-4 animate-spin text-accent" />
        <div>
          <div className="text-[14px] font-medium text-foreground">자료를 읽는 중입니다</div>
          <div className="text-[12px] text-foreground-subtle">
            고객 메일·요청 원문·첨부에서 질문 목록(P01~P48)의 답을 찾고 있습니다. 보통 30초~1분 걸립니다.
          </div>
        </div>
      </section>
    );
  }

  if (project.stage === "intake" || stuck) {
    return (
      <section className="flex flex-col gap-3 rounded-xl border border-border bg-surface px-4 py-4">
        <div className="text-[14px] font-medium text-foreground">
          {stuck ? "자료 읽기가 중단되었습니다." : "자료 읽기가 아직 끝나지 않았습니다."}
        </div>
        {errorBox}
        <div>
          <button type="button" className={primaryBtn} disabled={!!busy} onClick={() => void run("읽기 시작", "read")}>
            <Icon name="refresh" className={cn("h-4 w-4", busy && "animate-spin")} /> 자료 다시 읽기
          </button>
        </div>
      </section>
    );
  }

  if (project.stage !== "questioning") return null;   // 이후 단계는 작업 화면의 단계별 패널이 맡는다

  const qs = project.questions;
  const filled = qs.filter((q) => drafts[q.code]?.unknown || drafts[q.code]?.value.trim()).length;
  const canFinish = project.required_open.length === 0;

  function submitAnswers() {
    const answers = qs.map((q) => ({
      code: q.code,
      value: drafts[q.code]?.value ?? "",
      unknown: drafts[q.code]?.unknown ?? false,
    }));
    void run("답 저장 중", "answers", { answers }, () =>
      setDrafts((d) => Object.fromEntries(Object.entries(d).filter(([c]) => !qs.some((q) => q.code === c)))),
    );
  }

  return (
    <section className="flex flex-col gap-3 rounded-xl border border-accent/30 bg-surface p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-[15px] font-semibold text-foreground">핵심 질문</h2>
        <span className="text-[12px] text-foreground-subtle">
          공정 컨셉에 꼭 필요한데 자료에서 찾지 못한 것만 최대 5개 여쭙니다. 빈칸으로 두면 &lsquo;{catalog.statuses.unknown}&rsquo;으로 넘어가고 AI 가 제안합니다.
        </span>
      </div>

      {qs.length === 0 ? (
        <p className="text-[13px] text-foreground-muted">더 여쭐 문항이 없습니다.</p>
      ) : (
        <ol className="flex flex-col gap-3">
          {qs.map((q) => {
            const d = drafts[q.code] ?? EMPTY_DRAFT;
            return (
              <li key={q.code} className="rounded-lg border border-border/80 px-3 py-2.5">
                <div className="mb-0.5 text-[11px] text-foreground-subtle">{q.group}</div>
                <label htmlFor={`q-${q.code}`} className="flex flex-wrap items-baseline gap-2">
                  <span className="text-[12px] tabular-nums text-foreground-subtle">{q.code}</span>
                  <span className="text-[13.5px] text-foreground">{q.question}</span>
                  {q.required && (
                    <span className="rounded-full bg-accent-soft px-2 py-0.5 text-[10.5px] font-medium text-accent">컨셉 필수</span>
                  )}
                  {q.status === "conflict" && (
                    <span className="rounded-full bg-red-500/12 px-2 py-0.5 text-[10.5px] font-medium text-red-700 dark:text-red-300">
                      {catalog.statuses.conflict}
                    </span>
                  )}
                </label>
                {q.status === "conflict" && q.value && (
                  <p className="mt-1 text-[12px] text-foreground-muted">
                    자료 내용: {q.value}
                    {q.evidence && <span className="text-foreground-subtle"> — {q.evidence}</span>}
                  </p>
                )}
                <textarea
                  id={`q-${q.code}`}
                  rows={2}
                  maxLength={4000}
                  value={d.value}
                  disabled={d.unknown}
                  onChange={(e) => setDraft(q.code, { value: e.target.value })}
                  className="mt-2 w-full resize-y rounded-lg border border-foreground/15 bg-background px-3 py-2 text-[13px] leading-relaxed text-foreground placeholder:text-foreground-subtle/70 focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/40 disabled:opacity-50"
                />
                <label className="mt-1 inline-flex items-center gap-1.5 text-[12px] text-foreground-muted">
                  <input type="checkbox" checked={d.unknown} onChange={(e) => setDraft(q.code, { unknown: e.target.checked })} />
                  모름 (고객 확인 필요)
                </label>
              </li>
            );
          })}
        </ol>
      )}

      {errorBox}
      {!canFinish && (
        <p className="text-[12px] text-foreground-subtle">
          질문을 마치려면 컨셉 필수 문항 {project.required_open.length}개({project.required_open.join(", ")})에 답이 필요합니다.
        </p>
      )}
      <div className="flex flex-wrap items-center justify-end gap-2">
        {busy && <span className="text-[12px] text-foreground-subtle">{busy}…</span>}
        <button
          type="button"
          className={secondaryBtn}
          disabled={!!busy || !canFinish}
          onClick={() => void run("마무리 중", "finish-questions")}
        >
          질문 마치고 다음 단계로
        </button>
        {qs.length > 0 && (
          <button type="button" className={primaryBtn} disabled={!!busy} onClick={submitAnswers}>
            <Icon name="check" className="h-4 w-4" /> 답 저장 ({filled}/{qs.length})
          </button>
        )}
      </div>
    </section>
  );
}
