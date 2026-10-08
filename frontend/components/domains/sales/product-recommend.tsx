// 회사 제품 추천 — AI 와 같이 찾는 흐름(사용자 2026-10-02, 영업부 팀장 요청으로 개편).
// ① Q&A 를 순서대로 답한다(ATC: 영업부 질문지 J01~J10 → 앞 답에 해당하는 추가 질문. 미팅 질문지 파일 불러오기는 부가 기능)
// ② [AI와 같이 제품 검색 진행] → 왼쪽은 진행 과정·결과, 오른쪽은 대화창(반반).
//    AI 가 비워 둔 질문을 하나씩 묻고 → 정리한 조건이 맞는지 확인 → 후보를 찾으면 "추천해도 될까요?" → 예면 근거와 함께 결과.
//    AI 를 그대로 믿지 않고 서로 확인하며(더블체크) 답에 다다르는 순서.
// ③ 결과가 맘에 안 들거나 데이터를 바로잡고 싶으면 대화창에 말한다 → AI 가 "학습시킬까요?" → 예 → 학습 문구를 보여 주고
//    사용자가 정확한 워딩으로 고친 뒤 저장. 영업 관리자는 바로 반영, 그 외 사용자는 관리자 승인 후 반영.
// '그 외 제품' Q&A 는 의뢰인 자료가 오면 QNA 만 바꿔 끼운다(지금은 임시 문항).
"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import type {
  AskResult, AtcAnswer, AtcExtract, AtcFollowup, AtcIntake, AtcMeeting, AtcOutput, Correction, CorrectionDraft, CorrectionList,
  AtcTestSample, LearningKind, ProductProposal, QuoteSession, Recommendation, SpecSearch,
} from "@/lib/shared/product-recommend";
import { AgentBubble, AiBubble, RichText, streamNdjson } from "@/components/domains/sales/agent-chat";
import { OrderTab } from "@/components/domains/sales/order-workspace";
import { Icon } from "@/components/shared/ui/icon";
import { cn } from "@/lib/shared/utils";
import {
  AtcCandidatePreview, AtcIntakeSummary, AtcLivePreview, AtcMeetingUpload, GroupedFields, AtcResultView, AtcWizard, describeAnswer, emptyIntake,
  markUnknown, updateEntity, type RecState,
} from "./atc-recommend";
import { ErrorBox, inputCls, primaryBtn, secondaryBtn } from "./proposal-actions";
import { DealsTab } from "./deals-tab";
import { InitialsPrompt } from "./initials-prompt";
import { QuoteTab, savedContact } from "./quote-workspace";

/** 임시 Q&A(요구사항 시트 형태) — 의뢰인 Q&A 가 준비되면 이 목록만 바꾼다.
 *  options 가 있으면 하나 고르기(다시 누르면 해제), 없으면 직접 적기. 제품 DB 가 판단에 쓰는 항목(재질·무게·로봇 체급·공압·툴 교체)
 *  위주로 묻는다(사용자 요청 2026-10-01: 실제 프로그램에서 시험해 볼 수 있게). */
const QNA: { key: string; q: string; short: string; ph?: string; options?: string[]; wide?: boolean }[] = [
  { key: "area", q: "어떤 제품이 필요합니까?", short: "필요 제품",
    options: ["그리퍼(물건 집기)", "툴체인저(툴 교체)", "이동 로봇(AMR·이송)", "4족·순찰 로봇", "관제 솔루션", "잘 모르겠음"], wide: true },
  { key: "target", q: "무엇을 다룹니까(대상물)?", short: "대상물", ph: "예: 자동차 섀시 철판, 유리병, 부품이 담긴 선반 카트" },
  { key: "material", q: "대상물 재질은?", short: "재질",
    options: ["철·강(자석 붙음)", "알루미늄·비철금속", "플라스틱", "유리", "종이·박스", "혼합·기타"] },
  { key: "weight", q: "무게는? (툴 교체면 툴 무게)", short: "무게", ph: "예: 약 3kg / 툴 포함 150kg / 적재물 200kg" },
  { key: "shape", q: "크기·형상·표면은?", short: "크기·형상", ph: "예: 400×300mm 판재, 표면 매끈 / 지름 50mm 원통 / 비정형" },
  { key: "task", q: "어떤 동작을 해야 합니까?", short: "동작", ph: "예: 컨베이어에서 집어 적재대로 옮기기, 공정 사이 자율 운반" },
  { key: "robot", q: "어떤 로봇에 씁니까?", short: "로봇", options: ["협동로봇", "산업용 로봇", "이동 로봇(AMR) 위에", "로봇 없음·미정"] },
  { key: "air", q: "현장에 공압 설비가 있습니까?", short: "공압", options: ["있음", "없음", "모름"] },
  { key: "toolchange", q: "툴(그리퍼)을 바꿔 끼워야 합니까?", short: "툴 교체", options: ["자동 교체 필요", "수동 교체면 충분", "필요 없음"] },
  { key: "env", q: "작업 환경은?", short: "환경", ph: "예: 실내 일반 / 야외 험지 / 물기·세척 / 고온" },
  { key: "etc", q: "그 밖의 요구(선호 제품군·수량·예산 등)?", short: "기타 요구", ph: "없으면 비워 두세요", wide: true },
];
/** 그 외 제품: 비워 두면 AI 가 대화에서 다시 묻는 핵심 문항(나머지는 비워도 묻지 않는다). */
const QNA_CORE = ["area", "target", "material", "weight", "robot", "air"];

type Pending = AtcFollowup & { kind: "basic" | "followup" };
type Stage = "asking" | "confirm" | "searching" | "offer" | "done";
type Msg =
  | { id: number; role: "user"; text: string }
  | { id: number; role: "ai"; type: "text"; text: string }
  | { id: number; role: "ai"; type: "atcq"; qs: Pending[]; state?: string }
  | { id: number; role: "ai"; type: "qnaq"; key: string; state?: string }
  | { id: number; role: "ai"; type: "confirm"; lines: string[]; state?: "yes" | "no" }
  | { id: number; role: "ai"; type: "offer"; rec: Recommendation; state?: "yes" | "no" | "old" }
  | { id: number; role: "ai"; type: "learn_offer"; text: string; note: string; state?: "yes" | "no" }
  | { id: number; role: "ai"; type: "learn_draft"; draft: CorrectionDraft; state?: "saved" | "cancel" }
  | { id: number; role: "ai"; type: "learn_saved"; c: Correction }
  | { id: number; role: "ai"; type: "applied"; text: string; items: { label: string; display: string }[]; state?: "research" }
  | { id: number; role: "ai"; type: "spec_offer"; tool: number; name: string; product: string; state?: "yes" | "no" }
  | { id: number; role: "ai"; type: "spec_result"; tool: number; spec: SpecSearch; state?: "applied" | "skipped" }
  | { id: number; role: "ai"; type: "suggest"; model: string; text: string; state?: "yes" | "old" }
  | AgentMsg;
type AgentStep = { ok: boolean; text: string };
type AgentMsg = { id: number; role: "ai"; type: "agent"; think: string; steps: AgentStep[]; answer: string; live: boolean };
type AgentDone = { t: "done"; intake: AtcIntake; applied: { label: string; display: string }[];
  intent: "recommend" | "learn" | "chat" | "suggest" | "switch" | "revert"; model: string | null; version: number | null; answer: string };
type AgentEvent = { t: "think" | "answer" | "answer_set" | "error"; text: string } | ({ t: "step" } & AgentStep) | AgentDone;
/** 추천 결과 버전 — 추천 결과가 처음 나온 뒤([네, 이 추천으로 진행])부터 센다. 추천 전 Q&A 는 버전이 아니다(사용자 2026-10-06). */
type RecVersion = { rec: Recommendation; intake: AtcIntake; model: string | null };

const STATUS: Record<Correction["review_status"], { text: string; tone: string }> = {
  pending: { text: "승인 대기", tone: "bg-amber-500/15 text-amber-700 dark:text-amber-300" },
  kept: { text: "반영 중", tone: "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300" },
  off: { text: "꺼짐", tone: "bg-foreground/8 text-foreground-muted" },
  rejected: { text: "거절됨", tone: "bg-red-500/10 text-red-600 dark:text-red-400" },
};

const KIND_LABEL: Record<LearningKind, { text: string; tone: string; hint: string }> = {
  experience: { text: "경험", tone: "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300", hint: "실제 공정에서 써 본 결과" },
  correction: { text: "정정", tone: "bg-amber-500/15 text-amber-700 dark:text-amber-300", hint: "추천·데이터를 바로잡음" },
  rule: { text: "기준", tone: "bg-blue-500/12 text-blue-700 dark:text-blue-300", hint: "다음부터 이렇게 판단" },
};
const KINDS = Object.keys(KIND_LABEL) as LearningKind[];

