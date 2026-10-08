// 툴체인저(ATC) 단품 선정 — 주니어 영업 질문지(J01~J10, 한 번에 한 묶음)를 순서대로 → 앞 답에 해당하는 후속 질문
// (F01~F12)은 마지막 질문 다음에 하나씩 이어서(사용자 2026-10-02, 영업부 팀장 요청) → [AI와 같이 제품 검색 진행].
// 비워 두고 넘어간 질문은 그 뒤 AI 와의 대화에서 AI 가 묻는다. 결과는 선정 규칙 판정(추천 근거·상태·참고 후보·구성품).
// 질문 문구·도움말·선택지는 backend 질문지 원본(/atc/spec)을 그대로 그린다(사용자 2026-10-02, 영업부 질문지 v0.2).
// 모르는 값은 null — '모름'은 '없음'이 아니다. 모름이어도 다음으로 넘어갈 수 있다(최종 선정만 막힌다).
"use client";

import { useEffect, useState, type ReactNode } from "react";
import type {
  AtcBasis, AtcFollowup, AtcField, AtcIntake, AtcMeeting, AtcOutput, AtcQuestion, AtcSpec, Recommendation,
} from "@/lib/shared/product-recommend";
import { Icon } from "@/components/shared/ui/icon";
import { cn } from "@/lib/shared/utils";
import { inputCls, primaryBtn, secondaryBtn } from "./proposal-actions";

const OPTION_KO: Record<string, string> = {
  cobot: "협동", industrial: "산업용", other: "기타",
  transfer: "이송", assembly: "조립", machining: "가공", inspection: "검사",
};
const ENV_TAGS = ["철가루", "물·기름", "고온", "기타"];

export function emptyIntake(): AtcIntake {
  return {
    project: { process: {}, environment: { tags: [] } },
    robots: [{ id: "R1", speed: {}, cables: {} }],
    tools: [{ id: "T1", robot_ids: ["R1"], intake: { dimensions_mm: {} }, mass_components: {} }],
  };
}

// ── 경로 읽기·쓰기 ─────────────────────────────────────────────────────────────

function local(path: string): string {
  return path.replace(/^(tools\[\]|robots\[\]|project)\./, "");
}

function getPath(obj: unknown, path: string): unknown {
  return path.split(".").reduce<unknown>((cur, k) => (cur && typeof cur === "object" ? (cur as Record<string, unknown>)[k] : undefined), obj);
}

function setPath<T extends Record<string, unknown>>(obj: T, path: string, value: unknown): T {
  const [head, ...rest] = path.split(".");
  const cur = (obj[head] && typeof obj[head] === "object" ? obj[head] : {}) as Record<string, unknown>;
  return { ...obj, [head]: rest.length ? setPath(cur, rest.join("."), value) : value };
}

function entities(intake: AtcIntake, scope: AtcQuestion["scope"]): Record<string, unknown>[] {
  return scope === "robot" ? intake.robots : scope === "tool" ? intake.tools : [intake.project];
}

export function entityLabel(intake: AtcIntake, scope: AtcQuestion["scope"], i: number): string {
  if (scope === "tool") return (intake.tools[i]?.name as string) || `툴 ${i + 1}`;
  if (scope === "robot") {
    const r = intake.robots[i] ?? {};
    return [r.manufacturer, r.model].filter(Boolean).join(" ") || `로봇 ${i + 1}`;
  }
  return "";
}

export function updateEntity(intake: AtcIntake, scope: AtcQuestion["scope"], i: number, path: string, value: unknown): AtcIntake {
  if (scope === "project") return { ...intake, project: setPath(intake.project, path, value) };
  const key = scope === "robot" ? "robots" : "tools";
  return { ...intake, [key]: intake[key].map((e, j) => (j === i ? setPath(e, path, value) : e)) };
}

/** 질문의 칸을 모두 '모름'으로(대화에서 [모름]) — 다시 묻지 않고 자료 요청으로 넘어간다. */
export function markUnknown(intake: AtcIntake, q: AtcFollowup): AtcIntake {
  return q.fields.reduce((acc, f) => {
    const p = local(f.path);
    return updateEntity(updateEntity(acc, q.scope, q.entity_index ?? 0, p, null), q.scope, q.entity_index ?? 0, `${p}__unknown`, true);
  }, intake);
}

/** 대화 말풍선에 보일 내 답 — 질문 칸에 들어간 값을 한 줄로. 아무것도 안 넣었으면 빈 문자열. */
export function describeAnswer(q: AtcFollowup, intake: AtcIntake): string {
  const e = entities(intake, q.scope)[q.entity_index ?? 0] ?? {};
  const parts: string[] = [];
  for (const f of q.fields) {
    const p = local(f.path);
    const v = getPath(e, p);
    if (getPath(e, `${p}__unknown`) === true) { parts.push(`${f.label_ko}: 모름`); continue; }
    if (v == null || v === "" || (Array.isArray(v) && v.length === 0)) continue;
    let t: string;
    if (typeof v === "boolean") {
      const o = (f.options ?? []).find((x) => x !== null && typeof x === "object" && x.value === v) as { label_ko: string } | undefined;
      t = o?.label_ko ?? (v ? "있음" : "없음");
    } else if (Array.isArray(v)) t = v.join(", ");
    else if (typeof v === "object") {
      const pr = v as { min?: number | null; max?: number | null };
      t = pr.min === pr.max ? `${pr.min ?? "?"}bar` : `${pr.min ?? "?"}~${pr.max ?? "?"}bar`;
    } else t = `${OPTION_KO[String(v)] ?? v}${f.unit ?? ""}`;
    parts.push(`${f.label_ko}: ${t}`);
  }
  return parts.join(" · ");
}

// ── 입력 칸 ───────────────────────────────────────────────────────────────────

/** 숫자 입력 — 치는 동안은 글자 그대로(0. / 0.0 / 1,5) 두고, 숫자로 읽히면 값으로 넘긴다. 소수·정수만. */
function NumberInput({ value, onValue, integer, className, ...rest }: {
  value: number | null | undefined; onValue: (v: number | null) => void; integer?: boolean; className?: string;
  disabled?: boolean; placeholder?: string; "aria-label"?: string;
}) {
  const [text, setText] = useState(value == null ? "" : String(value));
  const [seen, setSeen] = useState(value);
  // 밖에서 값이 바뀐 경우(모름·외부 검색·대화 반영)만 글자를 맞춘다 — 치는 중인 '0.' 은 그대로(렌더 중 조정, effect 없이)
  if (value !== seen) {
    setSeen(value);
    const same = value == null ? text === "" || text === "." : Number(text) === value;
    if (!same) setText(value == null ? "" : String(value));
  }
  return (
    <input type="text" inputMode={integer ? "numeric" : "decimal"} value={text} className={className} {...rest}
           onChange={(e) => {
             const s = e.target.value.replace(",", ".").trim();
             if (!(integer ? /^\d*$/ : /^\d*\.?\d*$/).test(s)) return;
             setText(s);
             onValue(s === "" || s === "." ? null : integer ? parseInt(s, 10) : Number(s));
           }} />
  );
}

const chip = (on: boolean) =>
  cn("shrink-0 whitespace-nowrap rounded-full border px-2.5 py-1 text-[12px] transition-colors",
    on ? "border-accent bg-accent text-white" : "border-border text-foreground-muted hover:text-foreground");

