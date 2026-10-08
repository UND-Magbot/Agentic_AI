// 영업 건 관리 리스트 — 한 줄이 한 건(제품 추천 확정 → 견적 → 수주 → 거래명세서 발급·입금 확인 → 출하).
// 세금계산서는 이 프로그램 밖에서 처리한다(사용자 2026-10-07) — 계산서 열·'청구 중' 단계는 없앴다.
// 단계·계산서·입금·출하 칸은 직접 적는 칸이 아니라 각 단계 기록에서 계산된 표시다.
// 최근에 바뀐 건이 위(사용자 2026-10-08). 건(프로젝트) 아래에 히스토리(바뀔 때마다 번호 하나)를 펼쳐 본다. 새 건 등록·상태 변경 후에는 서버 데이터를 다시 받아 리스트를 갱신한다.
// 금액은 모두 공급가액(부가세는 거래명세서에서만). 예정 입금일이 지난 건은 줄을 붉게 강조하고, 하루 한 번 팝업으로 알린다.
// 삭제는 관리자만(버튼도 관리자에게만 — 실제 막는 것은 backend).
"use client";

import { useRouter } from "next/navigation";
import { Fragment, useMemo, useState } from "react";
import { DealDetail } from "@/components/domains/sales/deal-detail";
import { DealMeetingDialog, hasMeeting } from "@/components/domains/sales/deal-meeting";
import { ACTION_LABEL, Muted, STAGE_TONE, StageChip, dealRequest, eventDetail, quoteVer, shortDate, stamp, won } from "@/components/domains/sales/deal-format";
import { DealNewForm } from "@/components/domains/sales/deal-new-form";
import type { DealKpi, DealListData, DealRow, DealStage } from "@/lib/shared/sales-deals";
import { cn } from "@/lib/shared/utils";

const STAGES: DealStage[] = ["제품 추천 확정", "견적", "수주", "출하 후 입금 대기", "완료 (출하·완납)", "드랍", "출하 기록만 (이전)"];

const hasReview = (r: DealRow) => r.alerts.some((a) => a.level === "review");
// 미수 = 받을 일이 생겼는데(거래명세서 발급·출하, 이관 건은 옛 계산서 기록) 아직 완납이 아님
const isUnpaid = (r: DealRow) => (!!r.statement || r.shipped || r.invoice_status.issued > 0) && !!r.won && !r.paid_full && !r.dropped;
const isOverdue = (r: DealRow) => !!r.flow?.overdue?.length;
/** 표시 금액 = 공급가액(flow.total), 없으면 견적액(부가세 포함 견적은 ÷1.1) */
const supplyOf = (r: DealRow) => r.flow?.total ?? (r.quote?.amount == null ? null
  : r.quote.vat_included ? Math.round(r.quote.amount / 1.1) : r.quote.amount);

type Filter = { q: string; owner: string; stage: string; year: string; unpaid: boolean; review: boolean; overdue: boolean };

