// 미팅 정보 직접 입력 — AI 제품 추천 기록이 없는 영업 건(견적서 수기 작성으로 시작한 그리퍼·AMR 등, 사용자 2026-10-08).
// AI 추천으로 시작한 건은 회사 제품 추천의 [미팅 정보](확정 기록)를 쓰고, 이 창은 기록이 없을 때 담당자가 적는다.
"use client";

import { useState } from "react";
import { Field, INPUT, dealRequest, todayIso } from "@/components/domains/sales/deal-format";
import type { DealMeeting, DealRow } from "@/lib/shared/sales-deals";
import { cn } from "@/lib/shared/utils";

const CATEGORIES = ["툴체인저", "그리퍼", "AMR", "로봇 시스템(SI)", "기타"];
const ROWS: [keyof DealMeeting, string][] = [
  ["customer", "고객사"], ["contact", "고객 담당자"], ["meeting_date", "미팅일"], ["writer", "영업 담당"],
  ["category", "제품 분류"], ["robot", "로봇(제조사·모델)"], ["requirements", "요구 사항·미팅 요약"], ["notes", "메모"],
];

export function hasMeeting(r: DealRow): boolean {
  return !!r.proposal_id || !!r.meeting?.updated_at;
}

export function DealMeetingDialog({ deal, onClose, onSaved }: {
  deal: DealRow; onClose: () => void; onSaved: (row: DealRow) => void;
}) {
  const saved = deal.meeting?.updated_at ? deal.meeting : null;
  const [editing, setEditing] = useState(false);
  const [f, setF] = useState<DealMeeting>(() => ({
    customer: saved?.customer ?? deal.customer ?? "", contact: saved?.contact ?? deal.contact ?? "",
    meeting_date: saved?.meeting_date ?? todayIso(), writer: saved?.writer ?? deal.owner ?? "",
    category: saved?.category ?? "", robot: saved?.robot ?? "", requirements: saved?.requirements ?? "", notes: saved?.notes ?? "",
  }));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const set = (k: keyof DealMeeting, v: string) => setF((p) => ({ ...p, [k]: v }));

  async function save() {
    setBusy(true);
    setErr(null);
    try {
      const body = Object.fromEntries(ROWS.map(([k]) => [k, String(f[k] ?? "").trim() || null]));
      onSaved(await dealRequest(`/${deal.id}/meeting`, body));
      setEditing(false);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "저장하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/30 p-3 md:p-8" onClick={onClose}>
      <section role="dialog" aria-label="미팅 정보" onClick={(e) => e.stopPropagation()}
               className="scroll-thin flex max-h-full w-full max-w-2xl flex-col gap-4 overflow-y-auto rounded-2xl border border-border bg-background p-5 shadow-2xl">
        <div className="flex items-start gap-2">
          <div className="min-w-0">
            <h2 className="text-[17px] font-bold text-foreground">미팅 정보</h2>
            <p className="mt-0.5 text-[12px] text-foreground-subtle">
              {deal.display_no ?? deal.deal_no ?? "번호 미부여"} · {deal.customer ?? "고객사 미기재"}
              {saved?.updated_at ? ` · 직접 입력 ${saved.updated_at.slice(0, 10)}` : ""}
            </p>
          </div>
          <button type="button" onClick={onClose} aria-label="닫기"
                  className="ml-auto grid h-8 w-8 place-items-center rounded-lg text-foreground-muted hover:bg-foreground/8">✕</button>
        </div>

        {!editing && !saved && (
          <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed border-border px-4 py-8 text-center">
            <p className="text-[13.5px] font-semibold text-foreground">등록된 미팅 정보가 없습니다</p>
            <p className="text-[12.5px] text-foreground-muted">
              AI 제품 추천 없이 견적서부터 시작한 건입니다. 미팅 내용을 직접 적어 두면 이 건의 프로젝트 기록으로 함께 관리됩니다.
            </p>
            <button type="button" onClick={() => setEditing(true)}
                    className="h-9 rounded-lg bg-accent px-4 text-[13px] font-semibold text-white hover:opacity-90">직접 입력</button>
          </div>
        )}

        {!editing && saved && (
          <>
            <dl className="grid grid-cols-[120px_1fr] gap-x-3 gap-y-2 rounded-xl bg-foreground/[0.03] p-4 text-[13px]">
              {ROWS.map(([k, label]) => (
                <div key={k} className="contents">
                  <dt className="text-foreground-muted">{label}</dt>
                  <dd className="whitespace-pre-wrap text-foreground">{saved[k] || <span className="text-foreground-subtle">-</span>}</dd>
                </div>
              ))}
            </dl>
            <button type="button" onClick={() => setEditing(true)}
                    className="self-end rounded-lg border border-border px-3 py-1.5 text-[12.5px] font-semibold hover:bg-foreground/5">고치기</button>
          </>
        )}

        {editing && (
          <div className="flex flex-col gap-3">
            <div className="grid gap-3 md:grid-cols-2">
              <Field label="고객사"><input value={f.customer ?? ""} maxLength={200} onChange={(e) => set("customer", e.target.value)} className={INPUT} /></Field>
              <Field label="고객 담당자"><input value={f.contact ?? ""} maxLength={200} onChange={(e) => set("contact", e.target.value)} className={INPUT} placeholder="예: 홍길동 과장" /></Field>
              <Field label="미팅일"><input type="date" value={f.meeting_date ?? ""} onChange={(e) => set("meeting_date", e.target.value)} className={INPUT} /></Field>
              <Field label="영업 담당"><input value={f.writer ?? ""} maxLength={100} onChange={(e) => set("writer", e.target.value)} className={INPUT} /></Field>
              <Field label="제품 분류">
                <div className="flex flex-wrap gap-1.5">
                  {CATEGORIES.map((c) => (
                    <button key={c} type="button" onClick={() => set("category", f.category === c ? "" : c)}
                            className={cn("rounded-full border px-2.5 py-1 text-[12px]",
                              f.category === c ? "border-accent bg-accent/10 font-semibold text-accent" : "border-border text-foreground-muted hover:bg-foreground/5")}>
                      {c}
                    </button>
                  ))}
                </div>
              </Field>
              <Field label="로봇(제조사·모델)"><input value={f.robot ?? ""} maxLength={300} onChange={(e) => set("robot", e.target.value)} className={INPUT} placeholder="예: 두산 M1013" /></Field>
            </div>
            <Field label="요구 사항·미팅 요약" wide>
              <textarea value={f.requirements ?? ""} maxLength={2000} rows={4} onChange={(e) => set("requirements", e.target.value)}
                        className={cn(INPUT, "h-auto py-2")} placeholder="공정·대상물·무게·수량·납기·특이사항" />
            </Field>
            <Field label="메모" wide>
              <textarea value={f.notes ?? ""} maxLength={2000} rows={2} onChange={(e) => set("notes", e.target.value)} className={cn(INPUT, "h-auto py-2")} />
            </Field>
            {err && <p className="rounded-lg bg-red-500/10 px-3 py-2 text-[13px] text-red-700 dark:text-red-300">{err}</p>}
            <div className="flex justify-end gap-2">
              <button type="button" onClick={() => (saved ? setEditing(false) : onClose())} disabled={busy}
                      className="h-9 rounded-lg border border-border px-4 text-[13px]">취소</button>
              <button type="button" onClick={() => void save()} disabled={busy}
                      className="h-9 rounded-lg bg-accent px-4 text-[13px] font-semibold text-white disabled:opacity-40">{busy ? "저장 중…" : "저장"}</button>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
