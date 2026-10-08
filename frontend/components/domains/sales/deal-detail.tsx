// 영업 건 상세 — 오른쪽 패널. 진행 단계 + 상태 변경(수주 확정·계산서·입금·출하) + 품목·결제 회차·출하 사진·변경 이력.
// 단계는 직접 고르지 않는다 — 기록을 넣으면 backend 가 단계를 다시 계산하고, 리스트도 새로 받는다.
// 삭제·되돌리기·취소(기록을 지우는 일)는 관리자만 — 버튼을 관리자에게만 보이고, 실제 막는 것은 backend(사용자 2026-10-08).
"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { DealMeetingDialog, hasMeeting } from "@/components/domains/sales/deal-meeting";
import { DownloadDialog, PreviewDialog, savedContact } from "@/components/domains/sales/quote-workspace";
import { Icon } from "@/components/shared/ui/icon";
import { ACTION_LABEL, Field, INPUT, Muted, StageChip, dealRequest, eventDetail, hasVersion, quoteVer, shortDate, stamp, todayIso, won } from "@/components/domains/sales/deal-format";
import type { DealEvent, DealRow, DealShipment, PoCompare } from "@/lib/shared/sales-deals";
import { cn } from "@/lib/shared/utils";

const STEPS = ["제품 추천 확정", "견적", "수주", "거래명세서", "출하", "입금 완료"] as const;
const CARRIERS = ["로젠택배", "경동택배", "직납", "배차", "기타"];


type Act = (path: string, body: unknown) => Promise<boolean>;
/** 관리자 여부 — 지우기·되돌리기 버튼을 보일지(리스트의 me.admin) */
const AdminCtx = createContext(false);

