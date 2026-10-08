// 3단계 공정 컨셉 — 질문을 마치면 AI 가 공정 컨셉(기본 1개)과 컨셉 이미지를 만든다.
// 사용자는 말로 수정 요청을 여러 번 넣어 새 버전(v2, v3…)을 받고, 마음에 드는 버전을 승인한다.
// 비교안은 고객이 비교를 원할 때만 '비교안 추가' 로 더한다(최대 3개).
// 수정 요청은 구성 문장을 직접 고친 뒤에도 막히지 않는다 — 고친 구성을 먼저 저장하고 반영한다(사용자: v4 쯤에서
// 버튼이 이유 없이 잠겨 포기함). 수정할 때마다 구성에서 바뀐 점과 이전 버전 그림을 나란히 보여 준다.
"use client";

import { useState } from "react";
import type { Alternative, ConceptImage, ProposalProject } from "@/lib/shared/proposal-projects";
import { Icon } from "@/components/shared/ui/icon";
import { cn } from "@/lib/shared/utils";
import { ChangesBox } from "./proposal-changes";
import { ConceptImageCard } from "./proposal-concept-image";
import { ProposalPlanPanel } from "./proposal-plan-panel";
import { ProposalExperiencePanel } from "./proposal-experience-panel";
import { ErrorBox, inputCls, postAction, primaryBtn, secondaryBtn, useProjectAction } from "./proposal-actions";

const MAX_CONCEPTS = 3;

type Draft = Omit<Alternative, "structure" | "exclude"> & { structure: string; exclude: string };

function toDraft(a: Alternative): Draft {
  return { ...a, structure: a.structure.join("\n"), exclude: a.exclude.join("\n") };
}

function fromDraft(d: Draft) {
  const lines = (s: string) => s.split("\n").map((x) => x.trim()).filter(Boolean);
  return { ...d, structure: lines(d.structure), exclude: lines(d.exclude) };
}

function conceptTitle(count: number, id: string) {
  return count <= 1 ? "공정 컨셉" : `컨셉 ${id}`;
}

const STATUS_TEXT: Record<ConceptImage["status"], string> = {
  generating: "그리는 중", draft: "초안", approved: "승인", failed: "실패",
};