export function DealList({ data, onRefresh, onMeeting, onOrder, onQuote }: {
  data: DealListData;
  /** 상태 변경 뒤 리스트 다시 받기 — 탭(클라이언트)에서 쓰면 넘기고, 없으면 서버 컴포넌트 새로고침. */
  onRefresh?: () => void;
  /** [미팅 정보] — 최종 제안 확정 기록(미팅·최종 추천·근거·대화) 열기. 없으면 버튼을 숨긴다. */
  onMeeting?: (proposalId: number) => void;
  /** [수주 진행 →] — 수주 확인된 건을 AI 와 대화하는 수주 진행 탭으로 연다. */
  onOrder?: (dealId: number) => void;
  /** [견적서 고치기 →] — 견적서 탭 작업 화면으로. */
  onQuote?: (quoteId: number) => void;
}) {
  const router = useRouter();
  const refresh = onRefresh ?? (() => router.refresh());
  const [f, setF] = useState<Filter>({ q: "", owner: "", stage: "", year: "", unpaid: false, review: false, overdue: false });
  const admin = !!data.me?.admin;
  const overdueRows = useMemo(() => data.rows.filter(isOverdue), [data.rows]);
  // 예정 입금일 지난 건 팝업 — 하루 한 번(닫으면 그날은 다시 안 뜸, 리스트 강조는 그대로).
  // 리스트는 데이터를 받은 뒤 브라우저에서만 그려지므로(deals-tab) 처음 값을 바로 저장소에서 읽는다.
  const [lateOpen, setLateOpen] = useState(() => {
    try { return localStorage.getItem("deals-overdue-seen") !== data.today; } catch { return true; }
  });
  const closeLate = () => {
    setLateOpen(false);
    try { localStorage.setItem("deals-overdue-seen", data.today); } catch { /* 무시 */ }
  };
  const [openId, setOpenId] = useState<number | null>(null);
  // 히스토리를 펼친 건 — 건번호 아래 [히스토리 N]으로 열고 닫는다
  const [shown, setShown] = useState<Set<number>>(new Set());
  const toggleShown = (id: number) => setShown((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  });
  const [creating, setCreating] = useState(false);
  const [meetingRow, setMeetingRow] = useState<DealRow | null>(null);
  // AI 추천으로 시작한 건은 확정 기록(onMeeting), 수기 견적으로 시작한 건은 직접 입력 창(사용자 2026-10-08)
  const showMeeting = (r: DealRow) => (r.proposal_id && onMeeting ? onMeeting(r.proposal_id) : setMeetingRow(r));
  // 삭제 — [삭제]를 누르면 건번호 왼쪽에 체크박스가 나오고, 고른 건들을 한꺼번에 지운다(사용자 2026-10-07)
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
  const set = (patch: Partial<Filter>) => setF((prev) => ({ ...prev, ...patch }));

  const toggle = (id: number) => setSelected((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  });
  const stopSelecting = () => { setSelecting(false); setSelected(new Set()); setConfirming(false); };

  async function deleteSelected() {
    setDeleting(true);
    try {
      const r = await fetch("/api/sales-deals/delete", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids: [...selected] }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof d?.detail === "string" ? d.detail : "삭제하지 못했습니다.");
      if (openId && selected.has(openId)) setOpenId(null);
      setNotice({ ok: true, text: `${d.deleted}건을 삭제했습니다.` });
      stopSelecting();
      refresh();
    } catch (e) {
      setConfirming(false);
      setNotice({ ok: false, text: e instanceof Error ? e.message : "삭제하지 못했습니다." });
    } finally {
      setDeleting(false);
    }
  }

  const owners = useMemo(() => uniq(data.rows.map((r) => normOwner(r.owner))), [data.rows]);
  const years = useMemo(() => uniq(data.rows.map((r) => r.base_date?.slice(0, 4) ?? "")).sort().reverse(), [data.rows]);

  const rows = useMemo(() => {
    const q = f.q.trim().toLowerCase();
    return data.rows.filter((r) => {
      if (f.owner && normOwner(r.owner) !== f.owner) return false;
      if (f.stage && r.stage !== f.stage) return false;
      if (f.year && !r.base_date?.startsWith(f.year)) return false;
      if (f.unpaid && !isUnpaid(r)) return false;
      if (f.review && !hasReview(r)) return false;
      if (f.overdue && !isOverdue(r)) return false;
      if (!q) return true;
      const hay = [r.display_no, r.deal_no, r.legacy_no, r.customer, r.title, ...(r.quote?.items.map((i) => i.name) ?? [])]
        .join(" ").toLowerCase();
      return hay.includes(q);
    });
  }, [data.rows, f]);

  const open = openId ? data.rows.find((r) => r.id === openId) ?? null : null;
  const picked = data.rows.filter((r) => selected.has(r.id));
  const allShown = rows.length > 0 && rows.every((r) => selected.has(r.id));
  const toggleAll = () => setSelected((prev) => {
    const next = new Set(prev);
    for (const r of rows) {
      if (allShown) next.delete(r.id);
      else next.add(r.id);
    }
    return next;
  });

  return (
    <div className="mx-auto w-full max-w-[1400px] px-4 pb-16 pt-6 md:px-6">
      <header className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[20px] font-bold tracking-tight">제품 영업 건 관리</h1>
          <p className="mt-1 text-[12.5px] text-foreground-muted">
            제품 추천 확정 → 견적 → 수주 → 거래명세서 발급·입금 확인 → 출하를 한 건씩 관리합니다. (전체 {data.counts.total}건) · 기준일 {data.today}
            · 금액은 공급가액 · 최근에 바뀐 건이 위
          </p>
        </div>
        <div className="flex items-center gap-2">
          {selecting ? (
            <>
              <span className="text-[12.5px] text-foreground-muted">삭제할 건을 체크하세요</span>
              <button type="button" onClick={() => setConfirming(true)} disabled={selected.size === 0}
                      className="h-9 rounded-lg bg-red-600 px-4 text-[13px] font-semibold text-white hover:bg-red-700 disabled:opacity-40">
                선택한 {selected.size}건 삭제
              </button>
              <button type="button" onClick={stopSelecting}
                      className="h-9 rounded-lg border border-border px-4 text-[13px] hover:bg-foreground/5">취소</button>
            </>
          ) : (
            <>
              <button type="button" onClick={() => setCreating(true)}
                      className="h-9 rounded-lg bg-accent px-4 text-[13px] font-semibold text-white hover:opacity-90">
                + 새 건 등록
              </button>
              <a href="/api/sales-deals/export" download
                 className="flex h-9 items-center rounded-lg border border-border px-4 text-[13px] font-semibold hover:bg-foreground/5">
                엑셀 다운로드
              </a>
              {admin && (
                <button type="button" onClick={() => { setNotice(null); setSelecting(true); }}
                        className="h-9 rounded-lg border border-border px-4 text-[13px] font-semibold text-red-600 hover:bg-red-500/8 dark:text-red-400">
                  삭제
                </button>
              )}
            </>
          )}
        </div>
      </header>
      {notice && (
        <p className={cn("mb-3 rounded-lg px-3 py-2 text-[13px]",
                         notice.ok ? "bg-emerald-500/10 text-emerald-800 dark:text-emerald-200" : "bg-red-500/10 text-red-700 dark:text-red-300")}>
          {notice.text}
        </p>
      )}

      {data.kpi && <KpiBar kpi={data.kpi} />}

      {/* 단계 요약 — 누르면 그 단계만 */}
      <div className="mb-3 flex flex-wrap gap-2">
        <SummaryChip label="전체" count={data.counts.total} active={!f.stage && !f.review}
                     tone="bg-foreground/8 text-foreground" onClick={() => set({ stage: "", review: false })} />
        {STAGES.map((s) => (
          <SummaryChip key={s} label={s} count={data.summary[s]} active={f.stage === s}
                       tone={STAGE_TONE[s]} onClick={() => set({ stage: f.stage === s ? "" : s })} />
        ))}
        <SummaryChip label="⚠ 확인 필요" count={data.summary["확인 필요"]} active={f.review}
                     tone="bg-sky-500/12 text-sky-700 dark:text-sky-300" onClick={() => set({ review: !f.review })} />
        {overdueRows.length > 0 && (
          <SummaryChip label="⏰ 입금 예정일 지남" count={overdueRows.length} active={f.overdue}
                       tone="bg-red-500/12 text-red-700 dark:text-red-300" onClick={() => set({ overdue: !f.overdue })} />
        )}
      </div>

      {/* 필터 */}
      <div className="mb-3 flex flex-wrap items-center gap-2 text-[13px]">
        <input value={f.q} onChange={(e) => set({ q: e.target.value })} placeholder="고객사·품목·건번호 검색"
               className="h-9 w-64 rounded-lg border border-border bg-surface px-3 outline-none focus:border-accent" />
        <Select value={f.owner} onChange={(v) => set({ owner: v })} label="담당자" options={owners} />
        <Select value={f.stage} onChange={(v) => set({ stage: v })} label="단계" options={STAGES} />
        <Select value={f.year} onChange={(v) => set({ year: v })} label="연도" options={years} />
        <Check checked={f.unpaid} onChange={(v) => set({ unpaid: v })} label="미수만" />
        <Check checked={f.review} onChange={(v) => set({ review: v })} label="확인 필요만" />
        <span className="ml-auto text-foreground-subtle">{rows.length}건</span>
      </div>

      <div className="overflow-x-auto rounded-xl border border-border bg-surface">
        <table className="w-full min-w-[1100px] border-collapse text-[13px]">
          <thead>
            <tr className="border-b border-border bg-foreground/[0.03] text-left text-[12px] text-foreground-muted">
              {selecting && (
                <th className="w-10 px-3 py-2">
                  <input type="checkbox" checked={allShown} onChange={toggleAll} aria-label="보이는 건 모두 선택"
                         className="h-4 w-4 cursor-pointer accent-red-600" />
                </th>
              )}
              {["프로젝트", "번호", "담당", "최근 견적", "공급가액", "단계", "입금", "출하", "알림"].map((h) => (
                <th key={h} className={cn("whitespace-nowrap px-3 py-2 font-semibold", h === "공급가액" && "text-right")}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <Fragment key={r.id}>
              <tr onClick={() => (selecting ? toggle(r.id) : setOpenId(r.id))}
                  aria-selected={selecting ? selected.has(r.id) : undefined}
                  className={cn("cursor-pointer border-b border-border/70 last:border-0 hover:bg-accent/5",
                    isOverdue(r) && "bg-red-500/[0.07] shadow-[inset_3px_0_0_#dc2626]", r.dropped && "opacity-60",
                    !selecting && openId === r.id && "bg-accent/8", selecting && selected.has(r.id) && "bg-red-500/8")}>
                {selecting && (
                  <td className="w-10 px-3 py-2" onClick={(e) => e.stopPropagation()}>
                    <input type="checkbox" checked={selected.has(r.id)} onChange={() => toggle(r.id)}
                           aria-label={`${r.display_no ?? "번호 미부여"} 선택`} className="h-4 w-4 cursor-pointer accent-red-600" />
                  </td>
                )}
                {/* 프로젝트가 맨 앞(사용자 2026-10-08) — 번호는 같은 담당·같은 날이면 프로젝트끼리 같을 수 있어 고객사·건명으로 구분 */}
                <td className="max-w-[300px] px-3 py-2">
                  <div className="truncate font-semibold" title={r.customer ?? undefined}>{r.customer ?? <Muted>고객사 미기재</Muted>}</div>
                  <div className="mt-0.5 flex items-center gap-1.5">
                    <span className="truncate text-[12.5px] text-foreground-muted" title={r.title}>{r.title}</span>
                    {!r.imported && r.kind !== "shipment_only" && (
                      <button type="button" title={r.proposal_id ? "미팅 내용·최종 추천·근거·AI 대화 보기" : hasMeeting(r) ? "직접 입력한 미팅 정보 보기" : "미팅 정보 없음 — 직접 입력"}
                              onClick={(e) => { e.stopPropagation(); showMeeting(r); }}
                              className={cn("shrink-0 rounded-md border px-1.5 py-0.5 text-[11px] font-medium hover:bg-accent/8",
                                hasMeeting(r) ? "border-border text-accent" : "border-dashed border-border text-foreground-subtle")}>
                        미팅 정보
                      </button>
                    )}
                  </div>
                </td>
                <td className="whitespace-nowrap px-3 py-2 font-mono text-[12px]">
                  {r.display_no ?? r.deal_no ?? "번호 미부여"}
                  {r.legacy_no && <div className="text-[11px] text-foreground-subtle">옛 {r.legacy_no}</div>}
                  {!selecting && (r.history?.length ?? 0) > 0 && (
                    <button type="button" aria-expanded={shown.has(r.id)} onClick={(e) => { e.stopPropagation(); toggleShown(r.id); }}
                            className={cn("mt-0.5 block rounded-md border px-1.5 py-px font-sans text-[11px] font-medium",
                              shown.has(r.id) ? "border-accent/40 bg-accent/10 text-accent" : "border-border text-foreground-muted hover:bg-foreground/5")}>
                      히스토리 {r.history?.length}{shown.has(r.id) ? " · 닫기" : ""}
                    </button>
                  )}
                </td>
                <td className="whitespace-nowrap px-3 py-2">{normOwner(r.owner) || <Muted>-</Muted>}</td>
                <td className="whitespace-nowrap px-3 py-2">
                  {r.quote ? shortDate(r.quote.date)
                    : <Muted>{r.kind === "sales_only" ? "견적 없음" : r.kind === "recommend" ? "견적서 발행 전" : "-"}</Muted>}
                </td>
                <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums">{won(supplyOf(r), r.quote?.currency)}</td>
                <td className="whitespace-nowrap px-3 py-2">
                  <StageChip stage={r.stage} version={quoteVer(r.stage, r.quote?.revision)} />
                </td>
                <td className="whitespace-nowrap px-3 py-2">{paymentCell(r)}</td>
                <td className="whitespace-nowrap px-3 py-2">
                  {r.shipments.length ? `${r.shipments.length}회` : r.shipped ? <Muted>완료(비고)</Muted> : <Muted>-</Muted>}
                </td>
                <td className="max-w-[260px] px-3 py-2"><AlertCell row={r} /></td>
              </tr>
              {!selecting && shown.has(r.id) && (
                <tr className="border-b border-border/70 bg-foreground/[0.02]">
                  <td colSpan={9} className="px-3 pb-3 pt-1">
                    <HistoryPanel row={r} admin={admin} onChanged={refresh} />
                  </td>
                </tr>
              )}
              </Fragment>
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={selecting ? 10 : 9} className="px-3 py-12 text-center text-foreground-muted">조건에 맞는 건이 없습니다.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {open && <DealDetail key={open.id} row={open} admin={admin} onClose={() => setOpenId(null)} onChanged={refresh} onMeeting={onMeeting} onOrder={onOrder} onQuote={onQuote} />}
      {confirming && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-black/30 p-4" onClick={() => !deleting && setConfirming(false)}>
          <div role="alertdialog" aria-label="영업 건 삭제 확인" onClick={(e) => e.stopPropagation()}
               className="w-full max-w-[440px] rounded-2xl bg-background p-5 shadow-2xl">
            <h2 className="text-[16px] font-bold">{picked.length}건을 삭제할까요?</h2>
            <ul className="scroll-thin mt-3 max-h-48 overflow-y-auto rounded-lg border border-border text-[12.5px]">
              {picked.map((r) => (
                <li key={r.id} className="flex gap-2 border-b border-border/70 px-3 py-1.5 last:border-0">
                  <span className="truncate font-medium">{r.customer ?? "고객사 미기재"} · {r.title}</span>
                  <span className="ml-auto shrink-0 font-mono text-foreground-muted">{r.display_no ?? r.deal_no ?? "번호 미부여"}</span>
                </li>
              ))}
            </ul>
            <p className="mt-3 text-[12px] text-foreground-muted">
              리스트에서 사라지고 수주·입금·출하 기록도 함께 지워집니다. 지운 건은 따로 보관되어, 잘못 지웠으면 관리자가 되살릴 수 있습니다.
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" onClick={() => setConfirming(false)} disabled={deleting}
                      className="h-9 rounded-lg border border-border px-4 text-[13px]">취소</button>
              <button type="button" onClick={() => void deleteSelected()} disabled={deleting || picked.length === 0}
                      className="h-9 rounded-lg bg-red-600 px-4 text-[13px] font-semibold text-white hover:bg-red-700 disabled:opacity-40">
                {deleting ? "삭제 중…" : `${picked.length}건 삭제`}
              </button>
            </div>
          </div>
        </div>
      )}
      {lateOpen && overdueRows.length > 0 && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-black/30 p-4" onClick={closeLate}>
          <div role="alertdialog" aria-label="입금 예정일 지난 건" onClick={(e) => e.stopPropagation()}
               className="w-full max-w-[560px] rounded-2xl bg-background p-5 shadow-2xl">
            <h2 className="text-[16px] font-bold text-red-700 dark:text-red-300">⏰ 입금 예정일이 지난 건 {overdueRows.length}건</h2>
            <p className="mt-1 text-[12.5px] text-foreground-muted">입금되었으면 건을 열어 회차의 [입금 확인]을 눌러 주세요. 금액은 공급가액입니다.</p>
            <ul className="scroll-thin mt-3 max-h-72 overflow-y-auto rounded-lg border border-border text-[12.5px]">
              {overdueRows.flatMap((r) => (r.flow?.overdue ?? []).map((t) => (
                <li key={`${r.id}-${t.index}`}>
                  <button type="button" onClick={() => { closeLate(); setOpenId(r.id); }}
                          className="flex w-full items-center gap-2 border-b border-border/70 px-3 py-2 text-left last:border-0 hover:bg-red-500/5">
                    <span className="font-mono text-foreground-muted">{r.display_no ?? r.deal_no ?? "번호 미부여"}</span>
                    <span className="min-w-0 flex-1 truncate font-medium">{r.customer ?? "고객사 미기재"}</span>
                    <span className="whitespace-nowrap">{t.label} {t.pct}% · {won(t.amount)}원</span>
                    <span className="whitespace-nowrap font-semibold text-red-600 dark:text-red-400">{shortDate(t.expected_date)} ({t.days}일 지남)</span>
                  </button>
                </li>
              )))}
            </ul>
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" onClick={() => { closeLate(); set({ overdue: true, stage: "" }); }}
                      className="h-9 rounded-lg border border-border px-4 text-[13px]">리스트에서 보기</button>
              <button type="button" onClick={closeLate} className="h-9 rounded-lg bg-accent px-4 text-[13px] font-semibold text-white">확인</button>
            </div>
          </div>
        </div>
      )}
      {meetingRow && <DealMeetingDialog deal={meetingRow} onClose={() => setMeetingRow(null)}
                                        onSaved={(row) => { setMeetingRow(row); refresh(); }} />}
      {creating && (
        <DealNewForm
          owners={owners}
          onClose={() => setCreating(false)}
          onCreated={(row) => { setCreating(false); refresh(); setOpenId(row.id); }}
        />
      )}
    </div>
  );
}

