// 3단계 앞부분 — 공정 컨셉 계획 확인(이미지를 그리기 전에 글로 결정, 사용자 요청 2026-09-30).
// AI 가 종합해 낸 컨셉을 요약으로 보여 주고, 여기서 로봇 팔(로봇 DB 후보)·그리퍼(회사 제품 후보)·회사 경험 반영을
// 정한다. 마음에 안 드는 점은 계획 수정 요청(프롬프트)으로 글만 고친다 — 이미지는 [이 계획으로 그리기] 때 한 번.
"use client";

import { useState } from "react";
import type { Alternative, ConceptPlan, ProposalProject } from "@/lib/shared/proposal-projects";
import { Icon } from "@/components/shared/ui/icon";
import { cn } from "@/lib/shared/utils";
import { ChangesBox } from "./proposal-changes";
import { ProposalExperiencePanel } from "./proposal-experience-panel";
import { ErrorBox, inputCls, primaryBtn, secondaryBtn, useProjectAction } from "./proposal-actions";

const MAX_CONCEPTS = 3;

type Picks = Record<string, { robot: string; gripper: string }>;

function num(v: number | null | undefined, unit: string) {
  return v == null ? "확인 필요" : `${v.toLocaleString("ko-KR")}${unit}`;
}

function needLine(plan: ConceptPlan) {
  const n = plan.robot_need;
  const parts = [
    n.payload_kg != null ? `가반하중 ${n.payload_kg}kg 이상` : "",
    n.reach_mm != null ? `도달거리 ${n.reach_mm.toLocaleString("ko-KR")}mm 이상` : "",
    n.ip_min != null ? `IP${n.ip_min} 이상` : "",
  ].filter(Boolean);
  return parts.join(" · ");
}

/** 후보를 고른 기준 한 줄 — 회사 우선순위(K03)·지정 제조사·AMR 탑재·제외 제조사. */
function policyLine(plan: ConceptPlan) {
  const n = plan.robot_need;
  const bits = [
    n.makers?.length ? `지정 제조사 ${n.makers.join("·")} 우선` : n.priority === "domestic"
      ? "회사 기준: 국내 신뢰성 중시 → 레인보우 우선, 한화 국내 후보"
      : "회사 기준: 가격 중심 → DOBOT 우선, 국내 후보 레인보우·한화",
    n.mobile ? "AMR 탑재 — 가벼운 모델 우선" : "",
    n.exclude_makers?.length ? `제외: ${n.exclude_makers.join("·")}` : "",
  ].filter(Boolean);
  return bits.join(" · ");
}

/** 후보 목록 + 직접 입력 — 로봇·그리퍼 공용. */
function Choice({
  name, options, value, recommended, disabled, onChange, render,
}: {
  name: string; options: string[]; value: string; recommended?: string; disabled: boolean;
  onChange: (v: string) => void; render?: (opt: string) => React.ReactNode;
}) {
  const custom = !!value && !options.includes(value);
  const [draft, setDraft] = useState(custom ? value : "");
  return (
    <div className="flex flex-col gap-1">
      {options.map((opt) => (
        <label key={opt} className={cn("flex cursor-pointer items-start gap-2 rounded-md border px-2.5 py-1.5 text-[12.5px]",
          value === opt ? "border-accent bg-accent-soft/30" : "border-border hover:bg-foreground/[0.03]")}>
          <input type="radio" name={name} className="mt-0.5" checked={value === opt} disabled={disabled}
                 onChange={() => onChange(opt)} />
          <span className="flex-1">
            {render ? render(opt) : <span className="font-medium text-foreground">{opt}</span>}
          </span>
          {opt === recommended && (
            <span className="shrink-0 rounded-full bg-accent px-2 py-0.5 text-[11px] font-semibold text-white">AI 추천</span>
          )}
        </label>
      ))}
      <label className={cn("flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-[12.5px]",
        custom ? "border-accent bg-accent-soft/30" : "border-border")}>
        <input type="radio" name={name} checked={custom} disabled={disabled}
               onChange={() => draft.trim() && onChange(draft.trim())} />
        <span className="shrink-0 text-foreground-muted">직접 입력</span>
        <input className={`${inputCls} py-1`} value={draft} maxLength={60} disabled={disabled}
               placeholder="후보에 없으면 모델·제품 이름"
               onChange={(e) => setDraft(e.target.value)}
               onBlur={() => draft.trim() && draft.trim() !== value && onChange(draft.trim())} />
      </label>
    </div>
  );
}