/** 컨셉 1개 — 이미지 버전 이력 + 수정 요청 + 구성 문장(직접 고치기). */
function ConceptCard({
  project, draft, index, count, locked, dirty, onEdit, saveEdits,
}: {
  project: ProposalProject; draft: Draft; index: number; count: number; locked: boolean; dirty: boolean;
  onEdit: (i: number, patch: Partial<Draft>) => void;
  /** 직접 고친 구성이 있으면 먼저 저장(없으면 바로 true). */
  saveEdits: () => Promise<boolean>;
}) {
  const { busy, error, run } = useProjectAction(project.id);
  const [request, setRequest] = useState("");
  const versions = project.images.filter((im) => im.alt_id === draft.id).sort((a, b) => b.version - a.version);
  const [picked, setPicked] = useState<number | null>(null);
  const [compare, setCompare] = useState(false);
  const shown = versions.find((v) => v.id === picked) ?? versions[0];
  const prev = shown ? versions.find((v) => v.version < shown.version && v.attachment_id) : undefined;
  const changes = project.alternatives.find((a) => a.id === draft.id)?.changes;

  async function revise() {
    if (!(await saveEdits())) return;
    await run("요청 중", "revise", { alt_id: draft.id, request }, () => { setRequest(""); setPicked(null); });
  }

  async function redraw() {
    if (!(await saveEdits())) return;
    await run("요청 중", "image", { alt_id: draft.id, revision: "직접 고친 구성 문장대로 다시 그리기" }, () => setPicked(null));
  }

  return (
    <article className="flex flex-col gap-3 rounded-lg border border-border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded-md bg-accent-soft px-2 py-0.5 text-[12px] font-semibold text-accent">
          {conceptTitle(count, draft.id)}
        </span>
        <input className={`${inputCls} max-w-xs`} value={draft.name} maxLength={30} placeholder="컨셉 이름"
               aria-label={`${conceptTitle(count, draft.id)} 이름`} disabled={locked}
               onChange={(e) => onEdit(index, { name: e.target.value })} />
        <input className={`${inputCls} max-w-sm`} value={draft.robot} maxLength={200} placeholder="로봇 형태와 대수"
               aria-label={`${conceptTitle(count, draft.id)} 로봇`} disabled={locked}
               onChange={(e) => onEdit(index, { robot: e.target.value })} />
      </div>
      {draft.reason && <p className="text-[12.5px] text-foreground-muted">제안 이유: {draft.reason}</p>}

      {versions.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5" role="tablist" aria-label="이미지 버전">
          {versions.map((v) => (
            <button
              key={v.id}
              type="button"
              role="tab"
              aria-selected={shown?.id === v.id}
              title={v.revision ? `수정 요청: ${v.revision}` : "첫 버전"}
              onClick={() => setPicked(v.id)}
              className={cn(
                "rounded-full border px-2.5 py-0.5 text-[11.5px]",
                shown?.id === v.id ? "border-accent bg-accent-soft text-accent" : "border-border text-foreground-muted hover:bg-foreground/5",
              )}
            >
              v{v.version} · {STATUS_TEXT[v.status]}
            </button>
          ))}
        </div>
      )}
      {shown?.revision && (
        <p className="text-[12px] text-foreground-subtle">v{shown.version} 수정 요청: {shown.revision.slice(0, 160)}</p>
      )}
      {changes && shown?.id === versions[0]?.id && <ChangesBox changes={changes} />}
      {prev && shown?.status !== "generating" && (
        <label className="inline-flex items-center gap-1.5 text-[12px] text-foreground-muted">
          <input type="checkbox" checked={compare} onChange={(e) => setCompare(e.target.checked)} />
          v{prev.version}(이전)와 나란히 보기
        </label>
      )}
      {compare && prev && shown ? (
        <div className="grid gap-2 md:grid-cols-2">
          {[prev, shown].map((v) => (
            <figure key={v.id} className="flex flex-col gap-1">
              {/* eslint-disable-next-line @next/next/no-img-element -- 인증 쿠키가 필요한 첨부 프록시 */}
              <img src={`/api/attachments/${v.attachment_id}/download`} alt={`컨셉 이미지 v${v.version}`}
                   className="block h-auto w-full rounded-lg border border-border bg-white" />
              <figcaption className="text-[11.5px] text-foreground-subtle">
                v{v.version}{v.id === prev.id ? " (이전)" : " (지금)"}
              </figcaption>
            </figure>
          ))}
        </div>
      ) : shown ? (
        <ConceptImageCard key={`${shown.id}-${shown.status}`} projectId={project.id} image={shown} editable={!locked} />
      ) : (
        <p className="text-[12.5px] text-foreground-subtle">아직 컨셉 이미지가 없습니다.</p>
      )}

      <div className="flex flex-col gap-1.5 rounded-lg bg-foreground/[0.03] p-2.5">
        <label htmlFor={`rev-${draft.id}`} className="text-[12.5px] font-medium text-foreground">
          수정 요청 — 바꾸고 싶은 점을 말로 적으면 구성에 반영해 새 버전을 그립니다
        </label>
        <textarea
          id={`rev-${draft.id}`}
          rows={2}
          maxLength={1000}
          value={request}
          disabled={locked}
          onChange={(e) => setRequest(e.target.value)}
          placeholder="예: 로봇을 컨베이어 오른쪽으로 옮기고, 카메라는 위에서 내려다보게 해 주세요. 작업자 통로를 앞쪽에 넓게."
          className={inputCls}
        />
        <ErrorBox error={error} />
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            className={primaryBtn}
            disabled={!request.trim() || !!busy || locked}
            onClick={() => void revise()}
          >
            <Icon name="refresh" className="h-4 w-4" /> 수정 요청 반영해 다시 그리기
          </button>
          {!versions.length && (
            <button type="button" className={secondaryBtn} disabled={!!busy || locked}
                    onClick={() => void redraw()}>
              컨셉 이미지 그리기
            </button>
          )}
          <span className="text-[11.5px] text-foreground-subtle">
            {locked
              ? "다른 작업(이미지 생성 등)이 끝나면 누를 수 있습니다."
              : !request.trim()
                ? "바꿀 점을 적으면 누를 수 있습니다. 이미지 1장에 1~3분 걸립니다."
                : dirty
                  ? "직접 고친 구성도 함께 저장한 뒤 반영합니다."
                  : "이미지 1장에 1~3분 걸립니다. 횟수 제한은 없습니다."}
          </span>
        </div>
      </div>

      <details className="text-[12.5px]">
        <summary className="cursor-pointer select-none text-foreground-muted">구성 문장 직접 고치기 (그림 지시에 그대로 들어갑니다)</summary>
        <div className="mt-2 grid gap-3 md:grid-cols-2">
          <label className="flex flex-col gap-1 text-[12px] text-foreground-muted">
            구성(장치 종류·개수·위치 관계·흐름 방향, 한 줄에 하나)
            <textarea className={inputCls} rows={6} value={draft.structure} disabled={locked}
                      onChange={(e) => onEdit(index, { structure: e.target.value })} />
          </label>
          <label className="flex flex-col gap-1 text-[12px] text-foreground-muted">
            넣지 않을 것
            <textarea className={inputCls} rows={6} value={draft.exclude} disabled={locked}
                      onChange={(e) => onEdit(index, { exclude: e.target.value })} />
          </label>
        </div>
        <div className="mt-2">
          <button type="button" className={secondaryBtn} disabled={!dirty || !!busy || locked} onClick={() => void redraw()}>
            고친 구성 저장하고 다시 그리기
          </button>
        </div>
      </details>
    </article>
  );
}