function paymentCell(r: DealRow) {
  if (r.paid_full) return <span className="font-medium text-emerald-700 dark:text-emerald-300">완납 ✓</span>;
  const ts = r.flow?.terms ?? [];
  if (ts.length) {                                   // 결제 조건 건 — 마지막으로 확인한 회차('선금 30% 입금'), 없으면 다음 회차 입금 전
    const last = [...ts].reverse().find((t) => t.confirmed);
    const next = ts.find((t) => !t.confirmed);
    // 회차 전체는 마우스를 올리면(자세한 입금기한은 상세에서) — 사용자 2026-10-08
    const all = ts.map((t) => `${t.label} ${t.pct}% · ${t.confirmed ? `입금 ${t.paid_date ?? ""}` : t.expected_date ? `기한 ${t.expected_date}` : "기한 미정"}`).join("\n");
    return (
      <span title={all} className={cn(isOverdue(r) ? "font-semibold text-red-600 dark:text-red-400" : last ? "text-amber-700 dark:text-amber-300" : "")}>
        {last ? `${last.label} ${last.pct}% 입금` : next ? `${next.label} ${next.pct}% 입금 전` : "-"}
      </span>
    );
  }
  if (r.payments.length) return <span className="text-amber-700 dark:text-amber-300">일부</span>;
  if (isUnpaid(r)) return <span className="font-medium text-red-600 dark:text-red-400">미입금</span>;
  return <Muted>-</Muted>;
}