function PlanCard({
  project, alt, count, picks, setPick, locked,
}: {
  project: ProposalProject; alt: Alternative & { plan: ConceptPlan }; count: number; picks: Picks;
  setPick: (altId: string, key: "robot" | "gripper", v: string) => void; locked: boolean;
}) {
  const { busy, error, run } = useProjectAction(project.id);
  const [request, setRequest] = useState("");
  const plan = alt.plan;
  const pick = picks[alt.id] ?? { robot: plan.robot_pick, gripper: plan.gripper_pick };
  const robots = plan.robot_candidates;
  const byLabel = Object.fromEntries(robots.map((r) => [r.label, r]));
  const title = count <= 1 ? "공정 컨셉" : `컨셉 ${alt.id}`;
  const structure = alt.structure.filter((s) => !s.startsWith("선택 모델:"));

  return (
    <article className="flex flex-col gap-3 rounded-lg border border-border p-3">
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="rounded-md bg-accent-soft px-2 py-0.5 text-[12px] font-semibold text-accent">{title}</span>
        <span className="text-[14px] font-semibold text-foreground">{alt.name}</span>
        <span className="text-[12.5px] text-foreground-muted">{alt.robot}</span>
      </div>
      {alt.summary && <p className="text-[13px] text-foreground">{alt.summary}</p>}
      {alt.reason && <p className="text-[12.5px] text-foreground-muted">제안 이유: {alt.reason}</p>}

      <div className="rounded-md bg-foreground/[0.03] px-3 py-2">
        <div className="mb-1 text-[12.5px] font-semibold text-foreground">공정 구성 (그림에 그대로 들어갑니다)</div>
        <ol className="list-decimal pl-5 text-[12.5px] leading-relaxed text-foreground">
          {structure.map((s, i) => <li key={i}>{s}</li>)}
        </ol>
        {alt.exclude.length > 0 && (
          <p className="mt-1 text-[12px] text-foreground-subtle">그림에서 뺄 것: {alt.exclude.join(" · ")}</p>
        )}
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <section className="flex flex-col gap-1.5">
          <h4 className="text-[13px] font-semibold text-foreground">로봇 팔 — 로봇 DB 후보</h4>
          {plan.robot_need.arm ? (
            <p className="text-[12px] text-foreground-muted">
              필요 사양(AI 추정): {needLine(plan) || "추정 못 함"}
              {plan.robot_need.why && <> — {plan.robot_need.why}</>}
            </p>
          ) : (
            <p className="text-[12px] text-foreground-muted">
              6축 협동로봇 팔을 쓰지 않는 구성입니다{plan.robot_need.why ? ` — ${plan.robot_need.why}` : ""}. 필요하면 직접 입력하세요.
            </p>
          )}
          {plan.robot_need.arm && <p className="text-[12px] text-foreground-subtle">{policyLine(plan)}</p>}
          {robots.some((r) => r.ip_short) && (
            <p className="text-[12px] text-amber-700 dark:text-amber-300">
              IP 조건까지 맞는 모델이 DB 에 없어 IP 조건을 빼고 고른 후보입니다. 보호 커버 등을 검토하세요.
            </p>
          )}
          {plan.robot_need.arm && !robots.length && (
            <p className="text-[12px] text-amber-700 dark:text-amber-300">조건에 맞는 모델이 로봇 DB 에 없습니다. 직접 입력하거나 계획 수정 요청으로 조건을 바꾸세요.</p>
          )}
          <Choice
            name={`robot-${alt.id}`}
            options={robots.map((r) => r.label)}
            value={pick.robot}
            recommended={robots[0]?.label}
            disabled={locked || !!busy}
            onChange={(v) => setPick(alt.id, "robot", v)}
            render={(opt) => {
              const r = byLabel[opt];
              return (
                <>
                  <span className="font-medium text-foreground">{opt}</span>
                  {r?.reach_short && (
                    <span className="ml-1.5 rounded-full bg-amber-500/15 px-1.5 py-0.5 text-[10.5px] font-medium text-amber-700 dark:text-amber-300">
                      도달거리 약간 부족
                    </span>
                  )}
                  {r?.policy_note && <span className="block text-[11.5px] text-accent">{r.policy_note}</span>}
                  {r && (
                    <span className="block text-[11.5px] text-foreground-muted">
                      가반 {num(r.payload_kg, "kg")} · 도달 {num(r.reach_mm, "mm")} · 반복 ±{num(r.repeatability_mm, "mm")}
                      {" "}· 중량 {num(r.weight_kg, "kg")} · {r.ip_rating ?? "IP 확인 필요"}
                    </span>
                  )}
                </>
              );
            }}
          />
        </section>
        <section className="flex flex-col gap-1.5">
          <h4 className="text-[13px] font-semibold text-foreground">그리퍼 — 회사 제품 후보</h4>
          {plan.gripper_ai && (
            <p className="text-[12px] text-foreground-muted">
              AI 추천: {plan.gripper_ai}{plan.gripper_why ? ` — ${plan.gripper_why}` : ""}
            </p>
          )}
          <Choice
            name={`gripper-${alt.id}`}
            options={plan.gripper_candidates}
            value={pick.gripper}
            recommended={plan.gripper_ai}
            disabled={locked || !!busy}
            onChange={(v) => setPick(alt.id, "gripper", v)}
          />
        </section>
      </div>

      {alt.changes && <ChangesBox changes={alt.changes} planning />}
      <div className="flex flex-col gap-1.5 rounded-lg bg-foreground/[0.03] p-2.5">
        <label htmlFor={`plan-rev-${alt.id}`} className="text-[12.5px] font-medium text-foreground">
          계획 수정 요청 — 마음에 안 드는 점을 적으면 글로 먼저 고칩니다(이미지는 아직 그리지 않음)
        </label>
        <textarea id={`plan-rev-${alt.id}`} rows={2} maxLength={1000} value={request} disabled={locked}
                  onChange={(e) => setRequest(e.target.value)} className={inputCls}
                  placeholder="예: 로봇은 한 대로 줄이고 카세트는 로봇 뒤쪽에. 그리퍼는 석션으로, 로봇은 DOBOT 10kg급으로(KUKA 제외)." />
        <ErrorBox error={error} />
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className={secondaryBtn} disabled={!request.trim() || !!busy || locked}
                  onClick={() => void run("반영 중", "revise", { alt_id: alt.id, request }, () => setRequest(""))}>
            <Icon name="refresh" className="h-4 w-4" /> 계획에 반영
          </button>
          <span className="text-[11.5px] text-foreground-subtle">
            {locked ? "진행 중인 작업이 끝나면 누를 수 있습니다." : "30초 안팎. 로봇 후보도 새 조건으로 다시 고릅니다."}
          </span>
        </div>
      </div>
    </article>
  );
}