function FieldInput({ field, entity, onChange, robots, busy }: {
  field: AtcField; entity: Record<string, unknown>; onChange: (path: string, v: unknown) => void;
  robots: Record<string, unknown>[]; busy: boolean;
}) {
  const path = local(field.path);
  const v = getPath(entity, path);
  const unknownKey = `${path}__unknown`;
  const isUnknown = getPath(entity, unknownKey) === true;
  const setUnknown = () => { onChange(path, null); onChange(unknownKey, true); };
  const set = (val: unknown) => { onChange(path, val); onChange(unknownKey, false); };
  const unknownBtn = (
    <button type="button" disabled={busy} onClick={setUnknown} className={chip(isUnknown)}>모름</button>
  );

  if (field.type === "enum" || field.type === "boolean") {
    const opts = (field.options ?? []).map((o) =>
      o !== null && typeof o === "object" ? o : { value: o, label_ko: o === null ? "모름" : OPTION_KO[o] ?? o });
    return (
      <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label={field.label_ko}>
        {opts.map((o) => {
          const on = o.value === null ? isUnknown : v === o.value;
          return (
            <button key={String(o.value)} type="button" role="radio" aria-checked={on} disabled={busy}
                    onClick={() => (o.value === null ? setUnknown() : set(on ? null : o.value))} className={chip(on)}>
              {o.label_ko}
            </button>
          );
        })}
      </div>
    );
  }
  if (field.type === "array" && path === "robot_ids") {
    if (robots.length <= 1) return <span className="text-[12px] text-foreground-subtle">로봇 1대 — 자동 지정</span>;
    const sel = (v as string[] | undefined) ?? [];
    return (
      <div className="flex flex-wrap gap-1.5">
        {robots.map((r, i) => {
          const id = r.id as string;
          const on = sel.includes(id);
          return (
            <button key={id} type="button" disabled={busy} className={chip(on)}
                    onClick={() => set(on ? sel.filter((x) => x !== id) : [...sel, id])}>
              {[r.manufacturer, r.model].filter(Boolean).join(" ") || `로봇 ${i + 1}`}
            </button>
          );
        })}
      </div>
    );
  }
  if (field.type === "array" && path === "environment.tags") {
    const sel = (v as string[] | undefined) ?? [];
    return (
      <div className="flex flex-wrap gap-1.5">
        {ENV_TAGS.map((t) => (
          <button key={t} type="button" disabled={busy} className={chip(sel.includes(t))}
                  onClick={() => set(sel.includes(t) ? sel.filter((x) => x !== t) : [...sel, t])}>{t}</button>
        ))}
      </div>
    );
  }
  if (field.type === "object" && path === "pneumatic.pressure_bar") {
    const p = (v as { min?: number | null; max?: number | null } | null) ?? {};
    return (
      <div className="flex flex-wrap items-center gap-2 text-[12.5px]">
        <NumberInput aria-label="최저 압력" value={p.min} disabled={busy} placeholder="최저"
                     onValue={(n) => set({ ...p, min: n })} className={cn(inputCls, "w-24")} />
        <span className="text-foreground-subtle">~</span>
        <NumberInput aria-label="최고 압력" value={p.max} disabled={busy} placeholder="최고"
                     onValue={(n) => set({ ...p, max: n })} className={cn(inputCls, "w-24")} />
        <span className="text-foreground-subtle">bar</span>
        {unknownBtn}
      </div>
    );
  }
  const numeric = field.type === "integer" || field.type === "number";
  const text = Array.isArray(v) ? (v as string[]).join(", ") : v == null ? "" : String(v);
  if (numeric) {
    return (
      <div className="flex items-center gap-2">
        <NumberInput aria-label={field.label_ko} value={typeof v === "number" ? v : null} disabled={busy}
                     integer={field.type === "integer"} onValue={(n) => set(n)} className={cn(inputCls, "w-32")} />
        {field.unit && <span className="text-[12px] text-foreground-subtle">{field.unit}</span>}
        {unknownBtn}
      </div>
    );
  }
  return (
    <div className="flex items-center gap-2">
      <input
        type="text" value={text} disabled={busy}
        aria-label={field.label_ko}
        placeholder={field.type === "attachment" ? "받은 자료 메모(예: 사진 받음 / 요청함)" : ""}
        onChange={(e) => {
          const s = e.target.value;
          if (s.trim() === "") return set(null);
          if (field.type === "array") return set([s]);
          return set(s);
        }}
        className={cn(inputCls, "min-w-0 flex-1")}
      />
      {field.unit && <span className="text-[12px] text-foreground-subtle">{field.unit}</span>}
      {unknownBtn}
    </div>
  );
}

const DIM_KEYS = ["intake.dimensions_mm.x", "intake.dimensions_mm.y", "intake.dimensions_mm.z"];
const EACH_KEY = "mass_components.workpiece_each_kg";
const TOTAL_KEY = "mass_components.max_simultaneous_workpieces_kg";
const COUNT_KEY = "workpiece_count";
const MIXED_KEY = "mass_components.workpiece_weights_differ";
const LIST_KEY = "mass_components.workpiece_each_list_kg";
const round3 = (n: number) => Math.round(n * 1000) / 1000;

/** 크기 — 가로 × 세로 × 높이를 한 줄로(사용자 2026-10-02). '모름'은 세 칸 모두. */
function DimsInput({ entity, onChange, busy }: { entity: Record<string, unknown>; onChange: (path: string, v: unknown) => void; busy: boolean }) {
  const unknown = DIM_KEYS.every((k) => getPath(entity, `${k}__unknown`) === true);
  const set = (k: string, n: number | null) => { onChange(k, n); onChange(`${k}__unknown`, false); };
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {DIM_KEYS.map((k, i) => (
        <span key={k} className="flex items-center gap-1.5">
          {i > 0 && <span className="text-foreground-subtle">×</span>}
          <NumberInput aria-label={["가로", "세로", "높이"][i]} placeholder={["가로", "세로", "높이"][i]} disabled={busy}
                       value={getPath(entity, k) as number | null} onValue={(n) => set(k, n)} className={cn(inputCls, "w-24")} />
        </span>
      ))}
      <span className="text-[12px] text-foreground-subtle">mm</span>
      <button type="button" disabled={busy} className={chip(unknown)}
              onClick={() => DIM_KEYS.forEach((k) => { onChange(k, null); onChange(`${k}__unknown`, true); })}>모름</button>
    </div>
  );
}

export function FieldsBlock({ q, intake, setIntake, entityIndex, busy }: {
  q: AtcQuestion; intake: AtcIntake; setIntake: (f: (i: AtcIntake) => AtcIntake) => void;
  entityIndex: number; busy: boolean;
}) {
  const entity = entities(intake, q.scope)[entityIndex] ?? {};
  const set = (path: string, v: unknown) => setIntake((i) => updateEntity(i, q.scope, entityIndex, path, v));
  // 제품 개수를 바꾸면 '개당 무게 × 개수 = 총무게'를 다시 계산
  const onChange = (path: string, v: unknown) => {
    set(path, v);
    if (path === COUNT_KEY) {
      const each = getPath(entity, EACH_KEY);
      if (getPath(entity, MIXED_KEY) === true) {
        if (typeof v === "number" && v > 1) {
          const old = (getPath(entity, LIST_KEY) as (number | null)[] | undefined) ?? [];
          setList(Array.from({ length: v }, (_, j) => old[j] ?? null));
        } else set(MIXED_KEY, false);
      } else if (typeof each === "number" && typeof v === "number") set(TOTAL_KEY, round3(each * v));
    }
  };
  const dimsAt = q.fields.findIndex((f) => DIM_KEYS.includes(local(f.path)));
  const qty = entity.installed_quantity as number | undefined;
  const mass = getPath(entity, "intake.tool_assembly_mass_kg") as number | undefined;
  const count = getPath(entity, COUNT_KEY) as number | undefined;
  const each = getPath(entity, EACH_KEY) as number | undefined;
  const total = getPath(entity, TOTAL_KEY) as number | undefined;
  const mixed = getPath(entity, MIXED_KEY) === true;
  const list = (getPath(entity, LIST_KEY) as (number | null)[] | undefined) ?? [];
  // 제품마다 무게가 다르면 각각 적은 값의 합이 총무게 — 하나라도 비면 총무게는 비운다
  const setList = (xs: (number | null)[]) => {
    set(LIST_KEY, xs);
    set(TOTAL_KEY, xs.every((x) => typeof x === "number") ? round3((xs as number[]).reduce((a, b) => a + b, 0)) : null);
  };
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {q.fields.map((f, idx) => {
        const lp = local(f.path);
        if (DIM_KEYS.includes(lp)) {
          return idx === dimsAt ? (
            <div key="dims" className="flex flex-col gap-1 text-[12.5px] sm:col-span-2">
              <span className="font-medium text-foreground">크기 (가로 × 세로 × 높이)</span>
              <DimsInput entity={entity} onChange={onChange} busy={busy} />
            </div>
          ) : null;
        }
        // 선택 버튼 묶음은 <label> 로 감싸지 않는다 — 감싸면 첫 버튼 이름에 항목 이름이 붙어 낭독·선택이 꼬인다
        const group = f.type === "enum" || f.type === "boolean" || f.type === "array" || f.type === "object";
        const Wrap = group ? "div" : "label";
        const label = lp === "intake.tool_assembly_mass_kg" ? "툴 1개당 무게(제품 제외)"
          : lp === TOTAL_KEY ? "한 번에 드는 제품 총무게" : f.label_ko;
        return (
          <Wrap key={f.path} className={cn("flex flex-col gap-1 text-[12.5px]", (group || f.type === "attachment") && "sm:col-span-2")}>
            <span className="font-medium text-foreground">{label}</span>
            <FieldInput field={{ ...f, label_ko: label }} entity={entity} onChange={onChange} robots={intake.robots} busy={busy} />
          </Wrap>
        );
      })}
      {/* 툴이 2개 이상이면 개당 × 수량 = 총 — 툴체인저는 한 번에 1개씩 들어 1개당 무게로 계산 */}
      {q.id === "J04" && typeof qty === "number" && qty > 1 && typeof mass === "number" && (
        <p className="rounded-md bg-foreground/[0.04] px-2.5 py-1.5 text-[12px] text-foreground-muted sm:col-span-2">
          툴 1개당 <b className="text-foreground">{mass}kg</b> × {qty}개 = 총 <b className="text-foreground">{round3(mass * qty)}kg</b>
          <span className="text-foreground-subtle"> — 툴체인저는 한 번에 툴 1개만 들어 1개당 무게로 계산합니다</span>
        </p>
      )}
      {/* 제품을 여러 개 함께 들면 개당 무게 × 개수 = 총무게(자동 계산, 직접 고쳐도 됨) */}
      {q.id === "J05" && entity.workpiece_not_applicable !== true && typeof count === "number" && count > 1 && (
        <label className="flex items-center gap-2 text-[12.5px] text-foreground-muted sm:col-span-2">
          <input type="checkbox" checked={mixed} disabled={busy}
                 onChange={(e) => { set(MIXED_KEY, e.target.checked); if (e.target.checked) setList(Array(count).fill(null)); }} />
          제품마다 무게가 다름 — 하나씩 적으면 합계를 총무게로 씁니다
        </label>
      )}
      {q.id === "J05" && entity.workpiece_not_applicable !== true && mixed && typeof count === "number" && count > 1 && (
        <div className="flex flex-wrap items-center gap-1.5 rounded-md bg-foreground/[0.04] px-2.5 py-1.5 text-[12.5px] sm:col-span-2">
          {Array.from({ length: count }, (_, i) => (
            <span key={i} className="flex items-center gap-1.5">
              {i > 0 && <span className="text-foreground-subtle">+</span>}
              <NumberInput aria-label={`제품 ${i + 1} 무게`} placeholder={`제품 ${i + 1}`} value={list[i] ?? null} disabled={busy}
                           className={cn(inputCls, "w-20")}
                           onValue={(n) => setList(Array.from({ length: count }, (_, j) => (j === i ? n : list[j] ?? null)))} />
            </span>
          ))}
          <span className="text-foreground-muted">kg = 총</span>
          <b className="text-foreground">{typeof total === "number" ? `${total}kg` : "?"}</b>
          {list.filter((x) => typeof x === "number").length < count && (
            <span className="text-[11.5px] text-foreground-subtle">(다 적으면 자동 계산)</span>
          )}
        </div>
      )}
      {q.id === "J05" && entity.workpiece_not_applicable !== true && !mixed && (
        <div className="flex flex-wrap items-center gap-1.5 rounded-md bg-foreground/[0.04] px-2.5 py-1.5 text-[12.5px] sm:col-span-2">
          <span className="text-foreground-muted">제품 1개당</span>
          <NumberInput aria-label="제품 1개당 무게" value={each ?? null} disabled={busy} className={cn(inputCls, "w-24")}
                       onValue={(n) => { set(EACH_KEY, n); if (n != null && typeof count === "number") set(TOTAL_KEY, round3(n * count)); }} />
          <span className="text-foreground-muted">kg × {typeof count === "number" ? `${count}개` : "개수"}</span>
          <span className="text-foreground-muted">= 총</span>
          <b className="text-foreground">{typeof total === "number" ? `${total}kg` : "?"}</b>
          {typeof count !== "number" && <span className="text-[11.5px] text-foreground-subtle">(개수를 적으면 자동 계산)</span>}
        </div>
      )}
      {q.id === "J05" && (
        <label className="flex items-center gap-2 text-[12.5px] text-foreground-muted sm:col-span-2">
          <input type="checkbox" checked={entity.workpiece_not_applicable === true} disabled={busy}
                 onChange={(e) => onChange("workpiece_not_applicable", e.target.checked)} />
          이 툴은 제품을 들지 않음(해당 없음)
        </label>
      )}
    </div>
  );
}

