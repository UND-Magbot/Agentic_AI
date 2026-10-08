// 5단계 구성 확인 — 페이지 구성(쪽수 상한 안) + 견적 행(근거 있는 단가만) 확인·수정 → 승인하면 제작.
"use client";

import { useState } from "react";
import type { DeckPage, PageType, ProposalProject, QuoteLine } from "@/lib/shared/proposal-projects";
import { Icon } from "@/components/shared/ui/icon";
import { ErrorBox, inputCls, primaryBtn, secondaryBtn, useProjectAction } from "./proposal-actions";

const TYPE_LABEL: Record<PageType, string> = {
  cover: "표지", overview: "개요", flow: "공정 흐름", common_concept: "공통 컨셉", alternative: "컨셉",
  equipment: "주요 항목", poc: "실증·현장 적용", quote: "견적",
};
const GROUPS: QuoteLine["group"][] = ["공통", "통합·실증", "대안", "옵션", "현장 적용"];
const MAX_QUOTE = 30;
// 저장 값 "대안" 은 PPT 생성(5c) 계약 — 화면에서는 컨셉별 행으로 보여 준다.
const GROUP_LABEL: Partial<Record<QuoteLine["group"], string>> = { 대안: "컨셉별" };
const EMPTY_LINE: QuoteLine = {
  group: "공통", alt_id: null, item: "", qty: "1", unit: "식", unit_price: null, currency: "원", basis: "", included: true,
};