function kindOf(k: string | undefined): LearningKind {
  return k === "success" ? "experience" : k === "experience" || k === "rule" ? k : "correction";
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`/api/product-recommend${path}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof d?.detail === "string" ? d.detail : "요청을 처리하지 못했습니다.");
  return d as T;
}

/** GPT 식 대화 — 한 줄에 JSON 하나씩 흘러오는 이벤트를 받는 대로 넘긴다. */
function streamAgent(body: unknown, onEvent: (ev: AgentEvent) => void): Promise<void> {
  return streamNdjson<AgentEvent>("/api/product-recommend/atc/agent", body, onEvent);
}

/** 결과에서 1순위 모델 이름. */
function topModel(r: Recommendation): string | null {
  return r.atc?.screening_candidates.find((c) => c.models.length && !c.out_of_range)?.models.map((x) => x.name).join(" / ") ?? null;
}

function qnaText(answers: Record<string, string>) {
  return QNA.filter((x) => answers[x.key]?.trim()).map((x) => `Q. ${x.q}\nA. ${answers[x.key].trim()}`).join("\n");
}

const pendingKey = (q: Pending) => `${q.id}-${q.entity_index ?? "p"}`;

/** 조건 확인용 요약(대화창) — 툴체인저 선정에 쓰는 값만 짧게. */
function atcLines(intake: AtcIntake): string[] {
  const yn = (v: unknown) => (v === true ? "있음" : v === false ? "없음" : "모름");
  const typeKo: Record<string, string> = { cobot: "협동", industrial: "산업용" };
  const lines = intake.robots.map((r, i) => {
    const sp = (r.speed ?? {}) as Record<string, unknown>;
    return `로봇 ${i + 1}: ${[r.manufacturer, r.model].filter(Boolean).join(" ") || "모델 모름"} · ${typeKo[r.type as string] ?? "종류 모름"} · 속도 ${sp.value ?? "모름"}${(sp.unit as string) ?? ""}`;
  });
  lines.push(`교체 툴 ${(intake.project.tool_count as number | null) ?? "모름"}개`);
  for (const [i, t] of intake.tools.entries()) {
    const it = (t.intake ?? {}) as Record<string, unknown>;
    const wp = t.workpiece_not_applicable ? "제품 없음" : `제품 ${(t.mass_components as Record<string, unknown>)?.max_simultaneous_workpieces_kg ?? "?"}kg`;
    lines.push(`${(t.name as string) || `툴 ${i + 1}`}: 툴 ${it.tool_assembly_mass_kg ?? "?"}kg + ${wp} · 기울임 ${yn(it.tilts_or_flips)} · 전기 ${yn(it.needs_power)} · 공압 ${yn(it.needs_air)} · 센서 ${yn(it.has_sensors_or_signals)}`);
  }
  return lines;
}

/** 'tools[].intake.x' → 툴 하나 기준 경로 'intake.x'. */
const toolPath = (path: string) => path.replace(/^tools\[\]\./, "");
function readPath(obj: unknown, path: string): unknown {
  return path.split(".").reduce<unknown>((cur, k) => (cur && typeof cur === "object" ? (cur as Record<string, unknown>)[k] : undefined), obj);
}
const empty = (v: unknown) => v == null || v === "" || (Array.isArray(v) && v.length === 0);

/** 공개 사양 검색 결과 — 칸별 값·근거·출처. 이미 적은 값과 다르면 표시만(덮어쓰지 않음). */
function SpecResultBody({ spec, tool }: { spec: SpecSearch; tool: Record<string, unknown> }) {
  if (!spec.fields.length) {
    return <p>‘{spec.product}’의 공개 사양에서 쓸 만한 값을 찾지 못했습니다. 비어 있는 칸은 계속 여쭤보겠습니다.</p>;
  }
  return (
    <div className="flex flex-col gap-2">
      <p className="font-semibold">‘{spec.product}’ 공개 사양을 찾았습니다. <span className="font-normal text-foreground-muted">값은 &lsquo;추정&rsquo;이며 엔지니어 확인 전입니다.</span></p>
      <ul className="flex flex-col gap-1 text-[12.5px]">
        {spec.fields.map((f) => {
          const cur = readPath(tool, toolPath(f.path));
          const differs = !empty(cur) && JSON.stringify(cur) !== JSON.stringify(f.value);
          return (
            <li key={f.path} className="flex flex-wrap gap-x-1.5">
              <span className="text-foreground-subtle">{f.label}</span>
              <b className="text-foreground">{f.display}</b>
              {!empty(cur) && (differs
                ? <span className="text-amber-700 dark:text-amber-300">· 입력한 값({String(typeof cur === "object" ? JSON.stringify(cur) : cur)})과 다름 — 바꾸지 않음</span>
                : <span className="text-foreground-subtle">· 입력한 값과 같음</span>)}
              {f.evidence && <span className="w-full text-[11.5px] text-foreground-subtle">근거: {f.evidence}</span>}
            </li>
          );
        })}
      </ul>
      {spec.signals && <p className="text-[12px] text-foreground-muted">신호 정보(참고): {spec.signals}</p>}
      {spec.sources.length > 0 && (
        <div className="text-[11.5px] text-foreground-subtle">
          출처: {spec.sources.map((x, i) => (
            <a key={x.url} href={x.url} target="_blank" rel="noopener noreferrer" className="text-accent hover:underline">
              {i > 0 ? " · " : ""}{x.title || x.url}
            </a>
          ))}
        </div>
      )}
    </div>
  );
}

function KindBadge({ kind }: { kind?: string }) {
  const k = KIND_LABEL[kindOf(kind)];
  return <span className={cn("rounded-full px-2 py-0.5 text-[11px] font-semibold", k.tone)}>{k.text}</span>;
}

/** AI 말풍선 — 왼쪽, AI 아바타. */
/** 결과 창 위 ‹ n/m › — 예전 추천 결과 보기(Canvas 식). 최종 제안 확정 뒤에는 잠근다. */
function VersionNav({ at, count, locked, onGo }: { at: number; count: number; locked: boolean; onGo: (i: number) => void }) {
  const btn = "grid h-6 w-6 place-items-center rounded-md text-[15px] leading-none text-foreground-muted hover:bg-foreground/[0.06] disabled:opacity-30";
  return (
    <div className="-mb-2 flex items-center justify-end gap-1 text-[12px] text-foreground-subtle">
      <span className="mr-1">{locked ? "최종 제안 확정 — 버전 고정" : "추천 결과 버전"}</span>
      <button type="button" className={btn} disabled={locked || at <= 0} onClick={() => onGo(at - 1)} aria-label="이전 버전">‹</button>
      <span className="tabular-nums">{at + 1}/{count}</span>
      <button type="button" className={btn} disabled={locked || at >= count - 1} onClick={() => onGo(at + 1)} aria-label="다음 버전">›</button>
    </div>
  );
}

function Choice({ children, onClick, primary, disabled }: { children: ReactNode; onClick: () => void; primary?: boolean; disabled?: boolean }) {
  return (
    <button type="button" disabled={disabled} onClick={onClick}
            className={cn("inline-flex items-center gap-1 rounded-full border px-3 py-1 text-[12.5px] font-medium transition disabled:opacity-40",
              primary ? "border-accent bg-accent text-white hover:brightness-105" : "border-border bg-surface text-foreground hover:bg-foreground/5")}>
      {children}
    </button>
  );
}

/** 입력한 조건 요약 — 그 외 제품. */
function ConditionSummary({ answers, busy, onEdit, onReset }: {
  answers: Record<string, string>; busy: boolean; onEdit: () => void; onReset: () => void;
}) {
  const rows = QNA.filter((x) => answers[x.key]?.trim());
  return (
    <section className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-[15px] font-semibold text-foreground">입력한 조건</h2>
        <div className="flex flex-wrap gap-2">
          <button type="button" className={secondaryBtn} disabled={busy} onClick={onEdit}>
            <Icon name="edit" className="h-4 w-4" /> Q&amp;A 고치기
          </button>
          <button type="button" className={secondaryBtn} disabled={busy} onClick={onReset}>
            <Icon name="plus" className="h-4 w-4" /> 처음부터
          </button>
        </div>
      </div>
      <dl className="grid gap-x-4 gap-y-1.5 text-[12.5px] sm:grid-cols-2">
        {rows.map((x) => (
          <div key={x.key} className={cn("flex gap-2", x.wide && "sm:col-span-2")}>
            <dt className="w-[72px] shrink-0 text-foreground-subtle">{x.short}</dt>
            <dd className="min-w-0 break-words text-foreground">{answers[x.key].trim()}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

/** 1.4초마다 다음 단계 문구 — 기다리는 동안 무엇을 하는지 보이게(사용자 2026-10-02). */
function useTicker(count: number, ms = 1400) {
  const [i, setI] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setI((x) => (x + 1) % Math.max(count, 1)), ms);
    return () => clearInterval(t);
  }, [count, ms]);
  return i;
}

function StepList({ steps }: { steps: string[] }) {
  const at = useTicker(steps.length);
  return (
    <ul className="flex flex-col gap-1 text-left text-[12.5px]">
      {steps.map((x, i) => (
        <li key={x} className={cn("flex items-center gap-2 transition-colors", i < at ? "text-foreground-subtle" : i === at ? "font-semibold text-foreground" : "text-foreground-subtle/60")}>
          {i < at ? <Icon name="check" className="h-3.5 w-3.5 text-emerald-600" />
            : i === at ? <Icon name="refresh" className="h-3.5 w-3.5 animate-spin text-accent" />
              : <span className="h-3.5 w-3.5 rounded-full border border-border" />}
          {x}
        </li>
      ))}
    </ul>
  );
}

const SEARCH_STEPS = ["툴측 총무게 계산(툴 + 한 번에 드는 제품)", "제품 DB 툴체인저 정격과 대조", "속도 보정·포고핀·공압 모듈 산정", "후보와 추천 근거 정리"];

/** 찾는 동안 — 왼쪽에 단계 문구와 로딩. */
function Finding({ message, sub, steps = SEARCH_STEPS }: { message?: string; sub?: string; steps?: string[] }) {
  return (
    <section className="flex flex-col items-center gap-3 rounded-xl border border-accent/30 bg-surface px-4 py-8 text-center" aria-live="polite">
      <Icon name="refresh" className="h-6 w-6 animate-spin text-accent" />
      <p className="text-[14px] font-semibold text-foreground">{message ?? "AI 가 회사 제품 DB 에서 조건에 가장 맞는 제품을 찾는 중입니다…"}</p>
      <p className="text-[12px] text-foreground-subtle">{sub ?? "재질·무게·로봇·공압 조건을 제품 원리·사양과 대조하고 있습니다 (보통 10~30초)"}</p>
      <StepList steps={steps} />
    </section>
  );
}

const WORK_STEPS: Record<string, { title: string; steps: string[] }> = {
  "답하는 중": { title: "AI 가 말씀을 분석하는 중입니다…",
               steps: ["말씀에서 툴·전원·신호·무게·압력 값을 찾는 중", "질문 칸과 대조해 반영할 값을 고르는 중", "무엇을 원하시는지(다시 찾기·질문·학습) 판단하는 중"] },
  "답 정리 중": { title: "AI 가 답을 질문 칸에 옮기는 중입니다…", steps: ["답에서 값 찾기", "질문 칸과 대조", "검토 현황 다시 계산"] },
  "확인 중": { title: "검토 현황을 다시 계산하는 중입니다…", steps: ["답한 내용 반영", "빠진 정보 확인", "다음 질문 고르기"] },
};

/** 대화 처리 중 왼쪽에도 무엇을 하는지 보인다(사용자 2026-10-02: 분석 중 로딩이 안 보임). */
function WorkingCard({ busy }: { busy: string }) {
  const w = WORK_STEPS[busy] ?? { title: `${busy}…`, steps: [] };
  return (
    <section className="flex items-start gap-3 rounded-xl border border-accent/30 bg-accent/[0.04] px-4 py-3" aria-live="polite">
      <Icon name="refresh" className="mt-0.5 h-5 w-5 shrink-0 animate-spin text-accent" />
      <div className="flex min-w-0 flex-col gap-1.5">
        <p className="text-[13.5px] font-semibold text-foreground">{w.title}</p>
        {w.steps.length > 0 && <StepList steps={w.steps} />}
      </div>
    </section>
  );
}

/** 진행 과정 — 서로 확인하며 답에 다다르는 순서를 왼쪽에 보인다. */
const STEPS: { key: Stage | "qa"; label: string }[] = [
  { key: "qa", label: "질문 답변" },
  { key: "asking", label: "빠진 정보 확인" },
  { key: "confirm", label: "조건 확인" },
  { key: "searching", label: "후보 검색" },
  { key: "offer", label: "추천 확인" },
  { key: "done", label: "결과·근거" },
];
function Progress({ stage, left }: { stage: Stage; left: number | null }) {
  const at = STEPS.findIndex((s) => s.key === stage);
  return (
    <ol className="flex flex-wrap items-center gap-1.5 rounded-xl border border-border bg-surface px-3 py-2.5 text-[12px]" aria-label="진행 과정">
      {STEPS.map((s, i) => (
        <li key={s.key} className="flex items-center gap-1.5">
          <span className={cn("inline-flex items-center gap-1 rounded-full px-2.5 py-1 font-medium",
            i < at ? "bg-accent/12 text-accent" : i === at ? "bg-accent text-white" : "bg-foreground/[0.05] text-foreground-subtle")}>
            {i < at && <Icon name="check" className="h-3 w-3" />}
            {s.label}{s.key === "asking" && i === at && left ? ` · ${left}개 남음` : ""}
          </span>
          {i < STEPS.length - 1 && <span className="text-foreground-subtle/60">›</span>}
        </li>
      ))}
    </ol>
  );
}

/** 추천 결과(그 외 제품) — 가장 맞는 제품 하나를 크게(사진·이유·조건별 근거·DB 사양). */
const STATE_BADGE: Record<RecState, { text: string; tone: string }> = {
  pending: { text: "추천 확인 대기", tone: "bg-amber-500/15 text-amber-800 dark:text-amber-300" },
  confirmed: { text: "추천 확정", tone: "bg-emerald-600 text-white" },
  held: { text: "보류 — 대화로 보완 중", tone: "bg-foreground/10 text-foreground-muted" },
};

/** 그 외 제품 — 추천 확정 전에는 후보와 조건별 근거만. */
function OtherCandidatePreview({ rec, state }: { rec: Recommendation; state: RecState }) {
  const it = rec.items[0];
  return (
    <section className="flex flex-col gap-3 rounded-xl border border-dashed border-accent/50 bg-surface p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-[15px] font-semibold text-foreground">후보 검토</h2>
        <span className={cn("rounded-full px-2.5 py-0.5 text-[11.5px] font-semibold", STATE_BADGE[state].tone)}>{STATE_BADGE[state].text}</span>
      </div>
      <div className="flex flex-wrap items-baseline gap-x-3">
        <span className="text-[20px] font-bold text-foreground">{it.product}</span>
        {it.family && <span className="text-[12px] text-accent">{it.family}</span>}
      </div>
      {(it.checks ?? []).length > 0 && (
        <ul className="divide-y divide-border rounded-lg border border-border text-[12.5px]">
          {it.checks!.map((c, i) => (
            <li key={i} className="flex items-center gap-2 px-3 py-1.5">
              <span className={cn("grid h-4 w-4 shrink-0 place-items-center rounded-full text-white", c.ok ? "bg-emerald-600" : "bg-amber-500")}>
                <Icon name={c.ok ? "check" : "help"} className="h-3 w-3" />
              </span>
              <span className="font-medium text-foreground">{c.condition}</span>
            </li>
          ))}
        </ul>
      )}
      <p className="text-[12px] text-foreground-subtle">
        {state === "held" ? "보류 중입니다 — 대화창에 고려할 조건을 말씀하시면 다시 찾습니다." : "오른쪽 대화창에서 [네, 이 추천으로 진행]을 누르면 추천 결과를 정리합니다."}
      </p>
    </section>
  );
}

function RecommendationView({ rec, state, footer }: { rec: Recommendation; state?: RecState; footer?: ReactNode }) {
  const it = rec.items[0];
  return (
    <section className="flex flex-col gap-4 rounded-xl border border-accent/30 bg-surface p-4">
      <div>
        <h2 className="flex flex-wrap items-center gap-2 text-[15px] font-semibold text-foreground">
          AI 추천 제품
          {state && <span className={cn("rounded-full px-2.5 py-0.5 text-[11.5px] font-semibold", STATE_BADGE[state].tone)}>{STATE_BADGE[state].text}</span>}
        </h2>
        {rec.summary && <p className="text-[13px] text-foreground-muted">{rec.summary}</p>}
      </div>
      {!it ? (
        <p className="text-[12.5px] text-foreground-muted">조건에 맞는 회사 제품을 찾지 못했습니다. Q&amp;A 를 고치거나 대화로 조건을 바꿔 보세요.</p>
      ) : (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
          <div className="flex flex-col gap-2">
            <div className="relative aspect-[4/3] w-full overflow-hidden rounded-lg border border-border bg-white">
              {it.image_id ? (
                // eslint-disable-next-line @next/next/no-img-element -- 인증 쿠키가 필요한 사진 프록시
                <img src={`/api/product-images/file/${it.image_id}`} alt={it.product}
                     className="absolute inset-0 h-full w-full object-contain p-3" />
              ) : (
                <span className="absolute inset-0 grid place-items-center text-[12px] text-foreground-subtle">사진 없음</span>
              )}
            </div>
            {it.specs && it.specs.length > 0 && (
              <div className="rounded-lg border border-border">
                <div className="border-b border-border px-3 py-1.5 text-[12px] font-semibold text-foreground">주요 사양 <span className="font-normal text-foreground-subtle">(제품 DB)</span></div>
                <dl className="divide-y divide-border text-[12px]">
                  {it.specs.map((s) => (
                    <div key={s.item} className="grid grid-cols-[minmax(0,2fr)_minmax(0,3fr)] gap-2 px-3 py-1.5">
                      <dt className="text-foreground-subtle">{s.item}</dt>
                      <dd className="break-words text-foreground">{s.value}</dd>
                    </div>
                  ))}
                </dl>
              </div>
            )}
          </div>
          <div className="flex min-w-0 flex-col gap-3">
            <div>
              {it.family && <div className="text-[12px] font-medium text-accent">{it.family}</div>}
              <div className="text-[20px] font-bold leading-tight text-foreground">{it.product}</div>
            </div>
            {it.warning && <p className="rounded-md bg-red-500/10 px-2.5 py-1.5 text-[12.5px] font-medium text-red-600 dark:text-red-400">{it.warning}</p>}
            {it.reason && (
              <div>
                <div className="mb-1 text-[12.5px] font-semibold text-foreground">추천 이유</div>
                <p className="text-[13.5px] leading-relaxed text-foreground">{it.reason}</p>
              </div>
            )}
            {it.checks && it.checks.length > 0 && (
              <div>
                <div className="mb-1 text-[12.5px] font-semibold text-foreground">추천 근거 <span className="font-normal text-foreground-subtle">· 조건 ↔ 제품 사양</span></div>
                <ul className="flex flex-col gap-1.5">
                  {it.checks.map((c, i) => (
                    <li key={i} className="flex gap-2 text-[12.5px]">
                      <span className={cn("mt-0.5 grid h-4 w-4 shrink-0 place-items-center rounded-full text-white",
                                          c.ok ? "bg-emerald-600" : "bg-amber-500")}>
                        <Icon name={c.ok ? "check" : "help"} className="h-3 w-3" />
                      </span>
                      <span className="min-w-0">
                        <span className="font-medium text-foreground">{c.condition}</span>
                        <span className="text-foreground-muted"> — {c.basis}</span>
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {(it.how_it_works || it.applies_when) && (
              <dl className="grid gap-1 rounded-lg bg-foreground/[0.03] px-3 py-2 text-[12px]">
                {it.how_it_works && (<><dt className="text-foreground-subtle">동작 방식</dt><dd className="text-foreground">{it.how_it_works}</dd></>)}
                {it.applies_when && (<><dt className="mt-1 text-foreground-subtle">적용 조건</dt><dd className="text-foreground">{it.applies_when}</dd></>)}
              </dl>
            )}
            {it.caution && (
              <p className="rounded-md bg-amber-500/10 px-2.5 py-1.5 text-[12.5px] text-amber-800 dark:text-amber-300">
                <span className="font-semibold">확인 필요 · </span>{it.caution}
              </p>
            )}
            {it.info_note && (
              <p className="rounded-md bg-amber-500/10 px-2.5 py-1.5 text-[12px] text-amber-700 dark:text-amber-300">{it.info_note}</p>
            )}
          </div>
        </div>
      )}
      {rec.reconsidered.length > 0 && (
        <div className="rounded-lg border border-accent/30 bg-accent-soft/30 px-3 py-2 text-[12.5px]">
          <div className="mb-1 flex items-center gap-1.5 font-semibold text-accent">
            <Icon name="refresh" className="h-3.5 w-3.5" /> AI 학습 내용으로 다시 생각한 점
          </div>
          <ul className="flex flex-col gap-1">
            {rec.reconsidered.map((r) => (
              <li key={r.correction_id}>
                <span className="text-foreground">{r.note}</span>
                <span className="block text-[11.5px] text-foreground-subtle">근거 {r.correction}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {rec.corrections.length > 0 && rec.reconsidered.length === 0 && (
        <p className="text-[11.5px] text-foreground-subtle">비슷한 AI 학습 내용 {rec.corrections.length}건을 함께 보고 판단했습니다(이번 조건에는 해당하지 않음).</p>
      )}
      {footer && <div className="flex flex-wrap items-center justify-end gap-2 border-t border-border pt-3">{footer}</div>}
    </section>
  );
}

/** 후보 제안(대화창) — 근거 몇 줄과 함께 "추천해도 될까요?". */
function OfferBody({ rec }: { rec: Recommendation }) {
  if (rec.kind === "atc") {
    const o = rec.atc!;
    const first = o.screening_candidates.find((c) => c.models.length && !c.out_of_range);
    const order = ["툴측 총무게", "정격 가반하중", "운전 속도"];
    const basis = order.map((k) => (o.basis ?? []).find((b) => b.item === k)).filter((b): b is NonNullable<typeof b> => !!b);
    return (
      <>
        <p>AI 추천 후보 검색 <b>1건</b>이 나왔습니다.</p>
        <p className="mt-1 text-[14px] font-bold">{first!.models.map((m) => m.name).join(" / ")}
          <span className="ml-1.5 text-[12px] font-normal text-foreground-muted">{first!.series_label} · 정격 {first!.models[0].payload_kg ?? "?"}kg</span></p>
        {basis.length > 0 && (
          <ul className="mt-1.5 flex flex-col gap-0.5 text-[12px] text-foreground-muted">
            {basis.map((b) => <li key={b.item}>· {b.sentence ?? b.judgement}</li>)}
          </ul>
        )}
        <p className="mt-1.5 text-[12px] text-foreground-subtle">
          {o.catalog_approved ? "제품 DB 공식 사양 모델입니다" : "제품 DB 사양 확인이 필요한 참고 후보입니다"} — 최종 확정은 내부 엔지니어가 합니다.
        </p>
        <p className="mt-1.5 font-semibold">이 후보로 추천해도 될까요?</p>
      </>
    );
  }
  const it = rec.items[0];
  const checks = it.checks ?? [];
  return (
    <>
      <p>AI 추천 후보 검색 <b>1건</b>이 나왔습니다.</p>
      <p className="mt-1 text-[14px] font-bold">{it.product}<span className="ml-1.5 text-[12px] font-normal text-foreground-muted">{it.family}</span></p>
      {it.reason && <p className="mt-1 text-[12.5px] text-foreground-muted">{it.reason}</p>}
      {checks.length > 0 && (
        <p className="mt-1 text-[12px] text-foreground-subtle">조건별 근거 {checks.length}개 중 확인 필요 {checks.filter((c) => !c.ok).length}개</p>
      )}
      <p className="mt-1.5 font-semibold">이 제품으로 추천해도 될까요?</p>
    </>
  );
}

/** 학습 문구 확인 — 사용자가 정확한 워딩·개념으로 고친 뒤 저장(사용자 2026-10-02). */
function LearnDraftCard({ draft, busy, done, approver, onSave, onCancel }: {
  draft: CorrectionDraft; busy: boolean; done?: "saved" | "cancel"; approver: boolean;
  onSave: (d: CorrectionDraft) => void; onCancel: () => void;
}) {
  const [kind, setKind] = useState<LearningKind>(kindOf(draft.kind));
  const [rule, setRule] = useState(draft.rule || draft.reason);
  const [situation, setSituation] = useState(draft.situation);
  const locked = !!done || busy;
  return (
    <div className="flex flex-col gap-2">
      <p className="font-semibold">이렇게 학습하려고 합니다. 정확한 표현이 맞는지 확인하고, 다르면 직접 고쳐 주세요.</p>
      <div className="flex flex-wrap items-center gap-1.5 text-[12px]">
        <span className="text-foreground-subtle">종류</span>
        {KINDS.map((k) => (
          <button key={k} type="button" disabled={locked} onClick={() => setKind(k)} title={KIND_LABEL[k].hint}
                  className={cn("rounded-full border px-2.5 py-0.5 font-medium transition",
                    kind === k ? "border-accent bg-accent text-white" : "border-border text-foreground-muted hover:text-foreground")}>
            {KIND_LABEL[k].text}
          </button>
        ))}
        <span className="text-[11.5px] text-foreground-subtle">{KIND_LABEL[kind].hint}</span>
      </div>
      <label className="flex flex-col gap-1 text-[12px]">
        <span className="font-medium text-foreground">학습 문구 <span className="font-normal text-foreground-subtle">· AI 가 다음 추천 때 그대로 읽습니다</span></span>
        <textarea rows={3} maxLength={300} value={rule} disabled={locked} onChange={(e) => setRule(e.target.value)}
                  className={cn(inputCls, "resize-y bg-surface text-[13px] leading-relaxed")} />
      </label>
      <label className="flex flex-col gap-1 text-[12px]">
        <span className="font-medium text-foreground">해당 조건 <span className="font-normal text-foreground-subtle">· 이런 조건일 때만 떠올립니다</span></span>
        <input value={situation} maxLength={300} disabled={locked} onChange={(e) => setSituation(e.target.value)}
               className={cn(inputCls, "bg-surface text-[12.5px]")} />
      </label>
      {(draft.right_name || draft.wrong_name) && (
        <p className="text-[12px] text-foreground-muted">
          {kind === "correction" && draft.wrong_name && <><span className="line-through decoration-red-500/60">{draft.wrong_name}</span> → </>}
          {draft.right_name && <b className="text-foreground">{draft.right_name}</b>}
          {draft.reason && <> · {kind === "experience" ? "결과" : "이유"}: {draft.reason}</>}
        </p>
      )}
      {done ? (
        <p className="text-[12.5px] font-medium text-foreground-muted">{done === "saved" ? "저장했습니다." : "학습하지 않았습니다."}</p>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <Choice primary disabled={busy || !rule.trim()}
                  onClick={() => onSave({ ...draft, kind, rule: rule.trim(), situation: situation.trim() })}>
            <Icon name="check" className="h-3.5 w-3.5" /> 이 문구로 학습
          </Choice>
          <Choice disabled={busy} onClick={onCancel}>취소</Choice>
          <span className="text-[11.5px] text-foreground-subtle">
            {approver ? "영업 관리자 — 저장하면 바로 반영됩니다" : "저장하면 영업 관리자 승인 후 반영됩니다"}
          </span>
        </div>
      )}
    </div>
  );
}

function CorrectionRow({ c, approver, onReview }: {
  c: Correction; approver: boolean; onReview: (id: number, action: "approve" | "reject" | "off" | "on") => void;
}) {
  const st = STATUS[c.review_status] ?? STATUS.pending;
  const kind = kindOf(c.kind);
  const btn = "rounded-md border border-border px-2 py-0.5 hover:bg-foreground/5";
  return (
    <li className={cn("flex flex-col gap-1 rounded-lg border px-3 py-2 text-[12.5px]",
                      c.review_status === "pending" ? "border-amber-500/40 bg-amber-500/[0.03]" : "border-border", !c.active && c.review_status !== "pending" && "opacity-60")}>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="font-mono text-[11.5px] text-foreground-subtle">#{c.id}</span>
        <KindBadge kind={c.kind} />
        <span className={cn("rounded-full px-2 py-0.5 text-[11px] font-medium", st.tone)}>{st.text}</span>
        {kind === "correction" && c.wrong_name && (
          <>
            <span className="text-foreground line-through decoration-red-500/60">{c.wrong_name}</span>
            <span className="text-foreground-subtle">→</span>
          </>
        )}
        {c.right_name && <span className="font-semibold text-foreground">{c.right_name}</span>}
        <span className="text-[11.5px] text-foreground-subtle">추천에 쓰인 횟수 {c.hits}</span>
      </div>
      {c.rule && <p className="font-medium text-foreground">“{c.rule}”</p>}
      {c.reason && <p className="text-foreground-muted">{kind === "experience" ? "결과" : "이유"}: {c.reason}</p>}
      {c.situation && <p className="text-foreground-muted">조건: {c.situation}</p>}
      <div className="flex flex-wrap items-center gap-2 text-[11.5px] text-foreground-subtle">
        <span>{c.submitted_by ?? "알 수 없음"} · {new Date(c.created_at).toLocaleString("ko-KR")}</span>
        {approver && (
          <span className="ml-auto flex gap-1.5">
            {c.review_status === "pending" ? (
              <>
                <button type="button" className={cn(btn, "border-emerald-600/40 text-emerald-700 dark:text-emerald-300")} onClick={() => onReview(c.id, "approve")}>승인</button>
                <button type="button" className={cn(btn, "border-red-500/40 text-red-600")} onClick={() => onReview(c.id, "reject")}>거절</button>
              </>
            ) : c.active ? (
              <button type="button" className={btn} onClick={() => onReview(c.id, "off")}>끄기</button>
            ) : (
              <button type="button" className={btn} onClick={() => onReview(c.id, "on")}>다시 켜기</button>
            )}
          </span>
        )}
      </div>
    </li>
  );
}

function MemoryTab({ list, onReview }: { list: CorrectionList; onReview: (id: number, a: "approve" | "reject" | "off" | "on") => void }) {
  const [kind, setKind] = useState<LearningKind | "all">("all");
  const [status, setStatus] = useState<Correction["review_status"] | "all">("all");
  const items = list.items.filter((c) => (kind === "all" || kindOf(c.kind) === kind) && (status === "all" || c.review_status === status));
  const pending = list.items.filter((c) => c.review_status === "pending").length;
  const chip = (on: boolean) => cn("rounded-full border px-2.5 py-0.5 text-[12px] font-medium transition",
    on ? "border-accent bg-accent text-white" : "border-border text-foreground-muted hover:text-foreground");
  return (
    <section className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-4">
      <div>
        <h2 className="text-[15px] font-semibold text-foreground">AI가 배운 내용</h2>
        <p className="text-[12.5px] text-foreground-muted">
          추천 대화에서 &ldquo;학습시킬까요?&rdquo;에 예를 누르고 문구를 확인해 저장한 내용입니다. 종류는 <b>경험</b>(실제 공정에서 써 본 결과)·
          <b>정정</b>(추천·데이터를 바로잡음)·<b>기준</b>(다음부터 이렇게 판단). 영업 관리자가 저장한 것은 바로 반영되고, 다른 사용자가 저장한 것은
          {list.approver ? " 여기서 [승인]해야 반영됩니다." : " 영업 관리자가 승인해야 반영됩니다."} 제품 DB(사양·사진)는 바뀌지 않습니다.
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-1 text-[12px] text-foreground-subtle">종류</span>
        <button type="button" className={chip(kind === "all")} onClick={() => setKind("all")}>전체 {list.items.length}</button>
        {KINDS.map((k) => (
          <button key={k} type="button" className={chip(kind === k)} onClick={() => setKind(k)}>
            {KIND_LABEL[k].text} {list.items.filter((c) => kindOf(c.kind) === k).length}
          </button>
        ))}
        <span className="ml-3 mr-1 text-[12px] text-foreground-subtle">상태</span>
        {(["all", "pending", "kept", "off", "rejected"] as const).map((s) => (
          <button key={s} type="button" className={cn(chip(status === s), s === "pending" && pending > 0 && status !== s && "border-amber-500/60 text-amber-700 dark:text-amber-300")}
                  onClick={() => setStatus(s)}>
            {s === "all" ? "전체" : STATUS[s].text}{s === "pending" && pending > 0 ? ` ${pending}` : ""}
          </button>
        ))}
      </div>
      {items.length === 0 ? (
        <p className="rounded-lg border border-dashed border-border px-3 py-6 text-center text-[12.5px] text-foreground-subtle">
          {list.items.length === 0 ? "아직 AI가 배운 내용이 없습니다. 추천 대화에서 바로잡거나 경험을 알려 주고 '학습해 줘'라고 말해 보세요." : "조건에 맞는 항목이 없습니다."}
        </p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {items.map((c) => <CorrectionRow key={c.id} c={c} approver={list.approver} onReview={onReview} />)}
        </ul>
      )}
    </section>
  );
}

/** 확정 제안 내역 — 고객 제안 한 건씩(사용자 2026-10-02: 하나의 프로젝트로 관리·내역 확인). */
/** 제품 영업 건의 [미팅 정보] — 요약만(사용자 2026-10-07): 고객사·미팅, 최종 추천(모델·구성·이유), 고객 조건 요약.
 *  AI 대화 기록·추천 결과 전체는 싣지 않는다. 고객사·로봇 제조사가 기록에 없으면 영업 건·로봇 사양 DB 값을 쓴다(백엔드). */
type MeetingProposal = ProductProposal & { deal_no?: string | null; deal_customer?: string | null; robot_makers?: Record<string, string> };
const KO: Record<string, string> = {
  cobot: "협동로봇", industrial: "산업용 로봇", other: "기타",
  transfer: "이송", assembly: "조립", machining: "가공", inspection: "검사",
};
const str = (v: unknown) => (v == null || v === "" ? "" : String(v));
const num = (v: unknown) => (typeof v === "number" ? v : v != null && v !== "" && !Number.isNaN(Number(v)) ? Number(v) : null);

function MeetingInfo({ id, onClose }: { id: number; onClose: () => void }) {
  const [sel, setSel] = useState<MeetingProposal | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    fetch(`/api/product-recommend/proposals/${id}`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("미팅 정보를 불러오지 못했습니다."))))
      .then((d: MeetingProposal) => { if (alive) setSel(d); })
      .catch((e: Error) => { if (alive) setErr(e.message); });
    return () => { alive = false; };
  }, [id]);
  const rec = sel?.snapshot?.recommendation;
  const it = rec?.intake;
  const pj = (it?.project ?? {}) as Record<string, unknown>;
  const mt = rec?.meeting?.meeting;
  const customer = sel ? sel.customer || str(mt?.customer) || str(pj.customer_name) || sel.deal_customer || "" : "";
  const fromDeal = !!sel && !sel.customer && !str(mt?.customer) && !str(pj.customer_name) && !!sel.deal_customer;
  const atc = rec?.atc;
  const top = atc?.screening_candidates.find((c) => c.models.length && !c.out_of_range);
  const parts = (atc?.accessories?.items ?? []).filter((x) => !x.optional || x.included);
  const env = (pj.environment ?? {}) as Record<string, unknown>;
  const proc = (pj.process ?? {}) as Record<string, unknown>;

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/30 p-3 md:p-8" onClick={onClose}>
      <section role="dialog" aria-label="미팅 정보" onClick={(e) => e.stopPropagation()}
               className="scroll-thin flex max-h-full w-full max-w-2xl flex-col gap-4 overflow-y-auto rounded-2xl border border-border bg-background p-5 shadow-2xl">
        <div className="flex items-start gap-2">
          <div className="min-w-0">
            <h2 className="text-[17px] font-bold text-foreground">미팅 정보</h2>
            {sel && (
              <p className="text-[12px] text-foreground-subtle">
                {[sel.deal_no, `최종 제안 확정 ${sel.updated_at.slice(0, 10)}`, sel.submitted_by && `확정 ${sel.submitted_by}`].filter(Boolean).join(" · ")}
              </p>
            )}
          </div>
          <button type="button" className={cn(secondaryBtn, "ml-auto")} onClick={onClose}><Icon name="x" className="h-4 w-4" /> 닫기</button>
        </div>
        {err && <ErrorBox error={err} />}
        {!sel && !err && <p className="text-[12.5px] text-foreground-subtle">불러오는 중…</p>}

        {sel && (
          <>
            {/* 고객 */}
            <MeetBlock title="고객">
              <p className="text-[16px] font-bold">{customer || "고객사 미기재"}</p>
              {fromDeal && <p className="text-[11.5px] text-foreground-subtle">미팅 기록에 고객사가 없어 견적서에 적은 고객사를 보여 줍니다.</p>}
              <MeetRows rows={[
                ["고객 담당", str(pj.customer_contact)],
                ["미팅", [str(mt?.date) || str(pj.meeting_date), (str(mt?.writer) || str(pj.writer)) && `작성 ${str(mt?.writer) || str(pj.writer)}`].filter(Boolean).join(" · ")],
              ]} />
            </MeetBlock>

            {/* 최종 추천 */}
            <MeetBlock title="최종 추천" accent>
              {rec?.kind === "atc" ? (
                <>
                  <div className="flex flex-wrap items-baseline gap-2">
                    <span className="text-[20px] font-bold text-accent">{top?.models.map((m) => m.name).join(" / ") || sel.model || "-"}</span>
                    {atc?.junior_summary.candidate_series && <span className="text-[12.5px] text-foreground-muted">{atc.junior_summary.candidate_series}</span>}
                  </div>
                  {parts.length > 0 && (
                    <div className="flex flex-wrap gap-1.5">
                      {parts.map((x, i) => (
                        <span key={i} className="rounded-md bg-foreground/[0.05] px-2 py-0.5 text-[12px]">{x.name}{x.qty != null ? ` ×${x.qty}` : ""}</span>
                      ))}
                    </div>
                  )}
                  {atc?.junior_summary.reason_in_plain_korean && (
                    <p className="text-[12.5px] leading-relaxed text-foreground-muted">{atc.junior_summary.reason_in_plain_korean}</p>
                  )}
                </>
              ) : (
                <>
                  <p className="text-[18px] font-bold text-accent">{sel.model || "-"}</p>
                  {sel.summary && <p className="text-[12.5px] text-foreground-muted">{sel.summary}</p>}
                </>
              )}
            </MeetBlock>

            {/* 고객 조건 요약 */}
            <MeetBlock title="고객 조건 요약">
              {rec?.kind === "atc" && it ? (
                <MeetRows rows={[
                  ...it.robots.map((r, i): [string, string] => {
                    const model = str(r.model);
                    const maker = str(r.manufacturer) || sel.robot_makers?.[model] || "";
                    const sp = (r.speed ?? {}) as Record<string, unknown>;
                    return [it.robots.length > 1 ? `로봇 ${i + 1}` : "로봇", [
                      [maker || "제조사 미기재", model || "모델 미기재"].join(" "), KO[str(r.type)] ?? "", num(r.quantity) != null ? `${num(r.quantity)}대` : "",
                      num(sp.value) != null ? `속도 ${num(sp.value)}${str(sp.unit)}` : "",
                    ].filter(Boolean).join(" · ")];
                  }),
                  ["작업", [KO[str(proc.category)] ?? str(proc.category), num(pj.tool_count) != null && `교체 툴 ${num(pj.tool_count)}개`,
                           env.has_special_conditions === true ? "특별한 환경 있음" : env.has_special_conditions === false ? "특별한 환경 없음" : ""].filter(Boolean).join(" · ")],
                  ...it.tools.map((t, i): [string, string] => {
                    const ti = (t.intake ?? {}) as Record<string, unknown>;
                    const mc = (t.mass_components ?? {}) as Record<string, unknown>;
                    const uses = [ti.needs_power === true && "전기", ti.needs_air === true && "공압", ti.has_sensors_or_signals === true && "센서", ti.tilts_or_flips === true && "기울임"]
                      .filter(Boolean).join("·");
                    return [`툴 ${i + 1}`, [`${str(t.name) || `툴 ${i + 1}`}${num(t.installed_quantity) != null ? ` ×${num(t.installed_quantity)}` : ""}`,
                      num(ti.tool_assembly_mass_kg) != null && `툴 ${num(ti.tool_assembly_mass_kg)}kg`,
                      num(mc.max_simultaneous_workpieces_kg) != null && `제품 ${num(mc.max_simultaneous_workpieces_kg)}kg`, uses].filter(Boolean).join(" · ")];
                  }),
                ]} />
              ) : (
                <p className="whitespace-pre-wrap text-[12.5px] text-foreground-muted">{(rec?.request ?? "").slice(0, 600) || "-"}</p>
              )}
            </MeetBlock>

            {sel.memo && <MeetBlock title="메모"><p className="text-[12.5px]">{sel.memo}</p></MeetBlock>}
          </>
        )}
      </section>
    </div>
  );
}

function MeetBlock({ title, children, accent }: { title: string; children: ReactNode; accent?: boolean }) {
  return (
    <section className={cn("flex flex-col gap-2 rounded-xl border p-4", accent ? "border-accent/40 bg-accent/[0.03]" : "border-border bg-surface")}>
      <h3 className="text-[11.5px] font-semibold uppercase tracking-[0.1em] text-foreground-subtle">{title}</h3>
      {children}
    </section>
  );
}

function MeetRows({ rows }: { rows: [string, string][] }) {
  const shown = rows.filter(([, v]) => v);
  if (!shown.length) return null;
  return (
    <dl className="grid grid-cols-[72px_1fr] gap-x-3 gap-y-1.5 text-[13px]">
      {shown.map(([k, v], i) => (
        <div key={i} className="contents">
          <dt className="text-foreground-muted">{k}</dt>
          <dd className="text-foreground">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export type RecommendTab = "recommend" | "quote" | "order" | "deals" | "memory";

export function ProductRecommend({ initial, initialTab, startManual }: {
  initial: CorrectionList; initialTab?: RecommendTab; startManual?: boolean;
}) {
  const [tab, setTab] = useState<RecommendTab>(initialTab ?? "recommend");
  const [quoteId, setQuoteId] = useState<number | null>(null);
  const [testSamples, setTestSamples] = useState<AtcTestSample[] | null>(null);
  const [showTest, setShowTest] = useState(false);
  // 최종 제안 확정 — 이번 추천을 확정 제안으로 저장했는지(id), 내역 탭 새로고침·열기
  const [finalized, setFinalized] = useState<{ recId: number; id: number; dealNo: string | null } | null>(null);
  const [needIni, setNeedIni] = useState(false);
  const [meetingId, setMeetingId] = useState<number | null>(null);
  const [orderId, setOrderId] = useState<number | null>(null);
  // 제품 종류 탭 — 종류마다 질문지가 다르다(ATC 는 영업부 질문지, 그 외는 임시 질문)
  const [product, setProduct] = useState<"atc" | "other">("atc");
  const [phase, setPhase] = useState<"form" | "session">("form");
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [atcIntake, setAtcIntake] = useState<AtcIntake>(emptyIntake);
  const [atcStep, setAtcStep] = useState(0);
  const [showUpload, setShowUpload] = useState(false);
  const [meeting, setMeeting] = useState<AtcMeeting | null>(null);
  const [stage, setStage] = useState<Stage>("asking");
  const [left, setLeft] = useState<number | null>(null);
  const [finding, setFinding] = useState<{ message: string; sub: string } | null>(null);
  const [rec, setRec] = useState<Recommendation | null>(null);
  // 후보를 찾으면 바로 왼쪽에 결과를 보이고, 대화창의 '추천해도 될까요?' 답에 따라 상태만 바뀐다(사용자 2026-10-02, ui_v9)
  const [recState, setRecState] = useState<RecState>("pending");
  // 후보 찾기 전 왼쪽 '검토 현황' — 답할 때마다 다시 계산한 판정
  const [preview, setPreview] = useState<AtcOutput | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [list, setList] = useState<CorrectionList>(initial);
  const seq = useRef(0);
  const intakeRef = useRef(atcIntake);
  // 첫 화면 '견적서 수기 작성' → 빈 견적서를 만들고 견적서 탭으로(한 번만 — 새로고침해도 또 만들지 않게 주소에서 뺀다)
  const manualStarted = useRef(false);
  useEffect(() => {
    if (!startManual || manualStarted.current) return;
    manualStarted.current = true;
    window.history.replaceState(null, "", "/proposals/recommend?tab=quote");
    post<QuoteSession>("/quotes/manual", { form: savedContact() })
      .then((q) => { setQuoteId(q.id); setTab("quote"); })
      .catch((e: Error) => setError(e.message));
  }, [startManual]);
  const answersRef = useRef(answers);
  const skipped = useRef(new Set<string>());
  // 실제 제품으로 보여 외부 사양 검색을 이미 제안한 툴(같은 툴을 다시 묻지 않음)
  const specOffered = useRef(new Set<number>());
  const recRef = useRef<Recommendation | null>(null);
  // AI 가 마지막으로 제안한 모델 — '이걸로 할게'가 가리키는 대상
  const suggestedRef = useRef<string | null>(null);
  const [versions, setVersions] = useState<RecVersion[]>([]);
  const [verAt, setVerAt] = useState(0);
  const versionsRef = useRef<RecVersion[]>([]);
  const verAtRef = useRef(0);
  const chatEnd = useRef<HTMLDivElement>(null);
  useEffect(() => { intakeRef.current = atcIntake; }, [atcIntake]);
  useEffect(() => { answersRef.current = answers; }, [answers]);
  useEffect(() => { recRef.current = rec; }, [rec]);
  const finalizedRef = useRef(finalized);
  useEffect(() => { finalizedRef.current = finalized; }, [finalized]);
  useEffect(() => { chatEnd.current?.scrollIntoView({ block: "end", behavior: "smooth" }); }, [msgs, busy]);

  const push = (m: Omit<Msg, "id"> & Record<string, unknown>) => setMsgs((xs) => [...xs, { ...m, id: ++seq.current } as Msg]);
  const patch = (id: number, state: string) => setMsgs((xs) => xs.map((m) => (m.id === id ? ({ ...m, state } as Msg) : m)));
  const say = (t: string) => push({ role: "ai", type: "text", text: t });
  const setIntake = (v: AtcIntake) => { intakeRef.current = v; setAtcIntake(v); };

  /** 추천 결과가 확정될 때마다 새 버전 — 예전 버전을 보다가 바꿔도 맨 뒤에 쌓는다. */
  function addVersion(r: Recommendation) {
    const next = [...versionsRef.current, { rec: r, intake: intakeRef.current, model: topModel(r) }];
    versionsRef.current = next;
    verAtRef.current = next.length - 1;
    setVersions(next);
    setVerAt(next.length - 1);
  }

  function resetVersions() {
    versionsRef.current = [];
    verAtRef.current = 0;
    setVersions([]);
    setVerAt(0);
  }

  /** 예전 결과로 — 그때의 질문 답(intake)도 함께 되돌린다. */
  function showVersion(i: number) {
    const v = versionsRef.current[i];
    if (!v || finalizedNow()) return;
    setRec(v.rec);
    recRef.current = v.rec;
    setIntake(v.intake);
    setRecState("confirmed");
    setStage("done");
    verAtRef.current = i;
    setVerAt(i);
  }

  async function reloadList() {
    const r = await fetch("/api/product-recommend/corrections", { cache: "no-store" });
    if (r.ok) setList(await r.json());
  }

  function fail(e: unknown, fallback: string) {
    setError(e instanceof Error ? e.message : fallback);
  }

  // ── 진행: 빠진 정보 묻기 → 조건 확인 → 후보 검색 → 추천 확인 ──

  /** [AI와 같이 제품 검색 진행] — 대화를 새로 시작. */
  async function startSession(m: AtcMeeting | null = meeting) {
    setError(null);
    setPhase("session");
    setMsgs([]);
    setRec(null);
    setRecState("pending");
    setPreview(null);
    resetVersions();
    skipped.current = new Set();
    specOffered.current = new Set();
    setStage("asking");
    say(product === "atc"
      ? (m ? "미팅 질문지를 읽어 왼쪽에 정리했습니다. 비어 있는 것만 몇 가지 여쭤보고, 조건을 함께 확인한 뒤 툴체인저 후보를 찾겠습니다."
           : "질문 답을 왼쪽에 정리했습니다. 비어 있는 것만 몇 가지 여쭤보고, 조건을 함께 확인한 뒤 툴체인저 후보를 찾겠습니다.")
      : "Q&A 답을 왼쪽에 정리했습니다. 비어 있는 핵심 질문만 몇 가지 여쭤보고, 조건을 함께 확인한 뒤 제품을 찾겠습니다.");
    await advance();
  }

  /** [제품 추천 질문 모두 채우기 (테스트용)] — 샘플 질문지(정답지) 답을 넣고 Q&A·확인 질문 없이 바로 추천을 확정 상태로.
   *  최종 제안 이후(견적서 등)를 시험할 때마다 질문을 일일이 채우지 않으려고(사용자 2026-10-06). */
  async function openTestSamples() {
    setShowTest((v) => !v);
    if (testSamples) return;
    try {
      const r = await fetch("/api/product-recommend/atc/test-samples", { cache: "no-store" });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof d?.detail === "string" ? d.detail : "샘플 목록을 불러오지 못했습니다.");
      setTestSamples((d as { items: AtcTestSample[] }).items);
    } catch (e) {
      fail(e, "샘플 목록을 불러오지 못했습니다.");
    }
  }

  async function testFill(sample: AtcTestSample) {
    setError(null);
    setShowTest(false);
    setBusy("검색 중");
    try {
      const r = await fetch(`/api/product-recommend/atc/test-samples/${sample.id}`, { cache: "no-store" });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof d?.detail === "string" ? d.detail : "샘플을 불러오지 못했습니다.");
      const intake = (d as { intake: AtcIntake }).intake;
      setIntake(intake);
      setMeeting(null);
      setPhase("session");
      setMsgs([]);
      setRec(null);
      setRecState("pending");
      setPreview(null);
      resetVersions();
      skipped.current = new Set();
      specOffered.current = new Set();
      setLeft(0);
      setStage("searching");
      say(`테스트용 — ${sample.label} 정답지 답으로 질문을 모두 채웠습니다. Q&A 를 건너뛰고 바로 추천합니다.`);
      setFinding({ message: "AI 가 제품 DB 의 툴체인저 중에서 추천 후보를 검색하는 중입니다…", sub: "테스트용 정답지 입력으로 바로 판정합니다" });
      const res = await post<AskResult>("/atc/recommend", { intake, meeting: null });
      if (res.kind === "recommendation") {
        const rr = res.recommendation;
        setRec(rr);
        recRef.current = rr;
        setRecState("confirmed");
        setStage("done");
        if (topModel(rr)) addVersion(rr);
        const name = rr.atc?.screening_candidates.find((c) => c.models.length && !c.out_of_range)?.models.map((x) => x.name).join(" / ");
        say(name
          ? `추천을 확정했습니다 — ${name}. 왼쪽 아래 [최종 제안 확정]을 누르면 ${sample.quotable ? "[견적서 작성 →]으로 이어집니다." : "확정 제안으로 저장됩니다(이 모델은 단가표에 없어 견적서는 만들 수 없습니다)."}`
          : "이 샘플로는 후보를 고르지 못했습니다 — 왼쪽 결과의 이유를 확인해 주세요.");
      }
    } catch (e) {
      fail(e, "테스트 샘플로 추천하지 못했습니다.");
      setStage("asking");
    } finally {
      setFinding(null);
      setBusy(null);
    }
  }

  /** 다음에 물을 질문 — 없으면 조건 확인으로. */
  async function advance() {
    setBusy("확인 중");
    try {
      if (product === "atc") {
        const ev = await post<AtcOutput>("/atc/evaluate", { intake: intakeRef.current });
        setPreview(ev);
        // 실제 제품인 툴이면 먼저 공개 사양을 외부 검색할지 묻는다(비어 있는 칸을 묻기 전에)
        const real = (ev.real_products ?? []).find((x) => !specOffered.current.has(x.index));
        if (real) {
          specOffered.current.add(real.index);
          setStage("asking");
          push({ role: "ai", type: "spec_offer", tool: real.index, name: real.name, product: real.product });
          return;
        }
        const queue = ev.pending_questions.filter((q) => !skipped.current.has(pendingKey(q)));
        setLeft(queue.length);
        if (queue.length) {
          setStage("asking");
          // 같은 질문은 툴(로봇)마다 따로 묻지 않고 한 말풍선에서(사용자 2026-10-02)
          push({ role: "ai", type: "atcq", qs: queue.filter((x) => x.id === queue[0].id && x.kind === queue[0].kind) });
          return;
        }
      } else {
        const queue = QNA_CORE.filter((k) => !answersRef.current[k]?.trim() && !skipped.current.has(k));
        setLeft(queue.length);
        if (queue.length) {
          setStage("asking");
          push({ role: "ai", type: "qnaq", key: queue[0] });
          return;
        }
      }
      setStage("confirm");
      setLeft(0);
      push({ role: "ai", type: "confirm",
             lines: product === "atc" ? atcLines(intakeRef.current)
               : QNA.filter((x) => answersRef.current[x.key]?.trim()).map((x) => `${x.short}: ${answersRef.current[x.key].trim()}`) });
    } catch (e) {
      fail(e, "다음 질문을 정하지 못했습니다.");
    } finally {
      setBusy(null);
    }
  }

  function currentQuestion(): Msg | undefined {
    return [...msgs].reverse().find((m) => m.role === "ai" && (m.type === "atcq" || m.type === "qnaq") && !m.state);
  }

  async function answerAtc(m: Extract<Msg, { type: "atcq" }>, how: "done" | "unknown" | "skip") {
    if (how === "unknown") setIntake(m.qs.reduce((acc, q) => markUnknown(acc, q), intakeRef.current));
    const parts = m.qs.map((q) => ({ q, text: describeAnswer(q, intakeRef.current) }));
    for (const x of parts) if (how === "skip" || (how === "done" && !x.text)) skipped.current.add(pendingKey(x.q));
    const multi = m.qs.length > 1;
    const said = how === "unknown" ? "모름" : how === "skip" ? "건너뛰기 — 나중에 확인"
      : parts.filter((x) => x.text).map((x) => (multi ? `${x.q.entity_label}: ${x.text}` : x.text)).join("\n") || "건너뛰기 — 나중에 확인";
    patch(m.id, how);
    push({ role: "user", text: said });
    await advance();
  }

  async function answerQna(m: Extract<Msg, { type: "qnaq" }>, value: string | null) {
    if (value === null) skipped.current.add(m.key);
    else {
      const next = { ...answersRef.current, [m.key]: value };
      answersRef.current = next;
      setAnswers(next);
    }
    patch(m.id, value === null ? "skip" : "done");
    push({ role: "user", text: value ?? "건너뛰기 — 나중에 확인" });
    await advance();
  }

  async function confirmConditions(m: Extract<Msg, { type: "confirm" }>, ok: boolean) {
    patch(m.id, ok ? "yes" : "no");
    push({ role: "user", text: ok ? "맞아요, 후보를 찾아 주세요" : "고칠 게 있어요" });
    if (!ok) {
      say(product === "atc"
        ? "어느 부분이 다른가요? 왼쪽 [Q&A 고치기]에서 답을 고친 뒤 다시 [AI와 같이 제품 검색 진행]을 누르면 이어서 확인합니다."
        : "어느 부분이 다른가요? 바꿀 조건을 여기에 적어 주시면 조건에 더해 다시 확인하고, 왼쪽 [Q&A 고치기]에서 직접 고쳐도 됩니다.");
      return;
    }
    await search();
  }

  async function search(chosen?: string) {
    setStage("searching");
    setBusy("검색 중");
    setFinding(product === "atc"
      ? { message: "AI 가 제품 DB 의 툴체인저 중에서 추천 후보를 검색하는 중입니다…", sub: "툴측 무게·속도·전기·공압·케이블 조건을 선정 규칙으로 계산하고 있습니다" }
      : { message: "AI 가 회사 제품 DB 에서 조건에 가장 맞는 제품을 찾는 중입니다…", sub: "재질·무게·로봇·공압 조건을 제품 원리·사양과 대조하고 있습니다 (보통 10~30초)" });
    try {
      const res = product === "atc"
        ? await post<AskResult>("/atc/recommend", { intake: intakeRef.current, meeting })
        : await post<AskResult>("", { request: qnaText(answersRef.current), mode: "recommend" });
      if (res.kind === "recommendation") {
        if (chosen) acceptChosen(res.recommendation, chosen);
        else offer(res.recommendation);
      }
    } catch (e) {
      fail(e, "후보를 찾지 못했습니다.");
      setStage("confirm");
    } finally {
      setFinding(null);
      setBusy(null);
    }
  }

  /** 담당자가 대화로 고른 모델로 다시 찾은 결과 — 다시 묻지 않고 바로 확정 상태로(최종 제안 확정 전까지 몇 번이고 바꿀 수 있다). */
  function acceptChosen(r: Recommendation, chosen: string) {
    setRec(r);
    recRef.current = r;
    setMsgs((xs) => xs.map((x) => (x.role === "ai" && (x.type === "offer" || x.type === "suggest") && !x.state ? { ...x, state: "old" } : x)));
    const pref = r.atc?.preference;
    const name = r.atc?.screening_candidates.find((c) => c.models.length && !c.out_of_range)?.models.map((x) => x.name).join(" / ");
    setStage("done");
    setRecState("confirmed");
    addVersion(r);
    say(pref && !pref.applied
      ? `${chosen} 은(는) 적용할 수 없어 규칙 후보(${name ?? "-"})를 유지했습니다 — ${pref.reason}.`
      : `${name ?? chosen}(으)로 바꿔 추천했습니다. 왼쪽 결과와 근거를 확인해 주세요 — 또 바꾸고 싶으면 말씀해 주시고, 이대로면 [최종 제안 확정]을 눌러 주세요.`);
  }

  /** 대화에서 고른 모델로 다시 추천 — 질문지 입력에 담당자 선택을 남기고(규칙이 검증) 다시 찾는다. */
  async function switchTo(model: string) {
    if (finalizedNow()) {
      say("최종 제안을 이미 확정해서 모델을 바꿀 수 없습니다. 바꾸려면 [처음부터] 다시 추천을 받아 주세요.");
      return;
    }
    const cur = intakeRef.current;
    setIntake({ ...cur, project: { ...(cur.project ?? {}), preferred_model: model } } as AtcIntake);
    await search(model);
  }

  /** 지금 추천을 최종 제안으로 확정했는가 — 확정 뒤에는 모델 바꾸기를 잠근다(사용자 2026-10-06). */
  function finalizedNow() {
    return !!(finalizedRef.current && recRef.current && finalizedRef.current.recId === recRef.current.id);
  }

  /** 후보가 나오면 바로 보여 주지 않고 묻는다 — 후보가 없으면 왜 없는지와 확인할 것을 보인다. */
  function offer(r: Recommendation) {
    setRec(r);
    recRef.current = r;
    setRecState("pending");
    const has = r.kind === "atc"
      ? r.atc!.screening_candidates.some((c) => c.models.length && !c.out_of_range)
      : r.items.length > 0;
    if (has) {
      setStage("offer");
      // 새 후보를 제안하면 이전 제안의 [네]/[아니요]는 닫는다 — 지난 후보를 실수로 확정하지 않게
      setMsgs((xs) => xs.map((x) => (x.role === "ai" && x.type === "offer" && !x.state ? { ...x, state: "old" } : x)));
      push({ role: "ai", type: "offer", rec: r });
      return;
    }
    setStage("done");
    say(r.kind === "atc"
      ? `아직 후보를 고를 수 없습니다 — ${r.atc!.junior_summary.reason_in_plain_korean} 왼쪽 결과에 고객에게 확인할 것과 받아 올 자료를 정리했습니다.`
      : "조건에 맞는 제품을 찾지 못했습니다. 조건을 바꿔 다시 찾아 달라고 말씀해 주세요.");
  }

  function answerOffer(m: Extract<Msg, { type: "offer" }>, ok: boolean) {
    patch(m.id, ok ? "yes" : "no");
    push({ role: "user", text: ok ? "네, 이 추천으로 진행" : "아니요" });
    setStage("done");
    setRecState(ok ? "confirmed" : "held");
    if (ok) {
      addVersion(m.rec);
      say("이 추천으로 진행합니다. 다른 모델이 낫겠다 싶으면 \"이거 괜찮아?\"·\"TCV2로 바꿔 줘\"처럼 말씀해 주세요 — [최종 제안 확정] 전까지 몇 번이고 바꿀 수 있고, "
        + "\"아까 결과로 돌려 줘\"라고 하거나 결과 창 위 ‹ › 로 예전 결과로 돌아갈 수 있습니다. 이대로면 왼쪽 아래 [최종 제안 확정] → [견적서 작성 →]으로 이어집니다.");
    } else {
      say("어떤 점이 맞지 않나요? 고려할 조건이나 빠진 정보를 말씀해 주시면 반영해서 다시 찾겠습니다.");
    }
  }

  // ── 실제 제품 툴: 외부 공개 사양 검색 → 확인 후 빈 칸에만 채우기(사용자 2026-10-02) ──

  async function answerSpecOffer(m: Extract<Msg, { type: "spec_offer" }>, ok: boolean) {
    patch(m.id, ok ? "yes" : "no");
    push({ role: "user", text: ok ? "네, 검색해 주세요" : "건너뛰기" });
    if (!ok) {
      await advance();
      return;
    }
    setBusy("외부 AI 가 공개 사양을 검색하는 중(1~2분)");
    try {
      const spec = await post<SpecSearch>("/atc/spec-search", { product: m.product });
      push({ role: "ai", type: "spec_result", tool: m.tool, spec });
      if (!spec.fields.length) {
        setBusy(null);
        await advance();
      }
    } catch (e) {
      say(`공개 사양을 검색하지 못했습니다 — ${e instanceof Error ? e.message : "외부 AI 오류"}. 비어 있는 칸은 계속 여쭤보겠습니다.`);
      setBusy(null);
      await advance();
    } finally {
      setBusy(null);
    }
  }

  async function applySpec(m: Extract<Msg, { type: "spec_result" }>, apply: boolean) {
    patch(m.id, apply ? "applied" : "skipped");
    if (apply) {
      let next = intakeRef.current;
      const tool = (next.tools[m.tool] ?? {}) as Record<string, unknown>;
      const filled: string[] = [];
      const evidence = [...((tool.spec_evidence as unknown[]) ?? [])];
      for (const f of m.spec.fields) {
        const p = toolPath(f.path);
        if (!empty(readPath(next.tools[m.tool], p))) continue;
        next = updateEntity(next, "tool", m.tool, p, f.value);
        filled.push(f.label.split("(")[0].trim());
        // JSON answer_evidence — 외부 공개 자료에서 온 값은 '추정'(출처 기록), 확정은 엔지니어
        evidence.push({ field_path: f.path, normalized_value: f.value, raw_answer: f.evidence, status: "estimated",
                        source_document: m.spec.sources[0]?.url ?? null, source_location: "외부 공개 자료 검색" });
      }
      next = updateEntity(next, "tool", m.tool, "spec_evidence", evidence);
      setIntake(next);
      push({ role: "user", text: filled.length ? `빈 칸에 채우기 — ${filled.join(", ")}` : "채울 빈 칸이 없음" });
    } else {
      push({ role: "user", text: "쓰지 않기" });
    }
    await advance();
  }

  // ── 자유 입력 — 단계에 따라 질문의 답 / 조건 보탬 / 대화·학습 요청 ──

  function history() {
    return msgs.flatMap((m) => m.role === "user" ? [{ role: "user", text: m.text }]
      : m.type === "text" ? [{ role: "assistant", text: m.text }]
      : m.type === "agent" && m.answer ? [{ role: "assistant", text: m.answer }] : []).slice(-12);
  }

  async function send() {
    const t = text.trim();
    if (!t || busy) return;
    setError(null);
    setText("");
    const cur = currentQuestion();
    if (stage === "asking" && cur?.role === "ai" && cur.type === "qnaq") {
      await answerQna(cur, t);
      return;
    }
    push({ role: "user", text: t });
    if (stage === "asking" && cur?.role === "ai" && cur.type === "atcq") {
      // 여러 툴을 한 번에 묻는 질문이면 말에서 툴 이름으로 나눠 채운다(대화 처리)
      if (cur.qs.length > 1) {
        if (await atcChat(t)) patch(cur.id, "done");
        return;
      }
      setBusy("답 정리 중");
      try {
        const res = await post<AtcAnswer>("/atc/answer", { intake: intakeRef.current, question: cur.qs[0], text: t });
        if (!res.understood) {
          // 지금 질문의 답이 아니면(다른 정보·질문·요청) 일반 대화로 — 그 안의 정보도 칸에 채운다
          setBusy(null);
          if (await atcChat(t)) patch(cur.id, "done");
          return;
        }
        setIntake(res.intake);
        patch(cur.id, "done");
      } catch (e) {
        fail(e, "답을 정리하지 못했습니다.");
        return;
      } finally {
        setBusy(null);
      }
      await advance();
      return;
    }
    if (product === "atc") {
      await atcChat(t);
      return;
    }
    if (stage === "confirm" || !recRef.current) {
      if (product === "other") {
        const next = { ...answersRef.current, etc: [answersRef.current.etc, t].filter(Boolean).join(" / ") };
        answersRef.current = next;
        setAnswers(next);
        say("조건에 더했습니다(기타 요구). 다시 확인해 주세요.");
        await advance();
      } else {
        say("조건은 왼쪽 [Q&A 고치기]에서 고쳐 주세요. 고친 뒤 다시 [AI와 같이 제품 검색 진행]을 누르면 이어서 확인합니다.");
      }
      return;
    }
    setBusy("답하는 중");
    try {
      const res = await post<AskResult>("", { request: t, mode: "chat", recommendation_id: recRef.current.id, history: history() });
      if (res.kind === "learn_offer") {
        push({ role: "ai", type: "learn_offer", text: res.answer, note: t });
      } else if (res.kind === "answer") {
        say(res.answer);
        if (res.recommendation) offer(res.recommendation);
      }
    } catch (e) {
      fail(e, "요청을 처리하지 못했습니다.");
      setText(t);
    } finally {
      setBusy(null);
    }
  }

  /** 툴체인저 대화(GPT 식, 사용자 2026-10-06) — AI 의 생각 과정·실제로 한 일·답을 흘려 보이고,
   *  끝나면 검증된 결과대로 칸 채우기·다시 찾기·모델 바꾸기·되돌리기·학습 제안을 한다. */
  async function atcChat(t: string): Promise<number> {
    setBusy("AI 가 생각하는 중");
    const id = ++seq.current;
    setMsgs((xs) => [...xs, { id, role: "ai", type: "agent", think: "", steps: [], answer: "", live: true }]);
    const upd = (f: (m: AgentMsg) => AgentMsg) =>
      setMsgs((xs) => xs.map((x) => (x.id === id && x.role === "ai" && x.type === "agent" ? f(x) : x)));
    let res: AgentDone | null = null;
    let err: string | null = null;
    const vers = versionsRef.current;
    try {
      await streamAgent({
        intake: intakeRef.current, message: t, history: history(), recommendation_id: recRef.current?.id ?? null,
        suggested: suggestedRef.current, locked: finalizedNow(),
        versions: vers.map((v, i) => ({ n: i + 1, model: v.model, label: `${v.model ?? "후보 없음"}${i === 0 ? " (첫 추천)" : ""}` })),
        current: vers.length ? verAtRef.current + 1 : null,
      }, (ev) => {
        if (ev.t === "think") upd((m) => ({ ...m, think: m.think + ev.text }));
        else if (ev.t === "answer") upd((m) => ({ ...m, answer: m.answer + ev.text }));
        else if (ev.t === "answer_set") upd((m) => ({ ...m, answer: ev.text }));
        else if (ev.t === "step") upd((m) => ({ ...m, steps: [...m.steps, { ok: ev.ok, text: ev.text }] }));
        else if (ev.t === "error") err = ev.text;
        else if (ev.t === "done") res = ev;
      });
    } catch (e) {
      err = e instanceof Error ? e.message : "요청을 처리하지 못했습니다.";
    } finally {
      upd((m) => ({ ...m, live: false }));
      setBusy(null);
    }
    const done = res as AgentDone | null;
    if (err || !done) {
      setError(err ?? "답을 받지 못했습니다.");
      setText(t);
      return 0;
    }
    try {
      if (done.applied.length) {
        setIntake(done.intake);
        setPreview(await post<AtcOutput>("/atc/evaluate", { intake: done.intake }));
      }
      if (done.intent === "learn") {
        push({ role: "ai", type: "learn_offer", text: "", note: t });
      } else if (done.intent === "suggest" && done.model) {
        suggestedRef.current = done.model;
        setMsgs((xs) => xs.map((x) => (x.role === "ai" && x.type === "suggest" && !x.state ? { ...x, state: "old" } : x)));
        push({ role: "ai", type: "suggest", model: done.model, text: "" });
      } else if (done.intent === "switch" && done.model) {
        await switchTo(done.model);
      } else if (done.intent === "revert" && done.version) {
        const before = topModel(recRef.current!);
        showVersion(done.version - 1);
        const after = versionsRef.current[done.version - 1]?.model;
        upd((m) => ({ ...m, steps: [...m.steps, { ok: true, text: `결과 창: ${before ?? "-"} → ${after ?? "-"} (${done.version}/${versionsRef.current.length})` }] }));
      } else if (done.intent === "recommend") {
        await search();
      } else if (stage === "asking" && done.applied.length) {
        await advance();
      }
    } catch (e) {
      fail(e, "요청을 처리하지 못했습니다.");
    }
    return done.applied.length;
  }

  /** 확정 제안에 남길 대화 기록 — 말풍선을 글로(질문·조건 확인·후보 제안·반영한 값 포함). */
  function transcript() {
    return msgs.flatMap((m): { role: string; text: string }[] => {
      if (m.role === "user") return [{ role: "user", text: m.text }];
      switch (m.type) {
        case "text": return [{ role: "assistant", text: m.text }];
        case "agent": return m.answer ? [{ role: "assistant", text: m.steps.length
          ? `${m.answer.trim()}\n${m.steps.map((x) => `${x.ok ? "✓" : "✗"} ${x.text}`).join("\n")}` : m.answer.trim() }] : [];
        case "atcq": return [{ role: "assistant", text: `[질문] ${m.qs[0].question_ko}${m.qs[0].scope !== "project" ? ` (${m.qs.map((q) => q.entity_label).join(", ")})` : ""}` }];
        case "qnaq": return [{ role: "assistant", text: `[질문] ${QNA.find((x) => x.key === m.key)?.q ?? ""}` }];
        case "confirm": return [{ role: "assistant", text: `[조건 확인]\n${m.lines.join("\n")}` }];
        case "offer": {
          const r = m.rec;
          const name = r.kind === "atc" ? r.atc!.screening_candidates.find((c) => c.models.length && !c.out_of_range)?.models.map((x) => x.name).join(" / ") : r.items[0]?.product;
          return [{ role: "assistant", text: `[후보 제안] ${name ?? "-"} — 추천해도 될까요?` }];
        }
        case "applied": return [{ role: "assistant", text: `${m.text}\n[반영] ${m.items.map((x) => `${x.label}: ${x.display}`).join(", ")}` }];
        case "learn_offer": return [{ role: "assistant", text: `${m.text}\n[학습 제안]` }];
        case "learn_saved": return [{ role: "assistant", text: `[학습 저장 #${m.c.id}] ${m.c.rule}` }];
        case "spec_result": return [{ role: "assistant", text: `[공개 사양 검색] ${m.spec.product} — ${m.spec.fields.map((f) => `${f.label} ${f.display}`).join(", ")}` }];
        case "suggest": return [{ role: "assistant", text: `${m.text}\n[모델 제안] ${m.model}` }];
        default: return [];
      }
    });
  }

  /** 툴체인저 선택 품목 [견적에 포함] 체크 — 서버가 합계·비고를 다시 계산해 추천 기록에 저장한다. */
  /** [견적서 작성 →] — 이 추천의 견적을 열거나 추천 결과로 초안을 만들고 견적서 탭으로 넘어간다. */
  async function openQuote() {
    if (!rec) return;
    setBusy("견적서 초안 만드는 중");
    setError(null);
    try {
      const q = await post<QuoteSession>("/quotes/draft", { recommendation_id: rec.id, form: savedContact() });
      setQuoteId(q.id);
      setTab("quote");
    } catch (e) {
      fail(e, "견적서 초안을 만들지 못했습니다.");
    } finally {
      setBusy(null);
    }
  }

  async function finalize() {
    if (!rec) return;
    setBusy("확정 제안 저장 중");
    setError(null);
    setNeedIni(false);
    try {
      const p = await post<ProductProposal & { deal_no: string | null }>(`/recommendations/${rec.id}/finalize`, { history: transcript() });
      setFinalized({ recId: rec.id, id: p.id, dealNo: p.deal_no });
      // 건 번호는 견적서 첫 발행 때 부여(사용자 2026-10-08) — 확정만 한 건은 번호 없이 '제품 추천 확정'
      say(`최종 제안으로 확정했습니다 — 제품 영업 건${p.deal_no ? ` ${p.deal_no}` : ""}(제품 추천 확정)로 등록했습니다`
        + (p.deal_no ? ". " : "(건 번호는 견적서를 처음 발행할 때 붙습니다). ") + "미팅 내용·최종 추천·근거·대화는 "
        + "[제품 영업 건 관리]에서 그 건의 [미팅 정보]로 볼 수 있습니다. 이어서 [견적서 작성 →]으로 견적서를 만들면 같은 번호로 견적 단계가 됩니다.");
    } catch (e) {
      // 영업 건 번호 = 담당 이니셜 + 날짜 — 내 이니셜을 아직 안 정했으면 한 번 묻고 다시 확정
      if (e instanceof Error && e.message.includes("이니셜")) setNeedIni(true);
      else fail(e, "확정 제안을 저장하지 못했습니다.");
    } finally {
      setBusy(null);
    }
  }

  // ── 학습: 학습시킬까요? → 문구 확인 → 저장(관리자=바로 반영, 그 외=승인 대기) ──

  async function answerLearn(m: Extract<Msg, { type: "learn_offer" }>, ok: boolean) {
    patch(m.id, ok ? "yes" : "no");
    push({ role: "user", text: ok ? "네, 학습시켜 주세요" : "아니요" });
    if (!ok) {
      say("알겠습니다. 학습하지 않았습니다.");
      return;
    }
    setBusy("학습 문구 정리 중");
    try {
      const draft = await post<CorrectionDraft>("/corrections/draft", {
        note: m.note, recommendation_id: recRef.current?.id ?? null, history: history(),
      });
      push({ role: "ai", type: "learn_draft", draft });
    } catch (e) {
      fail(e, "학습 문구를 정리하지 못했습니다.");
    } finally {
      setBusy(null);
    }
  }

  async function saveLearn(m: Extract<Msg, { type: "learn_draft" }>, draft: CorrectionDraft) {
    setBusy("저장 중");
    setError(null);
    try {
      const c = await post<Correction>("/corrections", { draft, recommendation_id: recRef.current?.id ?? null });
      patch(m.id, "saved");
      push({ role: "ai", type: "learn_saved", c });
      void reloadList();
    } catch (e) {
      fail(e, "저장하지 못했습니다.");
    } finally {
      setBusy(null);
    }
  }

  async function review(id: number, action: "approve" | "reject" | "off" | "on") {
    setError(null);
    try {
      await post(`/corrections/${id}/review`, { action });
      void reloadList();
    } catch (e) {
      fail(e, "처리하지 못했습니다.");
    }
  }

  // ── 미팅 질문지 파일(부가 기능) → 읽은 값으로 바로 대화 시작 ──

  async function reviewMeeting(file: File | null, pasted: string) {
    if (busy) return;
    setError(null);
    setBusy("검토 중");
    setFinding({ message: "AI 가 미팅 질문지를 읽고 내용을 분석하는 중입니다…", sub: "로봇·툴·무게·전기·공압·환경 답을 정리하고 있습니다 (보통 20~40초)" });
    try {
      const fd = new FormData();
      if (file) fd.append("file", file);
      else fd.append("text", pasted);
      const r = await fetch("/api/product-recommend/atc/extract", { method: "POST", body: fd });
      const ex = (await r.json().catch(() => ({}))) as AtcExtract & { detail?: string };
      if (!r.ok) throw new Error(typeof ex.detail === "string" ? ex.detail : "질문지를 읽지 못했습니다.");
      const m: AtcMeeting = { meeting: ex.meeting, summary: ex.summary, notes: ex.notes, filename: file?.name };
      setIntake({ ...ex.intake, project: { ...ex.intake.project,
        customer_name: ex.intake.project.customer_name ?? ex.meeting.customer ?? undefined,
        writer: ex.intake.project.writer ?? ex.meeting.writer ?? undefined,
        meeting_date: ex.intake.project.meeting_date ?? ex.meeting.date ?? undefined } });
      setMeeting(m);
      setShowUpload(false);
      setFinding(null);
      setBusy(null);
      await startSession(m);
    } catch (e) {
      fail(e, "검토하지 못했습니다.");
    } finally {
      setFinding(null);
      setBusy(null);
    }
  }

  function editAnswers() {
    setError(null);
    setAtcStep(0);
    setShowUpload(false);
    setPhase("form");
  }

  function resetAll() {
    setAnswers({});
    answersRef.current = {};
    setIntake(emptyIntake());
    setAtcStep(0);
    setShowUpload(false);
    setMeeting(null);
    setRec(null);
    setRecState("pending");
    setPreview(null);
    setMsgs([]);
    setText("");
    setError(null);
    setPhase("form");
  }

  const filled = QNA.some((x) => answers[x.key]?.trim());
  // [최종 제안 확정] — 추천을 확정한 결과에서만(후보가 있을 때), 결과 섹션 오른쪽 아래
  const hasCandidate = !!rec && (rec.kind === "atc"
    ? rec.atc!.screening_candidates.some((c) => c.models.length && !c.out_of_range) : rec.items.length > 0);
  const done = finalized && rec && finalized.recId === rec.id ? finalized.id : null;
  const finalizeBar = recState === "confirmed" && hasCandidate ? (
    <>
      {needIni && <InitialsPrompt onCancel={() => setNeedIni(false)} onSaved={() => void finalize()} />}
      {done && (
        <button type="button" className="text-[12.5px] font-medium text-accent hover:underline"
                onClick={() => setTab("deals")}>
          제품 영업 건{finalized?.dealNo ? ` ${finalized.dealNo}` : ""} 등록됨 — 보기
        </button>
      )}
      <button type="button" className={done ? secondaryBtn : primaryBtn} disabled={!!busy} onClick={() => void finalize()}>
        <Icon name="check" className="h-4 w-4" /> {done ? "다시 확정(덮어쓰기)" : "최종 제안 확정"}
      </button>
      {done && rec?.kind === "atc" && (
        <button type="button" className={primaryBtn} disabled={!!busy} onClick={() => void openQuote()}>
          <Icon name="file-spreadsheet" className="h-4 w-4" /> 견적서 작성 →
        </button>
      )}
    </>
  ) : null;
  const session = tab === "recommend" && phase === "session";
  const cur = currentQuestion();
  const placeholder = stage === "asking" && cur
    ? "위 질문에 답을 적어도 됩니다 — 예: 24V 0.6A 사양서 받았어요 / 모름 (Enter 보내기 · Shift+Enter 줄바꿈)"
    : rec ? "궁금한 점을 묻거나, 틀린 데이터를 바로잡고 “다음부터 이렇게 학습해 줘”라고 말해 보세요 (Enter 보내기)"
      : "조건에 더할 내용을 적어 주세요 (Enter 보내기)";

  return (
    <div className="scroll-thin h-full overflow-y-auto">
    <div className={cn("mx-auto flex w-full flex-col gap-4 px-4 py-6", session || tab === "quote" || tab === "order" || tab === "deals" ? "max-w-[1440px]" : "max-w-4xl")}>
      <header>
        <h1 className="text-[18px] font-semibold text-foreground">회사 제품 추천</h1>
        <p className="text-[12.5px] text-foreground-muted">
          Q&amp;A 를 순서대로 답하고 [AI와 같이 제품 검색 진행]을 누르면, AI 가 빠진 정보를 묻고 조건을 함께 확인한 뒤 근거와 함께 제품을 추천합니다.
          결과나 데이터가 틀렸으면 대화창에서 말해 주세요 — 확인을 거쳐 AI 에 학습시킬 수 있습니다.
        </p>
      </header>

      {/* 탭: 제품 추천 / AI 학습 내용 관리 */}
      <div role="tablist" aria-label="회사 제품 추천 화면" className="flex gap-1 border-b border-border">
        {([["recommend", "제품 추천"], ["quote", "견적서"], ["order", "수주 진행"], ["deals", "제품 영업 건 관리"], ["memory", "AI 학습 내용 관리"]] as const).map(([key, label]) => {
          const pending = list.items.filter((c) => c.review_status === "pending").length;
          return (
            <button key={key} type="button" role="tab" aria-selected={tab === key} onClick={() => setTab(key)}
                    className={cn("-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-[13.5px] font-semibold transition-colors",
                      tab === key ? "border-accent text-accent" : "border-transparent text-foreground-muted hover:text-foreground")}>
              {label}
              {key === "memory" && (
                <span className={cn("ml-1.5 rounded-full px-1.5 py-0.5 text-[11px] font-medium",
                  tab === key ? "bg-accent/15 text-accent" : "bg-foreground/8 text-foreground-subtle")}>
                  {list.items.filter((c) => c.active).length}
                </span>
              )}
              {key === "memory" && list.approver && pending > 0 && (
                <span className="ml-1 rounded-full bg-amber-500 px-1.5 py-0.5 text-[11px] font-semibold text-white" title="승인 대기">{pending}</span>
              )}
            </button>
          );
        })}
      </div>

      {tab === "recommend" && phase === "form" && (
      <>
        {/* 제품 종류 — 누르면 질문지가 바뀐다 */}
        <div role="tablist" aria-label="제품 종류" className="flex flex-wrap gap-2">
          {([["atc", "ATC · 툴체인저", "영업부 질문지"], ["other", "그 외 제품", "그리퍼·이동 로봇 등 · 임시 질문"]] as const).map(([k, label, sub]) => (
            <button key={k} type="button" role="tab" aria-selected={product === k} disabled={!!busy}
                    onClick={() => setProduct(k)}
                    className={cn("flex flex-col items-start rounded-xl border px-4 py-2 text-left transition",
                      product === k ? "border-accent bg-accent/[0.07] ring-1 ring-accent/30" : "border-border bg-surface hover:border-foreground/25")}>
              <span className={cn("text-[13.5px] font-semibold", product === k ? "text-accent" : "text-foreground")}>{label}</span>
              <span className="text-[11.5px] text-foreground-subtle">{sub}</span>
            </button>
          ))}
        </div>

        {product === "atc" && (
          finding ? <Finding message={finding.message} sub={finding.sub} /> : showUpload ? (
            <>
              <button type="button" className={cn(secondaryBtn, "self-start")} disabled={!!busy} onClick={() => setShowUpload(false)}>
                <Icon name="back" className="h-4 w-4" /> 질문으로 돌아가기
              </button>
              <AtcMeetingUpload busy={!!busy} onStart={(f, t) => void reviewMeeting(f, t)} />
            </>
          ) : (
            <>
              <AtcWizard intake={atcIntake} setIntake={(f) => setIntake(f(intakeRef.current))} step={atcStep} setStep={setAtcStep}
                         busy={!!busy} onSubmit={() => { setMeeting(null); void startSession(null); }} />
              <div className="flex flex-wrap items-center gap-2 text-[12.5px] text-foreground-subtle">
                <span>미팅 때 채운 질문지 파일이 있나요?</span>
                <button type="button" disabled={!!busy} onClick={() => setShowUpload(true)}
                        className="inline-flex items-center gap-1 font-medium text-accent hover:underline">
                  <Icon name="paperclip" className="h-3.5 w-3.5" /> 미팅 질문지 파일로 불러오기
                </button>
                <button type="button" disabled={!!busy} onClick={() => void openTestSamples()}
                        className="inline-flex items-center gap-1 rounded-md border border-dashed border-amber-500/60 px-2 py-0.5 font-medium text-amber-700 hover:bg-amber-500/10 dark:text-amber-300">
                  <Icon name="spark" className="h-3.5 w-3.5" /> 제품 추천 질문 모두 채우기 (테스트용)
                </button>
                {rec && (
                  <button type="button" disabled={!!busy} onClick={() => setPhase("session")} className="ml-auto font-medium text-accent hover:underline">
                    ← 진행 중인 대화로 돌아가기
                  </button>
                )}
              </div>
              {showTest && (
                <div className="flex flex-col gap-2 rounded-xl border border-dashed border-amber-500/50 bg-amber-500/[0.04] p-3">
                  <p className="text-[12px] text-foreground-muted">
                    샘플 질문지(정답지) 답을 그대로 넣고 Q&amp;A·확인 질문 없이 바로 추천을 확정합니다 — 최종 제안·견적서 시험용.
                  </p>
                  {!testSamples && <p className="text-[12px] text-foreground-subtle">불러오는 중…</p>}
                  {testSamples && testSamples.length === 0 && <p className="text-[12px] text-foreground-subtle">등록된 샘플이 없습니다.</p>}
                  <div className="grid gap-1.5 sm:grid-cols-2">
                    {testSamples?.map((x) => (
                      <button key={x.id} type="button" disabled={!!busy} onClick={() => void testFill(x)}
                              className="flex items-center justify-between gap-2 rounded-lg border border-border bg-surface px-3 py-2 text-left text-[12.5px] hover:border-accent/50">
                        <span className="min-w-0">
                          <span className="block truncate font-medium text-foreground">{x.label}</span>
                          <span className="text-[11.5px] text-foreground-subtle">정답 {x.expected_model ?? "후보 없음"}</span>
                        </span>
                        <span className={cn("shrink-0 rounded-md px-1.5 py-0.5 text-[11px] font-semibold",
                          x.quotable ? "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300" : "bg-foreground/8 text-foreground-subtle")}>
                          {x.quotable ? "견적 가능" : "견적 불가"}
                        </span>
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </>
          )
        )}

        {/* 그 외 제품 · 임시 문항 */}
        {product === "other" && (
          <section className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-4">
            <div className="flex items-baseline justify-between gap-2">
              <h2 className="text-[15px] font-semibold text-foreground">조건 Q&amp;A</h2>
              <span className="text-[11.5px] text-foreground-subtle">임시 문항 — 의뢰인 Q&amp;A 가 준비되면 교체 · 비워 둔 핵심 질문은 AI 가 대화에서 다시 묻습니다</span>
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              {QNA.map((x) => x.options ? (
                <div key={x.key} className={cn("flex flex-col gap-1 text-[12.5px]", x.wide && "md:col-span-2")}>
                  <span className="font-medium text-foreground">{x.q}</span>
                  <div role="radiogroup" aria-label={x.q} className="flex flex-wrap gap-1.5">
                    {x.options.map((o) => {
                      const on = answers[x.key] === o;
                      return (
                        <button key={o} type="button" role="radio" aria-checked={on} disabled={!!busy}
                                onClick={() => setAnswers((a) => ({ ...a, [x.key]: on ? "" : o }))}
                                className={cn("rounded-full border px-2.5 py-1 text-[12px] transition-colors",
                                  on ? "border-accent bg-accent text-white" : "border-border text-foreground-muted hover:text-foreground")}>
                          {o}
                        </button>
                      );
                    })}
                  </div>
                </div>
              ) : (
                <label key={x.key} className={cn("flex flex-col gap-1 text-[12.5px]", x.wide && "md:col-span-2")}>
                  <span className="font-medium text-foreground">{x.q}</span>
                  <textarea rows={2} maxLength={400} value={answers[x.key] ?? ""} disabled={!!busy}
                            onChange={(e) => setAnswers((a) => ({ ...a, [x.key]: e.target.value }))}
                            placeholder={x.ph} className={inputCls} />
                </label>
              ))}
            </div>
            <div className="flex flex-wrap items-center justify-end gap-2">
              {rec && (
                <button type="button" className={secondaryBtn} disabled={!!busy} onClick={() => setPhase("session")}>
                  <Icon name="back" className="h-4 w-4" /> 진행 중인 대화로 돌아가기
                </button>
              )}
              <button type="button" className={primaryBtn} disabled={!filled || !!busy} onClick={() => void startSession(null)}>
                <Icon name="search" className="h-4 w-4" /> AI와 같이 제품 검색 진행
              </button>
            </div>
          </section>
        )}
      </>
      )}

      {/* 진행 — 왼쪽: 진행 과정·조건·결과 / 오른쪽: AI 와 대화(반반) */}
      {session && (
      <div className="grid items-start gap-4 lg:grid-cols-2">
        <div className="flex min-w-0 flex-col gap-4">
          <Progress stage={stage} left={left} />
          {product === "atc"
            ? <AtcIntakeSummary key={rec ? "c" : "o"} intake={atcIntake} meeting={meeting} busy={!!busy} onEdit={editAnswers} onReset={resetAll} collapsed={!!rec} />
            : <ConditionSummary answers={answers} busy={!!busy} onEdit={editAnswers} onReset={resetAll} />}
          {finding && <Finding message={finding.message} sub={finding.sub} />}
          {busy && !finding && <WorkingCard busy={busy} />}
          {!finding && !rec && product === "atc" && preview && <AtcLivePreview ev={preview} left={left} />}
          {!finding && rec && product === "atc" && versions.length > 1 && recState === "confirmed" && (
            <VersionNav at={verAt} count={versions.length} locked={!!done} onGo={showVersion} />
          )}
          {!finding && rec && (
            // [네, 이 추천으로 진행] 전에는 후보와 근거만, 확정 후 선정 결과(후보가 없으면 이유·확인사항을 바로)
            rec.kind === "atc"
              ? (recState !== "confirmed" && rec.atc!.screening_candidates.some((c) => c.models.length && !c.out_of_range)
                ? <AtcCandidatePreview rec={rec} state={recState} />
                : <AtcResultView rec={rec} state={recState} footer={finalizeBar} />)
              : (recState !== "confirmed" && rec.items.length > 0
                ? <OtherCandidatePreview rec={rec} state={recState} />
                : <RecommendationView rec={rec} state={recState} footer={finalizeBar} />)
          )}
        </div>

        <aside className="flex flex-col overflow-hidden rounded-xl border border-border bg-surface lg:sticky lg:top-4 lg:h-[calc(100svh-2rem)]">
          <div className="flex items-center gap-2 border-b border-border px-4 py-3">
            <span className="und-grad grid h-7 w-7 place-items-center rounded-full text-[11px] font-bold text-white" aria-hidden>AI</span>
            <div className="min-w-0">
              <h2 className="text-[14px] font-semibold leading-tight text-foreground">AI 와 같이 제품 찾기</h2>
              <p className="truncate text-[11.5px] text-foreground-subtle">
                {product === "atc" ? "툴체인저" : "그 외 제품"} · 묻고 확인하며 진행합니다 · {list.approver ? "학습 내용은 바로 반영(영업 관리자)" : "학습 내용은 관리자 승인 후 반영"}
              </p>
            </div>
          </div>
          <div className="scroll-thin flex min-h-[320px] flex-1 flex-col gap-3 overflow-y-auto px-4 py-3" aria-live="polite">
            {msgs.map((m) => {
              if (m.role === "user") {
                return (
                  <div key={m.id} className="flex justify-end">
                    <div className="bubble-user max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md px-3.5 py-2 text-[13px] leading-relaxed text-white">{m.text}</div>
                  </div>
                );
              }
              switch (m.type) {
                case "text":
                  return <AiBubble key={m.id}><RichText text={m.text} /></AiBubble>;
                case "agent":
                  return <AgentBubble key={m.id} m={m} />;
                case "atcq":
                  return (
                    <AiBubble key={m.id} wide>
                      <div className="flex flex-col gap-2">
                        <div>
                          <span className="text-[11px] font-medium text-amber-700 dark:text-amber-300">
                            {m.qs[0].kind === "followup" ? "앞 답에 따른 추가 질문" : "비워 둔 질문"}
                            {m.qs[0].scope !== "project" ? ` · ${m.qs.map((q) => q.entity_label).join(", ")}` : ""}
                          </span>
                          <p className="font-semibold">{m.qs[0].question_ko}</p>
                          <p className="text-[12px] text-foreground-subtle">{m.qs[0].help_text_ko}</p>
                        </div>
                        {m.state ? (
                          <p className="text-[12px] text-foreground-subtle">{m.state === "skip" ? "건너뜀 — 결과의 추가 확인사항에 남깁니다" : "답했습니다"}</p>
                        ) : (
                          <>
                            <div className="rounded-lg bg-surface p-2.5">
                              <GroupedFields items={m.qs} intake={atcIntake} setIntake={(f) => setIntake(f(intakeRef.current))} busy={!!busy} />
                            </div>
                            <div className="flex flex-wrap gap-1.5">
                              <Choice primary disabled={!!busy} onClick={() => void answerAtc(m, "done")}><Icon name="check" className="h-3.5 w-3.5" /> 답변 완료</Choice>
                              <Choice disabled={!!busy} onClick={() => void answerAtc(m, "unknown")}>모름 — 자료 요청으로</Choice>
                              <Choice disabled={!!busy} onClick={() => void answerAtc(m, "skip")}>건너뛰기</Choice>
                            </div>
                          </>
                        )}
                      </div>
                    </AiBubble>
                  );
                case "qnaq": {
                  const x = QNA.find((y) => y.key === m.key)!;
                  return (
                    <AiBubble key={m.id} wide>
                      <div className="flex flex-col gap-2">
                        <div>
                          <span className="text-[11px] font-medium text-amber-700 dark:text-amber-300">비워 둔 질문</span>
                          <p className="font-semibold">{x.q}</p>
                          {x.ph && <p className="text-[12px] text-foreground-subtle">{x.ph} — 아래 입력창에 적어 주세요</p>}
                        </div>
                        {!m.state && (
                          <div className="flex flex-wrap gap-1.5">
                            {x.options?.map((o) => <Choice key={o} disabled={!!busy} onClick={() => void answerQna(m, o)}>{o}</Choice>)}
                            <Choice disabled={!!busy} onClick={() => void answerQna(m, null)}>건너뛰기</Choice>
                          </div>
                        )}
                      </div>
                    </AiBubble>
                  );
                }
                case "confirm":
                  return (
                    <AiBubble key={m.id} wide>
                      <p className="font-semibold">후보를 찾기 전에 조건을 확인할게요. 이렇게 이해했는데 맞나요?</p>
                      <ul className="mt-1.5 flex flex-col gap-0.5 text-[12.5px] text-foreground-muted">
                        {m.lines.map((l) => <li key={l}>· {l}</li>)}
                      </ul>
                      {!m.state && (
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          <Choice primary disabled={!!busy} onClick={() => void confirmConditions(m, true)}><Icon name="search" className="h-3.5 w-3.5" /> 맞아요, 후보 검색</Choice>
                          <Choice disabled={!!busy} onClick={() => void confirmConditions(m, false)}>고칠 게 있어요</Choice>
                        </div>
                      )}
                    </AiBubble>
                  );
                case "offer":
                  return (
                    <AiBubble key={m.id} wide>
                      <OfferBody rec={m.rec} />
                      {m.state === "old" && <p className="mt-1.5 text-[12px] text-foreground-subtle">다시 찾은 후보로 바뀌었습니다 — 아래 제안에서 확인해 주세요.</p>}
                      {!m.state && (
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          <Choice primary disabled={!!busy} onClick={() => answerOffer(m, true)}><Icon name="thumb-up" className="h-3.5 w-3.5" /> 네, 이 추천으로 진행</Choice>
                          <Choice disabled={!!busy} onClick={() => answerOffer(m, false)}><Icon name="thumb-down" className="h-3.5 w-3.5" /> 아니요</Choice>
                        </div>
                      )}
                    </AiBubble>
                  );
                case "learn_offer":
                  return (
                    <AiBubble key={m.id} wide>
                      {m.text && <RichText text={m.text} />}
                      <p className={cn("font-semibold", m.text && "mt-1.5")}>이 내용을 AI 에 학습시키겠습니까?</p>
                      {!m.state && (
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          <Choice primary disabled={!!busy} onClick={() => void answerLearn(m, true)}>예</Choice>
                          <Choice disabled={!!busy} onClick={() => void answerLearn(m, false)}>아니요</Choice>
                        </div>
                      )}
                    </AiBubble>
                  );
                case "learn_draft":
                  return (
                    <AiBubble key={m.id} wide>
                      <LearnDraftCard draft={m.draft} busy={!!busy} done={m.state} approver={list.approver}
                                      onSave={(d) => void saveLearn(m, d)}
                                      onCancel={() => { patch(m.id, "cancel"); say("학습하지 않았습니다."); }} />
                    </AiBubble>
                  );
                case "applied":
                  return (
                    <AiBubble key={m.id} wide>
                      {m.text && <RichText text={m.text} />}
                      <div className="mt-1.5 rounded-lg bg-surface px-2.5 py-2 text-[12.5px]">
                        <div className="mb-1 font-semibold text-emerald-700 dark:text-emerald-300">질문 칸에 반영한 값</div>
                        <ul className="flex flex-col gap-0.5">
                          {m.items.map((x) => <li key={x.label}><span className="text-foreground-subtle">{x.label}</span> <b>{x.display}</b></li>)}
                        </ul>
                      </div>
                      {!m.state && rec && stage !== "searching" && (
                        <div className="mt-2">
                          <Choice primary disabled={!!busy} onClick={() => { patch(m.id, "research"); void search(); }}>
                            <Icon name="search" className="h-3.5 w-3.5" /> 이 정보로 다시 찾기
                          </Choice>
                        </div>
                      )}
                    </AiBubble>
                  );
                case "spec_offer":
                  return (
                    <AiBubble key={m.id} wide>
                      <p>‘<b>{m.product}</b>’은(는) 실제 제품으로 보입니다. 외부 AI 로 공개 사양(무게·크기·전원·공압)을 검색해서 비어 있는 칸을 채워 볼까요?</p>
                      <p className="mt-1 text-[12px] text-foreground-subtle">외부로는 제품명만 보냅니다. 찾은 값은 &lsquo;추정&rsquo;으로 보여 드리고, 확인을 누르셔야 칸에 들어갑니다.</p>
                      {!m.state && (
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          <Choice primary disabled={!!busy} onClick={() => void answerSpecOffer(m, true)}><Icon name="search" className="h-3.5 w-3.5" /> 검색해 주세요</Choice>
                          <Choice disabled={!!busy} onClick={() => void answerSpecOffer(m, false)}>건너뛰기</Choice>
                        </div>
                      )}
                    </AiBubble>
                  );
                case "spec_result":
                  return (
                    <AiBubble key={m.id} wide>
                      <SpecResultBody spec={m.spec} tool={m.state ? {} : (atcIntake.tools[m.tool] ?? {}) as Record<string, unknown>} />
                      {!m.state && m.spec.fields.length > 0 && (
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          <Choice primary disabled={!!busy} onClick={() => void applySpec(m, true)}><Icon name="check" className="h-3.5 w-3.5" /> 빈 칸에 채우기</Choice>
                          <Choice disabled={!!busy} onClick={() => void applySpec(m, false)}>쓰지 않기</Choice>
                        </div>
                      )}
                    </AiBubble>
                  );
                case "suggest":
                  return (
                    <AiBubble key={m.id} wide>
                      {m.text && <RichText text={m.text} />}
                      <p className={cn("font-semibold", m.text && "mt-1.5")}>{m.model}(으)로 바꿔 추천할까요?</p>
                      {m.state === "old" && <p className="mt-1 text-[12px] text-foreground-subtle">지난 제안입니다.</p>}
                      {!m.state && (
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          <Choice primary disabled={!!busy} onClick={() => { patch(m.id, "yes"); push({ role: "user", text: `${m.model}(으)로 추천해 주세요` }); void switchTo(m.model); }}>
                            <Icon name="check" className="h-3.5 w-3.5" /> 이 모델로 추천해 주세요
                          </Choice>
                          <Choice disabled={!!busy} onClick={() => { patch(m.id, "old"); say("지금 추천을 유지합니다."); }}>지금 모델 유지</Choice>
                        </div>
                      )}
                    </AiBubble>
                  );
                case "learn_saved":
                  return (
                    <AiBubble key={m.id}>
                      <p className="flex items-center gap-1.5 font-semibold text-emerald-700 dark:text-emerald-300">
                        <Icon name="check" className="h-4 w-4" />
                        {m.c.review_status === "kept"
                          ? `학습했습니다 (#${m.c.id}) — 다음 추천부터 반영합니다`
                          : `학습 요청을 올렸습니다 (#${m.c.id}) — 영업 관리자가 승인하면 반영됩니다`}
                      </p>
                      <p className="mt-1 text-[12.5px] text-foreground-muted"><KindBadge kind={m.c.kind} /> “{m.c.rule}”</p>
                    </AiBubble>
                  );
                default:
                  return null;
              }
            })}
            {busy && !finding && (
              <AiBubble>
                <span className="flex items-center gap-2 text-foreground-subtle">
                  <Icon name="refresh" className="h-4 w-4 animate-spin text-accent" /> {busy}…
                </span>
              </AiBubble>
            )}
            <div ref={chatEnd} />
          </div>

          <div className="border-t border-border p-3">
            <div className="flex flex-col rounded-xl border border-foreground/15 bg-background transition-colors focus-within:ring-1 focus-within:ring-accent/40">
              <textarea rows={3} maxLength={2000} value={text} disabled={!!busy || stage === "searching"}
                        onChange={(e) => setText(e.target.value)}
                        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(); } }}
                        aria-label="AI 에게 보내기" placeholder={placeholder}
                        className="w-full resize-none bg-transparent px-3 pt-2.5 text-[13px] leading-relaxed text-foreground placeholder:text-foreground-subtle/70 focus:outline-none" />
              <div className="flex items-center gap-2 px-2 pb-2">
                <span className="text-[11px] text-foreground-subtle">학습시키려면 “다음부터 이렇게 학습해 줘”라고 말해 보세요</span>
                <button type="button" aria-label="보내기" title="보내기 (Enter)" disabled={!text.trim() || !!busy} onClick={() => void send()}
                        className="und-grad ml-auto grid h-8 w-8 place-items-center rounded-full text-white transition hover:brightness-105 disabled:opacity-40">
                  <Icon name="send" className="h-4 w-4" />
                </button>
              </div>
            </div>
          </div>
        </aside>
      </div>
      )}

      {tab === "quote" && <QuoteTab openId={quoteId} onOpen={setQuoteId} />}
      {tab === "order" && <OrderTab openId={orderId} onOpen={setOrderId} />}
      {tab === "deals" && <DealsTab onMeeting={setMeetingId} onOrder={(id) => { setOrderId(id); setTab("order"); }}
                                     onQuote={(id) => { setQuoteId(id); setTab("quote"); }} />}
      {meetingId && <MeetingInfo id={meetingId} onClose={() => setMeetingId(null)} />}
      {tab === "memory" && <MemoryTab list={list} onReview={(id, a) => void review(id, a)} />}
      <ErrorBox error={error} />
    </div>
    </div>
  );
}
