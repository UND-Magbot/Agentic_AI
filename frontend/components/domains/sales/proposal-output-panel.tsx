// 6단계 제작·검수 — 완성 PPT 내려받기 + 지식 저장(한 번) + 프롬프트로 수정(입력할 때마다 새 버전, 계속 이어서) + 검수 결과.
"use client";

import { useState } from "react";
import type { ProposalProject } from "@/lib/shared/proposal-projects";
import { Icon } from "@/components/shared/ui/icon";
import { ErrorBox, inputCls, primaryBtn, secondaryBtn, useProjectAction } from "./proposal-actions";
import { KnowledgeSaveButton } from "./proposal-experience-panel";

const REPORT_ROWS: { key: "overflow" | "masked" | "softened" | "missing_facts" | "warnings"; label: string; hint: string }[] = [
  { key: "overflow", label: "글자 넘침", hint: "칸을 넘친 문구 — 아래 '프롬프트로 수정'에 줄여 달라고 적으세요" },
  { key: "masked", label: "가린 수치", hint: "문항에 근거가 없어 가린 수치·등급" },
  { key: "softened", label: "순화한 표현", hint: "근거 없는 성능 단정('대응 가능' 등)을 '검토'로 바꾼 곳" },
  { key: "missing_facts", label: "빠진 확인값", hint: "확인된 문항의 수치가 해당 쪽에 없음" },
  { key: "warnings", label: "경고", hint: "" },
];

const EDIT_EXAMPLES = [
  "2쪽 핵심 수치에 300식/끼와 3,000개도 넣어 줘",
  "3쪽 공정 흐름을 세척 전 비전부터 카세트 교체까지 더 자세히",
  "실증 쪽 문장을 더 짧고 고객 입장에서 읽기 쉽게",
  "공통 적용 컨셉 쪽을 빼 줘",
];

/** 프롬프트로 수정 — 입력한 요청대로 쪽 문구·제목·순서·삭제를 고쳐 새 버전으로. 최신 버전에 이어서 계속 고칠 수 있다. */
function DeckEditBox({ project, latestVersion }: { project: ProposalProject; latestVersion: number }) {
  const { busy, error, run } = useProjectAction(project.id);
  const [request, setRequest] = useState("");
  const locked = project.job_alive;
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-accent/25 bg-accent-soft/20 p-3">
      <label htmlFor="deck-edit" className="text-[13px] font-semibold text-foreground">
        프롬프트로 수정 — PPT 를 보고 바꿀 점을 입력하면 최신 버전(v{latestVersion})에 반영해 새 버전을 만듭니다. 몇 번이든 이어서 고칠 수 있습니다
      </label>
      <textarea
        id="deck-edit"
        rows={3}
        maxLength={1000}
        value={request}
        disabled={locked}
        onChange={(e) => setRequest(e.target.value)}
        placeholder="예: 2쪽 요지를 '실증으로 확인할 3가지'가 드러나게 바꾸고, 9쪽 확인 자료에 배출 영상도 넣어 줘"
        className={inputCls}
      />
      <div className="flex flex-wrap gap-1.5">
        {EDIT_EXAMPLES.map((ex) => (
          <button key={ex} type="button" disabled={locked}
                  className="rounded-full border border-border px-2.5 py-0.5 text-[11.5px] text-foreground-muted hover:bg-foreground/5 disabled:opacity-50"
                  onClick={() => setRequest((r) => (r.trim() ? `${r.trim()}\n${ex}` : ex))}>
            {ex}
          </button>
        ))}
      </div>
      <ErrorBox error={error} />
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className={primaryBtn} disabled={!request.trim() || !!busy || locked}
                onClick={() => void run("요청 중", "deck-edit", { request }, () => setRequest(""))}>
          <Icon name="refresh" className="h-4 w-4" /> 수정 반영해 새 버전 만들기
        </button>
        <span className="text-[11.5px] text-foreground-subtle">
          {locked
            ? "진행 중인 작업이 끝나면 누를 수 있습니다."
            : "쪽 문구·제목·순서·쪽 빼기를 고칩니다(약 1분). 견적 표·컨셉 그림은 아래 '구성·견적' / '컨셉부터'에서 고치세요."}
        </span>
      </div>
    </div>
  );
}

