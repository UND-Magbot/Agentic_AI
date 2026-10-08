// 제안서 작업 화면 — 가이드 "질문에서 제작까지" 단계 표시 + 단계별 패널 + 첨부·입력 자료·진행 기록.
// 문항 현황(P01~P48 목록·상태 개수)은 보여 주지 않는다 — 비어 있음이 잔뜩 보여 난잡하다(사용자 결정 2026-09-30).
"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import type { ProposalCatalog, ProposalProject } from "@/lib/shared/proposal-projects";
import { Icon } from "@/components/shared/ui/icon";
import { JobStatus, QuickBuild } from "./proposal-actions";
import { ProposalConceptPanel } from "./proposal-concept-panel";
import { ProposalOutputPanel } from "./proposal-output-panel";
import { ProposalQuestionPanel } from "./proposal-question-panel";
import { ProposalStructurePanel } from "./proposal-structure-panel";
import { cn } from "@/lib/shared/utils";

const STEPS: { key: string; label: string }[] = [
  { key: "intake", label: "자료 입력" },
  { key: "reading", label: "자료 읽기" },
  { key: "questioning", label: "핵심 질문" },
  { key: "concept", label: "공정·컨셉 이미지" },
  { key: "structure", label: "구성 확인" },
  { key: "producing", label: "제작·검수" },
  { key: "done", label: "완료" },
];

const POLL_MS = 3000;