function AlertCell({ row }: { row: DealRow }) {
  if (!row.alerts.length) return <Muted>-</Muted>;
  // 업무 알림(미입금·무응답)을 먼저, 이관 확인은 그다음
  const sorted = [...row.alerts].sort((a, b) => (a.level === b.level ? 0 : a.level === "alert" ? -1 : 1));
  const first = sorted[0];
  return (
    <span className="flex max-w-[250px] items-center gap-1.5">
      <span className={cn("truncate text-[12px]", first.level === "alert" ? "text-red-600 dark:text-red-400" : "text-sky-700 dark:text-sky-300")}
            title={sorted.map((a) => a.text).join("\n")}>
        {first.level === "review" ? "⚠ " : ""}{first.text}
      </span>
      {sorted.length > 1 && <span className="shrink-0 rounded bg-foreground/8 px-1 text-[11px] text-foreground-muted">+{sorted.length - 1}</span>}
    </span>
  );
}

/** 올해 분기별 수주금액(수주일 기준) · 올해 매출액(출하일 기준) — 공급가액.
 *  분기마다 배지 카드(금액 + 분기 안에서의 비중 막대), 지금 분기는 강조, 합계·매출은 오른쪽 큰 숫자(사용자 2026-10-08). */
function KpiBar({ kpi }: { kpi: DealKpi }) {
  const eok = (n: number) => (n >= 1e8 ? `${(n / 1e8).toFixed(2)}억` : `${Math.round(n / 1e4).toLocaleString("ko-KR")}만`);
  const top = Math.max(1, ...kpi.won_by_quarter);
  return (
    <div className="mb-3 grid gap-2 lg:grid-cols-[1fr_auto]">
      <div className="rounded-xl border border-border bg-surface px-4 py-3">
        <div className="flex flex-wrap items-baseline gap-2">
          <span className="text-[13px] font-semibold">{kpi.year}년 분기별 수주금액</span>
          <span className="text-[11.5px] text-foreground-subtle">공급가액 · 수주일 기준</span>
          <span className="ml-auto rounded-full bg-accent/10 px-2.5 py-0.5 text-[12px] font-semibold text-accent" title={`${won(kpi.won_total)}원`}>
            올해 합계 <span className="tabular-nums">{eok(kpi.won_total)}</span>
          </span>
        </div>
        <div className="mt-2.5 grid grid-cols-2 gap-2 sm:grid-cols-4">
          {kpi.won_by_quarter.map((v, i) => {
            const now = i + 1 === kpi.quarter;
            const future = i + 1 > kpi.quarter;
            return (
              <div key={i} title={`${won(v)}원`}
                   className={cn("flex flex-col gap-1.5 rounded-lg border px-3 py-2",
                     now ? "border-accent/50 bg-accent/[0.06]" : "border-border bg-foreground/[0.015]", future && !v && "opacity-55")}>
                <div className="flex items-center gap-1.5">
                  <span className={cn("rounded-full px-2 py-0.5 text-[11px] font-bold",
                    now ? "bg-accent text-white" : v ? "bg-foreground/10 text-foreground" : "bg-foreground/5 text-foreground-subtle")}>
                    {i + 1}분기
                  </span>
                  {now && <span className="text-[10.5px] font-semibold text-accent">이번 분기</span>}
                </div>
                <span className={cn("text-[17px] font-bold tabular-nums", !v && "text-foreground-subtle")}>{v ? eok(v) : "-"}</span>
                <span className="h-1.5 overflow-hidden rounded-full bg-foreground/[0.07]" aria-hidden>
                  <span className={cn("block h-full rounded-full", now ? "bg-accent" : "bg-foreground/35")} style={{ width: `${(v / top) * 100}%` }} />
                </span>
              </div>
            );
          })}
        </div>
        {kpi.won_estimated > 0 && (
          <div className="mt-1.5 text-[11.5px] text-foreground-subtle">
            엑셀에서 옮긴 {kpi.won_estimated}건은 수주일이 없어 첫 세금계산서 발행일(없으면 출하일·견적일)로 셈했습니다.
          </div>
        )}
      </div>
      <div className="flex flex-col justify-between rounded-xl border border-border bg-surface px-4 py-3 lg:min-w-[220px]">
        <div className="flex flex-wrap items-baseline gap-2">
          <span className="text-[13px] font-semibold">{kpi.year}년 매출액</span>
          <span className="text-[11.5px] text-foreground-subtle">출하일 기준</span>
        </div>
        <div className="mt-2 text-[22px] font-bold tabular-nums" title={`${won(kpi.sales)}원`}>{kpi.sales ? eok(kpi.sales) : "-"}</div>
        <div className="text-[11.5px] tabular-nums text-foreground-subtle">{won(kpi.sales)}원</div>
      </div>
    </div>
  );
}