/** 같은 질문을 툴(로봇)마다 따로 묻지 않고 한 섹션에서 대상별로(사용자 2026-10-02). */
export function GroupedFields({ items, intake, setIntake, busy }: {
  items: AtcFollowup[]; intake: AtcIntake; setIntake: (f: (i: AtcIntake) => AtcIntake) => void; busy: boolean;
}) {
  return (
    <div className="flex flex-col gap-2">
      {items.map((it) => (
        <div key={`${it.id}-${it.entity_index ?? "p"}`} className={cn("flex flex-col gap-2", items.length > 1 || it.scope !== "project" ? "rounded-lg border border-border p-3" : "")}>
          {it.scope !== "project" && <span className="text-[12.5px] font-semibold text-accent">{it.entity_label}</span>}
          <FieldsBlock q={it} intake={intake} setIntake={setIntake} entityIndex={it.entity_index ?? 0} busy={busy} />
        </div>
      ))}
    </div>
  );
}

/** 같은 id 끼리 묶기(순서 유지). */
export function groupById<T extends { id: string }>(xs: T[]): T[][] {
  const out: T[][] = [];
  for (const x of xs) {
    const g = out.find((a) => a[0].id === x.id);
    if (g) g.push(x);
    else out.push([x]);
  }
  return out;
}

// 고객 정보 — 질문지 맨 앞(사용자 2026-10-02: 질문에 고객사 정보가 없음). 확정 제안 내역에 이 이름으로 남는다.
export const CUSTOMER_Q: AtcQuestion = {
  id: "C00", scope: "project", question_ko: "어느 고객사의 건인가요?",
  help_text_ko: "확정 제안 내역에 이 고객사 이름으로 남습니다. 모르면 비워 두어도 됩니다.",
  fields: [
    { path: "project.customer_name", type: "string", label_ko: "고객사" },
    { path: "project.customer_contact", type: "string", label_ko: "고객 담당자(이름·부서)" },
    { path: "project.writer", type: "string", label_ko: "작성자(영업 담당)" },
    { path: "project.meeting_date", type: "string", label_ko: "미팅일(예: 2026-10-02)" },
  ],
};

// ── 질문지 진행 ───────────────────────────────────────────────────────────────

