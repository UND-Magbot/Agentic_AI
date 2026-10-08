// 제안서 작업 화면 공용 — 단계 동작 호출, 진행 중 작업 표시, 바로 만들기(실행 시험용) 버튼.
"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { ProposalProject } from "@/lib/shared/proposal-projects";
import { Icon } from "@/components/shared/ui/icon";

const JOB_LABEL: Record<string, string> = {
  propose: "공정 컨셉 제안",
  concept: "공정 컨셉 계획 만들기",
  "plan-confirm": "계획 확정·컨셉 이미지 생성",
  extra: "비교안 추가",
  revise: "수정 요청 반영",
  image: "컨셉 이미지 생성",
  structure: "페이지 구성·견적 초안",
  produce: "제안서 제작",
  "deck-edit": "프롬프트 수정 반영",
  quick: "바로 제안서 만들기",
};

export const primaryBtn =
  "und-grad inline-flex items-center gap-2 rounded-xl px-4 py-2 text-[13px] font-semibold text-white disabled:cursor-not-allowed disabled:opacity-60";
export const secondaryBtn =
  "inline-flex items-center gap-2 rounded-xl border border-border bg-surface px-4 py-2 text-[13px] font-medium text-foreground hover:bg-foreground/5 disabled:cursor-not-allowed disabled:opacity-60";
export const inputCls =
  "w-full rounded-lg border border-foreground/15 bg-background px-2.5 py-1.5 text-[13px] text-foreground placeholder:text-foreground-subtle/70 focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/40";

export async function postAction(projectId: number, action: string, body?: unknown) {
  const r = await fetch(`/api/proposal-projects/${projectId}/${action}`, {
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof d?.detail === "string" ? d.detail : "요청을 처리하지 못했습니다.");
  return d;
}

/** 동작 호출 + 바쁨·오류 상태. 성공하면 서버 데이터를 다시 받는다. */
export function useProjectAction(projectId: number) {
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function run(label: string, action: string, body?: unknown, after?: () => void) {
    setBusy(label);
    setError(null);
    try {
      await postAction(projectId, action, body);
      after?.();
      router.refresh();
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : "요청을 처리하지 못했습니다.");
      return false;
    } finally {
      setBusy(null);
    }
  }
  return { busy, error, run, setError };
}

export function ErrorBox({ error }: { error: string | null }) {
  if (!error) return null;
  return (
    <div role="alert" className="rounded-md border border-red-500/30 bg-red-500/5 px-3 py-2 text-[13px] text-red-600 dark:text-red-400">
      {error}
    </div>
  );
}

/** 백그라운드 작업 상태 — 진행 중·실패·중단. 완료 메시지는 진행 기록에 남으므로 여기선 숨긴다. */
export function JobStatus({ project }: { project: ProposalProject }) {
  const job = project.job ?? {};
  const label = JOB_LABEL[job.kind ?? ""] ?? "작업";
  if (project.job_alive) {
    return (
      <section className="flex items-center gap-3 rounded-xl border border-accent/30 bg-surface px-4 py-3" aria-live="polite">
        <Icon name="refresh" className="h-4 w-4 animate-spin text-accent" />
        <div className="text-[13px]">
          <span className="font-medium text-foreground">{label} 진행 중</span>
          {job.step && <span className="ml-2 text-foreground-subtle">— {job.step}</span>}
          {["image", "quick", "plan-confirm", "revise"].includes(job.kind ?? "") ? (
            <div className="text-[12px] text-foreground-subtle">이미지 생성은 장당 1~2분 걸립니다. 이 화면을 떠나도 계속 진행됩니다.</div>
          ) : null}
        </div>
      </section>
    );
  }
  if (job.status === "failed") {
    return <ErrorBox error={job.message || `${label}에 실패했습니다.`} />;
  }
  if (job.status === "running") {
    return <ErrorBox error={`${label}이 중단되었습니다(서버 재시작 등). 다시 시도해 주세요.`} />;
  }
  return null;
}

/** 바로 제안서 만들기 — 실행 시험용. 남은 질문·승인을 건너뛰고 AI 초안으로 제작까지. */
export function QuickBuild({ project }: { project: ProposalProject }) {
  const { busy, error, run } = useProjectAction(project.id);
  const [withImages, setWithImages] = useState(false);
  if (project.stage === "done" || project.job_alive) return null;
  return (
    <details className="rounded-xl border border-dashed border-foreground/20 px-4 py-3 text-[13px]">
      <summary className="cursor-pointer select-none font-medium text-foreground-muted">
        바로 제안서 만들기 (실행 시험용)
      </summary>
      <div className="mt-2 flex flex-col gap-2">
        <p className="text-[12px] text-foreground-subtle">
          남은 질문과 승인을 건너뛰고 AI 초안(공정 컨셉·구성·견적 행)을 그대로 채택해 PPT 까지 만듭니다.
          건너뛴 항목은 진행 기록과 결과 경고에 남습니다.
        </p>
        <label className="inline-flex items-center gap-1.5 text-[12px] text-foreground-muted">
          <input type="checkbox" checked={withImages} onChange={(e) => setWithImages(e.target.checked)} />
          컨셉 이미지도 만들기 (1~2분)
        </label>
        <ErrorBox error={error} />
        <div>
          <button
            type="button"
            className={secondaryBtn}
            disabled={!!busy}
            onClick={() => void run("시작 중", "quick", { with_images: withImages })}
          >
            <Icon name="refresh" className={busy ? "h-4 w-4 animate-spin" : "h-4 w-4"} /> 바로 만들기
          </button>
        </div>
      </div>
    </details>
  );
}