export function ProposalStructurePanel({ project }: { project: ProposalProject }) {
  const { busy, error, run } = useProjectAction(project.id);
  const [pages, setPages] = useState<DeckPage[]>(project.pages);
  const [lines, setLines] = useState<QuoteLine[]>(project.quote_lines);
  const [dirty, setDirty] = useState(false);
  const locked = project.job_alive;
  const maxPages = project.output?.max_pages ?? 10;
  // 구성 초안 작업이 끝나 서버 값이 들어오면 편집본을 맞춘다(렌더 중 비교).
  const serverKey = JSON.stringify([project.pages, project.quote_lines]);
  const [seenKey, setSeenKey] = useState(serverKey);
  if (serverKey !== seenKey && !dirty) {
    setSeenKey(serverKey);
    setPages(project.pages);
    setLines(project.quote_lines);
  }

  function changePages(next: DeckPage[]) {
    setPages(next.map((p, i) => ({ ...p, page_no: i + 1 })));
    setDirty(true);
  }
  function changeLine(i: number, patch: Partial<QuoteLine>) {
    setLines((ls) => ls.map((l, j) => (j === i ? { ...l, ...patch } : l)));
    setDirty(true);
  }
  function move(i: number, d: -1 | 1) {
    const j = i + d;
    if (j < 0 || j >= pages.length) return;
    const next = [...pages];
    [next[i], next[j]] = [next[j], next[i]];
    changePages(next);
  }

  if (!project.pages.length) {
    return (
      <section className="rounded-xl border border-border bg-surface px-4 py-3 text-[13px] text-foreground-muted">
        페이지 구성과 견적 초안을 준비하고 있습니다.
      </section>
    );
  }

  // 가이드 K08: 공정 컨셉(컨셉마다 1쪽)·주요 항목·견적 쪽은 뺄 수 없다(서버도 같은 규칙으로 거부).
  const required = (p: DeckPage) =>
    (p.type === "equipment" || p.type === "quote" || p.type === "alternative") &&
    pages.filter((x) => x.type === p.type && (p.type !== "alternative" || x.alt_id === p.alt_id)).length <= 1;
  const altName = (id: string | null | undefined) => project.alternatives.find((a) => a.id === id)?.name ?? id ?? "";

  return (
    <section className="flex flex-col gap-4 rounded-xl border border-accent/30 bg-surface p-4">
      <div>
        <h2 className="text-[15px] font-semibold text-foreground">
          구성 확인 — {pages.length}쪽
          <span className={pages.length > maxPages ? "ml-2 text-[12px] font-medium text-amber-700 dark:text-amber-300" : "ml-2 text-[12px] font-normal text-foreground-subtle"}>
            {pages.length > maxPages ? `권장 ${maxPages}쪽을 넘었습니다 — 그대로 제작할 수 있습니다` : `권장 ${maxPages}쪽 이내`}
          </span>
        </h2>
        <p className="text-[12px] text-foreground-subtle">
          쪽 제목·순서를 고치고 필요 없는 쪽은 빼세요. 공정 컨셉·주요 항목·견적 쪽은 필수입니다. 고객 제안서는 10쪽 이내를 권장합니다. 승인하면 이 구성 그대로 PPT 를 만듭니다.
        </p>
      </div>
      <ol className="flex flex-col gap-1.5">
        {pages.map((p, i) => (
          <li key={`${p.type}-${p.alt_id ?? ""}-${i}`} className="flex flex-wrap items-center gap-2 rounded-lg border border-border/70 px-2.5 py-1.5">
            <span className="w-6 text-right text-[12px] tabular-nums text-foreground-subtle">{i + 1}</span>
            <span className="w-24 shrink-0 text-[12px] text-foreground-muted">
              {TYPE_LABEL[p.type]}{p.alt_id ? ` ${p.alt_id}` : ""}
            </span>
            <input className={`${inputCls} min-w-40 flex-1`} value={p.title} maxLength={40} disabled={locked}
                   aria-label={`${i + 1}쪽 제목`}
                   onChange={(e) => changePages(pages.map((x, j) => (j === i ? { ...x, title: e.target.value } : x)))} />
            <span className="text-[11px] text-foreground-subtle">{p.asset_ids.length ? "이미지 있음" : ""}</span>
            <div className="flex gap-1">
              <button type="button" aria-label="위로" className="grid h-7 w-7 place-items-center rounded-md hover:bg-foreground/10 disabled:opacity-30"
                      disabled={i === 0 || locked} onClick={() => move(i, -1)}>▲</button>
              <button type="button" aria-label="아래로" className="grid h-7 w-7 place-items-center rounded-md hover:bg-foreground/10 disabled:opacity-30"
                      disabled={i === pages.length - 1 || locked} onClick={() => move(i, 1)}>▼</button>
              <button type="button" aria-label="이 쪽 빼기" className="grid h-7 w-7 place-items-center rounded-md text-foreground-subtle hover:bg-foreground/10 disabled:opacity-30"
                      title={required(p) ? "필수 쪽(공정 컨셉·주요 항목·견적)은 뺄 수 없습니다" : undefined}
                      disabled={pages.length <= 1 || locked || required(p)} onClick={() => changePages(pages.filter((_, j) => j !== i))}>
                <Icon name="x" className="h-3.5 w-3.5" />
              </button>
            </div>
          </li>
        ))}
      </ol>

      <div className="flex flex-col gap-2">
        <h3 className="text-[14px] font-semibold text-foreground">견적 구성 ({lines.length}행)</h3>
        <p className="text-[12px] text-foreground-subtle">
          단가는 근거(견적서·단가표 등)를 함께 적을 때만 들어갑니다. 비워 두면 &lsquo;별도 협의&rsquo;로 표시되고, 비교안이 있으면 비교안끼리는 합산하지 않습니다.
        </p>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-[12.5px]">
            <thead>
              <tr className="text-left text-[11.5px] text-foreground-subtle">
                <th className="px-1 py-1">구분</th><th className="px-1">컨셉</th><th className="px-1">항목</th>
                <th className="px-1">수량</th><th className="px-1">단위</th><th className="px-1">단가(원)</th>
                <th className="px-1">단가 근거</th><th className="px-1">포함</th><th />
              </tr>
            </thead>
            <tbody>
              {lines.map((l, i) => (
                <tr key={i} className="border-t border-border/60">
                  <td className="px-1 py-1">
                    <select className={inputCls} value={l.group} disabled={locked} aria-label="구분"
                            onChange={(e) => changeLine(i, { group: e.target.value as QuoteLine["group"],
                              alt_id: e.target.value === "대안" ? l.alt_id ?? project.alternatives[0]?.id ?? null : null })}>
                      {GROUPS.map((g) => <option key={g} value={g}>{GROUP_LABEL[g] ?? g}</option>)}
                    </select>
                  </td>
                  <td className="px-1">
                    {l.group === "대안" ? (
                      <select className={inputCls} value={l.alt_id ?? ""} disabled={locked} aria-label="컨셉"
                              onChange={(e) => changeLine(i, { alt_id: e.target.value || null })}>
                        <option value="">선택</option>
                        {project.alternatives.map((a) => <option key={a.id} value={a.id}>{a.id} {altName(a.id)}</option>)}
                      </select>
                    ) : <span className="text-foreground-subtle">—</span>}
                  </td>
                  <td className="px-1"><input className={inputCls} value={l.item} maxLength={60} disabled={locked} aria-label="항목"
                                             onChange={(e) => changeLine(i, { item: e.target.value })} /></td>
                  <td className="w-16 px-1"><input className={inputCls} value={l.qty} maxLength={10} disabled={locked} aria-label="수량"
                                                  onChange={(e) => changeLine(i, { qty: e.target.value })} /></td>
                  <td className="w-16 px-1"><input className={inputCls} value={l.unit} maxLength={6} disabled={locked} aria-label="단위"
                                                  onChange={(e) => changeLine(i, { unit: e.target.value })} /></td>
                  <td className="w-28 px-1"><input className={inputCls} inputMode="numeric" disabled={locked} aria-label="단가"
                                                  value={l.unit_price ?? ""} placeholder="별도 협의"
                                                  onChange={(e) => {
                                                    const v = e.target.value.replace(/[^\d.]/g, "");
                                                    changeLine(i, { unit_price: v ? Number(v) : null });
                                                  }} /></td>
                  <td className="px-1"><input className={inputCls} value={l.basis} maxLength={120} disabled={locked} aria-label="단가 근거"
                                             placeholder={l.unit_price ? "근거 필수" : ""}
                                             onChange={(e) => changeLine(i, { basis: e.target.value })} /></td>
                  <td className="px-1 text-center"><input type="checkbox" checked={l.included} disabled={locked} aria-label="포함"
                                                          onChange={(e) => changeLine(i, { included: e.target.checked })} /></td>
                  <td className="px-1">
                    <button type="button" aria-label="행 삭제" className="grid h-7 w-7 place-items-center rounded-md text-foreground-subtle hover:bg-foreground/10"
                            disabled={locked} onClick={() => { setLines((ls) => ls.filter((_, j) => j !== i)); setDirty(true); }}>
                      <Icon name="x" className="h-3.5 w-3.5" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div>
          <button type="button" className={secondaryBtn} disabled={locked || lines.length >= MAX_QUOTE}
                  onClick={() => { setLines((ls) => [...ls, { ...EMPTY_LINE }]); setDirty(true); }}>
            행 추가
          </button>
        </div>
      </div>

      <ErrorBox error={error} />
      <div className="flex flex-wrap items-center justify-end gap-2">
        {busy && <span className="text-[12px] text-foreground-subtle">{busy}…</span>}
        <button type="button" className={secondaryBtn} disabled={!!busy || locked}
                onClick={() => void run("되돌리는 중", "back", { stage: "concept" })}>
          공정·컨셉으로 되돌리기
        </button>
        <button type="button" className={secondaryBtn} disabled={!dirty || !!busy || locked}
                onClick={() => void run("저장 중", "structure", { pages, quote_lines: lines }, () => setDirty(false))}>
          구성 저장
        </button>
        <button type="button" className={primaryBtn} disabled={dirty || !!busy || locked}
                title={dirty ? "구성을 먼저 저장하세요" : undefined}
                onClick={() => void run("승인 중", "approve-structure")}>
          <Icon name="check" className="h-4 w-4" /> 구성 승인하고 제안서 만들기
        </button>
      </div>
    </section>
  );
}
