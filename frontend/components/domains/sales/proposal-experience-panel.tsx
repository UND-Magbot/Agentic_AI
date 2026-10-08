// 회사 경험 — 두 시점(사용자 결정 2026-09-30).
//  - 공정 컨셉 단계: 떠올린 경험을 [컨셉에 반영] / [넘기기]. 판단하기 쉽게 그 경험이 정확히 무슨 공정이었는지
//    (공정·문제·해결 방식·적용 조건·출처 쪽)와 AI 권장(반영 권장 / 조건 확인 후)을 함께 보여 준다.
//  - 제안서 완성 후: PPT 내려받기 옆 [지식 저장] 한 번 — 컨셉마다 경험 카드로 저장하고, 반영한 경험은 맞는 지식으로
//    기록한다(낱개로 고르면 무엇을 저장할지 헷갈린다는 사용자 요청). 넘긴 경험은 기록하지 않는다.
//  - 저장한 컨셉은 영업 관리자가 승인해야 회상에 쓰인다(/proposals/knowledge).
"use client";

import Link from "next/link";
import type { ExperienceHit, KnowledgeStatus, ProposalProject } from "@/lib/shared/proposal-projects";
import { Icon } from "@/components/shared/ui/icon";
import { cn } from "@/lib/shared/utils";
import { ErrorBox, primaryBtn, secondaryBtn, useProjectAction } from "./proposal-actions";

const EVIDENCE_TONE: Record<string, string> = {
  실적: "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300",
  "실적(회사 수행)": "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300",
};

function HitHead({ hit }: { hit: ExperienceHit }) {
  const recommend = hit.fit !== "주의";
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-[12px]">
      <span
        className={cn(
          "rounded-full px-2 py-0.5 font-semibold",
          recommend
            ? "bg-accent text-white"
            : "bg-amber-500/15 text-amber-700 dark:text-amber-300",
        )}
        title={recommend ? "이번 조건과 잘 맞아 반영을 권합니다" : "조건이 일부 달라 주의 사항을 확인한 뒤 반영하세요"}
      >
        {recommend ? "AI 판단: 반영 권장" : "AI 판단: 조건 확인 후 반영"}
      </span>
      <span className={cn("rounded-full px-2 py-0.5 font-medium", EVIDENCE_TONE[hit.evidence] ?? "bg-sky-500/12 text-sky-700 dark:text-sky-300")}>
        {hit.evidence || "경험"}
      </span>
      <span className="font-medium text-foreground">{hit.title}</span>
    </div>
  );
}

/** 그 경험이 정확히 무슨 공정이었나 — 반영 여부를 판단할 근거. */
function HitDetail({ hit }: { hit: ExperienceHit }) {
  const d = hit.detail ?? {};
  const rows: [string, string | undefined][] = [
    ["공정", d.process || hit.process],
    ["그때 문제", d.problem],
    ["해결 방식", d.solution || d.how_it_works],
    ["잘 맞는 조건", d.applies_when],
    ["업종", d.industry],
  ];
  const where = [hit.source, d.pages?.length ? `${d.pages.join("·")}쪽` : ""].filter(Boolean).join(" ");
  const shown = rows.filter(([, v]) => v);
  if (!shown.length && !where) return null;
  return (
    <dl className="grid gap-x-3 gap-y-1 rounded-md bg-foreground/[0.03] px-3 py-2 text-[12.5px] sm:grid-cols-[88px_1fr]">
      {shown.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-foreground-subtle">{k}</dt>
          <dd className="text-foreground">{v}</dd>
        </div>
      ))}
      {where && (
        <div className="contents">
          <dt className="text-foreground-subtle">출처</dt>
          <dd className="text-foreground-muted">{where}</dd>
        </div>
      )}
    </dl>
  );
}