export function DealDetail({ row, admin = false, onClose, onChanged, onMeeting, onOrder, onQuote }: {
  row: DealRow;
  /** 관리자 — 삭제·되돌리기·입금 확인 취소·드랍 되살리기 버튼을 보인다 */
  admin?: boolean;
  onClose: () => void;
  onChanged: () => void;
  /** [미팅 정보] — 최종 제안 확정 기록 열기(회사 제품 추천 탭에서 넘김). */
  onMeeting?: (proposalId: number) => void;
  /** [수주 진행 →] — 수주 확인 뒤 AI 와 대화하는 수주 진행 탭으로. */
  onOrder?: (dealId: number) => void;
  /** [견적서 고치기 →] — 견적서 탭 작업 화면으로(덮어쓰기·수정본 발행). */
  onQuote?: (quoteId: number) => void;
}) {
  const [deal, setDeal] = useState<DealRow>(row);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [photo, setPhoto] = useState<string | null>(null);
  const [meetingOpen, setMeetingOpen] = useState(false);
  /** 지금 AI 가 품목을 대조 중인 발주서 순번 */
  const [comparing, setComparing] = useState<number | null>(null);

  // 리스트 행에는 변경 이력이 없어 한 건을 다시 받는다
  useEffect(() => {
    let alive = true;
    dealRequest(`/${row.id}`).then((d) => { if (alive) setDeal(d); }).catch(() => {});
    return () => { alive = false; };
  }, [row.id]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (photo) setPhoto(null);
      else onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [photo, onClose]);

  const act: Act = async (path, body) => {
    setBusy(true);
    setError(null);
    try {
      setDeal(await dealRequest(`/${deal.id}/${path}`, body));
      onChanged();
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : "저장하지 못했습니다.");
      return false;
    } finally {
      setBusy(false);
    }
  };

  /** 발주서 파일 올리기 — 본문이 JSON 이 아니라 파일이라 act 와 따로. */
  const uploadPo = async (file: File): Promise<boolean> => {
    setBusy(true);
    setError(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await fetch(`/api/sales-deals/${deal.id}/po`, { method: "POST", body: fd });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof data?.detail === "string" ? data.detail : "발주서를 올리지 못했습니다.");
      setDeal(data as DealRow);
      onChanged();
      // 올린 발주서는 바로 견적 품목과 대조한다(사용자 2026-10-08) — 결과는 발주서 아래에
      const n = (data as DealRow).po_files?.length ?? 0;
      if (n > 0 && (data as DealRow).quote?.items.length) void comparePo(n - 1);
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : "발주서를 올리지 못했습니다.");
      return false;
    } finally {
      setBusy(false);
    }
  };

  /** 발주서 ↔ 견적 품목 AI 대조 — 오래 걸릴 수 있어 다른 버튼은 막지 않는다. */
  const comparePo = async (index: number) => {
    setComparing(index);
    setError(null);
    try {
      setDeal(await dealRequest(`/${row.id}/po/${index}/compare`, {}));
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "발주서를 대조하지 못했습니다.");
    } finally {
      setComparing(null);
    }
  };

  const reviews = deal.alerts.filter((a) => a.level === "review");
  const alerts = deal.alerts.filter((a) => a.level === "alert");
  const done = [!!deal.proposal_id, !!deal.quote, !!deal.won, !!deal.statement, deal.shipped, deal.paid_full];
  const isShipOnly = deal.kind === "shipment_only";

  return (
    <AdminCtx.Provider value={admin}>
    <div className="fixed inset-0 z-40 flex justify-end bg-black/25" onClick={onClose}>
      <aside role="dialog" aria-label={`${deal.display_no ?? deal.deal_no ?? "번호 미부여"} 상세`} onClick={(e) => e.stopPropagation()}
             className="scroll-thin h-full w-full max-w-[680px] overflow-y-auto bg-background shadow-2xl">
        <div className="sticky top-0 z-10 border-b border-border bg-background/95 px-5 py-4 backdrop-blur">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="font-mono text-[12px] text-foreground-subtle">
                {deal.display_no ?? deal.deal_no ?? "번호 미부여"}{deal.legacy_no && ` · 옛 번호 ${deal.legacy_no}`}
                {hasVersion(deal) && <span className="ml-1.5">(견적서 번호 {deal.deal_no} · 뒤 번호는 사내용 히스토리 번호 — 바뀔 때마다 +1, 날짜가 바뀌면 그 날짜로)</span>}
              </div>
              <h2 className="mt-0.5 truncate text-[18px] font-bold">{deal.customer ?? "고객사 미기재"}</h2>
              <p className="mt-0.5 text-[13px] text-foreground-muted">{deal.title}</p>
              {!deal.imported && deal.kind !== "shipment_only" && (
                <button type="button" onClick={() => (deal.proposal_id && onMeeting ? onMeeting(deal.proposal_id) : setMeetingOpen(true))}
                        className={cn("mt-1.5 rounded-md border px-2 py-0.5 text-[12px] font-medium hover:bg-accent/8",
                          hasMeeting(deal) ? "border-border text-accent" : "border-dashed border-border text-foreground-subtle")}>
                  {hasMeeting(deal) ? "미팅 정보 보기" : "미팅 정보 없음 — 직접 입력"}
                </button>
              )}
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <StageChip stage={deal.stage} version={quoteVer(deal.stage, deal.quote?.revision)} className="px-2.5 py-1" />
              <button type="button" onClick={onClose} aria-label="닫기"
                      className="grid h-8 w-8 place-items-center rounded-lg text-foreground-muted hover:bg-foreground/8">✕</button>
            </div>
          </div>
          {!isShipOnly && (
            <ol className="mt-3 flex items-center gap-1 text-[12px]">
              {STEPS.map((s, i) => (
                <li key={s} className={cn("flex-1 rounded-md px-2 py-1 text-center font-medium",
                  done[i] ? "bg-accent/15 text-accent" : "bg-foreground/5 text-foreground-subtle")}>
                  {done[i] ? "✓ " : ""}{s}
                </li>
              ))}
            </ol>
          )}
          {error && <p className="mt-3 rounded-lg bg-red-500/10 px-3 py-2 text-[13px] text-red-700 dark:text-red-300">{error}</p>}
        </div>

        <div className="flex flex-col gap-6 px-5 py-5 text-[13px]">
          {(alerts.length > 0 || reviews.length > 0) && (
            <Section title="알림 · 확인 필요">
              <ul className="flex flex-col gap-1.5">
                {alerts.map((a) => (
                  <li key={a.code} className="rounded-lg bg-red-500/8 px-3 py-2 text-red-700 dark:text-red-300">{a.text}</li>
                ))}
                {reviews.map((a) => (
                  <li key={a.code} className="rounded-lg bg-sky-500/10 px-3 py-2 text-sky-800 dark:text-sky-200">⚠ {a.text}</li>
                ))}
              </ul>
              {reviews.length > 0 && (
                <button type="button" disabled={busy} onClick={() => act("review", { done: true })}
                        className="mt-2 rounded-lg border border-sky-500/40 px-3 py-1.5 text-[12.5px] font-semibold text-sky-700 hover:bg-sky-500/10 dark:text-sky-300">
                  ✓ 확인 완료 (이관 내용 맞음)
                </button>
              )}
            </Section>
          )}
          {deal.review_done && deal.review_alerts.length > 0 && (
            <p className="-mt-3 text-[12px] text-foreground-subtle">
              이관 확인 완료됨 ·{" "}
              <button type="button" className="underline" disabled={busy} onClick={() => act("review", { done: false })}>되돌리기</button>
            </p>
          )}

          {!isShipOnly && <DropBlock deal={deal} busy={busy} act={act} />}
          {!isShipOnly && !deal.dropped && <WonBlock deal={deal} busy={busy} act={act} uploadPo={uploadPo} comparing={comparing} comparePo={comparePo} />}
          {!isShipOnly && deal.won && onOrder && deal.kind !== "sales_only" && (
            <button type="button" onClick={() => onOrder(deal.id)}
                    className="-mb-2 flex items-center justify-between rounded-xl border border-accent/50 bg-accent/[0.06] px-4 py-2.5 text-left hover:bg-accent/10">
              <span>
                <b className="text-[13.5px] text-accent">AI 와 수주 진행 →</b>
                <span className="block text-[12px] text-foreground-muted">{orderGuide(deal)}</span>
              </span>
            </button>
          )}

          <Section title="기본 정보">
            <Grid rows={[
              ["영업 담당", deal.owner],
              ["고객 담당자", deal.contact],
              ["견적일", deal.quote?.date ? `${deal.quote.date}${hasVersion(deal) && deal.quote.revision ? ` (V${deal.quote.revision})` : ""}` : null],
              ["발송일", deal.quote?.sent_date],
            ]} />
          </Section>

          {deal.quote && <QuoteTable deal={deal} />}
          {!!deal.quotes?.length && <QuoteDocs deal={deal} onQuote={onQuote} />}
          {deal.kind === "recommend" && !deal.quotes?.length && !deal.dropped && deal.recommendation_id && onQuote && (
            <StartQuote recommendationId={deal.recommendation_id} onQuote={onQuote} />
          )}
          {deal.statement && <StatementDoc deal={deal} />}

          {!isShipOnly && deal.flow?.terms.length ? <TermsView deal={deal} /> : !isShipOnly && (
            <Section title="결제 회차">
              {deal.pay_terms.length ? (
                <ul className="flex flex-wrap gap-2">
                  {deal.pay_terms.map((t, i) => (
                    <li key={i} className="rounded-lg border border-border px-3 py-1.5">
                      <b>{t.label}</b> {t.pct}%{t.when ? ` · ${t.when}` : ""}
                    </li>
                  ))}
                </ul>
              ) : <Muted>결제조건에서 회차를 찾지 못했습니다(별도 협의 등).</Muted>}
            </Section>
          )}

          {!isShipOnly && <InvoiceHistory deal={deal} />}
          {!isShipOnly && <PaymentBlock deal={deal} busy={busy} act={act} />}
          <ShipmentBlock deal={deal} busy={busy} act={act} onPhoto={setPhoto} />

          {deal.note && (
            <Section title="비고">
              <p className="whitespace-pre-line rounded-lg bg-foreground/[0.04] px-3 py-2 text-[12.5px] text-foreground-muted">{deal.note}</p>
            </Section>
          )}

          {deal.imported && deal.legacy?.source && (
            <Section title="원본 위치">
              <p className="text-[12.5px] text-foreground-muted">
                {deal.legacy.file} · {deal.legacy.source.row_start === deal.legacy.source.row_end
                  ? `${deal.legacy.source.row_start}행` : `${deal.legacy.source.row_start}~${deal.legacy.source.row_end}행`}
              </p>
            </Section>
          )}

          <Section title="변경 이력">
            {deal.events?.length ? <EventList events={deal.events} /> : <Muted>불러오는 중…</Muted>}
          </Section>
        </div>
      </aside>

      {meetingOpen && <DealMeetingDialog deal={deal} onClose={() => setMeetingOpen(false)}
                                         onSaved={(row) => { setDeal({ ...deal, ...row }); onChanged(); }} />}
      {photo && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-black/80 p-4" onClick={(e) => { e.stopPropagation(); setPhoto(null); }}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={photo.startsWith("/") ? photo : `/api/sales-deals/photos/${photo}`} alt="출하 사진" className="max-h-full max-w-full rounded-lg" />
        </div>
      )}
    </div>
    </AdminCtx.Provider>
  );
}