export function AtcWizard({ intake, setIntake, step, setStep, busy, onSubmit }: {
  intake: AtcIntake; setIntake: (f: (i: AtcIntake) => AtcIntake) => void;
  step: number; setStep: (n: number) => void; busy: boolean; onSubmit: () => void;
}) {
  const [spec, setSpec] = useState<AtcSpec | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [followups, setFollowups] = useState<AtcFollowup[] | null>(null);
  const [checking, setChecking] = useState(false);

  useEffect(() => {
    let alive = true;
    fetch("/api/product-recommend/atc/spec", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("질문지를 불러오지 못했습니다."))))
      .then((d: AtcSpec) => {
        if (!alive) return;
        setSpec(d);
        // 'Q&A 고치기'로 돌아와 추가 질문 단계에 있으면 그 목록을 다시 계산
        if (step >= d.question_bank.length + 1) void loadFollowups();     // +1: 맨 앞 고객 정보 단계
      })
      .catch((e: Error) => { if (alive) setError(e.message); });
    return () => { alive = false; };
    // 질문지는 처음 한 번만 받는다(그때의 단계로 추가 질문을 판단)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 후속 질문 — 지금까지의 답으로 해당하는 것만 계산(backend 조건 언어). 추가 질문 단계로 갈 때마다 다시 계산
  async function loadFollowups() {
    setChecking(true);
    setError(null);
    try {
      const r = await fetch("/api/product-recommend/atc/evaluate", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ intake }),
      });
      if (!r.ok) throw new Error("추가 질문을 계산하지 못했습니다.");
      setFollowups(((await r.json()) as AtcOutput).followup_questions);
    } catch (e) {
      setError(e instanceof Error ? e.message : "추가 질문을 계산하지 못했습니다.");
    } finally {
      setChecking(false);
    }
  }
  function goto(n: number) {
    setStep(Math.max(0, n));
    if (spec && n >= spec.question_bank.length + 1) void loadFollowups();
  }

  if (error) return <p className="rounded-lg border border-red-500/30 bg-red-500/5 px-3 py-2 text-[12.5px] text-red-600">{error}</p>;
  if (!spec) return <p className="text-[12.5px] text-foreground-subtle">질문지를 불러오는 중…</p>;

  // 단계: 0 고객 정보 → 1..n 기본 질문 → 앞 답에 해당하는 추가 질문(같은 질문은 툴별로 한 섹션) → (없으면) 마무리 한 칸
  const bank = [CUSTOMER_Q, ...spec.question_bank];
  const nBasic = bank.length;
  const fus = followups ?? [];
  const groups = groupById(fus);
  const total = nBasic + Math.max(groups.length, 1);
  const q = step < nBasic ? bank[step] : undefined;
  const fg = step >= nBasic ? groups[step - nBasic] : undefined;
  const last = step >= total - 1;
  const ents = q ? entities(intake, q.scope) : [];
  const addEntity = (scope: "robot" | "tool") => setIntake((i) => scope === "robot"
    ? { ...i, robots: [...i.robots, { id: `R${i.robots.length + 1}`, speed: {}, cables: {} }] }
    : { ...i, tools: [...i.tools, { id: `T${i.tools.length + 1}`, robot_ids: i.robots.length === 1 ? [i.robots[0].id] : [],
                                     intake: { dimensions_mm: {} }, mass_components: {} }] });
  const removeEntity = (scope: "robot" | "tool", idx: number) => setIntake((i) => scope === "robot"
    ? { ...i, robots: i.robots.filter((_, j) => j !== idx) }
    : { ...i, tools: i.tools.filter((_, j) => j !== idx) });
  const chips = [{ key: "C00", label: "고객" }, ...spec.question_bank.map((x, i) => ({ key: x.id, label: String(i + 1) })),
                 ...(followups === null || groups.length === 0 ? [{ key: "추가", label: "추가" }]
                   : groups.map((g, i) => ({ key: g[0].id, label: `추가 ${i + 1}` })))];

  return (
    <section className="flex flex-col gap-4 rounded-xl border border-border bg-surface p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-[15px] font-semibold text-foreground">툴체인저 선정 질문</h2>
        <span className="text-[11.5px] text-foreground-subtle">
          아는 것만 답하고 모르면 &lsquo;모름&rsquo; · 비워 둔 질문은 AI 가 대화에서 다시 묻습니다
        </span>
      </div>

      {/* 진행 — 번호를 눌러 이동. 추가 질문은 앞 답에 따라 생긴다 */}
      <ol className="flex flex-wrap gap-1" aria-label="질문 진행">
        {chips.map((c, i) => (
          <li key={c.key}>
            <button type="button" onClick={() => goto(i)} disabled={busy} aria-current={i === step ? "step" : undefined}
                    className={cn("h-7 min-w-7 rounded-full px-2 text-[11.5px] font-semibold transition-colors",
                      i === step ? "bg-accent text-white" : i < step ? "bg-accent/15 text-accent" : "bg-foreground/[0.06] text-foreground-subtle",
                      i >= nBasic && i !== step && "ring-1 ring-inset ring-amber-500/40")}>
              {c.label}
            </button>
          </li>
        ))}
      </ol>

      {q ? (
        <div className="flex flex-col gap-3">
          <div>
            <div className="text-[11.5px] font-medium text-accent">{step === 0 ? "고객 정보" : `${step} / ${nBasic - 1}`}</div>
            <h3 className="text-[17px] font-semibold text-foreground">{q.question_ko}</h3>
            <details className="mt-1 text-[12px] text-foreground-subtle">
              <summary className="cursor-pointer select-none">도움말</summary>
              <p className="mt-1">{q.help_text_ko}</p>
            </details>
          </div>
          {ents.map((_, idx) => (
            <div key={idx} className={cn("flex flex-col gap-2", ents.length > 1 || q.scope !== "project" ? "rounded-lg border border-border p-3" : "")}>
              {q.scope !== "project" && (
                <div className="flex items-center justify-between">
                  <span className="text-[12.5px] font-semibold text-foreground">{entityLabel(intake, q.scope, idx)}</span>
                  {ents.length > 1 && (q.id === "J01" || q.id === "J04") && (
                    <button type="button" disabled={busy} onClick={() => removeEntity(q.scope as "robot" | "tool", idx)}
                            className="text-[12px] text-foreground-subtle hover:text-red-600">삭제</button>
                  )}
                </div>
              )}
              <FieldsBlock q={q} intake={intake} setIntake={setIntake} entityIndex={idx} busy={busy} />
            </div>
          ))}
          {(q.id === "J01" || q.id === "J04") && (
            <button type="button" disabled={busy} onClick={() => addEntity(q.id === "J01" ? "robot" : "tool")}
                    className={cn(secondaryBtn, "self-start")}>
              <Icon name="plus" className="h-4 w-4" /> {q.id === "J01" ? "다른 로봇 추가" : "툴 추가"}
            </button>
          )}
          {q.id === "J04" && (
            <p className="text-[12px] text-foreground-subtle">같은 툴·같은 조건이면 한 줄에 수량으로 적어도 됩니다. 툴 무게는 드는 제품을 뺀 무게입니다.</p>
          )}
        </div>
      ) : checking ? (
        <p className="text-[12.5px] text-foreground-subtle">앞 답에 따라 추가로 물어볼 질문을 확인하는 중…</p>
      ) : fg ? (
        <div className="flex flex-col gap-3">
          <div>
            <div className="text-[11.5px] font-medium text-amber-700 dark:text-amber-300">
              추가 질문 {step - nBasic + 1} / {groups.length} · 앞에서 답한 내용 때문에 묻습니다{fg.length > 1 ? ` · ${fg.length}개 툴` : ""}
            </div>
            <h3 className="text-[17px] font-semibold text-foreground">{fg[0].question_ko}</h3>
            <p className="text-[12px] text-foreground-subtle">{fg[0].help_text_ko}</p>
          </div>
          <GroupedFields items={fg} intake={intake} setIntake={setIntake} busy={busy} />
        </div>
      ) : (
        <p className="rounded-lg border border-dashed border-border px-3 py-6 text-center text-[12.5px] text-foreground-subtle">
          앞 답에 따라 추가로 물어볼 질문이 없습니다. 이제 AI 와 함께 제품을 찾습니다.
        </p>
      )}

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-3">
        <button type="button" className={secondaryBtn} disabled={busy || step === 0} onClick={() => goto(step - 1)}>
          <Icon name="back" className="h-4 w-4" /> 이전
        </button>
        {last && step >= nBasic && !checking ? (
          <button type="button" className={primaryBtn} disabled={busy} onClick={onSubmit}>
            <Icon name="search" className="h-4 w-4" /> AI와 같이 제품 검색 진행
          </button>
        ) : (
          <button type="button" className={primaryBtn} disabled={busy || checking} onClick={() => goto(step + 1)}>
            다음 →
          </button>
        )}
      </div>
    </section>
  );
}

// ── 채운 미팅 질문지 첨부 ─────────────────────────────────────────────────────

const ACCEPT = ".docx,.pdf,.txt";