export function ProposalPlanPanel({ project }: { project: ProposalProject }) {
  const { busy, error, run } = useProjectAction(project.id);
  const [picks, setPicks] = useState<Picks>({});
  const locked = project.job_alive;
  const pending = project.alternatives.filter((a): a is Alternative & { plan: ConceptPlan } => !!a.plan && !a.plan.confirmed);
  const undecided = project.experience.filter((h) => h.status === "suggested").length;

  function setPick(altId: string, key: "robot" | "gripper", v: string) {
    const alt = pending.find((a) => a.id === altId);
    if (!alt) return;
    const cur = picks[altId] ?? { robot: alt.plan.robot_pick, gripper: alt.plan.gripper_pick };
    setPicks({ ...picks, [altId]: { ...cur, [key]: v } });
    void run("저장 중", "plan-select", { alt_id: altId, [key === "robot" ? "robot_pick" : "gripper_pick"]: v });
  }

  function confirm() {
    const body = pending.map((a) => {
      const p = picks[a.id] ?? { robot: a.plan.robot_pick, gripper: a.plan.gripper_pick };
      return { alt_id: a.id, robot_pick: p.robot, gripper_pick: p.gripper };
    });
    void run("시작 중", "plan-confirm", { picks: body });
  }

  return (
    <section className="flex flex-col gap-4 rounded-xl border border-accent/30 bg-surface p-4">
      <div>
        <h2 className="text-[15px] font-semibold text-foreground">공정 컨셉 계획 확인 — 이미지를 그리기 전에 정하세요</h2>
        <p className="text-[12.5px] text-foreground-muted">
          AI 가 질문 답변을 종합해 낸 계획입니다. 로봇 팔·그리퍼를 고르고, 회사 경험을 반영할지 정하고, 고칠 점은 계획 수정
          요청에 적으세요. 확정하면 이 내용 그대로 컨셉 이미지를 그립니다.
        </p>
      </div>

      {locked && project.job?.kind === "concept" && (
        // 계획은 먼저 보이고 회사 경험 회상이 뒤에서 1분쯤 더 돈다(QA 2회차) — 그동안 버튼이 잠기는 이유를 알린다.
        <p className="flex items-center gap-2 rounded-lg bg-accent-soft/30 px-3 py-2 text-[12.5px] text-foreground-muted">
          <Icon name="refresh" className="h-4 w-4 animate-spin text-accent" />
          회사 경험을 떠올리는 중입니다(약 1분). 끝나면 여기에 나타나고 버튼을 누를 수 있습니다. 그동안 계획을 읽어 보세요.
        </p>
      )}
      <ProposalExperiencePanel project={project} />

      {pending.map((a) => (
        <PlanCard key={a.id} project={project} alt={a} count={project.alternatives.length} picks={picks}
                  setPick={setPick} locked={locked} />
      ))}

      <ErrorBox error={error} />
      <div className="flex flex-wrap items-center justify-end gap-2">
        {busy && <span className="text-[12px] text-foreground-subtle">{busy}…</span>}
        {undecided > 0 && (
          <span className="text-[12px] text-amber-700 dark:text-amber-300">
            고르지 않은 회사 경험 {undecided}개 — 그대로 진행하면 반영하지 않습니다.
          </span>
        )}
        {project.alternatives.length < MAX_CONCEPTS && (
          <button type="button" className={secondaryBtn} disabled={!!busy || locked}
                  title="고객이 다른 로봇 형태와의 비교를 원할 때"
                  onClick={() => void run("요청 중", "extra")}>
            비교안 추가 (AI 제안)
          </button>
        )}
        <button type="button" className={primaryBtn} disabled={!!busy || locked} onClick={confirm}>
          <Icon name="check" className="h-4 w-4" /> 이 계획으로 컨셉 이미지 그리기
        </button>
      </div>
    </section>
  );
}