export function ProposalWorkspace({ project, catalog }: { project: ProposalProject; catalog: ProposalCatalog }) {
  const router = useRouter();
  // 이미지 검수 초안은 작업이 끝난 뒤에도 뒤에서 채워진다(vision_pending) — 그동안도 새로 받아 온다.
  const busy = project.reading || project.job_alive || project.vision_pending;

  // 자료 읽기·공정 제안·이미지·제작은 백그라운드 작업 — 끝날 때까지 서버 데이터를 다시 받아 온다.
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(() => router.refresh(), POLL_MS);
    return () => clearInterval(t);
  }, [busy, router]);

  const stepIndex = Math.max(0, STEPS.findIndex((s) => s.key === project.stage));

  // 서버 기준(dialog.required_open) — 확인됨·채택 컨셉·가정이면 채워진 것으로 본다.
  const requiredOpen = project.required_open;

  return (
    <div className="scroll-thin h-full overflow-y-auto">
      <div className="mx-auto flex max-w-5xl flex-col gap-6 px-4 py-8 md:px-6">
        <header className="flex flex-col gap-3">
          <div className="flex items-center gap-2 text-[12px] text-foreground-subtle">
            <Link href="/chat/sales" className="inline-flex items-center gap-1 hover:text-foreground">
              <Icon name="back" className="h-3.5 w-3.5" /> 기술영업
            </Link>
            <span>·</span>
            <span>제안서 작업</span>
          </div>
          <h1 className="text-[22px] font-semibold tracking-tight text-foreground">{project.title}</h1>
          <ol className="flex flex-wrap gap-1.5" aria-label="진행 단계">
            {STEPS.map((s, i) => (
              <li
                key={s.key}
                aria-current={i === stepIndex ? "step" : undefined}
                className={cn(
                  "rounded-full px-3 py-1 text-[12px]",
                  i < stepIndex && "bg-accent-soft text-accent",
                  i === stepIndex && "und-grad font-semibold text-white",
                  i > stepIndex && "bg-foreground/5 text-foreground-subtle",
                )}
              >
                {i + 1}. {s.label}
              </li>
            ))}
          </ol>
        </header>

        <JobStatus project={project} />
        <ProposalQuestionPanel project={project} catalog={catalog} />
        {project.stage === "concept" && <ProposalConceptPanel project={project} />}
        {project.stage === "structure" && <ProposalStructurePanel project={project} />}
        {(project.stage === "producing" || project.stage === "done") && <ProposalOutputPanel project={project} />}
        {["questioning", "concept", "structure"].includes(project.stage) && <QuickBuild project={project} />}

        {requiredOpen.length > 0 && ["intake", "reading", "questioning"].includes(project.stage) && (
          <div className="rounded-xl border border-amber-500/30 bg-amber-500/8 px-4 py-3 text-[13px] text-foreground">
            컨셉 초안을 그리려면 아직 {requiredOpen.length}개 핵심 문항이 필요합니다:{" "}
            {requiredOpen.map((c) => catalog.questions[c].question).join(" · ")}
          </div>
        )}

        <InputAnswers project={project} catalog={catalog} />

        <div className="grid gap-4 lg:grid-cols-2">
          <div className="rounded-xl border border-border bg-surface p-4">
            <h2 className="text-[14px] font-semibold text-foreground">첨부 자료</h2>
            {project.assets.length === 0 ? (
              <p className="mt-1 text-[12px] text-foreground-subtle">첨부한 자료가 없습니다.</p>
            ) : (
              <ul className="mt-2 flex flex-col gap-2">
                {project.assets.map((a) => (
                  <li key={a.id} className="text-[12px]">
                    <a href={`/api/attachments/${a.attachment_id}/download`} className="block truncate text-foreground underline decoration-foreground/30 underline-offset-2">
                      {a.filename}
                    </a>
                    <div className="text-foreground-subtle">
                      {catalog.asset_roles[a.role]}
                      {a.external_ok ? " · 외부 전송 허용" : ""}
                      {a.readable === true ? " · 읽음" : a.readable === false ? " · 글자를 읽지 못함" : ""}
                      {a.note ? ` · ${a.note}` : ""}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="rounded-xl border border-border bg-surface p-4">
            <h2 className="text-[14px] font-semibold text-foreground">진행 기록</h2>
            <ul className="mt-2 flex flex-col gap-1.5">
              {project.messages.map((m, i) => (
                <li key={i} className="text-[12.5px] text-foreground">
                  <span className="text-foreground-subtle">{new Date(m.at).toLocaleString("ko-KR")}</span>
                  <div className="whitespace-pre-wrap">{m.content}</div>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
}

/** 첫 화면 답 — 묶음별로 질문과 답을 나눠 보여 준다(답한 문항만). 예전 폼(묶음 키)의 답과 요청 원문은 아래에. */
function InputAnswers({ project, catalog }: { project: ProposalProject; catalog: ProposalCatalog }) {
  const groups = [...catalog.essential, ...catalog.optional]
    .map((g) => ({ ...g, questions: g.questions.filter((q) => project.intake[q.code]?.trim()) }))
    .filter((g) => g.questions.length > 0);
  const legacy = catalog.sections.filter((s) => project.intake[s.key]?.trim());
  const count = groups.reduce((n, g) => n + g.questions.length, 0);
  if (!groups.length && !legacy.length && !project.request_text) return null;

  return (
    <section className="flex flex-col gap-4 rounded-xl border border-border bg-surface p-4">
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="text-[15px] font-semibold text-foreground">입력 자료</h2>
        {count > 0 && <span className="text-[12px] text-foreground-subtle">첫 화면 답변 {count}개</span>}
      </div>
      {groups.map((g) => (
        <div key={g.title} className="flex flex-col gap-2">
          <h3 className="text-[12px] font-semibold text-foreground-muted">{g.title}</h3>
          <dl className="grid gap-2 md:grid-cols-2">
            {g.questions.map((q) => (
              <div key={q.code} className="flex flex-col gap-1.5 rounded-lg border border-border/80 bg-background px-3 py-2.5">
                <dt className="flex gap-1.5 text-[12px] leading-snug text-foreground-muted">
                  <span className="shrink-0 font-semibold text-accent">Q</span>
                  <span>{q.question}</span>
                </dt>
                <dd className="flex gap-1.5 text-[13px] leading-relaxed text-foreground">
                  <span className="shrink-0 font-semibold text-foreground-subtle">A</span>
                  <span className="whitespace-pre-wrap">{project.intake[q.code]}</span>
                </dd>
              </div>
            ))}
          </dl>
        </div>
      ))}
      {legacy.map((sec) => (
        <div key={sec.key} className="flex flex-col gap-1">
          <h3 className="text-[12px] font-semibold text-foreground-muted">{sec.title}</h3>
          <p className="whitespace-pre-wrap rounded-lg border border-border/80 bg-background px-3 py-2.5 text-[13px] text-foreground">
            {project.intake[sec.key]}
          </p>
        </div>
      ))}
      {project.request_text && (
        <details open={count === 0 && legacy.length === 0}>
          <summary className="cursor-pointer text-[12px] font-semibold text-foreground-muted">고객 메일·요청 원문(기타 메모)</summary>
          <p className="mt-2 whitespace-pre-wrap rounded-lg border border-border/80 bg-background px-3 py-2.5 text-[13px] text-foreground">
            {project.request_text}
          </p>
        </details>
      )}
    </section>
  );
}