/** 드랍 사유 입력 칸 — 아래 '이 건 드랍하기'와 수주 확인의 [드랍]이 같이 쓴다. */
function DropForm({ busy, act, onDone }: { busy: boolean; act: Act; onDone: () => void }) {
  const [reason, setReason] = useState("");
  return (
    <FormBox onCancel={onDone} busy={busy} canSave={!busy}
             onSave={async () => { if (await act("drop", { dropped: true, reason: reason.trim() || null })) onDone(); }}>
      <Field label="드랍 사유 (선택) — 수주까지 못 간 건·진행을 접은 건은 '드랍' 단계로 따로 모입니다">
        <input value={reason} maxLength={200} onChange={(e) => setReason(e.target.value)} placeholder="예: 경쟁사 수주, 고객사 일정 무기한 연기" className={INPUT} />
      </Field>
    </FormBox>
  );
}

/** 드랍(수주까지 못 간 건·진행을 접음) — 누구나, 되살리기는 관리자만. 실주는 없애고 드랍 하나로(사용자 2026-10-08). */
function DropBlock({ deal, busy, act }: { deal: DealRow; busy: boolean; act: Act }) {
  const admin = useContext(AdminCtx);
  const [open, setOpen] = useState(false);
  if (deal.dropped) {
    return (
      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-zinc-400/50 bg-zinc-500/[0.06] px-4 py-2.5 text-[12.5px]">
        <b>드랍한 건</b>
        <span className="text-foreground-muted">{deal.drop_reason ? `사유: ${deal.drop_reason}` : "사유 없음"}</span>
        {admin && <GhostBtn disabled={busy} onClick={() => act("drop", { dropped: false })}>되살리기</GhostBtn>}
      </div>
    );
  }
  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)} disabled={busy}
              className="-mb-3 self-end text-[12px] text-foreground-subtle hover:text-foreground hover:underline">이 건 드랍하기</button>
    );
  }
  return <DropForm busy={busy} act={act} onDone={() => setOpen(false)} />;
}

/** 입금기한 글 — '2026년 11월 30일까지' */
function dueText(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return `${y}년 ${m}월 ${d}일까지`;
}

/** 결제 조건 회차 — 선금·중도금·잔금마다 비율·시점·금액(공급가액)·입금기한·확인 여부(사용자 2026-10-08: 몇 월까지 입금인지 보이게).
 *  입금 확인은 수주 진행 화면에서 버튼으로. */