export function AtcMeetingUpload({ busy, onStart }: { busy: boolean; onStart: (file: File | null, text: string) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [paste, setPaste] = useState(false);
  const [text, setText] = useState("");
  const [over, setOver] = useState(false);
  const pick = (f: File | undefined) => { if (f) { setFile(f); setPaste(false); } };
  const ready = paste ? text.trim().length > 30 : file !== null;
  return (
    <section className="flex flex-col gap-4 rounded-xl border border-border bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-[15px] font-semibold text-foreground">첫 고객 미팅 질문지 검토</h2>
          <p className="text-[12.5px] text-foreground-muted">
            고객 미팅 때 채운 질문지를 올리면 AI 가 내용을 읽고 요약한 뒤, 제품 DB 의 툴체인저 중에서 선정 규칙으로 후보를 찾습니다.
          </p>
        </div>
        <a href="/forms/magbot_atc_meeting_form.docx" download className={cn(secondaryBtn, "shrink-0")}>
          <Icon name="download" className="h-4 w-4" /> 빈 질문지 양식
        </a>
      </div>

      {!paste ? (
        <label
          onDragOver={(e) => { e.preventDefault(); setOver(true); }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => { e.preventDefault(); setOver(false); pick(e.dataTransfer.files?.[0]); }}
          className={cn("flex cursor-pointer flex-col items-center gap-2 rounded-xl border-2 border-dashed px-4 py-8 text-center transition-colors",
            over ? "border-accent bg-accent/[0.06]" : "border-border hover:border-foreground/25")}>
          <input type="file" accept={ACCEPT} className="sr-only" disabled={busy}
                 onChange={(e) => pick(e.target.files?.[0] ?? undefined)} />
          <Icon name="paperclip" className="h-6 w-6 text-foreground-subtle" />
          {file ? (
            <>
              <span className="text-[14px] font-semibold text-foreground">{file.name}</span>
              <span className="text-[12px] text-foreground-subtle">{(file.size / 1024).toFixed(0)}KB · 다른 파일을 놓으면 바꿉니다</span>
            </>
          ) : (
            <>
              <span className="text-[14px] font-semibold text-foreground">채운 질문지를 끌어다 놓거나 눌러서 고르세요</span>
              <span className="text-[12px] text-foreground-subtle">docx · pdf · txt (10MB 이하)</span>
            </>
          )}
        </label>
      ) : (
        <textarea rows={8} value={text} disabled={busy} onChange={(e) => setText(e.target.value)}
                  placeholder="미팅 메모나 질문지 내용을 붙여 넣으세요"
                  className={cn(inputCls, "resize-y leading-relaxed")} aria-label="미팅 내용" />
      )}

      <div className="flex flex-wrap items-center justify-between gap-2">
        <button type="button" disabled={busy} onClick={() => setPaste((v) => !v)}
                className="text-[12.5px] font-medium text-accent hover:underline">
          {paste ? "파일로 올리기" : "파일 없이 내용 붙여 넣기"}
        </button>
        <button type="button" className={primaryBtn} disabled={busy || !ready}
                onClick={() => onStart(paste ? null : file, paste ? text : "")}>
          <Icon name="check" className="h-4 w-4" /> AI 검토 시작
        </button>
      </div>
    </section>
  );
}

// ── 결과 ──────────────────────────────────────────────────────────────────────

const STATUS_TONE: Record<string, string> = {
  needs_information: "bg-amber-500/15 text-amber-800 dark:text-amber-300",
  engineering_review: "bg-blue-500/12 text-blue-700 dark:text-blue-300",
  nonstandard_or_out_of_range: "bg-red-500/12 text-red-700 dark:text-red-300",
  standard_candidate: "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300",
};

export function AtcIntakeSummary({ intake, meeting, busy, onEdit, onReset, collapsed, readOnly }: {
  intake: AtcIntake; meeting?: AtcMeeting | null; busy: boolean; onEdit: () => void; onReset: () => void;
  /** 확정 제안 내역에서 볼 때 — 고치기·처음부터 버튼 없음 */
  readOnly?: boolean;
  /** 결과가 나온 뒤에는 접어 둔다 — 결과가 아래로 밀리지 않게(사용자 2026-10-02: 핵심만) */
  collapsed?: boolean;
}) {
  const p = intake.project as Record<string, Record<string, unknown> & { tags?: string[] }>;
  const yn = (v: unknown) => (v === true ? "있음" : v === false ? "없음" : "모름");
  const rows: [string, string][] = [
    ...intake.robots.map((r, i): [string, string] => {
      const sp = (r.speed ?? {}) as Record<string, unknown>;
      return [`로봇 ${i + 1}`, `${[r.manufacturer, r.model].filter(Boolean).join(" ") || "모델 모름"} · ${OPTION_KO[r.type as string] ?? "종류 모름"} · ${r.quantity ?? "?"}대 · 속도 ${sp.value ?? "?"}${(sp.unit as string) ?? ""}`];
    }),
    ["작업", `${OPTION_KO[p.process?.category as string] ?? "모름"}${p.process?.description ? ` — ${p.process.description}` : ""}`],
    ["교체 툴 수", `${(intake.project.tool_count as number | null) ?? "모름"}개`],
    ...intake.tools.map((t, i): [string, string] => {
      const it = (t.intake ?? {}) as Record<string, unknown>;
      const d = (it.dimensions_mm ?? {}) as Record<string, unknown>;
      const wp = t.workpiece_not_applicable ? "해당 없음" : `${(t.mass_components as Record<string, unknown>)?.max_simultaneous_workpieces_kg ?? "?"}kg`;
      return [(t.name as string) || `툴 ${i + 1}`,
        `×${t.installed_quantity ?? "?"} · ${d.x ?? "?"}×${d.y ?? "?"}×${d.z ?? "?"}mm · 툴 ${it.tool_assembly_mass_kg ?? "?"}kg · 제품 ${wp} · 기울임 ${yn(it.tilts_or_flips)} · 전기 ${yn(it.needs_power)} · 공압 ${yn(it.needs_air)} · 센서 ${yn(it.has_sensors_or_signals)}`];
    }),
    ["환경", p.environment?.has_special_conditions === false ? "해당 없음" : (p.environment?.tags as string[] | undefined)?.join(", ") || "모름"],
  ];
  const m = meeting?.meeting;
  const pj = intake.project as Record<string, unknown>;
  const cust = [pj.customer_name, pj.customer_contact && `담당 ${pj.customer_contact}`, pj.writer && `작성 ${pj.writer}`, pj.meeting_date]
    .filter(Boolean).join(" · ");
  return (
    <section className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <h2 className="text-[15px] font-semibold text-foreground">{meeting ? "미팅 내용 분석" : "입력한 조건"}
            <span className="text-[12px] font-normal text-foreground-subtle"> · 툴체인저</span></h2>
          {!m && cust && <p className="text-[12px] text-foreground-subtle">{cust}</p>}
          {m && (m.customer || m.writer || m.date) && (
            <p className="text-[12px] text-foreground-subtle">
              {[m.customer, m.writer && `작성 ${m.writer}`, m.date].filter(Boolean).join(" · ")}
              {meeting?.filename ? ` · ${meeting.filename}` : ""}
            </p>
          )}
        </div>
        {!readOnly && (
          <div className="flex flex-wrap gap-2">
            <button type="button" className={secondaryBtn} disabled={busy} onClick={onEdit}>
              <Icon name="edit" className="h-4 w-4" /> Q&amp;A 고치기
            </button>
            <button type="button" className={secondaryBtn} disabled={busy} onClick={onReset}>
              <Icon name="plus" className="h-4 w-4" /> 처음부터
            </button>
          </div>
        )}
      </div>
      <details open={!collapsed} className="group flex flex-col gap-3">
      <summary className={cn("cursor-pointer select-none text-[12.5px] font-medium text-accent", !collapsed && "hidden")}>
        입력한 조건 보기
      </summary>
      <div className="flex flex-col gap-3">
      {meeting?.summary && (
        <div className="rounded-lg bg-accent/[0.06] px-3 py-2.5">
          <div className="mb-1 flex items-center gap-1.5 text-[12px] font-semibold text-accent">
            <span className="und-grad grid h-4 w-4 place-items-center rounded-full text-[8px] font-bold text-white" aria-hidden>AI</span>
            미팅 요약
          </div>
          <p className="text-[13px] leading-relaxed text-foreground">{meeting.summary}</p>
        </div>
      )}
      {meeting && meeting.notes.length > 0 && (
        <ul className="flex flex-col gap-0.5 text-[12px] text-amber-800 dark:text-amber-300">
          {meeting.notes.map((n) => <li key={n}>· 확인 필요: {n}</li>)}
        </ul>
      )}
      {meeting && <div className="text-[12px] font-semibold text-foreground">질문지에서 읽은 내용 <span className="font-normal text-foreground-subtle">· 틀리면 [Q&amp;A 고치기]</span></div>}
      <dl className="flex flex-col gap-1.5 text-[12.5px]">
        {rows.map(([k, v], i) => (
          <div key={i} className="flex gap-2">
            <dt className="w-[84px] shrink-0 text-foreground-subtle">{k}</dt>
            <dd className="min-w-0 break-words text-foreground">{v}</dd>
          </div>
        ))}
      </dl>
      </div>
      </details>
    </section>
  );
}

type Group = { key: string; kind: "project" | "robot" | "tool"; label: string; asks: string[]; requests: string[] };
const GROUP_KO = { project: "공통", robot: "로봇", tool: "툴" } as const;
const GROUP_TONE = {
  project: "bg-foreground/[0.07] text-foreground-muted",
  robot: "bg-blue-500/12 text-blue-700 dark:text-blue-300",
  tool: "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300",
} as const;

/** 추가 확인사항을 대상(공통·로봇·툴)별로 묶는다 — 물어볼 질문 + '모름'이라 받아 올 자료. */
function followupGroups(o: AtcOutput, intake?: AtcIntake): Group[] {
  const groups = new Map<string, Group>();
  const toolNames = new Set((intake?.tools ?? []).map((t) => String(t.name ?? "")));
  const get = (kind: Group["kind"], label: string) => {
    const key = `${kind}:${label}`;
    if (!groups.has(key)) groups.set(key, { key, kind, label, asks: [], requests: [] });
    return groups.get(key)!;
  };
  for (const q of o.pending_questions ?? []) {
    const kind = q.scope === "project" ? "project" : q.scope;
    const ask = q.kind === "basic" ? `${q.question_ko} (${q.fields.map((x) => x.label_ko).join(", ")})` : q.question_ko;
    get(kind, kind === "project" ? "프로젝트·현장" : q.entity_label).asks.push(ask);
  }
  for (const r of o.information_reasons ?? []) get("project", "프로젝트·현장").asks.push(r.text_ko);
  for (const r of o.data_requests ?? []) {
    const i = r.indexOf(" — ");
    const label = i > 0 ? r.slice(0, i) : "";
    const text = i > 0 ? r.slice(i + 3) : r;
    const kind: Group["kind"] = !label ? "project" : toolNames.has(label) ? "tool" : "robot";
    get(kind, kind === "project" ? "프로젝트·현장" : label).requests.push(text);
  }
  const order = { project: 0, robot: 1, tool: 2 };
  return [...groups.values()].sort((a, b) => order[a.kind] - order[b.kind]);
}

/** 추천 상태(대화창 확인) — 후보를 찾자마자 왼쪽에 보이고, 사용자가 확인하면 '확정', 아니요면 '보류'. */
export type RecState = "pending" | "confirmed" | "held";
const REC_STATE: Record<RecState, { text: string; tone: string }> = {
  pending: { text: "추천 확인 대기", tone: "bg-amber-500/15 text-amber-800 dark:text-amber-300" },
  confirmed: { text: "추천 확정", tone: "bg-emerald-600 text-white" },
  held: { text: "보류 — 대화로 보완 중", tone: "bg-foreground/10 text-foreground-muted" },
};

/** 핵심 숫자 칸 — 큰 글씨 결론 + 괄호 속 세부(사용자 2026-10-02: '최종 4개 (세부)' 식으로). */
function KeyTile({ label, value, detail, warn }: { label: string; value: string; detail?: string; warn?: boolean }) {
  return (
    <div className="flex flex-col gap-0.5 rounded-lg border border-border bg-background px-3 py-2">
      <span className="text-[11.5px] text-foreground-subtle">{label}</span>
      <span className={cn("text-[17px] font-bold leading-tight", warn ? "text-amber-700 dark:text-amber-300" : "text-foreground")}>{value}</span>
      {detail && <span className="text-[11.5px] leading-snug text-foreground-muted">({detail})</span>}
    </div>
  );
}

/** 단가 칸 — 단가표 0원은 '가격 미정', 단가표에 없으면 '견적 후'. */
function won(v: number | null | undefined, status?: string): string {
  if (v != null) return v.toLocaleString("ko-KR");
  return status === "unset" ? "가격 미정" : status === "none" ? "견적 후" : "-";
}

/** 툴측 총무게 — 산업용이면 약 2배 선정 하중(v0.8 C01)도 함께. */
function payloadLabel(o: AtcOutput): string {
  const base = `툴측 ${o.required_payload_kg ?? "?"}kg`;
  return o.industrial_factor && o.screening_payload_kg != null ? `${base} × ${o.industrial_factor} = ${o.screening_payload_kg}kg` : base;
}

/** 포고핀·공압·툴플레이트·케이블 — 결론 한 단어 + 세부. */
function keyTiles(o: AtcOutput): { label: string; value: string; detail?: string; warn?: boolean }[] {
  const js = o.junior_summary;
  const tp = o.tool_plates;
  const pts = o.pogo_tools ?? [];
  const short = (s: string) => s.replace(/\s*\(.*\)$/, "");
  const pogoCond = pts.some((p) => p.status === "conditional");
  const pogo = !o.pogo_needed
    ? { value: js.pogo_modules.startsWith("미정") ? "미정" : "없음", detail: js.pogo_modules.startsWith("미정") ? "툴의 전기·센서 사용 여부 확인 필요" : "전기·센서 없음", warn: js.pogo_modules.startsWith("미정") }
    : o.pogo_modules != null
      ? { value: `PPM ${o.pogo_modules}개${pogoCond ? " (조건부)" : ""}`, warn: pogoCond,
          detail: pts.map((p) => `${short(p.tool)} ${p.pins}핀`).join(" · ") + " · 모듈당 8핀, 전류는 정격·피크 중 큰 값" }
      : { value: "미정", warn: true, detail: pts.filter((p) => p.pins == null).map((p) => `${short(p.tool)} ${p.missing.join("·")} 필요`).join(" · ") };
  const pn = o.pneumatic_tools ?? [];
  const airCond = pn.some((x) => x.status === "conditional");
  const air = o.pneumatic_needed
    ? o.pneumatic_modules != null
      ? { value: `PMM ${o.pneumatic_modules}개${airCond ? " (조건부)" : ""}`, warn: airCond,
          detail: pn.map((x) => `${short(x.tool)} 유로 ${x.paths}`).join(" · ") + " · 커플러 1쌍 = 독립 유로 2" }
      : { value: "미정", warn: true, detail: pn.filter((x) => x.paths == null).map((x) => `${short(x.tool)} ${(x.missing ?? []).join("·") || "공압 회로"} 필요`).join(" · ") || "공압 회로 확인 후" }
    : js.pneumatic_modules.startsWith("미정") ? { value: "미정", warn: true, detail: "툴의 공압 사용 여부 확인 필요" } : { value: "없음" };
  const cableShort = js.controller_and_cables.includes("부족");
  return [
    { label: "툴플레이트", value: tp.operating != null ? `${tp.operating}개` : "미정", warn: tp.operating == null,
      detail: tp.operating != null ? `교체 툴 ${tp.operating}${tp.spare != null ? ` · 예비 ${tp.spare}` : " · 예비는 따로 확인"}` : "툴 수량 확인 필요" },
    { label: "포고핀 모듈", ...pogo },
    { label: "공압 모듈", ...air },
    { label: "컨트롤러·케이블", value: "4m / 1m", warn: cableShort,
      detail: [cableShort ? "부족 구간 연장 검토" : "기본 길이",
               o.controller_info?.power && `구동 전원 ${o.controller_info.power}`,
               o.controller_info?.control].filter(Boolean).join(" · ") },
  ];
}

/** 대화 중(후보 찾기 전) 왼쪽에 보이는 실시간 검토 현황 — 답할 때마다 다시 계산된다. */
export function AtcLivePreview({ ev, left }: { ev: AtcOutput; left: number | null }) {
  const b = Object.fromEntries((ev.basis ?? []).map((x) => [x.item, x]));
  const first = ev.screening_candidates.find((c) => c.models.length && !c.out_of_range);
  const rows: [string, string, boolean | null | undefined][] = [
    ["예상 후보", first ? first.models.map((m) => m.name).join(" / ") : "아직 정할 수 없음", first ? true : false],
    ["툴측 총무게", b["툴측 총무게"]?.short ?? "미정", b["툴측 총무게"]?.ok],
    ["포고핀", b["포고핀"]?.short ?? "미정", b["포고핀"]?.ok],
    ["공압", b["공압"]?.short ?? "미정", b["공압"]?.ok],
  ];
  return (
    <section className="flex flex-col gap-2 rounded-xl border border-accent/30 bg-surface p-4" aria-live="polite">
      <div className="flex items-center justify-between gap-2">
        <h2 className="flex items-center gap-1.5 text-[14px] font-semibold text-foreground">
          <span className="und-grad grid h-5 w-5 place-items-center rounded-full text-[9px] font-bold text-white" aria-hidden>AI</span>
          검토 현황 <span className="text-[11.5px] font-normal text-foreground-subtle">· 답할 때마다 다시 계산</span>
        </h2>
        {left != null && left > 0 && <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-[11.5px] font-medium text-amber-800 dark:text-amber-300">확인할 것 {left}개</span>}
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        {rows.map(([k, v, ok]) => (
          <div key={k} className="flex items-center justify-between gap-2 rounded-lg bg-background px-3 py-2">
            <span className="text-[12px] text-foreground-subtle">{k}</span>
            <span className={cn("text-[14px] font-bold", ok === false ? "text-amber-700 dark:text-amber-300" : "text-foreground")}>{v}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

/** 추천 근거 — '왜 이 제품인가'로 이어지는 문장(사용자 2026-10-02: 숫자만으로는 왜 골랐는지 안 보임).
 *  핵심값(short)은 앞에 굵게, 계산 과정은 [계산 보기]로 펼친다. */
function BasisSentences({ basis, withCalc }: { basis: AtcBasis[]; withCalc?: boolean }) {
  return (
    <ol className="flex flex-col divide-y divide-border rounded-lg border border-border text-[13px]">
      {basis.map((b) => (
        <li key={b.item} className="flex gap-2.5 px-3 py-2">
          <span className={cn("mt-0.5 grid h-4 w-4 shrink-0 place-items-center rounded-full text-white", b.ok ? "bg-emerald-600" : "bg-amber-500")}>
            <Icon name={b.ok ? "check" : "help"} className="h-3 w-3" />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-baseline gap-x-2">
              <span className="text-[12px] text-foreground-subtle">{b.item}</span>
              <span className={cn("text-[13px] font-bold", b.ok ? "text-foreground" : "text-amber-700 dark:text-amber-300")}>{b.short}</span>
            </div>
            <p className="mt-0.5 leading-relaxed text-foreground">{b.sentence ?? b.judgement}</p>
            {withCalc && (
              <details className="mt-0.5 text-[12px]">
                <summary className="cursor-pointer select-none text-accent">계산 보기</summary>
                <div className="mt-0.5 leading-relaxed text-foreground-muted">{b.input} → {b.judgement}</div>
              </details>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}

/** 추천 확정 전(확인 대기·보류) — 선정 결과 대신 후보와 근거만(사용자 2026-10-02: [네] 전에 선정 결과가 뜨면 안 됨). */
export function AtcCandidatePreview({ rec, state }: { rec: Recommendation; state: RecState }) {
  const o = rec.atc!;
  const first = o.screening_candidates.find((c) => c.models.length && !c.out_of_range);
  const basis = (o.basis ?? []).filter((b) => (b.group ?? (b.ok === null ? "other" : "selection")) === "selection");
  return (
    <section className="flex flex-col gap-3 rounded-xl border border-dashed border-accent/50 bg-surface p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-[15px] font-semibold text-foreground">후보 검토</h2>
        <span className={cn("rounded-full px-2.5 py-0.5 text-[11.5px] font-semibold", REC_STATE[state].tone)}>{REC_STATE[state].text}</span>
      </div>
      {first && (
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <span className="text-[20px] font-bold text-foreground">{first.models.map((m) => m.name).join(" / ")}</span>
          <span className="text-[12px] text-accent">{first.series_label}</span>
          <span className="rounded-md bg-accent/10 px-2 py-0.5 text-[12.5px] font-semibold text-accent">정격 {first.models[0].payload_kg ?? "?"}kg</span>
          <span className="rounded-md bg-foreground/[0.06] px-2 py-0.5 text-[12.5px] font-semibold text-foreground">{payloadLabel(o)}</span>
        </div>
      )}
      <div className="text-[12.5px] font-semibold text-foreground">왜 이 모델인가</div>
      <BasisSentences basis={basis} />
      <p className="text-[12px] text-foreground-subtle">
        {state === "held"
          ? "보류 중입니다 — 대화창에 빠진 정보나 고려할 조건을 말씀하시면 반영해 다시 찾습니다."
          : "오른쪽 대화창에서 [네, 이 추천으로 진행]을 누르면 선정 결과(구성품·추가 확인사항·내부 검토)를 정리합니다. 다른 모델이 낫겠다 싶으면 대화창에 물어보세요."}
      </p>
    </section>
  );
}

export function AtcResultView({ rec, state, footer }: { rec: Recommendation; state?: RecState; footer?: ReactNode }) {
  const o = rec.atc!;
  const js = o.junior_summary;
  const first = o.screening_candidates.find((c) => c.models.length && !c.out_of_range);
  const lead = first ? rec.items.find((i) => i.card_id === first.models[0].card_id) : undefined;
  const groups = followupGroups(o, rec.intake);
  const basis = (o.basis ?? []).filter((b) => (b.group ?? (b.ok === null ? "other" : "selection")) === "selection");
  const config = (o.basis ?? []).filter((b) => b.group === "config");
  const tiles = keyTiles(o);
  return (
    <section className="flex flex-col gap-4 rounded-xl border border-accent/30 bg-surface p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-[15px] font-semibold text-foreground">툴체인저 선정 결과</h2>
        {state && <span className={cn("rounded-full px-2.5 py-0.5 text-[11.5px] font-semibold", REC_STATE[state].tone)}>{REC_STATE[state].text}</span>}
        {o.statuses.map((s) => (
          <span key={s.status} className={cn("rounded-full px-2 py-0.5 text-[11px] font-medium", STATUS_TONE[s.status])}>{s.status_ko}</span>
        ))}
        {(o.db_confirmed?.length ?? 0) > 0 && (
          <span className="rounded-full border border-emerald-600/40 px-2 py-0.5 text-[11px] font-medium text-emerald-700 dark:text-emerald-300"
                title={o.db_confirmed!.map((x) => `· ${x.text}`).join("\n")}>
            제품 DB 기준 확정 {o.db_confirmed!.length}건
          </span>
        )}
        {(o.provisional_applied?.length ?? 0) > 0 && (
          <span className="rounded-full border border-dashed border-amber-500/60 px-2 py-0.5 text-[11px] font-medium text-amber-800 dark:text-amber-300"
                title={o.provisional_applied!.map((x) => `· ${x.text}`).join("\n")}>
            잠정 기준 {o.provisional_applied!.length}건 — 엔지니어 확정 전
          </span>
        )}
      </div>

      {/* 후보 — 이름·정격·툴측 무게를 가장 크게 */}
      {first ? (
        <div className="grid gap-4 md:grid-cols-[minmax(0,4fr)_minmax(0,7fr)]">
          <div className="relative aspect-[4/3] w-full overflow-hidden rounded-lg border border-border bg-white">
            {lead?.image_id ? (
              // eslint-disable-next-line @next/next/no-img-element -- 인증 쿠키가 필요한 사진 프록시
              <img src={`/api/product-images/file/${lead.image_id}`} alt={first.models[0].name} className="absolute inset-0 h-full w-full object-contain p-3" />
            ) : <span className="absolute inset-0 grid place-items-center text-[12px] text-foreground-subtle">사진 없음</span>}
          </div>
          <div className="flex min-w-0 flex-col gap-2">
            <div>
              <div className="text-[12px] font-medium text-accent">{first.series_label}</div>
              <div className="text-[24px] font-bold leading-tight text-foreground">{first.models.map((m) => m.name).join(" / ")}</div>
            </div>
            <div className="flex flex-wrap gap-2 text-[13px]">
              <span className="rounded-md bg-accent/10 px-2 py-1 font-semibold text-accent">정격 {first.models[0].payload_kg ?? "?"}kg</span>
              <span className="rounded-md bg-foreground/[0.06] px-2 py-1 font-semibold text-foreground">{payloadLabel(o)}</span>
              <span className="rounded-md bg-foreground/[0.06] px-2 py-1 text-foreground-muted">{js.evidence_status}</span>
            </div>
            <p className="text-[13px] leading-relaxed text-foreground-muted">{js.reason_in_plain_korean}</p>
            {first.notes.length > 0 && (
              <ul className="flex flex-col gap-0.5 text-[12px] text-amber-800 dark:text-amber-300">
                {first.notes.map((n) => <li key={n}>· {n}</li>)}
              </ul>
            )}
            <p className="text-[11.5px] text-foreground-subtle">최종 모델은 내부 엔지니어가 확정합니다.</p>
          </div>
        </div>
      ) : (
        <p className="rounded-lg bg-amber-500/10 px-3 py-2 text-[13px] font-medium text-amber-800 dark:text-amber-300">{js.reason_in_plain_korean}</p>
      )}

      {/* 추천 근거 — 왜 이 모델인가(문장), 계산 과정은 [계산 보기] */}
      {basis.length > 0 && (
        <div className="rounded-lg border border-border">
          <div className="flex flex-wrap items-baseline gap-x-2 border-b border-border px-3 py-2">
            <span className="text-[13px] font-semibold text-foreground">추천 근거 — 왜 이 모델인가</span>
            <span className="text-[11.5px] text-foreground-subtle">영업부 선정 규칙 · 제품 정격은 제품 DB · [계산 보기]로 숫자 확인</span>
          </div>
          <BasisSentences basis={basis} withCalc />
        </div>
      )}

      {/* 구성품 — 결론 숫자 + (세부), 수량을 정한 근거는 접어서 */}
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        {tiles.map((t) => <KeyTile key={t.label} {...t} />)}
      </div>
      {o.accessories && o.accessories.items.length > 0 && <AccessoryTable acc={o.accessories} layout={o.accessory} />}
      {config.length > 0 && (
        <details className="rounded-lg border border-border px-3 py-2 text-[12.5px]">
          <summary className="cursor-pointer select-none font-semibold text-foreground">구성품 수량은 어떻게 정했나 <span className="font-normal text-foreground-subtle">· 포고핀·공압</span></summary>
          <div className="mt-2"><BasisSentences basis={config} withCalc /></div>
        </details>
      )}

      {groups.length > 0 && (
        <div>
          <div className="mb-2 flex flex-wrap items-baseline gap-x-2 text-[12.5px]">
            <span className="font-semibold text-foreground">추가 확인사항</span>
            <span className="text-foreground-subtle">고객에게 확인하거나 받아 올 자료</span>
          </div>
          <div className="grid gap-2.5 sm:grid-cols-2">
            {groups.map((g) => (
              <div key={g.key} className="flex flex-col gap-2 rounded-lg border border-border bg-background p-3">
                <div className="flex items-center gap-2">
                  <span className={cn("rounded-md px-1.5 py-0.5 text-[10.5px] font-semibold", GROUP_TONE[g.kind])}>{GROUP_KO[g.kind]}</span>
                  <span className="min-w-0 truncate text-[13px] font-semibold text-foreground" title={g.label}>{g.label}</span>
                </div>
                {g.asks.length > 0 && (
                  <ul className="flex flex-col gap-1 text-[12.5px] text-foreground">
                    {g.asks.map((x) => (
                      <li key={x} className="flex gap-1.5">
                        <span className="mt-[1px] grid h-4 w-4 shrink-0 place-items-center rounded-full bg-amber-500/15 text-[10px] font-bold text-amber-700 dark:text-amber-300">?</span>
                        <span className="min-w-0">{x}</span>
                      </li>
                    ))}
                  </ul>
                )}
                {g.requests.length > 0 && (
                  <ul className="flex flex-col gap-1 border-t border-dashed border-border pt-2 text-[12.5px] text-foreground-muted">
                    {g.requests.map((x) => (
                      <li key={x} className="flex gap-1.5">
                        <Icon name="paperclip" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-foreground-subtle" />
                        <span className="min-w-0">자료 요청 · {x}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
      {rec.corrections.length > 0 && (
        <details className="rounded-lg border border-border px-3 py-2 text-[12.5px]">
          <summary className="cursor-pointer select-none font-semibold text-foreground">함께 참고한 AI 학습 내용 ({rec.corrections.length})</summary>
          <ul className="mt-1.5 flex flex-col gap-0.5 text-foreground-muted">
            {rec.corrections.map((c) => <li key={c.id}>· {c.text}</li>)}
          </ul>
        </details>
      )}
      {o.engineering_review_reasons.length > 0 && (
        <details className="rounded-lg border border-border px-3 py-2 text-[12.5px]">
          <summary className="cursor-pointer select-none font-semibold text-foreground">
            내부 검토 {o.engineering_review_reasons.length}건 <span className="font-normal text-foreground-subtle">· 엔지니어가 확인할 이유</span>
          </summary>
          <ul className="mt-1.5 flex flex-col gap-1 text-foreground-muted">
            {o.engineering_review_reasons.map((r) => <li key={r.text_ko}>· {r.text_ko}</li>)}
          </ul>
        </details>
      )}
      {/* 2·3순위 후보(사용자 2026-10-07: '다른 계열 후보' 대신) — 바꾸려면 대화창에 'TCV1으로 바꿔 줘' */}
      {(o.alternatives?.length ?? 0) > 0 && (
        <div className="rounded-lg border border-border px-3 py-2.5 text-[12.5px]">
          <div className="mb-2 flex flex-wrap items-baseline gap-x-2">
            <span className="font-semibold text-foreground">2·3순위 후보</span>
            <span className="text-[11.5px] text-foreground-subtle">이 로봇·선정 하중에 쓸 수 있는 다음 모델 · 바꾸려면 대화창에 &quot;{o.alternatives![0].name}으로 바꿔 줘&quot;</span>
          </div>
          <ul className="grid gap-2 md:grid-cols-2">
            {o.alternatives!.map((a) => (
              <li key={a.name} className="flex gap-2 rounded-md bg-foreground/[0.03] px-2.5 py-2">
                <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-foreground/10 text-[11px] font-bold text-foreground-muted">{a.rank}</span>
                <div className="min-w-0">
                  <div className="flex flex-wrap items-baseline gap-x-2">
                    <b className="text-[14px] text-foreground">{a.name}</b>
                    <span className="text-foreground-muted">정격 {a.payload_kg}kg</span>
                  </div>
                  <div className="text-[11.5px] text-foreground-subtle">{a.series_label}</div>
                  <div className="mt-0.5 text-[12px] text-foreground-muted">{a.reason}</div>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
      {footer && <div className="flex flex-wrap items-center justify-end gap-2 border-t border-border pt-3">{footer}</div>}
    </section>
  );
}


/** 구성품 표 — 추천 단계에서는 수량만. 금액은 [예상 금액 보기]로 펼칠 때만(단가표 기준, 제품만) — 실제 금액·옵션(IB)·물류비·
 *  인건비는 견적서에서 정한다(사용자 2026-10-06: 추천 결과의 금액이 견적처럼 보여 헷갈림). */
function AccessoryTable({ acc, layout }: { acc: NonNullable<AtcOutput["accessories"]>; layout?: AtcOutput["accessory"] }) {
  const [showPrice, setShowPrice] = useState(false);
  const items = acc.items.filter((x) => !x.optional);
  const options = acc.items.filter((x) => x.optional);
  const p = acc.pricing;
  return (
    <div className="rounded-lg border border-border">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-border px-3 py-2">
        <span className="text-[13px] font-semibold text-foreground">ATC 구성품·액세서리</span>
        <span className="text-[11.5px] text-foreground-subtle">수량 기준 · 금액·옵션은 견적서에서 정합니다</span>
        {p && (
          <button type="button" onClick={() => setShowPrice((v) => !v)} aria-expanded={showPrice}
                  className="ml-auto rounded-md border border-border px-2 py-0.5 text-[11.5px] text-foreground-muted hover:text-foreground">
            {showPrice ? "예상 금액 접기" : "예상 금액 보기 (단가표 기준)"}
          </button>
        )}
      </div>
      <table className="w-full text-[12.5px]">
        <thead>
          <tr className="text-left text-[11.5px] text-foreground-subtle">
            <th className="px-3 py-1.5 font-medium">품목</th>
            <th className="whitespace-nowrap px-2 py-1.5 font-medium">위치</th>
            <th className="whitespace-nowrap px-2 py-1.5 text-right font-medium">수량</th>
            {showPrice && <th className="whitespace-nowrap px-2 py-1.5 text-right font-medium">단가</th>}
            {showPrice && <th className="whitespace-nowrap px-2 py-1.5 text-right font-medium">금액</th>}
            <th className="px-3 py-1.5 font-medium">수량 근거</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {items.map((x) => (
            <tr key={`${x.name}-${x.side}`}>
              <td className="px-3 py-1.5">
                <div className="flex items-center gap-2">
                  {x.image_id ? (
                    // eslint-disable-next-line @next/next/no-img-element -- 인증 쿠키가 필요한 사진 프록시
                    <img src={`/api/product-images/file/${x.image_id}`} alt="" className="h-7 w-7 shrink-0 rounded border border-border bg-white object-contain" />
                  ) : null}
                  <span className="font-semibold text-foreground">{x.name}</span>
                </div>
                {x.remark && <div className="text-[11px] text-foreground-subtle">{x.remark}</div>}
              </td>
              <td className="px-2 py-1.5 text-foreground-muted">{x.side}</td>
              <td className={cn("px-2 py-1.5 text-right text-[14px] font-bold", x.qty == null || x.conditional ? "text-amber-700 dark:text-amber-300" : "text-foreground")}>
                {x.qty ?? "미정"}
                {x.conditional && x.qty != null && <div className="text-[10.5px] font-semibold">조건부</div>}
              </td>
              {showPrice && (
                <td className={cn("whitespace-nowrap px-2 py-1.5 text-right", x.unit_price == null ? "text-amber-700 dark:text-amber-300" : "text-foreground-muted")}>
                  {won(x.unit_price, x.price_status)}
                </td>
              )}
              {showPrice && (
                <td className="whitespace-nowrap px-2 py-1.5 text-right text-foreground-muted">{x.amount != null ? x.amount.toLocaleString("ko-KR") : "-"}</td>
              )}
              <td className="px-3 py-1.5 text-[12px] text-foreground-muted">{x.basis}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {showPrice && p && (
        <div className="flex flex-col gap-0.5 border-t border-border bg-foreground/[0.02] px-3 py-2 text-[12.5px]">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <span className="text-foreground-muted">예상 금액 — 제품만, 단가표 고객사가(VAT 별도)</span>
            <span className={cn("text-[15px] font-bold", p.complete ? "text-foreground" : "text-amber-700 dark:text-amber-300")}>
              {p.subtotal.toLocaleString("ko-KR")}원{p.complete ? "" : " 이상(미정 품목 있음)"}
            </span>
          </div>
          <div className="text-[11.5px] text-foreground-subtle">물류비·설치 인건비·옵션(IB)은 빠져 있습니다 — 확정 금액은 [최종 제안 확정] 뒤 견적서에서.</div>
          {p.missing.map((m) => <div key={m} className="text-[11.5px] text-amber-700 dark:text-amber-300">· {m}</div>)}
        </div>
      )}
      {layout && <AccessoryLayout a={layout} />}
      {(options.length > 0 || acc.notes.length > 0) && (
        <ul className="flex flex-col gap-0.5 border-t border-border px-3 py-2 text-[11.5px] text-foreground-subtle">
          {options.length > 0 && <li>· 옵션 {options.map((x) => x.name).join(", ")} — 고객 요청 시 견적서에서 넣습니다.</li>}
          {acc.notes.map((n) => <li key={n}>· {n}</li>)}
        </ul>
      )}
    </div>
  );
}

const ACC_STATUS_TONE: Record<string, string> = {
  needs_data: "bg-amber-500/15 text-amber-800 dark:text-amber-200",
  conditional_draft: "bg-amber-500/10 text-amber-700 dark:text-amber-300",
  engineering_review: "bg-sky-500/10 text-sky-700 dark:text-sky-300",
};

/** v1.2 액세서리 — 마스터 공통 배치(모듈별 논리 핀 배정)·툴별 PPF/PMF·장착 위치·확인할 질문. 계산은 backend 규칙(atc_accessory). */
function AccessoryLayout({ a }: { a: NonNullable<AtcOutput["accessory"]> }) {
  const m = a.master;
  const mods = m.electrical_modules ?? [];
  const rows = a.tool_layouts.filter((t) => t.PPF || t.PMF);
  if (!mods.length && !m.pmm_per_master && !a.open_questions.length) return null;
  return (
    <div className="flex flex-col gap-2 border-t border-border px-3 py-2.5 text-[12px]">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold text-foreground">액세서리 배치</span>
        <span className={cn("rounded px-1.5 py-0.5 text-[11px] font-semibold", ACC_STATUS_TONE[a.status] ?? "bg-foreground/8")}>{a.status_ko}</span>
        {m.positions != null && (
          <span className={cn("text-[11.5px]", m.customization_required ? "font-semibold text-red-600 dark:text-red-400" : "text-foreground-muted")}>
            장착 위치 {m.positions} / 표준 {m.standard_positions}개소{m.customization_required ? " — 초과, 커스텀 필요" : ""}
          </span>
        )}
      </div>
      {mods.length > 0 && (
        <div className="flex flex-col gap-1">
          <span className="text-[11.5px] text-foreground-muted">마스터 공통 배치(논리 배정 — 실제 핀 번호 아님)</span>
          {mods.map((pins, i) => (
            <div key={i} className="flex flex-wrap items-center gap-1">
              <span className="w-10 shrink-0 text-[11px] font-semibold text-foreground-muted">PPM{i + 1}</span>
              {pins.map((p, j) => (
                <span key={j} className={cn("rounded border px-1.5 py-0.5 font-mono text-[10.5px]",
                  p === "미사용" ? "border-dashed border-border text-foreground-subtle"
                    : p.startsWith("+") ? "border-red-400/50 text-red-700 dark:text-red-300"
                      : p.startsWith("0V") ? "border-slate-400/60 text-slate-600 dark:text-slate-300" : "border-accent/40 text-accent")}>{p}</span>
              ))}
            </div>
          ))}
        </div>
      )}
      {rows.length > 0 && (
        <div className="flex flex-wrap gap-x-4 gap-y-0.5 text-foreground-muted">
          {rows.map((t) => (
            <span key={t.tool}>{t.tool} ×{t.quantity ?? "?"}: {t.PPF ? `PPF ${t.PPF}` : ""}{t.PPF && t.PMF ? " · " : ""}{t.PMF ? `PMF ${t.PMF}` : ""}</span>
          ))}
        </div>
      )}
      {(a.review_notes ?? []).length > 0 && (
        <ul className="flex flex-col gap-0.5 text-[11.5px] text-sky-700 dark:text-sky-300">
          {(a.review_notes ?? []).map((n) => <li key={n}>· 엔지니어 확인: {n}</li>)}
        </ul>
      )}
      {a.open_questions.length > 0 && (
        <div className="rounded-md bg-amber-500/[0.07] px-2.5 py-1.5">
          <div className="mb-0.5 text-[11.5px] font-semibold text-amber-800 dark:text-amber-200">확정 전에 확인할 것</div>
          <ul className="flex flex-col gap-0.5 text-[11.5px] text-foreground-muted">
            {a.open_questions.map((q) => <li key={q}>· {q}</li>)}
          </ul>
        </div>
      )}
    </div>
  );
}