export function ProposalConceptPanel({ project }: { project: ProposalProject }) {
  const { busy, error, run } = useProjectAction(project.id);
  const [drafts, setDrafts] = useState<Draft[]>(() => project.alternatives.map(toDraft));
  const [dirty, setDirty] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const locked = project.job_alive;
  // 서버 컨셉이 바뀌면(제안·수정 반영 완료) 편집본을 새로 맞춘다 — 렌더 중 비교(effect 대신).
  const serverKey = JSON.stringify(project.alternatives);
  const [seenKey, setSeenKey] = useState(serverKey);
  if (serverKey !== seenKey && !dirty) {
    setSeenKey(serverKey);
    setDrafts(project.alternatives.map(toDraft));
  }

  function edit(i: number, patch: Partial<Draft>) {
    setDrafts((ds) => ds.map((d, j) => (j === i ? { ...d, ...patch } : d)));
    setDirty(true);
  }

  // 수정 요청·다시 그리기 전에 직접 고친 구성을 저장한다 — 저장 버튼을 따로 누르지 않아도 막히지 않게.
  async function saveEdits(): Promise<boolean> {
    if (!dirty) return true;
    try {
      await postAction(project.id, "alternatives", { alternatives: drafts.map(fromDraft) });
      setDirty(false);
      setSaveError(null);
      return true;
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : "직접 고친 구성을 저장하지 못했습니다.");
      return false;
    }
  }

  // 계획 확인 중(이미지 그리기 전) — 로봇·그리퍼·회사 경험을 글로 먼저 정한다.
  if (project.plan_pending) return <ProposalPlanPanel project={project} />;

  if (project.alternatives.length === 0) {
    if (locked) return null;            // 자동 진행 중 — JobStatus 가 보여 준다
    return (
      <section className="flex flex-col gap-3 rounded-xl border border-accent/30 bg-surface p-4">
        <h2 className="text-[15px] font-semibold text-foreground">공정 컨셉</h2>
        <p className="text-[13px] text-foreground-muted">
          확인된 문항을 바탕으로 공정 컨셉(흐름·장치 구성·배치)과 컨셉 이미지를 만듭니다. 고객이 여러 로봇 형태의
          비교를 원한 경우에만 비교안을 함께 만듭니다.
        </p>
        <p className="text-[11.5px] text-foreground-subtle">
          공정 컨셉 제안·수정 반영·컨셉 이미지는 AI 가 합니다. 가명 처리된 문항 요약만 외부로 보냅니다.
        </p>
        <ErrorBox error={error} />
        <div>
          <button type="button" className={primaryBtn} disabled={!!busy} onClick={() => void run("요청 중", "propose")}>
            공정 컨셉 제안 받기
          </button>
        </div>
      </section>
    );
  }

  const count = drafts.length;
  const approvedFor = (id: string) => project.images.some((im) => im.approved && im.alt_id === id);

  return (
    <section className="flex flex-col gap-4 rounded-xl border border-accent/30 bg-surface p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-[15px] font-semibold text-foreground">
          {count <= 1 ? "공정 컨셉" : `공정 컨셉 — 비교안 포함 ${count}개`}
        </h2>
        <span className="text-[12px] text-foreground-subtle">
          마음에 들 때까지 수정 요청을 넣고, 쓸 버전을 승인하세요.
        </span>
      </div>
      <p className="text-[11.5px] text-foreground-subtle">
        공정 컨셉 제안·수정 반영·컨셉 이미지는 AI 가 합니다. 가명 처리된 문항 요약만 외부로 보냅니다.
      </p>

      <ProposalExperiencePanel project={project} />

      {drafts.map((d, i) => (
        <ConceptCard key={d.id} project={project} draft={d} index={i} count={count} locked={locked} dirty={dirty}
                     onEdit={edit} saveEdits={saveEdits} />
      ))}

      <ErrorBox error={error ?? saveError} />
      <div className="flex flex-wrap items-center justify-end gap-2">
        {busy && <span className="text-[12px] text-foreground-subtle">{busy}…</span>}
        {count < MAX_CONCEPTS && (
          <button type="button" className={secondaryBtn} disabled={!!busy || locked || dirty}
                  title="고객이 다른 로봇 형태와의 비교를 원할 때"
                  onClick={() => void run("요청 중", "extra")}>
            비교안 추가 (AI 제안)
          </button>
        )}
        <button type="button" className={secondaryBtn} disabled={!dirty || !!busy || locked}
                onClick={() => void run("저장 중", "alternatives", { alternatives: drafts.map(fromDraft) }, () => setDirty(false))}>
          직접 고친 구성 저장
        </button>
        <button type="button" className={primaryBtn} disabled={dirty || !!busy || locked}
                title={dirty ? "직접 고친 구성을 먼저 저장하세요" : undefined}
                onClick={() => void run("확정 중", "finish-concept")}>
          <Icon name="check" className="h-4 w-4" /> 컨셉 확정하고 구성 단계로
        </button>
      </div>
      {project.alternatives.some((a) => !approvedFor(a.id)) && (
        <p className="text-right text-[12px] text-foreground-subtle">
          승인된 이미지가 없는 컨셉은 이미지 없이 제안서에 들어갑니다.
        </p>
      )}
    </section>
  );
}