function TermsView({ deal }: { deal: DealRow }) {
  const late = new Map((deal.flow?.overdue ?? []).map((t) => [t.index, t.days]));
  return (
    <Section title="결제 조건 · 입금기한 (공급가액)">
      <table className="w-full border-collapse text-[12.5px]">
        <thead>
          <tr className="border-b border-border text-left text-[11.5px] text-foreground-muted">
            {["회차", "시점", "금액", "입금기한", "입금"].map((h) => (
              <th key={h} className={cn("whitespace-nowrap px-3 py-1.5 font-semibold", h === "금액" && "text-right")}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {deal.flow!.terms.map((t) => (
            <tr key={t.index} className={cn("border-b border-border/60 last:border-0",
              t.confirmed ? "bg-emerald-500/[0.06]" : late.has(t.index) && "bg-red-500/[0.07]")}>
              <td className="whitespace-nowrap px-3 py-1.5"><b>{t.label} {t.pct}%</b></td>
              <td className="px-3 py-1.5 text-foreground-muted">{t.when || <Muted>-</Muted>}</td>
              <td className="whitespace-nowrap px-3 py-1.5 text-right font-semibold tabular-nums">{won(t.amount)}원</td>
              <td className={cn("whitespace-nowrap px-3 py-1.5", late.has(t.index) ? "font-semibold text-red-600 dark:text-red-400" : "")}>
                {t.expected_date ? dueText(t.expected_date) : <Muted>미정</Muted>}
                {late.has(t.index) && <span className="ml-1">· {late.get(t.index)}일 지남</span>}
              </td>
              <td className={cn("whitespace-nowrap px-3 py-1.5", t.confirmed ? "text-emerald-700 dark:text-emerald-300" : "text-foreground-subtle")}>
                {t.confirmed ? `✓ ${shortDate(t.paid_date)} 입금` : "입금 전"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Section>
  );
}

/** 영업 건 상세의 수주 진행 안내 한 줄 — 단계 버튼은 수주 진행 탭에서만(사용자 2026-10-07: 상세에 같이 있으면 지저분). */
function orderGuide(deal: DealRow): string {
  const steps = deal.flow?.steps ?? [];
  const next = steps.find((s) => s.ready);
  if (!steps.length || !deal.statement) return "거래명세서 발급부터 시작합니다 — 이어서 결제 조건 → 입금 확인 → 출하를 AI 와 대화로 진행합니다.";
  if (!next) return steps.every((s) => s.done) ? "모든 절차가 끝났습니다 — 출하·완납 완료." : "진행 중 — 수주 진행 화면에서 이어서 합니다.";
  return `다음: ${next.label} — 수주 진행 화면에서 AI 와 이어서 진행합니다.`;
}

// ── 수주 확인 ─────────────────────────────────────────────────────────────

// 발주서로 오는 형식(backend api_sales_deals.PO_TYPES 와 같게)
const PO_ACCEPT = ".pdf,.png,.jpg,.jpeg,.webp,.xlsx,.xls,.docx,.doc,.hwp,.hwpx";
const PO_MAX_MB = 20;

function fileSize(n: number): string {
  return n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)}MB` : `${Math.max(1, Math.round(n / 1024))}KB`;
}

function WonBlock({ deal, busy, act, uploadPo, comparing, comparePo }: {
  deal: DealRow; busy: boolean; act: Act; uploadPo: (f: File) => Promise<boolean>;
  comparing: number | null; comparePo: (index: number) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [date, setDate] = useState(todayIso());
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [reset, setReset] = useState(false);
  // [드랍] — 수주까지 못 간 건(실주 대신, 사용자 2026-10-08)
  const [dropping, setDropping] = useState(false);
  const admin = useContext(AdminCtx);
  const files = deal.po_files ?? [];

  const pick = (f: File | null) => {
    setFileError(f && f.size > PO_MAX_MB * 1024 * 1024 ? `발주서 파일은 ${PO_MAX_MB}MB 까지 올릴 수 있습니다.` : null);
    setFile(f);
  };
  // 수주 확정 → (발주서가 있으면) 이어서 올린다. 올리기에 실패해도 수주는 확정된 상태로 두고 아래에서 다시 첨부할 수 있다.
  const confirm = async () => {
    if (!(await act("won", { won: true, date }))) return;
    if (file) await uploadPo(file);
    setOpen(false);
    setFile(null);
  };

  return (
    <Section title="수주 확인">
      <div className="flex flex-wrap items-center gap-2">
        {deal.won === null && !open && !deal.dropped && !dropping && (
          <>
            <span className="mr-1 text-foreground-muted">수주 결과 미확인</span>
            <PrimaryBtn disabled={busy} onClick={() => { setDate(todayIso()); pick(null); setOpen(true); }}>✓ 수주 확정</PrimaryBtn>
            <GhostBtn disabled={busy} onClick={() => setDropping(true)}>드랍</GhostBtn>
          </>
        )}
        {deal.won === true && (
          <>
            <span className="rounded-lg bg-blue-500/12 px-3 py-1.5 font-semibold text-blue-700 dark:text-blue-300">
              ✓ 수주 확정{deal.won_date ? ` · ${deal.won_date}` : ""}
            </span>
            {admin && <GhostBtn disabled={busy} onClick={() => setReset(true)}>미확인으로 되돌리기</GhostBtn>}
          </>
        )}
      </div>
      {dropping && !deal.dropped && <div className="mt-2"><DropForm busy={busy} act={act} onDone={() => setDropping(false)} /></div>}

      {reset && admin && deal.won === true && (
        <div className="mt-2 rounded-xl border border-amber-500/40 bg-amber-500/[0.06] p-3 text-[12.5px]">
          <p className="mb-2 font-semibold">미확인으로 되돌리기</p>
          <div className="flex flex-col gap-1.5">
            <button type="button" disabled={busy} onClick={async () => { if (await act("won", { won: null })) setReset(false); }}
                    className="rounded-lg border border-border px-3 py-1.5 text-left hover:bg-foreground/5 disabled:opacity-40">
              <b>수주 표시만 되돌리기</b>
              <span className="block text-[11.5px] text-foreground-muted">거래명세서·입금·출하 기록은 그대로 둡니다.</span>
            </button>
            {!deal.imported && (
              <button type="button" disabled={busy} onClick={async () => { if (await act("reset-to-quote", {})) setReset(false); }}
                      className="rounded-lg border border-red-500/40 px-3 py-1.5 text-left text-red-700 hover:bg-red-500/5 disabled:opacity-40 dark:text-red-300">
                <b>(테스트용) 견적 상태로 전부 되돌리기</b>
                <span className="block text-[11.5px] opacity-80">
                  수주 이후의 발주서·거래명세서·결제 조건·입금·출하(사진 포함)·수주 진행 대화와 그 이력을 모두 지우고, 견적서 발행 후 수주를 기다리는 상태로 돌립니다.
                  제품 추천 확정·견적서는 그대로입니다. 되돌릴 수 없습니다.
                </span>
              </button>
            )}
            <button type="button" className="self-start text-[12px] text-foreground-subtle hover:underline" onClick={() => setReset(false)}>취소</button>
          </div>
        </div>
      )}

      {open && deal.won === null && (
        <FormBox onCancel={() => setOpen(false)} onSave={confirm} canSave={!!date && !fileError && !busy} busy={busy}>
          <div className="grid gap-2 md:grid-cols-[160px_1fr]">
            <Field label="수주일"><input type="date" value={date} onChange={(e) => setDate(e.target.value)} className={INPUT} /></Field>
            <Field label="발주서 (고객사 주문서 · 선택)">
              <input type="file" accept={PO_ACCEPT} onChange={(e) => pick(e.target.files?.[0] ?? null)}
                     className="text-[12.5px] file:mr-2 file:rounded-md file:border file:border-border file:bg-surface file:px-2.5 file:py-1" />
            </Field>
          </div>
          <p className={cn("mt-1 text-[11.5px]", fileError ? "text-red-600 dark:text-red-400" : "text-foreground-subtle")}>
            {fileError ?? `PDF·사진·엑셀·워드·한글 파일, ${PO_MAX_MB}MB 까지. 발주서가 아직 없으면 비워 두고 나중에 첨부할 수 있습니다.`}
          </p>
        </FormBox>
      )}

      {(deal.won === true || files.length > 0) && (
        <div className="mt-3">
          <div className="mb-1.5 flex items-center justify-between">
            <span className="text-[12.5px] font-semibold text-foreground">발주서</span>
            <label className={cn("cursor-pointer rounded-lg border border-border px-2.5 py-1 text-[12px] font-semibold text-accent hover:bg-accent/5",
                                 busy && "pointer-events-none opacity-40")}>
              + 발주서 첨부
              <input type="file" accept={PO_ACCEPT} className="hidden" disabled={busy}
                     onChange={(e) => {
                       const f = e.target.files?.[0];
                       e.target.value = "";  // 같은 파일을 다시 골라도 올라가게
                       if (f) void uploadPo(f);
                     }} />
            </label>
          </div>
          {files.length === 0
            ? <Muted>첨부한 발주서 없음</Muted>
            : files.map((f, i) => (
                <EntryCard key={`${f.filename}-${f.uploaded_at}`} busy={busy}
                           onRemove={() => act("remove", { kind: "po_files", index: i })}>
                  <a href={`/api/sales-deals/${deal.id}/po/${i}`} target="_blank" rel="noreferrer"
                     className="font-medium text-accent underline-offset-2 hover:underline">{f.filename}</a>
                  <div className="mt-0.5 text-[12px] text-foreground-subtle">
                    {fileSize(f.size)} · {f.uploaded_at.slice(0, 10)}{f.uploaded_by ? ` · ${f.uploaded_by}` : ""}
                  </div>
                  {deal.quote && (
                    <PoCompareCard result={f.compare ?? null} running={comparing === i}
                                   disabled={comparing !== null} onRun={() => void comparePo(i)} />
                  )}
                </EntryCard>
              ))}
        </div>
      )}
    </Section>
  );
}

const VERDICT_TONE: Record<string, string> = {
  일치: "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300",
  다름: "bg-red-500/12 text-red-700 dark:text-red-300",
  "확인 필요": "bg-amber-500/15 text-amber-800 dark:text-amber-200",
  "발주서에 없음": "bg-red-500/12 text-red-700 dark:text-red-300",
  "견적에 없음": "bg-red-500/12 text-red-700 dark:text-red-300",
};

/** 발주서 ↔ 견적 품목 대조 결과(사용자 2026-10-08) — 품목은 사내 AI 가 읽고, 수량·단가 비교는 서버 코드가 했다. */
function PoCompareCard({ result, running, disabled, onRun }: {
  result: PoCompare | null; running: boolean; disabled: boolean; onRun: () => void;
}) {
  const [open, setOpen] = useState(false);
  const ok = result?.status === "ok";
  const allSame = ok && !!result.lines?.length && result.lines.every((l) => l.verdict === "일치") && result.totals?.same !== false;
  return (
    <div className="mt-2 rounded-lg border border-border bg-foreground/[0.02] px-2.5 py-2 text-[12.5px]">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold">견적 품목 대조</span>
        {running ? (
          <span className="text-foreground-muted">AI 가 발주서 품목을 읽고 견적과 맞춰 보는 중… (1분 안팎)</span>
        ) : result ? (
          <span className={cn(allSame ? "text-emerald-700 dark:text-emerald-300" : ok ? "text-red-700 dark:text-red-300" : "text-foreground-muted")}>
            {allSame ? "✓ " : ok ? "⚠ " : ""}{result.summary}
          </span>
        ) : <Muted>아직 대조하지 않았습니다.</Muted>}
        <span className="ml-auto flex items-center gap-2">
          {ok && !running && (
            <button type="button" className="text-accent hover:underline" onClick={() => setOpen((v) => !v)}>
              {open ? "접기" : "품목별 보기"}
            </button>
          )}
          <button type="button" disabled={disabled} onClick={onRun}
                  className="rounded-md border border-border px-2 py-0.5 font-semibold text-accent hover:bg-accent/5 disabled:opacity-40">
            {result ? "다시 대조" : "대조하기"}
          </button>
        </span>
      </div>
      {ok && open && !running && (
        <div className="mt-2 overflow-x-auto">
          <table className="w-full min-w-[520px] border-collapse text-[12px]">
            <thead>
              <tr className="border-b border-border text-left text-foreground-muted">
                <th className="py-1 pr-2 font-semibold">견적</th>
                <th className="py-1 pr-2 font-semibold">발주서</th>
                <th className="py-1 font-semibold">결과</th>
              </tr>
            </thead>
            <tbody>
              {result.lines!.map((l, i) => (
                <tr key={i} className="border-b border-border/60 align-top last:border-0">
                  <td className="py-1 pr-2">
                    {l.quote
                      ? <>{l.quote.name}<div className="text-foreground-subtle">{l.quote.qty ?? "-"}개 · {won(l.quote.unit_price)}원</div></>
                      : <Muted>-</Muted>}
                  </td>
                  <td className="py-1 pr-2">
                    {l.po
                      ? <>{l.po.name}{l.po.spec ? <span className="text-foreground-subtle"> · {l.po.spec}</span> : null}
                          <div className="text-foreground-subtle">{l.po.qty ?? "?"}개 · {l.po.unit_price == null ? "단가 ?" : `${won(l.po.unit_price)}원`}</div></>
                      : <Muted>-</Muted>}
                  </td>
                  <td className="py-1">
                    <span className={cn("rounded px-1.5 py-0.5 font-semibold", VERDICT_TONE[l.verdict])}>{l.verdict}</span>
                    {l.notes.map((n) => <div key={n} className="mt-0.5 text-foreground-muted">{n}</div>)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {result.totals && (
            <p className="mt-1.5 text-foreground-muted">
              합계(공급가액) 견적 {won(result.totals.quote)}원 · 발주서 {result.totals.po == null ? "읽지 못함" : `${won(result.totals.po)}원`}
              {result.totals.same === false && <b className="ml-1 text-red-700 dark:text-red-300">— 다름</b>}
            </p>
          )}
          <p className="mt-1 text-[11.5px] text-foreground-subtle">
            {result.source === "image" ? "사진·스캔본에서 읽은 숫자라 원문과 한 번 더 확인해 주세요. " : "발주서 원문에 있는 숫자만 썼습니다. "}
            어느 견적 줄과 같은 물건인지는 AI 판단이니, 최종 확인은 담당자가 해 주세요.
          </p>
        </div>
      )}
    </div>
  );
}

// ── 세금계산서(이전 기록) ─────────────────────────────────────────────────
// 세금계산서는 이 프로그램 밖에서 처리한다(사용자 2026-10-07). 기존 엑셀에서 옮긴 발행 기록만 보기 전용으로 보인다.

function InvoiceHistory({ deal }: { deal: DealRow }) {
  if (!deal.invoices.length) return null;
  return (
    <Section title="세금계산서 (이전 기록 · 보기 전용)">
      {deal.invoices.map((iv, i) => (
        <div key={i} className="mb-2 rounded-xl border border-border p-3">
          <Grid rows={[
            ["발행일", iv.date ?? "날짜 없음"],
            ["공급가액 / 부가세", iv.supply != null ? `${won(iv.supply)} / ${won(iv.vat)}` : null],
            ["합계", iv.total != null ? `${won(iv.total)}원` : null],
            ["발행처", iv.issuer_note],
          ]} />
        </div>
      ))}
      <p className="text-[12px] text-foreground-subtle">세금계산서는 이 프로그램 밖에서 처리합니다 — 기존 엑셀에서 옮긴 기록입니다.</p>
    </Section>
  );
}

// ── 입금 ──────────────────────────────────────────────────────────────────

function PaymentBlock({ deal, busy, act }: { deal: DealRow; busy: boolean; act: Act }) {
  const [open, setOpen] = useState(false);
  const [date, setDate] = useState(todayIso());
  const [amount, setAmount] = useState("");
  const [full, setFull] = useState(false);
  const admin = useContext(AdminCtx);
  const byTerms = !!deal.pay_case;                       // 결제 조건 건 — 입금 확인은 회차 버튼(수주 진행 화면)으로
  const expected = deal.statement || deal.pay_case ? deal.flow?.total ?? 0 : deal.invoices.reduce((s, i) => s + (i.total ?? 0), 0);
  const paid = deal.payments.reduce((s, p) => s + (p.amount ?? 0), 0);

  const start = () => {
    const rest = expected - paid;
    setAmount(rest > 0 ? String(rest) : "");
    setDate(todayIso());
    // 이번 입금으로 발행 합계를 다 받고, 결제 회차도 다 발행했으면 완납으로 제안
    // 이번 입금으로 받을 금액을 다 받으면 완납으로 제안
    setFull(rest > 0);
    setOpen(true);
  };
  const save = async () => {
    if (await act("payments", { date, amount: Number(amount.replaceAll(",", "")), full })) setOpen(false);
  };

  return (
    <Section title="입금" action={!open && !byTerms && <AddBtn onClick={start} disabled={busy}>+ 입금 확인</AddBtn>}>
      <div className="mb-2 flex flex-wrap items-center gap-2 text-[12.5px]">
        {deal.paid_full
          ? <span className="rounded-lg bg-emerald-500/12 px-2.5 py-1 font-semibold text-emerald-700 dark:text-emerald-300">완납 ✓</span>
          : <span className="text-foreground-muted">
              {expected ? `${deal.statement || deal.pay_case ? "공급가액" : "받을 금액"} ${won(expected)}원 중 입금 ${won(paid)}원` : "완납 전"}
            </span>}
        {(deal.payments.length > 0 || deal.paid_full) && !byTerms && (deal.paid_full ? admin : true) && (
          <button type="button" disabled={busy} onClick={() => act("paid-full", { full: !deal.paid_full })}
                  className="text-foreground-subtle underline">{deal.paid_full ? "완납 해제" : "완납으로 표시"}</button>
        )}
      </div>
      {deal.payments.length === 0 && !open && <Muted>입금 기록 없음</Muted>}
      {deal.payments.map((p, i) => (
        <EntryCard key={i} onRemove={p.term != null ? undefined : () => act("remove", { kind: "payments", index: i })} busy={busy}>
          <Grid rows={[["입금일", p.date ?? "날짜 없음"], ["입금액", p.amount != null ? `${won(p.amount)}원` : null], ["메모", p.note]]} />
        </EntryCard>
      ))}
      {open && (
        <FormBox onCancel={() => setOpen(false)} onSave={save} busy={busy}
                 canSave={Number(amount.replaceAll(",", "")) > 0 && !!date && !busy}>
          <div className="grid gap-2 md:grid-cols-3">
            <Field label="입금일"><input type="date" value={date} onChange={(e) => setDate(e.target.value)} className={INPUT} /></Field>
            <Field label="입금액"><input value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="numeric" className={cn(INPUT, "text-right")} /></Field>
            <label className="flex items-end gap-1.5 pb-2">
              <input type="checkbox" checked={full} onChange={(e) => setFull(e.target.checked)} /> 이 입금으로 완납
            </label>
          </div>
        </FormBox>
      )}
    </Section>
  );
}

// ── 출하 ──────────────────────────────────────────────────────────────────

function ShipmentBlock({ deal, busy, act, onPhoto }: { deal: DealRow; busy: boolean; act: Act; onPhoto: (n: string) => void }) {
  const last = deal.shipments[deal.shipments.length - 1];
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ date: todayIso(), carrier: CARRIERS[0], tracking: "", items: "", qty_text: "",
    receiver: "", address: "", purpose: "판매" });
  const set = (patch: Partial<typeof f>) => setF((p) => ({ ...p, ...patch }));

  const start = () => {
    // 받는 사람·주소는 이 건의 직전 출하에서 가져온다
    setF({ date: todayIso(), carrier: last?.carrier ?? CARRIERS[0], tracking: "", items: "", qty_text: "",
      receiver: last?.receiver ?? "", address: last?.address ?? "", purpose: "판매" });
    setOpen(true);
  };
  const save = async () => { if (await act("shipments", f)) setOpen(false); };

  return (
    <Section title={`출하${deal.shipments.length ? ` ${deal.shipments.length}회` : ""}`}
             action={!open && deal.kind !== "shipment_only" && !deal.pay_case && <AddBtn onClick={start} disabled={busy}>+ 출하 기록</AddBtn>}>
      {deal.shipments.length === 0 && !open && (
        <Muted>{deal.shipped ? "비고에 '출하완료'만 적혀 있음(출하시트에서 짝을 찾지 못함)" : "출하 기록 없음"}</Muted>
      )}
      {deal.shipments.map((s, i) => (
        <EntryCard key={i} onRemove={deal.kind === "shipment_only" ? undefined : () => act("remove", { kind: "shipments", index: i })} busy={busy}>
          <ShipmentInfo s={s} dealId={deal.id} onPhoto={onPhoto} />
        </EntryCard>
      ))}
      {open && (
        <FormBox onCancel={() => setOpen(false)} onSave={save} busy={busy}
                 canSave={!!f.date && !busy}>
          <div className="grid gap-2 md:grid-cols-3">
            <Field label="출하일"><input type="date" value={f.date} onChange={(e) => set({ date: e.target.value })} className={INPUT} /></Field>
            <Field label="배송">
              <select value={f.carrier} onChange={(e) => set({ carrier: e.target.value })} className={INPUT}>
                {CARRIERS.map((c) => <option key={c}>{c}</option>)}
              </select>
            </Field>
            <Field label="용도">
              <select value={f.purpose} onChange={(e) => set({ purpose: e.target.value })} className={INPUT}>
                {["판매", "개발", "샘플", "기타"].map((c) => <option key={c}>{c}</option>)}
              </select>
            </Field>
            <Field label="운송장 번호 (여러 개는 쉼표로)" wide><input value={f.tracking} onChange={(e) => set({ tracking: e.target.value })} className={INPUT} /></Field>
            <Field label="수량"><input value={f.qty_text} onChange={(e) => set({ qty_text: e.target.value })} className={INPUT} placeholder="예: 1set" /></Field>
            <Field label="출하 품목" wide>
              <textarea value={f.items} onChange={(e) => set({ items: e.target.value })} rows={2} className={cn(INPUT, "h-auto py-2")} />
            </Field>
            <Field label="받는 사람·연락처"><input value={f.receiver} onChange={(e) => set({ receiver: e.target.value })} className={INPUT} /></Field>
            <Field label="주소" wide><input value={f.address} onChange={(e) => set({ address: e.target.value })} className={INPUT} /></Field>
          </div>
        </FormBox>
      )}
    </Section>
  );
}

function ShipmentInfo({ s, dealId, onPhoto }: { s: DealShipment; dealId: number; onPhoto: (name: string) => void }) {
  return (
    <>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <b>{s.date ?? s.date_text ?? "출하일 미기재"}</b>
        {s.date_guessed && <span className="text-[11.5px] text-sky-700 dark:text-sky-300">(연도 추정)</span>}
        {s.purpose && <span className="rounded bg-foreground/8 px-1.5 text-[11.5px]">{s.purpose}</span>}
        {s.carrier && <span className="text-foreground-muted">{s.carrier}</span>}
        {s.forced && <span className="text-[11.5px] text-amber-700 dark:text-amber-300">입금 확인 전 출하</span>}
      </div>
      {s.lines?.length ? (
        <ul className="mb-2 flex flex-col gap-1.5">
          {s.lines.map((ln, i) => (
            <li key={i}>
              <span className="font-medium">{ln.name}</span>{ln.qty_text ? <span className="text-foreground-muted"> {ln.qty_text}</span> : ln.qty != null && <span className="text-foreground-muted"> × {ln.qty}</span>}
              {ln.photos.length > 0 ? (
                <div className="mt-1 flex flex-wrap gap-1.5">
                  {ln.photos.map((p) => {
                    const url = `/api/sales-deals/${dealId}/ship-photos/${p}`;
                    return (
                      <button key={p} type="button" onClick={() => onPhoto(url)} className="overflow-hidden rounded-lg ring-1 ring-border hover:ring-accent">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img src={url} alt={`${ln.name} 출하 사진`} className="h-20 w-20 object-cover" loading="lazy" />
                      </button>
                    );
                  })}
                </div>
              ) : <div className="text-[12px] text-foreground-subtle">사진 없음</div>}
            </li>
          ))}
        </ul>
      ) : null}
      <Grid rows={[
        ["품목", s.lines?.length ? null : s.items],
        ["수량", s.qty_text],
        ["금액", s.amount != null ? `${won(s.amount)}원` : null],
        ["운송장", s.tracking.length ? s.tracking.join(", ") : null],
        ["받는 사람", s.receiver],
        ["주소", s.address],
        ["담당", s.owner],
      ]} />
      {s.photos.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-2">
          {s.photos.map((p) => (
            <button key={p} type="button" onClick={() => onPhoto(p)} className="overflow-hidden rounded-lg ring-1 ring-border hover:ring-accent">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={`/api/sales-deals/photos/${p}`} alt="출하 사진" className="h-24 w-24 object-cover" loading="lazy" />
            </button>
          ))}
        </div>
      )}
    </>
  );
}

// ── 견적서 발행본(사용자 2026-10-07: 발행한 견적은 영업 건에서 관리, 견적서 탭은 작성 중인 것만) ──

function QuoteDocs({ deal, onQuote }: { deal: DealRow; onQuote?: (quoteId: number) => void }) {
  const [saving, setSaving] = useState<{ quote: number; revision: number; fileName: string; title: string } | null>(null);
  return (
    <Section title="견적서">
      {deal.quotes!.map((q) => (
        <div key={q.id} className="mb-2 rounded-xl border border-border p-3">
          <div className="mb-1.5 flex flex-wrap items-center gap-2">
            <b>{q.quote_no ?? "작성 중(발행 전)"}</b>
            {q.quote_no && <span className="text-foreground-muted">최신 v{q.revision + 1}</span>}
            {q.writer && <span className="text-[12px] text-foreground-subtle">작성 {q.writer}</span>}
            {onQuote && (
              <button type="button" onClick={() => onQuote(q.id)}
                      className="ml-auto rounded-lg border border-accent/50 px-2.5 py-1 text-[12px] font-semibold text-accent hover:bg-accent/5">
                {q.status === "issued" ? "견적서 고치기 →" : "이어서 작성 →"}
              </button>
            )}
          </div>
          {q.issues.length === 0 ? <Muted>아직 발행하지 않았습니다.</Muted> : (
            <ul className="flex flex-col gap-1 text-[12.5px]">
              {[...q.issues].reverse().map((h) => (
                <li key={h.revision} className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">v{h.revision + 1}{h.revision ? "" : " (최초 발행)"}</span>
                  <span className="text-foreground-subtle">{h.issued_at.slice(0, 16).replace("T", " ")}</span>
                  <span>{h.currency === "USD" ? `$${h.total.toLocaleString("en-US")}` : `${won(h.total)}원`}</span>
                  <button type="button" className="ml-auto inline-flex items-center gap-1 font-semibold text-accent hover:underline"
                          onClick={() => setSaving({ quote: q.id, revision: h.revision, fileName: h.file_name,
                                                     title: `${q.quote_no ?? "견적서"} v${h.revision + 1} 다시 다운로드` })}>
                    <Icon name="download" className="h-3.5 w-3.5" /> 다시 다운로드
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
      {saving && (
        <DownloadDialog urlFor={(fmt) => `/api/sales-deals/${deal.id}/quote-file?quote=${saving.quote}&revision=${saving.revision}&fmt=${fmt}`}
                        fileName={saving.fileName} title={saving.title} onClose={() => setSaving(null)} />
      )}
    </Section>
  );
}

/** 견적서 발행 전(제품 추천 확정) 건 — 추천 결과로 견적서 초안을 열고 견적서 탭으로(사용자 2026-10-08). */
function StartQuote({ recommendationId, onQuote }: { recommendationId: number; onQuote: (quoteId: number) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await fetch("/api/product-recommend/quotes/draft", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ recommendation_id: recommendationId, form: savedContact() }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof d?.detail === "string" ? d.detail : "견적서 초안을 만들지 못했습니다.");
      onQuote((d as { id: number }).id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "견적서 초안을 만들지 못했습니다.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Section title="견적서">
      <button type="button" disabled={busy} onClick={() => void start()}
              className="flex w-full items-center justify-between rounded-xl border border-accent/50 bg-accent/[0.06] px-4 py-2.5 text-left hover:bg-accent/10 disabled:opacity-60">
        <span>
          <b className="text-[13.5px] text-accent">{busy ? "견적서 초안 만드는 중…" : "견적서 작성 →"}</b>
          <span className="block text-[12px] text-foreground-muted">추천 결과로 초안을 만들고 견적서 화면으로 넘어갑니다. 발행하면 건 번호가 붙고 견적 단계가 됩니다.</span>
        </span>
        <Icon name="file-spreadsheet" className="h-5 w-5 shrink-0 text-accent" />
      </button>
      {error && <p className="mt-2 rounded-lg bg-red-500/10 px-3 py-2 text-[12.5px] text-red-700 dark:text-red-300">{error}</p>}
    </Section>
  );
}

// ── 거래명세서(발급본) — 영업 건에서 확인·다시 받기(사용자 2026-10-07). 고치기·다시 발급은 수주 진행에서. ──

function StatementDoc({ deal }: { deal: DealRow }) {
  const st = deal.statement!;
  const [view, setView] = useState<"preview" | "save" | null>(null);
  const fileName = `(주)유엔디로보틱스_거래명세서_${st.buyer.name}_${st.no}`;
  return (
    <Section title="거래명세서">
      <div className="rounded-xl border border-border p-3">
        <div className="mb-1.5 flex flex-wrap items-center gap-2">
          <b>{st.no}</b>
          <span className="text-foreground-muted">발행 {st.date}</span>
          <span className="ml-auto flex gap-3">
            <button type="button" className="inline-flex items-center gap-1 font-semibold text-accent hover:underline" onClick={() => setView("preview")}>
              <Icon name="eye" className="h-3.5 w-3.5" /> 보기
            </button>
            <button type="button" className="inline-flex items-center gap-1 font-semibold text-accent hover:underline" onClick={() => setView("save")}>
              <Icon name="download" className="h-3.5 w-3.5" /> 다시 다운로드 (현재 발급본)
            </button>
          </span>
        </div>
        <Grid rows={[
          ["공급받는자", `${st.buyer.name}${st.buyer.ceo ? ` (대표 ${st.buyer.ceo})` : ""} · ${st.buyer.reg_no}`],
          ["주소", st.buyer.address],
          ["품목", st.items.map((it) => `${it.name} × ${it.qty ?? "-"}`).join(", ")],
          ["합계", st.totals ? `${won(st.totals.total)}원 (공급가액 ${won(st.totals.supply)} + 부가세 ${won(st.totals.vat)})` : null],
        ]} />
      </div>
      {view === "preview" && (
        <PreviewDialog url={`/api/sales-deals/${deal.id}/statement/file?fmt=pdf`} title="거래명세서" sub={`${st.no} · 발행 ${st.date} · 현재 발급본`}
                       onClose={() => setView(null)} />
      )}
      {view === "save" && (
        <DownloadDialog urlFor={(fmt) => `/api/sales-deals/${deal.id}/statement/file?fmt=${fmt}`} fileName={fileName}
                        title="거래명세서 다시 다운로드" onClose={() => setView(null)} />
      )}
    </Section>
  );
}

// ── 공통 조각 ─────────────────────────────────────────────────────────────

function QuoteTable({ deal }: { deal: DealRow }) {
  const q = deal.quote!;
  return (
    <Section title={`견적 품목 · ${q.vat_included ? "부가세 포함" : "부가세 별도"}`}>
      <table className="w-full border-collapse text-[12.5px]">
        <thead>
          <tr className="border-b border-border text-left text-foreground-muted">
            <th className="py-1.5 pr-2 font-semibold">품목</th>
            <th className="py-1.5 pr-2 text-right font-semibold">단가</th>
            <th className="py-1.5 pr-2 text-right font-semibold">수량</th>
            <th className="py-1.5 text-right font-semibold">금액</th>
          </tr>
        </thead>
        <tbody>
          {q.items.map((it, i) => (
            <tr key={i} className="border-b border-border/60 align-top">
              <td className="whitespace-pre-line py-1.5 pr-2">{it.name || <Muted>-</Muted>}</td>
              <td className="py-1.5 pr-2 text-right tabular-nums">{won(it.unit_price, q.currency)}</td>
              <td className="py-1.5 pr-2 text-right tabular-nums">{it.qty ?? it.qty_text ?? "-"}</td>
              <td className="py-1.5 text-right tabular-nums">
                {won(it.amount ?? (it.unit_price != null ? it.unit_price * (it.qty ?? 1) : null), q.currency)}
              </td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <td colSpan={3} className="py-2 text-right font-semibold">합계</td>
            <td className="py-2 text-right font-bold tabular-nums">{won(q.amount, q.currency)}</td>
          </tr>
        </tfoot>
      </table>
    </Section>
  );
}

function EntryCard({ children, onRemove, busy }: { children: React.ReactNode; onRemove?: () => void; busy: boolean }) {
  const [confirming, setConfirming] = useState(false);
  const admin = useContext(AdminCtx);
  return (
    <div className="relative mb-2 rounded-xl border border-border p-3">
      {onRemove && admin && (
        <div className="absolute right-2 top-2 flex gap-1 text-[12px]">
          {confirming ? (
            <>
              <button type="button" disabled={busy} onClick={() => { setConfirming(false); onRemove(); }}
                      className="rounded px-2 py-0.5 font-semibold text-red-600 hover:bg-red-500/10">삭제</button>
              <button type="button" onClick={() => setConfirming(false)} className="rounded px-2 py-0.5 text-foreground-muted hover:bg-foreground/8">취소</button>
            </>
          ) : (
            <button type="button" onClick={() => setConfirming(true)} className="rounded px-2 py-0.5 text-foreground-subtle hover:bg-foreground/8">
              잘못 입력함
            </button>
          )}
        </div>
      )}
      {children}
    </div>
  );
}

function FormBox({ children, onCancel, onSave, canSave, busy }: {
  children: React.ReactNode; onCancel: () => void; onSave: () => void; canSave: boolean; busy: boolean;
}) {
  return (
    <div className="rounded-xl border border-accent/40 bg-accent/[0.04] p-3">
      {children}
      <div className="mt-3 flex justify-end gap-2">
        <button type="button" onClick={onCancel} className="h-8 rounded-lg border border-border px-3 text-[12.5px]">취소</button>
        <button type="button" onClick={onSave} disabled={!canSave}
                className="h-8 rounded-lg bg-accent px-3 text-[12.5px] font-semibold text-white disabled:opacity-40">
          {busy ? "저장 중…" : "저장"}
        </button>
      </div>
    </div>
  );
}

function EventList({ events }: { events: DealEvent[] }) {
  return (
    <ul className="flex flex-col gap-1 text-[12.5px]">
      {events.map((e, i) => (
        <li key={i} className="flex gap-2">
          <span className="w-[120px] shrink-0 tabular-nums text-foreground-subtle">{stamp(e.created_at)}</span>
          {e.ver_no && <span className="shrink-0 font-mono text-[11.5px] text-foreground-muted">{e.ver_no}</span>}
          <span className="font-medium">{ACTION_LABEL[e.action] ?? e.action}</span>
          <span className="text-foreground-muted">{eventDetail(e)}</span>
          <span className="ml-auto shrink-0 text-foreground-subtle">{e.user_name ?? "시스템"}</span>
        </li>
      ))}
    </ul>
  );
}


function Section({ title, children, action }: { title: string; children: React.ReactNode; action?: React.ReactNode }) {
  return (
    <section>
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-[12px] font-semibold uppercase tracking-[0.1em] text-foreground-subtle">{title}</h3>
        {action}
      </div>
      {children}
    </section>
  );
}

function Grid({ rows }: { rows: [string, string | null | undefined][] }) {
  const shown = rows.filter(([, v]) => v);
  if (!shown.length) return null;
  return (
    <dl className="grid grid-cols-[110px_1fr] gap-x-3 gap-y-1 pr-20">
      {shown.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-foreground-muted">{k}</dt>
          <dd className="whitespace-pre-line break-words">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function PrimaryBtn({ children, ...p }: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button type="button" {...p} className="h-8 rounded-lg bg-accent px-3 text-[12.5px] font-semibold text-white disabled:opacity-40">{children}</button>;
}

function GhostBtn({ children, ...p }: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button type="button" {...p} className="h-8 rounded-lg border border-border px-3 text-[12.5px] hover:bg-foreground/5 disabled:opacity-40">{children}</button>;
}

function AddBtn({ children, ...p }: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button type="button" {...p} className="rounded-lg border border-border px-2.5 py-1 text-[12px] font-semibold text-accent hover:bg-accent/5 disabled:opacity-40">{children}</button>;
}