/** 공정 컨셉 단계 — 이번 컨셉에 쓸지만 고른다. */
function ConceptHit({ project, hit }: { project: ProposalProject; hit: ExperienceHit }) {
  const { busy, error, run } = useProjectAction(project.id);
  const locked = project.job_alive;
  const done = hit.status !== "suggested";
  return (
    <li className={cn("flex flex-col gap-2 rounded-lg border px-3 py-2.5", done ? "border-border/60 opacity-75" : "border-border")}>
      <HitHead hit={hit} />
      <HitDetail hit={hit} />
      <p className="text-[13px] leading-relaxed text-foreground">
        <span className="font-medium">이번에 쓸 점 — </span>{hit.suggestion}
      </p>
      {hit.effect_numbers.length > 0 && (
        <p className="text-[12px] text-foreground-muted">
          효과(기록된 수치): {hit.effect_numbers.map((e) => `${e.metric} ${e.value}`).join(" · ")}
        </p>
      )}
      {hit.caution && <p className="text-[12px] text-amber-700 dark:text-amber-300">주의: {hit.caution}</p>}
      {!!hit.facts?.length && (
        <details className="text-[12px] text-foreground-muted">
          <summary className="cursor-pointer select-none">근거 문장 {hit.facts.length}개</summary>
          <ul className="mt-1 list-disc pl-4">
            {hit.facts.map((f, i) => <li key={i}>{f}</li>)}
          </ul>
        </details>
      )}
      <ErrorBox error={error} />
      {done ? (
        <p className="text-[12px] font-medium text-foreground-muted">
          {hit.status === "reflected" ? "컨셉에 반영했습니다 — 바뀐 점은 아래 컨셉 카드에 표시됩니다" : "이번 컨셉에는 쓰지 않습니다"}
        </p>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className={primaryBtn} disabled={!!busy || locked}
                  onClick={() => void run("반영 중", "experience", { card_id: hit.card_id, action: "reflect" })}>
            <Icon name="check" className="h-4 w-4" /> 컨셉에 반영
          </button>
          <button type="button" className={secondaryBtn} disabled={!!busy || locked}
                  onClick={() => void run("넘기는 중", "experience", { card_id: hit.card_id, action: "dismiss" })}>
            넘기기
          </button>
          <span className="text-[11.5px] text-foreground-subtle">
            {locked ? "진행 중인 작업이 끝나면 누를 수 있습니다." : "반영하면 이 경험의 방식을 구성에 넣어 새 버전을 그립니다."}
          </span>
        </div>
      )}
    </li>
  );
}

export function ProposalExperiencePanel({ project }: { project: ProposalProject }) {
  if (!project.experience.length) return null;
  return (
    <section className="flex flex-col gap-2 rounded-lg border border-accent/20 bg-accent-soft/30 p-3">
      <div>
        <h3 className="text-[14px] font-semibold text-foreground">이전에 이런 경험이 있습니다</h3>
        <p className="text-[12px] text-foreground-subtle">
          그 경험이 무슨 공정이었는지 보고 이번 컨셉에 쓸지만 고르세요. 실적은 실제 수행 결과, 제안은 검증 전 제안입니다.
        </p>
      </div>
      <ul className="flex flex-col gap-2">
        {project.experience.map((h) => <ConceptHit key={`${h.card_table}-${h.card_id}`} project={project} hit={h} />)}
      </ul>
    </section>
  );
}

const KNOWLEDGE_STATUS: Record<KnowledgeStatus, string> = {
  approved: "승인됨",
  pending: "승인 대기",
  rejected: "반려",
};

/** 제안서 완성 후 — PPT 내려받기 옆 [지식 저장] 한 번. 무엇이 저장되는지 미리 한 줄로 알려 준다. */
export function KnowledgeSaveButton({ project }: { project: ProposalProject }) {
  const { busy, error, run } = useProjectAction(project.id);
  const reflected = project.experience.filter((h) => h.status === "reflected");
  const toJudge = reflected.filter((h) => !h.judged);
  const toSave = project.alternatives.filter((a) => !a.knowledge_card_id || a.knowledge_status === "rejected");
  const saved = project.alternatives.filter((a) => a.knowledge_card_id && a.knowledge_status !== "rejected");
  const pending = toSave.length > 0 || toJudge.length > 0;
  const what = [
    toSave.length ? `컨셉 ${toSave.length}개를 경험 카드로` : "",
    toJudge.length ? `반영한 회사 경험 ${toJudge.length}개를 맞는 지식으로` : "",
  ].filter(Boolean).join(", ");
  return (
    <div className="flex flex-col items-end gap-1">
      {pending ? (
        <button type="button" className={secondaryBtn} disabled={!!busy || project.job_alive}
                title={`${what} 저장합니다. 영업 관리자가 승인하면 다음 프로젝트의 공정 컨셉 때 떠오릅니다.`}
                onClick={() => void run("저장 중", "save-knowledge-all")}>
          <Icon name="check" className="h-4 w-4" /> {busy ? "저장 중…" : "지식 저장"}
        </button>
      ) : (
        <span className="inline-flex items-center gap-1.5 rounded-xl border border-emerald-500/30 px-3 py-2 text-[12.5px] font-medium text-emerald-700 dark:text-emerald-300">
          <Icon name="check" className="h-4 w-4" /> 지식 저장됨
        </span>
      )}
      <span className="max-w-[340px] text-right text-[11.5px] text-foreground-subtle">
        {pending
          ? `${what} 저장합니다.`
          : saved.map((a) => `E${a.knowledge_card_id} ${KNOWLEDGE_STATUS[a.knowledge_status ?? "pending"]}`).join(" · ")}
        {project.knowledge_approver && (
          <>
            {" "}
            <Link href="/proposals/knowledge" className="font-medium text-accent hover:underline">승인 화면 →</Link>
          </>
        )}
      </span>
      <ErrorBox error={error} />
    </div>
  );
}