function SummaryChip({ label, count, active, tone, onClick }: {
  label: string; count: number; active: boolean; tone: string; onClick: () => void;
}) {
  return (
    <button type="button" onClick={onClick} aria-pressed={active}
            className={cn("flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-semibold ring-1 transition",
              tone, active ? "ring-current" : "ring-transparent hover:ring-border")}>
      {label}<span className="tabular-nums">{count}</span>
    </button>
  );
}

function Select({ value, onChange, label, options }: {
  value: string; onChange: (v: string) => void; label: string; options: readonly string[];
}) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} aria-label={label}
            className="h-9 rounded-lg border border-border bg-surface px-2 outline-none focus:border-accent">
      <option value="">{label} 전체</option>
      {options.map((o) => <option key={o} value={o}>{o}</option>)}
    </select>
  );
}

function Check({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <label className="flex h-9 cursor-pointer items-center gap-1.5 rounded-lg border border-border bg-surface px-2.5">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} className="accent-[var(--color-accent,#2563eb)]" />
      {label}
    </label>
  );
}

// 엑셀에 'lucas'·'Lucas' 처럼 대소문자가 섞여 있다
function normOwner(o: string | null): string {
  return o ? o.charAt(0).toUpperCase() + o.slice(1).toLowerCase() : "";
}