export function ProposalOutputPanel({ project }: { project: ProposalProject }) {
  const { busy, error, run } = useProjectAction(project.id);
  const outputs = [...project.outputs].sort((a, b) => b.version - a.version);
  const latest = outputs[0];

  if (project.stage === "producing") {
    if (project.job_alive) return null;       // JobStatus 가 진행을 보여 준다
    return (
      <section className="flex flex-col gap-2 rounded-xl border border-border bg-surface px-4 py-3">
        <p className="text-[13px] text-foreground">제작이 끝나지 않았습니다.</p>
        <ErrorBox error={error} />
        <div className="flex gap-2">
          <button type="button" className={primaryBtn} disabled={!!busy} onClick={() => void run("시작 중", "produce")}>
            다시 제작
          </button>
          <button type="button" className={secondaryBtn} disabled={!!busy} onClick={() => void run("되돌리는 중", "back", { stage: "structure" })}>
            구성으로 되돌리기
          </button>
        </div>
      </section>
    );
  }
  if (!latest) return null;
  const edit = latest.report.edit;

  return (
    <section className="flex flex-col gap-3 rounded-xl border border-emerald-500/30 bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-[15px] font-semibold text-foreground">
            제안서 v{latest.version} — {latest.report.pages ?? "?"}쪽
          </h2>
          {edit && (
            <p className="text-[12px] text-foreground-muted">
              v{edit.from}에서 프롬프트로 수정: {edit.changed.join(", ")}
              {edit.note ? ` — ${edit.note}` : ""}
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-start gap-2">
          <a href={`/api/attachments/${latest.attachment_id}/download`} className={primaryBtn}>
            <Icon name="download" className="h-4 w-4" /> {latest.filename} 내려받기
          </a>
          <KnowledgeSaveButton project={project} />
        </div>
      </div>

      <DeckEditBox project={project} latestVersion={latest.version} />

      <details className="text-[12.5px]">
        <summary className="cursor-pointer select-none text-foreground-muted">
          조판 검수 결과 ({REPORT_ROWS.reduce((n, r) => n + (latest.report[r.key]?.length ?? 0), 0)}건)
        </summary>
        <dl className="mt-2 grid gap-2 sm:grid-cols-2">
          {REPORT_ROWS.map((r) => {
            const list = latest.report[r.key] ?? [];
            return (
              <div key={r.key} className="rounded-lg border border-border/70 px-3 py-2">
                <dt className="flex items-baseline justify-between text-[12.5px]">
                  <span className="font-medium text-foreground">{r.label}</span>
                  <span className={list.length ? "text-amber-700 dark:text-amber-300" : "text-emerald-700 dark:text-emerald-300"}>
                    {list.length}건
                  </span>
                </dt>
                {r.hint && <dd className="text-[11.5px] text-foreground-subtle">{r.hint}</dd>}
                {list.length > 0 && (
                  <dd>
                    <ul className="mt-1 list-disc pl-4 text-[12px] text-foreground-muted">
                      {list.slice(0, 8).map((x, i) => <li key={i}>{x}</li>)}
                      {list.length > 8 && <li>외 {list.length - 8}건</li>}
                    </ul>
                  </dd>
                )}
              </div>
            );
          })}
        </dl>
      </details>
      {outputs.length > 1 && (
        <details className="text-[12.5px]">
          <summary className="cursor-pointer text-foreground-muted">이전 버전 {outputs.length - 1}개</summary>
          <ul className="mt-1 flex flex-col gap-1">
            {outputs.slice(1).map((o) => (
              <li key={o.id}>
                <a href={`/api/attachments/${o.attachment_id}/download`} className="underline decoration-foreground/30 underline-offset-2">
                  v{o.version} {o.filename}
                </a>{" "}
                <span className="text-foreground-subtle">
                  {new Date(o.created_at).toLocaleString("ko-KR")}
                  {o.report.edit ? ` · 프롬프트 수정: ${o.report.edit.request.slice(0, 40)}` : ""}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
      <ErrorBox error={error} />
      <div className="flex flex-wrap justify-end gap-2">
        <button type="button" className={secondaryBtn} disabled={!!busy || project.job_alive} onClick={() => void run("되돌리는 중", "back", { stage: "structure" })}>
          구성·견적 고쳐서 다시 만들기
        </button>
        <button type="button" className={secondaryBtn} disabled={!!busy || project.job_alive} onClick={() => void run("되돌리는 중", "back", { stage: "concept" })}>
          컨셉부터 고치기
        </button>
      </div>
    </section>
  );
}