function uniq(xs: string[]): string[] {
  return [...new Set(xs.filter(Boolean))].sort();
}

/** 건(프로젝트) 안의 히스토리 — 바뀔 때마다 번호 하나, 최신 먼저(사용자 2026-10-08). 견적서가 새 판으로 바뀌면 옛 판 줄에
 *  '버전 업데이트됨'. 관리자는 되돌리기: 맨 위 줄만 단독으로, 아래 줄을 누르면 맨 위부터 그 줄까지 한 덩어리로 골라진다
 *  (중간 한 줄만 지우면 내용이 꼬이므로). 고른 줄을 지우면 그 바로 아래 줄 때 내용·번호로 돌아간다. */
function HistoryPanel({ row, admin, onChanged }: { row: DealRow; admin: boolean; onChanged: () => void }) {
  // 고른 범위 = 0 ~ upto(최신부터). null = 안 고름
  const [upto, setUpto] = useState<number | null>(null);
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const hist = row.history ?? [];
  // 견적서 발행 줄부터는 못 고른다(그 줄 위까지만) — 맨 아래 줄은 돌아갈 곳이 없어 못 고름
  const stopAt = hist.findIndex((h) => !h.removable);
  const maxPick = Math.min(stopAt === -1 ? hist.length - 1 : stopAt, hist.length - 1) - 1;
  const keep = upto != null ? hist[upto + 1] : null;
  const nextNo = keep ? Number(keep.ver_no.split("-").pop()) + 1 : null;

  function pick(i: number) {
    setAsking(false);
    setErr(null);
    setUpto((cur) => (cur === i ? (i === 0 ? null : i - 1) : i));
  }

  async function rollback() {
    if (!keep) return;
    setBusy(true);
    setErr(null);
    try {
      await dealRequest(`/${row.id}/history/rollback`, { keep_id: keep.id });
      setUpto(null);
      setAsking(false);
      onChanged();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-lg border border-border bg-surface">
      {admin && upto != null && keep && (
        <div className="flex flex-wrap items-center gap-2 border-b border-red-500/30 bg-red-500/[0.06] px-3 py-2 text-[12.5px]">
          {!asking ? (
            <>
              <span><b>{upto + 1}개</b>를 되돌리면 <b className="font-mono">{keep.ver_no}</b> 때 내용으로 돌아갑니다.</span>
              <button type="button" onClick={() => setAsking(true)} disabled={!keep.restorable}
                      className="ml-auto rounded-md bg-red-600 px-2.5 py-1 text-[12px] font-semibold text-white disabled:opacity-40">되돌리기</button>
              <button type="button" onClick={() => setUpto(null)} className="text-[12px] text-foreground-muted">선택 해제</button>
              {!keep.restorable && <span className="w-full text-[11.5px] text-red-700 dark:text-red-300">{keep.ver_no} 은(는) 되돌리기 기능 전에 남은 기록이라 그때 내용으로 돌아갈 수 없습니다.</span>}
            </>
          ) : (
            <>
              <span className="text-red-800 dark:text-red-200">
                정말 되돌릴까요? 고른 줄 뒤에 바뀐 내용(입금 확인·출하·명세서 등)이 모두 <b className="font-mono">{keep.ver_no}</b> 때로 돌아가고,
                번호는 <b className="font-mono">-{String(nextNo).padStart(2, "0")}</b>부터 다시 이어집니다.
              </span>
              <button type="button" disabled={busy} onClick={() => void rollback()}
                      className="ml-auto rounded-md bg-red-600 px-2.5 py-1 text-[12px] font-semibold text-white disabled:opacity-40">
                {busy ? "되돌리는 중…" : `${upto + 1}개 되돌리기`}
              </button>
              <button type="button" disabled={busy} onClick={() => setAsking(false)} className="text-[12px] text-foreground-muted">취소</button>
            </>
          )}
        </div>
      )}
      <table className="w-full border-collapse text-[12.5px]">
        <thead>
          <tr className="border-b border-border text-left text-[11.5px] text-foreground-muted">
            {admin && <th className="w-8 px-3 py-1.5" aria-label="되돌릴 줄 고르기" />}
            {["번호", "바뀐 때", "바뀐 내용", "단계", "공급가액", "입력"].map((h) => (
              <th key={h} className={cn("whitespace-nowrap px-3 py-1.5 font-semibold", h === "공급가액" && "text-right")}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {hist.map((h, i) => {
            const picked = upto != null && i <= upto;
            const canPick = admin && i <= maxPick;
            return (
              <tr key={h.id} onClick={canPick ? () => pick(i) : undefined}
                  className={cn("border-b border-border/60 last:border-0", h.superseded && "text-foreground-muted",
                    canPick && "cursor-pointer hover:bg-foreground/[0.03]", picked && "bg-red-500/10 hover:bg-red-500/12")}>
                {admin && (
                  <td className="w-8 px-3 py-1.5" onClick={(e) => e.stopPropagation()}>
                    {canPick && (
                      <input type="checkbox" checked={picked} onChange={() => pick(i)} aria-label={`${h.ver_no}까지 되돌리기 선택`}
                             className="h-4 w-4 cursor-pointer accent-red-600" />
                    )}
                  </td>
                )}
                <td className="whitespace-nowrap px-3 py-1.5 font-mono text-[12px]">
                  {h.ver_no}
                  {i === 0 && <span className="ml-1.5 rounded bg-accent/10 px-1 font-sans text-[10.5px] font-semibold text-accent">현재</span>}
                </td>
                <td className="whitespace-nowrap px-3 py-1.5 tabular-nums">{stamp(h.created_at)}</td>
                <td className="px-3 py-1.5">
                  <span className="font-medium">{ACTION_LABEL[h.action] ?? h.action}</span>
                  {eventDetail(h) && <span className="ml-1.5 text-foreground-muted">{eventDetail(h)}</span>}
                </td>
                <td className="whitespace-nowrap px-3 py-1.5">
                  {h.stage ? <StageChip stage={h.stage} version={quoteVer(h.stage, h.quote_rev)} className={cn(h.superseded && "opacity-70")} /> : <Muted>-</Muted>}
                  {h.superseded && <div className="mt-0.5 text-[10.5px] font-semibold text-amber-700 dark:text-amber-300">└ 버전 업데이트됨</div>}
                </td>
                <td className="whitespace-nowrap px-3 py-1.5 text-right tabular-nums">{h.amount != null ? won(h.amount) : <Muted>-</Muted>}</td>
                <td className="whitespace-nowrap px-3 py-1.5">{h.user_name ?? <Muted>시스템</Muted>}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {admin && maxPick >= 0 && upto == null && (
        <p className="border-t border-border/60 px-3 py-1.5 text-[11.5px] text-foreground-subtle">
          실수로 바꿨으면 되돌릴 줄을 고르세요 — 맨 위 줄만 따로, 아래 줄을 누르면 맨 위부터 그 줄까지 같이 골라집니다. 견적서 발행 줄은 되돌리지 않습니다.
        </p>
      )}
      {err && <p className="px-3 py-1.5 text-[12px] text-red-600">{err}</p>}
    </div>
  );
}
