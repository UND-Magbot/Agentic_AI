// 제품 구매 건 상세(오른쪽 패널, 사용자 2026-11-02 → 2026-11-11 개편) — 단계마다 산출물 첨부·확인 버튼.
// 견적(최종 견적서 첨부·교체) → 재가(서류 첨부 + [재가 완료]) → 발주(회사 양식 발주서 작성·발행) → 발주 뒤(순서 자유):
// 국내 = 송금(% 회차마다 [송금 확인]) · 배송([배송 시작] → 배송 중) · 입고(사진·수량·S/N) + 거래명세서·세금계산서(선택: 첨부 또는 선결제·선발행 사유)
// 해외 = 제조사 송금(확인증 첨부 선택) · 배송 · 통관 서류(원산지 증명서 → 없으면 기타 서류) · 관세사 송금 · 입고.
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { PurchasePoEditor } from "@/components/domains/sales/purchase-po-editor";
import { DownloadDialog, PreviewDialog } from "@/components/domains/sales/quote-workspace";
import { INPUT, Muted, shortDate, stamp, todayIso, won } from "@/components/domains/sales/deal-format";
import {
  CUSTOMS_DOC_EXAMPLES, DOC_SKIP_HELP, DOC_SKIP_MODES, purchaseRequest, purchaseUpload, REGION_LABEL,
  type PurchaseFile, type PurchaseRow,
} from "@/lib/shared/purchases";
import { Icon } from "@/components/shared/ui/icon";
import { cn } from "@/lib/shared/utils";
import { primaryBtn, secondaryBtn } from "./proposal-actions";
import { HEAD_FIELD } from "./quote-styles";

type FileKind = "quote" | "approval" | "statement" | "tax" | "customs" | "remit" | "customs_etc";

const BTN = "h-8 rounded-lg border border-border px-3 text-[12.5px] font-medium hover:bg-foreground/5 disabled:opacity-40";
const PRIMARY = "h-8 rounded-lg bg-accent px-3 text-[12.5px] font-semibold text-white hover:opacity-90 disabled:opacity-40";
const fileUrl = (id: number, kind: string, n: number) => `/api/purchases/${id}/files/${kind}/${n}`;

export function PurchaseDetail({ id, admin, onClose, onChanged }: {
  id: number; admin: boolean; onClose: () => void; onChanged: () => void;
}) {
  const [p, setP] = useState<PurchaseRow | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [poOpen, setPoOpen] = useState(false);

  useEffect(() => {
    purchaseRequest(`/${id}`).then(setP).catch((e) => setErr(e instanceof Error ? e.message : "불러오지 못했습니다."));
  }, [id]);

  /** 저장 요청 공통 — 바뀐 건으로 화면·리스트를 갱신하고, 실패하면 서버 문장을 위에 보인다. */
  const act = useCallback(async (label: string, fn: () => Promise<PurchaseRow>) => {
    setBusy(label);
    setErr(null);
    try {
      setP(await fn());
      onChanged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "처리하지 못했습니다.");
    } finally {
      setBusy(null);
    }
  }, [onChanged]);

  if (!p) {
    return (
      <div className="fixed inset-0 z-40 flex justify-end bg-black/25" onClick={onClose}>
        <aside className="h-full w-full max-w-[720px] bg-background p-6 text-[13px] text-foreground-muted" onClick={(e) => e.stopPropagation()}>
          {err ?? "불러오는 중…"}
        </aside>
      </div>
    );
  }
  const step = Object.fromEntries(p.steps.map((s) => [s.key, s]));
  const num = Object.fromEntries(p.steps.map((s, i) => [s.key, i + 1]));
  // 해외(사용자 2026-11-11) — 제조사 송금 · 배송 · 통관 서류 · 관세사 송금 · 입고(입고에서 끝, 명세서·계산서 없음)
  const over = p.region === "overseas";
  const payLabel = over ? "제조사 송금 확인" : "송금 확인";
  const g = guide(p, step, over);
  const marks: Record<string, Mark> = Object.fromEntries(g.items.map((x) => [x.key, g.optional ? "opt" : g.ordered ? "now" : "left"]));
  const upload = (kind: FileKind, f: File, label?: string) => act(`file-${kind}`, () => purchaseUpload(`/${id}/files/${kind}`, f, label));
  const removeFile = (kind: FileKind, n: number) => act(`rm-${kind}`, () => purchaseRequest(`/${id}/files/${kind}/${n}/remove`, {}));
  const lock = (k: string) => !step[k].ready && !step[k].done;
  // 첨부가 없어도 버튼으로 넘어가기(사용자 2026-11-11 — 영업팀이 아무것도 안 하고도 다음 단계를 테스트할 수 있게)
  const skipQuote = () => act("quote-skip", () => purchaseRequest(`/${id}/quote/skip`, { value: true }));
  const afterOrder = over ? "발주서를 발행한 뒤에 기록합니다." : AFTER_ORDER;

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/25" onClick={onClose}>
      <aside role="dialog" aria-label={`${p.supplier} 구매 건 상세`} onClick={(e) => e.stopPropagation()}
             className="scroll-thin h-full w-full max-w-[720px] overflow-y-auto bg-background shadow-2xl">
        <div className="sticky top-0 z-10 border-b border-border bg-background/95 px-5 py-4 backdrop-blur">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="font-mono text-[12px] text-foreground-subtle">
                <span className={cn("mr-1.5 rounded px-1.5 font-sans font-semibold", over ? "bg-sky-500/12 text-sky-700 dark:text-sky-300" : "bg-foreground/8")}>
                  {REGION_LABEL[p.region]}
                </span>
                {p.po.no ?? "발주 전"} · 담당 {p.owner || "-"}{p.expected_date ? ` · ${over ? "납기일" : "입고 예정"} ${p.expected_date}` : ""}
              </div>
              <h2 className="mt-0.5 truncate text-[18px] font-bold">{p.supplier}</h2>
              <p className="mt-0.5 text-[13px] text-foreground-muted">{p.title || "품목 미기재"}</p>
            </div>
            <div className="flex shrink-0 items-start gap-2">
              <span className="flex flex-col items-end gap-0.5">
                <span className="rounded-full bg-accent/12 px-2.5 py-0.5 text-[12px] font-semibold text-accent">{p.stage === "배송 중" ? "🚚 " : ""}{p.stage}</span>
                {p.stage_note && <span className="text-[11.5px] text-foreground-muted">{p.stage_note}</span>}
              </span>
              <button type="button" onClick={onClose} aria-label="닫기" className="h-8 w-8 rounded-lg text-[18px] hover:bg-foreground/8">×</button>
            </div>
          </div>
          {err && <p className="mt-2 rounded-lg bg-red-500/10 px-3 py-2 text-[12.5px] text-red-700 dark:text-red-300">{err}</p>}
        </div>

        <div className="space-y-4 px-5 py-4 text-[13px]">
          {p.alerts.length > 0 && (
            <ul className="space-y-1 rounded-lg border border-amber-500/30 bg-amber-500/8 px-3 py-2 text-[12.5px]">
              {p.alerts.map((a, i) => <li key={i} className={a.level === "alert" ? "text-red-700 dark:text-red-300" : ""}>⚠ {a.text}</li>)}
            </ul>
          )}
          {p.dropped && (
            <div className="rounded-lg bg-foreground/5 px-3 py-2">
              취소한 구매 건입니다{p.drop_reason ? ` — ${p.drop_reason}` : ""}.
              {admin && <button type="button" className={cn(BTN, "ml-2")} disabled={!!busy}
                                onClick={() => act("drop", () => purchaseRequest(`/${id}/drop`, { dropped: false }))}>되살리기</button>}
            </div>
          )}
          <InfoEditor p={p} busy={!!busy} onSave={(v) => act("info", () => purchaseRequest(`/${id}/info`, v))} />
          {!p.dropped && g.items.length > 0 && (
            <NextStep g={g}>
              {p.stage === "견적 대기" && (
                <span className="flex flex-wrap gap-1.5">
                  <UploadButton label="견적서 올리기" busy={busy === "file-quote"} disabled={!!busy} onFile={(f) => upload("quote", f)} primary />
                  <button type="button" className={cn(BTN, "bg-surface")} disabled={!!busy} onClick={skipQuote}>견적서 없이 넘어가기</button>
                </span>
              )}
            </NextStep>
          )}

          {/* 1. 견적 */}
          <Section id="quote" n={num.quote} title="견적" done={step.quote.done} mark={marks["quote"]}
                   hint="발주할 최종 견적서를 올립니다. 고쳐 받은 견적서는 다시 올리면 교체되고, 예전 파일은 이전 견적서로 남습니다. 견적서가 없으면 [견적서 없이 넘어가기]로 다음 단계로 갈 수 있습니다.">
            <FileList id={id} kind="quote" files={p.quote_files} currentLast canRemove={admin} onRemove={(n) => removeFile("quote", n)} />
            {!p.quote_files.length && p.approval.quote_skip && (
              <p className="flex flex-wrap items-center gap-2 rounded-lg bg-indigo-500/[0.07] px-3 py-2">
                <span className="rounded bg-indigo-500/15 px-1.5 text-[11.5px] font-bold text-indigo-700 dark:text-indigo-300">첨부 없이 넘어감</span>
                <span className="text-foreground-muted">{p.approval.quote_skip.by} · {stamp(p.approval.quote_skip.at)} — 견적서는 나중에 올려도 됩니다</span>
                {!p.approval.done_at && !p.dropped && (
                  <button type="button" className="ml-auto text-[12px] text-foreground-subtle hover:underline" disabled={!!busy}
                          onClick={() => act("quote-skip", () => purchaseRequest(`/${id}/quote/skip`, { value: false }))}>되돌리기</button>
                )}
              </p>
            )}
            <div className="flex flex-wrap items-center gap-2">
              <UploadButton label={p.quote_files.length ? "고친 견적서로 교체" : "견적서 올리기"} busy={busy === "file-quote"}
                            disabled={p.dropped || !!busy} onFile={(f) => upload("quote", f)} primary={!p.quote_files.length && !p.approval.quote_skip} />
              {!p.quote_files.length && !p.approval.quote_skip && (
                <button type="button" className={BTN} disabled={p.dropped || !!busy} onClick={skipQuote}>견적서 없이 넘어가기</button>
              )}
            </div>
          </Section>

          {/* 2. 재가 */}
          <Section id="approval" n={num.approval} title="재가" done={step.approval.done} locked={lock("approval")} mark={marks["approval"]}
                   lockText="견적서를 먼저 올리거나 [견적서 없이 넘어가기]를 눌러 주세요."
                   hint="대표님 재가 서류를 첨부하고 [재가 완료]를 누르면 발주 단계로 넘어갑니다. 첨부가 없어도 [재가 완료]로 넘어갈 수 있습니다(첨부 없이로 기록).">
            <FileList id={id} kind="approval" files={p.approval.files} canRemove={admin || !p.approval.done_at} onRemove={(n) => removeFile("approval", n)} />
            {p.approval.done_at ? (
              <p className="text-emerald-700 dark:text-emerald-300">
                ✓ 재가 완료 {shortDate(p.approval.date)} · {p.approval.done_by}{p.approval.no_file && !p.approval.files.length ? " (첨부 없이)" : ""}
                {admin && !p.po.no && <button type="button" className={cn(BTN, "ml-2")} disabled={!!busy}
                                              onClick={() => act("unapprove", () => purchaseRequest(`/${id}/unapprove`, {}))}>재가 취소</button>}
              </p>
            ) : (
              <div className="flex flex-wrap items-center gap-2">
                <UploadButton label="재가 서류 첨부" busy={busy === "file-approval"} disabled={p.dropped || !!busy} onFile={(f) => upload("approval", f)} />
                <DateAction label={p.approval.files.length ? "재가 완료" : "재가 완료(첨부 없이)"} busy={busy === "approve"} disabled={p.dropped || !!busy}
                            title={p.approval.files.length ? undefined : "재가 서류 없이 넘어갑니다 — '첨부 없이'로 기록됩니다"}
                            onRun={(date) => act("approve", () => purchaseRequest(`/${id}/approve`, { date }))} />
              </div>
            )}
          </Section>

          {/* 3. 발주 — 발주서 발행 + 입고 예정일(사용자 2026-11-02: 입고 예정일을 적어야 다음 단계로) */}
          <Section id="order" n={num.order} title="발주" done={step.order.done} locked={lock("order") && !p.po.no} mark={marks["order"]}
                   lockText="재가를 마친 뒤 발주서를 작성합니다."
                   hint={`회사 ${over ? "해외(Purchasing order, 달러)" : "국내(원화, VAT 별도)"} 양식으로 작성·발행합니다. 엑셀에서 고친 발주서는 작성 창의 [고친 파일로 발행]으로 올립니다. ${over ? "납기일은 알면 적습니다(선택)." : "입고 예정일을 적어야 다음 단계로 넘어갑니다."}`}>
            {p.po.no ? (
              <div className="space-y-2">
                <PoDocs id={id} p={p} onEdit={p.dropped ? undefined : () => setPoOpen(true)} />
                <PoItems p={p} over={over} />
                <ExpectedDateEditor p={p} over={over} busy={!!busy}
                                    onSave={(date, reason) => act("expected", () => purchaseRequest(`/${id}/expected-date`, { date, reason }))} />
              </div>
            ) : (
              <button type="button" className={PRIMARY} disabled={!step.order.ready || !!busy} onClick={() => setPoOpen(true)}>
                {p.po.has_draft ? "작성 중인 발주서 이어서 쓰기" : "발주서 작성"}
              </button>
            )}
          </Section>

          {/* 발주 뒤(순서 자유) */}
          <p className="px-1 text-[12px] text-foreground-subtle">
            아래 단계는 정석 순서({over ? "제조사 송금 → 배송 → 통관 서류 → 관세사 송금 → 입고" : "송금 → 배송 → 입고"})를 안내할 뿐, 어느 것부터 기록해도 됩니다.
            {over ? " 입고까지 끝나면 구매 완료입니다." : " 송금·입고까지 끝나면 구매 완료이고, 거래명세서·세금계산서는 선택입니다."}
          </p>

          {/* 송금 — 국내: 결제 조건(선금·중도금·잔금 %, 합 100%) → 회차마다 [송금 확인](사용자 2026-11-11).
              해외: [제조사 송금 확인] 한 번 + 송금 확인증 첨부(선택). 예전에 한 번에 확인한 국내 건은 그 기록을 그대로 보인다 */}
          <Section id="pay" n={num.pay} title={step.pay.label} done={step.pay.done} locked={lock("pay")} lockText={afterOrder} mark={marks["pay"]}
                   hint={over ? "제조사에 물품 대금을 보냈으면 [제조사 송금 확인]을 누르면 넘어갑니다. 제조사 송금 확인증을 받았으면 첨부하세요(첨부는 선택)."
                     : "결제 조건(선금·중도금·잔금 %)을 정하면 회차마다 [송금 확인] 버튼이 나옵니다. 비율 합은 100% 여야 합니다. 금액은 공급가액 기준."}>
            {over && (
              <FileList id={id} kind="remit" files={p.customs.remit_files} canRemove={admin || !step.pay.done} onRemove={(n) => removeFile("remit", n)} />
            )}
            {p.payment ? (
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-emerald-700 dark:text-emerald-300">
                  ✓ {payLabel} {shortDate(p.payment.date)} · {p.payment.by}{p.payment.note ? ` — ${p.payment.note}` : ""}
                  {!p.dropped && <button type="button" className={cn(BTN, "ml-2")} disabled={!!busy}
                                          onClick={() => { if (window.confirm(`${payLabel}을 취소할까요? 금액이 바뀌었으면 취소한 뒤 다시 확인하세요.`)) void act("unpaid", () => purchaseRequest(`/${id}/paid/undo`, {})); }}>확인 취소</button>}
                </p>
                {over && !p.dropped && (
                  <UploadButton label="송금 확인증 첨부" busy={busy === "file-remit"} disabled={!!busy} onFile={(f) => upload("remit", f)} />
                )}
              </div>
            ) : over ? (
              <div className="flex flex-wrap items-center gap-2">
                <UploadButton label="송금 확인증 첨부(선택)" busy={busy === "file-remit"} disabled={!step.pay.ready || !!busy} onFile={(f) => upload("remit", f)} />
                <DateNoteAction label={payLabel} placeholder="메모(송금 방식·금액 등, 선택)" busy={busy === "paid"} disabled={!step.pay.ready || !!busy}
                                onRun={(date, note) => act("paid", () => purchaseRequest(`/${id}/paid`, { date, note }))} />
              </div>
            ) : (
              <>
                <TermsEditor key={JSON.stringify(p.pay_terms) + p.terms.filter((t) => t.confirmed).length} p={p} busy={!!busy}
                             onSave={(terms) => act("terms", () => purchaseRequest(`/${id}/terms`, { terms }))} />
                <TermList p={p} busy={busy} ready={step.pay.ready}
                          onConfirm={(i, date) => act(`confirm-${i}`, () => purchaseRequest(`/${id}/terms/${i}/confirm`, { date, mismatch_note: null }))}
                          onUnconfirm={(i) => act(`un-${i}`, () => purchaseRequest(`/${id}/terms/${i}/unconfirm`, {}))} />
              </>
            )}
          </Section>

          {/* 배송(사용자 2026-11-11) — 공급사 연락으로 배송 시작을 알면 [배송 시작] → 상태 '배송 중'. 입고 기록이 있으면 배송은 끝 */}
          <Section id="ship" n={num.ship} title="배송" done={step.ship.done} locked={lock("ship")} lockText={afterOrder} mark={marks["ship"]}
                   hint="공급사에서 물건을 보냈다는 연락(전화 등)을 받으면 [배송 시작]을 누르세요. 상태가 '배송 중'으로 바뀌고, 입고 기록을 남기면 배송이 끝납니다.">
            {p.shipping ? (
              <p className={p.in_transit ? "font-medium text-sky-700 dark:text-sky-300" : "text-emerald-700 dark:text-emerald-300"}>
                {p.in_transit ? "🚚 배송 중" : "✓ 배송 끝(입고됨)"} — 배송 시작 {shortDate(p.shipping.date)} · {p.shipping.by}{p.shipping.note ? ` — ${p.shipping.note}` : ""}
                {admin && p.in_transit && <button type="button" className={cn(BTN, "ml-2")} disabled={!!busy}
                                                  onClick={() => act("unship", () => purchaseRequest(`/${id}/ship/undo`, {}))}>취소</button>}
              </p>
            ) : p.receipt.records.length ? (
              <Muted>배송 시작 기록 없이 입고됐습니다 — 배송 단계는 끝난 것으로 봅니다.</Muted>
            ) : (
              <DateNoteAction label="배송 시작" placeholder="메모(운송사·연락 받은 내용 등, 선택)" busy={busy === "ship"} disabled={!step.ship.ready || !!busy}
                              onRun={(date, note) => act("ship", () => purchaseRequest(`/${id}/ship`, { date, note }))} />
            )}
          </Section>

          {over && (
            <>
              {/* 통관 서류(사용자 2026-11-11) — 원산지 증명서 1순위, [증빙 자료 없음]이면 CI·Packing List 등 기타 서류 여러 개.
                  첨부 없이도 [통관 서류 제출 완료] */}
              <Section id="customs" n={num.customs} title="통관 서류" done={step.customs.done} locked={lock("customs")} lockText={afterOrder}
                       mark={marks["customs"]}
                       hint="1순위는 원산지 증명서입니다. 없으면 [증빙 자료 없음]을 누르고 CI·Packing List 같은 통관 기타 서류를 항목별로 올리세요. 첨부가 없어도 [통관 서류 제출 완료]로 넘어갈 수 있습니다.">
                <CustomsDocs id={id} p={p} admin={admin} busy={busy} ready={step.customs.ready}
                             onUpload={(kind, f, label) => upload(kind, f, label)} onRemove={removeFile}
                             onNoOrigin={(value) => act("no-origin", () => purchaseRequest(`/${id}/customs/no-origin`, { value }))} />
                {p.customs.done_at ? (
                  <p className="text-emerald-700 dark:text-emerald-300">
                    ✓ 통관 서류 제출 완료 {shortDate(p.customs.date)} · {p.customs.done_by}{p.customs.note ? ` — ${p.customs.note}` : ""}
                    {admin && <button type="button" className={cn(BTN, "ml-2")} disabled={!!busy} onClick={() => act("undo-c", () => purchaseRequest(`/${id}/undo/customs`, {}))}>취소</button>}
                  </p>
                ) : (
                  <DateNoteAction label="통관 서류 제출 완료" placeholder="메모(선택)" busy={busy === "customs"} disabled={!step.customs.ready || !!busy}
                                  onRun={(date, note) => act("customs", () => purchaseRequest(`/${id}/customs`, { date, note }))} />
                )}
              </Section>

              {/* 관세사 송금(사용자 2026-11-11) — 관세 때문에 관세사로 보내는 돈 */}
              <Section id="broker" n={num.broker} title="관세사 송금" done={step.broker.done} locked={lock("broker")} lockText={afterOrder}
                       mark={marks["broker"]} hint="관세·통관 비용을 관세사에 보냈으면 [관세사 송금 확인]을 누르세요.">
                {p.customs.broker ? (
                  <p className="text-emerald-700 dark:text-emerald-300">
                    ✓ 관세사 송금 확인 {shortDate(p.customs.broker.date)} · {p.customs.broker.by}{p.customs.broker.note ? ` — ${p.customs.broker.note}` : ""}
                    {!p.dropped && <button type="button" className={cn(BTN, "ml-2")} disabled={!!busy}
                                            onClick={() => { if (window.confirm("관세사 송금 확인을 취소할까요?")) void act("unbroker", () => purchaseRequest(`/${id}/broker/undo`, {})); }}>확인 취소</button>}
                  </p>
                ) : (
                  <DateNoteAction label="관세사 송금 확인" placeholder="메모(관세사·금액 등, 선택)" busy={busy === "broker"} disabled={!step.broker.ready || !!busy}
                                  onRun={(date, note) => act("broker", () => purchaseRequest(`/${id}/broker`, { date, note }))} />
                )}
              </Section>
            </>
          )}

          <Section id="receipt" n={num.receipt} title="입고" done={step.receipt.done} locked={lock("receipt")} lockText={afterOrder}
                   mark={marks["receipt"]}
                   hint="출하 기록처럼 입고 기록(입고일·운송·받은 사람·보관 위치·품목별 수량·사진·S/N)을 남깁니다. 나눠 들어오면 여러 번, 발주 수량을 다 받거나 '입고 완료'로 남기면 끝. S/N 은 기록을 남긴 뒤에도 [S/N 등록]으로 따로 적을 수 있습니다.">
            <ReceiptSection id={id} p={p} admin={admin} busy={busy} ready={step.receipt.ready}
                            onSave={(index, body) => act("receipt", () => purchaseRequest(index == null ? `/${id}/receipt` : `/${id}/receipt/${index}/edit`, body))}
                            onSerials={(index, lines) => act("receipt-sn", () => purchaseRequest(`/${id}/receipt/${index}/serials`, { lines }))}
                            onRemove={(index) => act("receipt", () => purchaseRequest(`/${id}/receipt/${index}/remove`, {}))} />
          </Section>

          {/* 거래명세서·세금계산서(국내, 선택 — 사용자 2026-11-11): 받으면 첨부, 못 끊는 건은 선결제·선발행 사유만 기록. 완료와는 상관없음 */}
          {!over && (["statement", "tax"] as const).map((kind) => (
            <Section key={kind} id={kind} n={num[kind]} title={kind === "statement" ? "거래명세서" : "세금계산서"} done={step[kind].done} optional
                     mark={marks[kind]} locked={lock(kind)} lockText="발주서를 발행한 뒤에 기록합니다."
                     hint="받으면 첨부하세요. 전달에 미리 지불(선결제)했거나 먼저 발행(선발행)돼 이번에 못 끊는 건은 그 사유만 기록합니다. 구매 완료와는 상관없는 선택 서류입니다.">
              <DocBox id={id} kind={kind} p={p} busy={busy} disabled={!step[kind].ready || !!busy}
                      onUpload={(f) => upload(kind, f)} onRemove={(n) => removeFile(kind, n)}
                      onSkip={(mode, date, note) => act(kind, () => purchaseRequest(`/${id}/${kind}`, { mode, date, note }))}
                      onUndo={() => act(`undo-${kind}`, () => purchaseRequest(`/${id}/undo/${kind}`, {}))} />
            </Section>
          ))}

          {!p.dropped && (
            <div className="flex justify-end">
              <button type="button" className={cn(BTN, "text-red-600")} disabled={!!busy} onClick={() => {
                const reason = window.prompt("구매를 취소하는 이유를 적어 주세요(되살리기는 관리자).");
                if (reason !== null) void act("drop", () => purchaseRequest(`/${id}/drop`, { dropped: true, reason }));
              }}>구매 취소</button>
            </div>
          )}

          {(p.history?.length ?? 0) > 0 && (
            // 늘 펼쳐 둔다(사용자 2026-11-11 — 눌러야 보이는 것도 번거로움)
            <section className="rounded-lg border border-border px-3 py-2">
              <h3 className="font-semibold">변경 이력 <span className="font-normal text-foreground-subtle">{p.history?.length}</span></h3>
              <ul className="mt-2 space-y-1 text-[12px]">
                {p.history?.map((e) => (
                  <li key={e.id} className="flex gap-2">
                    <span className="shrink-0 text-foreground-subtle">{stamp(e.created_at)}</span>
                    <span className="font-medium">{e.label}</span>
                    <span className="min-w-0 truncate text-foreground-muted" title={eventText(e.detail)}>{eventText(e.detail)}</span>
                    <span className="ml-auto shrink-0 text-foreground-subtle">{e.user_name}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>
      </aside>

      {poOpen && <PurchasePoEditor id={id} reissue={!!p.po.no} currentNo={p.po.no}
                                   currentExpected={p.expected_date} onClose={() => setPoOpen(false)}
                                   onIssued={(row) => { setP(row); setPoOpen(false); onChanged(); }} />}
    </div>
  );
}

/** 발주 품목 표(사용자 2026-11-11 — 발주서에 들어간 품목·수량·단가가 상세에도 보이게, 영업 건 '견적 품목'과 같은 표).
 *  지금 발주서(최신 판) 기준. 입고 칸 = 지금까지 받은 수량/발주 수량(입고 기록의 합). */
function PoItems({ p, over }: { p: PurchaseRow; over: boolean }) {
  const items = p.po.items;
  if (!items.length) {
    return p.po.forced ? (
      <p className="rounded-xl border border-amber-500/40 bg-amber-500/[0.06] px-3 py-2 text-[12.5px] text-amber-800 dark:text-amber-200">
        AI 판독이 실패해 품목 없이 파일만 발행했습니다(버그 {p.po.forced.bug_report || "신고"}). [발주서 고치기]에서 품목을 채워 다시 발행하면 입고 수량·송금 금액이 맞춰집니다.
      </p>
    ) : null;
  }
  const got = Object.fromEntries(p.receipt.lines.map((ln) => [ln.line_no, ln.received]));
  const amount = (q: number | null, pr: number | null) =>
    q == null || pr == null ? null : over ? Math.round(q * pr * 100) / 100 : Math.round(q * pr);
  const th = "py-1.5 pr-2 font-semibold whitespace-nowrap";
  return (
    <div className="rounded-xl border border-border p-3">
      <div className="mb-1 text-[12.5px] font-semibold text-foreground-muted">
        발주 품목 · {p.po.no} {over ? "· USD" : "· 부가세 별도"}
      </div>
      <table className="w-full border-collapse text-[12.5px]">
        <thead>
          <tr className="border-b border-border text-left text-foreground-muted">
            <th className={th}>Commodity</th>
            <th className={th}>Description</th>
            <th className={th}>{over ? "Remark" : "MOQ"}</th>
            <th className={cn(th, "text-right")}>수량</th>
            <th className={cn(th, "text-right")} title="지금까지 입고한 수량 / 발주 수량">입고</th>
            <th className={cn(th, "text-right")}>단가</th>
            <th className="py-1.5 text-right font-semibold">금액</th>
          </tr>
        </thead>
        <tbody>
          {items.map((it, i) => {
            const rec = got[i] ?? 0;
            const full = it.qty != null && rec >= it.qty;
            return (
              <tr key={i} className="border-b border-border/60 align-top">
                <td className="py-1.5 pr-2 font-medium">{it.commodity || <Muted>-</Muted>}</td>
                <td className="whitespace-pre-line py-1.5 pr-2">{it.description || <Muted>-</Muted>}</td>
                <td className="whitespace-pre-line py-1.5 pr-2">{(over ? it.remark : it.moq) || <Muted>-</Muted>}</td>
                <td className="py-1.5 pr-2 text-right tabular-nums">{it.qty ?? "-"}</td>
                <td className={cn("py-1.5 pr-2 text-right tabular-nums", full ? "text-emerald-700 dark:text-emerald-300" : rec ? "text-amber-700 dark:text-amber-300" : "text-foreground-subtle")}>
                  {rec}/{it.qty ?? "-"}
                </td>
                <td className="py-1.5 pr-2 text-right tabular-nums">{won(it.price, p.currency)}</td>
                <td className="py-1.5 text-right tabular-nums">{won(amount(it.qty, it.price), p.currency)}</td>
              </tr>
            );
          })}
        </tbody>
        <tfoot>
          <tr>
            <td colSpan={6} className="py-2 text-right font-semibold">합계{over ? "" : " (VAT 별도)"}</td>
            <td className="py-2 text-right font-bold tabular-nums">{won(p.po.total, p.currency)}</td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

/** 발주서 카드(사용자 2026-11-11 — 견적서 카드와 같게): [발주서 고치기 →] + 발행한 판마다 발행 시각·금액·[보기]·[다시 다운로드].
 *  판 = 발행·재발행(새 번호)마다 하나. 같은 번호로 고친 파일을 올리면 그 판이 바뀐다(견적서 '파일 교체'와 같음). */
function PoDocs({ id, p, onEdit }: { id: number; p: PurchaseRow; onEdit?: () => void }) {
  type Ver = { no: string; revision: number; total: number | null; issued_at: string | null; issued_by: string | null;
               file_name: string | null; upload: PurchaseRow["po"]["upload"]; current: boolean };
  const [viewing, setViewing] = useState<Ver | null>(null);
  const [saving, setSaving] = useState<Ver | null>(null);
  const po = p.po;
  const vers: Ver[] = [
    { no: po.no ?? "", revision: po.revision ?? 1, total: po.total, issued_at: po.issued_at, issued_by: po.issued_by,
      file_name: po.file_name, upload: po.upload, current: true },
    ...[...po.history].reverse().map((h) => ({ ...h, current: false })),
  ];
  const fileUrlOf = (v: Ver, fmt: string) => `/api/purchases/${id}/po/file?fmt=${fmt}&revision=${v.revision}`;
  const label = (v: Ver) => `${v.no} · v${v.revision}${v.issued_at ? ` · 발행 ${v.issued_at.slice(0, 10)}` : ""}`;
  return (
    <div className="rounded-xl border border-border p-3">
      <div className="mb-1.5 flex flex-wrap items-center gap-2">
        <b className="font-mono">{po.no}</b>
        <span className="text-foreground-muted">최신 v{po.revision ?? 1}</span>
        {onEdit && (
          <button type="button" onClick={onEdit}
                  className="ml-auto rounded-lg border border-accent/50 px-2.5 py-1 text-[12px] font-semibold text-accent hover:bg-accent/5">
            발주서 고치기 →
          </button>
        )}
      </div>
      <ul className="flex flex-col gap-1 text-[12.5px]">
        {vers.map((v) => (
          <li key={v.revision} className={cn("flex flex-wrap items-center gap-2", !v.current && "text-foreground-muted")}>
            <span className="font-mono font-medium">{v.no}</span>
            <span className="text-[11.5px] text-foreground-muted">v{v.revision}</span>
            {v.revision === 1 && <span className="text-foreground-subtle">(최초 발행)</span>}
            {v.current && <span className="rounded bg-blue-500/12 px-1.5 text-[11px] font-semibold text-blue-700 dark:text-blue-300">지금 발주서</span>}
            {v.upload && <span className="rounded bg-violet-500/12 px-1.5 text-[11px] font-semibold text-violet-700 dark:text-violet-300"
                               title={v.upload.filename}>고친 파일</span>}
            {v.current && po.forced && (
              <span className="rounded bg-amber-500/15 px-1.5 text-[11px] font-semibold text-amber-700 dark:text-amber-300"
                    title="AI 판독이 실패해 파일만 발행했습니다 — 품목은 [발주서 고치기]에서 채워 주세요">
                판독 실패 · 버그 {po.forced.bug_report || "신고"}
              </span>
            )}
            <span className="text-foreground-subtle">{v.issued_at ? stamp(v.issued_at) : ""}{v.issued_by ? ` · ${v.issued_by}` : ""}</span>
            <span>{won(v.total, p.currency)}{p.currency === "KRW" ? " (VAT 별도)" : ""}</span>
            <button type="button" className="ml-auto inline-flex items-center gap-1 font-semibold text-accent hover:underline" onClick={() => setViewing(v)}>
              <Icon name="eye" className="h-3.5 w-3.5" /> 보기
            </button>
            <button type="button" className="inline-flex items-center gap-1 font-semibold text-accent hover:underline" onClick={() => setSaving(v)}>
              <Icon name="download" className="h-3.5 w-3.5" /> 다시 다운로드
            </button>
          </li>
        ))}
      </ul>
      {viewing && <PreviewDialog url={fileUrlOf(viewing, "pdf")} title="발주서" sub={label(viewing)} onClose={() => setViewing(null)} />}
      {saving && (
        <DownloadDialog urlFor={(fmt) => fileUrlOf(saving, fmt)} fileName={saving.file_name || saving.no} title={`${saving.no} 다시 다운로드`}
                        formats={saving.upload?.mime === "application/pdf" ? ["pdf"] : undefined} onClose={() => setSaving(null)} />
      )}
    </div>
  );
}

function eventText(d: Record<string, unknown>): string {
  return Object.entries(d).map(([k, v]) => {
    const val = Array.isArray(v) ? v.join(", ") : typeof v === "number" ? v.toLocaleString("ko-KR") : String(v);
    return k === "filename" || k === "summary" || k === "no" ? val : `${k}: ${val}`;
  }).join(" · ");
}

// 상세 맨 위 '다음 할 일' 안내(사용자 2026-11-11) — 발주까지는 정해진 순서라 한 가지, 발주 뒤는 순서가 정해져 있지 않다
// (입고가 송금보다 먼저 올 수도 있음) → 꼭 할 남은 일을 모두 보여 주고 먼저 생긴 일부터 기록하라고 안내한다.
// 꼭 할 일이 다 끝났는데 국내 거래명세서·세금계산서가 비어 있으면 '선택'으로 안내한다(완료와는 상관없음).
type Mark = "now" | "left" | "opt" | undefined;
type GuideItem = { key: string; label: string; text: string };
type Guide = { title: string; ordered: boolean; optional?: boolean; sub?: string; items: GuideItem[] };
type StepMap = Record<string, { done: boolean; ready: boolean; label: string }>;

function guide(p: PurchaseRow, step: StepMap, over: boolean): Guide {
  const one = (key: string, text: string): Guide => ({ title: text, ordered: true, items: [{ key, label: step[key].label, text }] });
  if (p.dropped) return { title: "", ordered: true, items: [] };
  if (!step.quote.done) return one("quote", "공급사에서 받은 최종 견적서를 올리세요(없으면 [견적서 없이 넘어가기]).");
  if (!step.approval.done) return one("approval", p.approval.files.length ? "재가 서류가 올라왔습니다 — 대표님 재가가 끝났으면 [재가 완료]를 누르세요."
                                                                          : "대표님 재가 서류를 첨부하고 [재가 완료]를 누르세요(첨부 없이도 넘어갈 수 있습니다).");
  if (!step.order.done) return one("order", p.po.no ? "발주서는 발행했습니다 — 입고 예정일을 적으면 발주 단계가 끝납니다."
                                                    : over ? "[발주서 작성]으로 발주서를 발행하세요(납기일은 알면 적기, 엑셀에서 고친 파일은 작성 창의 [고친 파일로 발행])."
                                                    : "[발주서 작성]으로 발주서를 발행하고 입고 예정일을 적으세요(엑셀에서 고친 파일은 작성 창의 [고친 파일로 발행]).");
  const post = over ? ["pay", "ship", "customs", "broker", "receipt"] : ["pay", "ship", "receipt"];
  const confirmed = p.terms.filter((t) => t.confirmed).length;
  const detail: Record<string, string> = {
    pay: over ? "제조사에 물품 대금을 보냈으면 [제조사 송금 확인]을 누르세요(송금 확인증은 받으면 첨부, 선택)."
      : !p.terms.length ? "결제 조건(선금·중도금·잔금 %, 합 100%)을 정하고, 회차마다 송금하면 [송금 확인]을 누르세요."
      : confirmed ? `${confirmed}/${p.terms.length}회차 송금 확인됨 — 남은 회차는 송금하면 [송금 확인]을 누르세요.`
      : "회차마다 송금하면 [송금 확인]을 누르세요(예정일 지난 회차는 빨갛게 표시).",
    ship: "공급사에서 배송을 시작했다는 연락을 받으면 [배송 시작]을 누르세요(연락 없이 물건이 먼저 오면 입고 기록만 남겨도 됩니다).",
    customs: p.customs.no_origin ? "CI·Packing List 등 통관 기타 서류를 올리고 [통관 서류 제출 완료]를 누르세요(첨부 없이도 됩니다)."
      : "원산지 증명서를 첨부하고 [통관 서류 제출 완료]를 누르세요(없으면 [증빙 자료 없음], 첨부 없이도 완료 가능).",
    broker: "관세사에 관세·통관 비용을 보냈으면 [관세사 송금 확인]을 누르세요.",
    receipt: p.receipt.records.length ? `입고 ${p.receipt.records.length}회 기록됨 — 남은 수량이 들어오면 이어서 기록하세요(다 받으면 입고 완료).`
      : p.in_transit ? "배송 중입니다 — 물건이 들어오면 입고 기록(날짜·수량·사진·S/N)을 남기세요."
      : "물건이 들어오면 입고 기록(날짜·수량·사진·S/N)을 남기세요.",
  };
  // 한 번에 하나만(사용자 2026-11-11: 남은 일을 다 늘어놓으니 더 복잡해 보임) — 정석 순서의 다음 절차 + '순서는 바뀔 수 있음' 한 줄
  const next = post.find((k) => !step[k].done);
  if (!next) {
    const doc = over ? undefined : (["statement", "tax"] as const).find((k) => !step[k].done);
    if (!doc) return { title: "", ordered: false, items: [] };
    return { title: `구매 완료 — ${step[doc].label}는 받으면 첨부하고, 못 끊는 건은 [선결제]·[선발행]으로 기록하세요.`, ordered: false, optional: true,
             items: [{ key: doc, label: step[doc].label, text: "" }] };
  }
  const rest = post.filter((k) => k !== next && !step[k].done).length;
  return { title: `${step[next].label} — ${detail[next]}`, ordered: true, items: [{ key: next, label: step[next].label, text: detail[next] }],
           sub: rest ? "이후 순서는 바뀔 수 있습니다. 다른 일이 먼저 생기면 그 칸에 바로 기록하세요." : undefined };
}

const goTo = (key: string) => document.getElementById(`purchase-step-${key}`)?.scrollIntoView({ behavior: "smooth", block: "start" });

function NextStep({ g, children }: { g: Guide; children?: React.ReactNode }) {
  const many = g.items.length > 1;
  return (
    <div className={cn("flex flex-col gap-2 rounded-xl border px-4 py-3", g.optional ? "border-emerald-500/40 bg-emerald-500/[0.06]" : "border-accent/40 bg-accent/[0.06]")}>
      <div className="flex flex-wrap items-center gap-2">
        <span className={cn("rounded-full px-2 py-0.5 text-[11.5px] font-bold text-white", g.optional ? "bg-emerald-600" : "bg-accent")}>{g.optional ? "선택" : "다음 할 일"}</span>
        <span className="min-w-0 flex-1 font-medium text-foreground">{g.title}</span>
        {!many && (children ?? <button type="button" className={cn(BTN, "bg-surface")} onClick={() => goTo(g.items[0].key)}>바로 가기</button>)}
      </div>
      {g.sub && <p className="text-[12px] text-foreground-muted">{g.sub}</p>}
      {many && (
        <ul className="flex flex-col gap-1">
          {g.items.map((x) => (
            <li key={x.key} className="flex items-start gap-2 text-[12.5px]">
              <button type="button" onClick={() => goTo(x.key)}
                      className="shrink-0 rounded-md border border-accent/40 bg-surface px-2 py-0.5 text-[12px] font-semibold text-accent hover:bg-accent/5">
                {x.label} →
              </button>
              <span className="pt-0.5 text-foreground-muted">{x.text}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// 단계 칸 — 늘 펼쳐 둔다(사용자 2026-11-11: 펼치기 누르는 것도 번거로움). 지금 할 단계·남은 일만 파란 테두리로 표시.
// optional = 거래명세서·세금계산서(완료와 상관없는 선택 서류) — 제목 옆 '선택' 표시
function Section({ id, n, title, done, mark, locked, lockText, hint, optional, children }: {
  id: string; n: number; title: string; done: boolean; mark?: Mark; locked?: boolean; lockText?: string; hint: string; optional?: boolean;
  children: React.ReactNode;
}) {
  const now = mark === "now" || mark === "left";
  return (
    <section id={`purchase-step-${id}`}
             className={cn("scroll-mt-28 rounded-xl border px-4 py-3",
               now ? "border-accent/60 bg-accent/[0.03] shadow-[0_0_0_3px_rgba(37,99,235,0.08)]"
                 : done ? "border-emerald-500/40 bg-emerald-500/[0.04]" : optional ? "border-dashed border-border" : "border-border")}>
      <h3 className="flex items-center gap-2 text-[14px] font-bold">
        <span className={cn("grid h-6 w-6 place-items-center rounded-full text-[12px]",
          done ? "bg-emerald-600 text-white" : now ? "bg-accent text-white" : "bg-foreground/8")}>{done ? "✓" : n}</span>
        {title}
        {optional && <span className="rounded-full bg-foreground/8 px-2 py-0.5 text-[11px] font-semibold text-foreground-muted">선택</span>}
        {now && <span className="rounded-full bg-accent/12 px-2 py-0.5 text-[11px] font-bold text-accent">{mark === "now" ? "지금 할 단계" : "남은 일"}</span>}
      </h3>
      <p className="mt-1 text-[12px] text-foreground-muted">{hint}</p>
      <div className="mt-2 space-y-2">{locked ? <Muted>{lockText}</Muted> : children}</div>
    </section>
  );
}

/** 해외 통관 서류(사용자 2026-11-11) — 1순위 원산지 증명서, 오른쪽 [증빙 자료 없음]을 누르면 통관 기타 서류(CI·Packing List 등)를
 *  [항목 추가]로 서류 이름을 적고 하나씩 올린다. 첨부는 모두 선택(완료 버튼은 첨부 없이도 됨). */
function CustomsDocs({ id, p, admin, busy, ready, onUpload, onRemove, onNoOrigin }: {
  id: number; p: PurchaseRow; admin: boolean; busy: string | null; ready: boolean;
  onUpload: (kind: FileKind, f: File, label?: string) => Promise<void>; onRemove: (kind: FileKind, n: number) => void;
  onNoOrigin: (value: boolean) => void;
}) {
  const cu = p.customs;
  const [rows, setRows] = useState<{ key: number; label: string }[]>([]);
  const canRemove = admin || !cu.done_at;
  const off = !ready || !!busy || p.dropped;
  const showEtc = !!cu.no_origin || cu.etc_files.length > 0;
  const addRow = () => setRows((r) => [...r, { key: Date.now(), label: "" }]);
  return (
    <div className="space-y-2">
      <div className="rounded-lg border border-border px-3 py-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded bg-accent/12 px-1.5 text-[11px] font-bold text-accent">1순위</span>
          <b>원산지 증명서</b>
          <span className="ml-auto flex flex-wrap items-center gap-1.5">
            {!cu.no_origin && (
              <UploadButton label="원산지 증명서 첨부" busy={busy === "file-customs"} disabled={off} onFile={(f) => void onUpload("customs", f)}
                            primary={!cu.files.length} />
            )}
            {cu.no_origin ? (
              <>
                <span className="rounded bg-amber-500/12 px-1.5 py-0.5 text-[11.5px] font-semibold text-amber-700 dark:text-amber-300">증빙 자료 없음</span>
                <button type="button" className="text-[12px] text-foreground-subtle hover:underline" disabled={off} onClick={() => onNoOrigin(false)}>되돌리기</button>
              </>
            ) : !cu.files.length && (
              <button type="button" className={BTN} disabled={off} onClick={() => { onNoOrigin(true); if (!cu.etc_files.length && !rows.length) addRow(); }}
                      title="원산지 증명서가 없으면 통관 기타 서류(CI·Packing List 등)를 올립니다">증빙 자료 없음</button>
            )}
          </span>
        </div>
        {cu.files.length > 0 && <div className="mt-2"><FileList id={id} kind="customs" files={cu.files} canRemove={canRemove} onRemove={(n) => onRemove("customs", n)} /></div>}
      </div>
      {showEtc && (
        <div className="rounded-lg border border-border px-3 py-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded bg-foreground/8 px-1.5 text-[11px] font-bold text-foreground-muted">2순위</span>
            <b>통관 기타 서류</b>
            <span className="text-[12px] text-foreground-subtle">예: {CUSTOMS_DOC_EXAMPLES.join(" · ")} — 여러 개 올릴 수 있습니다</span>
          </div>
          {cu.etc_files.length > 0 && (
            <ul className="mt-2 space-y-1">
              {cu.etc_files.map((f, n) => (
                <li key={n} className="flex items-center gap-2 rounded-lg border border-border px-2.5 py-1.5">
                  <span className="shrink-0 rounded bg-sky-500/12 px-1.5 text-[11px] font-semibold text-sky-700 dark:text-sky-300">{f.label}</span>
                  <a href={fileUrl(id, "customs_etc", n)} target="_blank" rel="noreferrer" className="min-w-0 truncate text-accent hover:underline">{f.filename}</a>
                  <span className="ml-auto shrink-0 text-[11.5px] text-foreground-subtle">{f.uploaded_by} · {stamp(f.uploaded_at)}</span>
                  {canRemove && (
                    <button type="button" onClick={() => { if (window.confirm(`'${f.filename}'을(를) 지울까요?`)) onRemove("customs_etc", n); }}
                            className="shrink-0 text-[11.5px] text-foreground-subtle hover:text-red-600">지우기</button>
                  )}
                </li>
              ))}
            </ul>
          )}
          <datalist id="purchase-customs-docs">{CUSTOMS_DOC_EXAMPLES.map((x) => <option key={x} value={x} />)}</datalist>
          {rows.map((r) => (
            <div key={r.key} className="mt-2 flex flex-wrap items-center gap-1.5">
              <input value={r.label} list="purchase-customs-docs" maxLength={40} placeholder="서류 이름(예: CI)" aria-label="서류 이름"
                     onChange={(e) => setRows((xs) => xs.map((x) => (x.key === r.key ? { ...x, label: e.target.value } : x)))}
                     className="h-8 w-[180px] rounded-lg border border-border bg-surface px-2 text-[12.5px]" />
              <UploadButton label="파일 첨부" busy={busy === "file-customs_etc"} disabled={off || !r.label.trim()}
                            onFile={(f) => void onUpload("customs_etc", f, r.label.trim()).then(() => setRows((xs) => xs.filter((x) => x.key !== r.key)))} />
              <button type="button" className="text-[12px] text-foreground-subtle hover:text-red-600" onClick={() => setRows((xs) => xs.filter((x) => x.key !== r.key))}>빼기</button>
            </div>
          ))}
          <button type="button" className={cn(BTN, "mt-2")} disabled={off} onClick={addRow}>+ 항목 추가</button>
        </div>
      )}
    </div>
  );
}

/** 거래명세서·세금계산서(국내, 선택 — 사용자 2026-11-11). 첨부하면 '첨부', 못 끊는 건은 [선결제]·[선발행]으로 사유만 기록. */
function DocBox({ id, kind, p, busy, disabled, onUpload, onRemove, onSkip, onUndo }: {
  id: number; kind: "statement" | "tax"; p: PurchaseRow; busy: string | null; disabled: boolean;
  onUpload: (f: File) => void; onRemove: (n: number) => void; onSkip: (mode: string, date: string, note: string) => void; onUndo: () => void;
}) {
  const doc = kind === "statement" ? p.statement : p.tax_invoice;
  const st = p.docs[kind];
  const name = kind === "statement" ? "거래명세서" : "세금계산서";
  const [mode, setMode] = useState<keyof typeof DOC_SKIP_MODES | null>(null);
  const [date, setDate] = useState(todayIso());
  const [note, setNote] = useState("");
  return (
    <div className="space-y-2">
      <FileList id={id} kind={kind} files={doc.files} canRemove={!p.dropped} onRemove={onRemove} />
      {st.state === "skip" && (
        <p className="flex flex-wrap items-center gap-2 rounded-lg bg-indigo-500/[0.07] px-3 py-2">
          <span className="rounded bg-indigo-500/15 px-1.5 text-[11.5px] font-bold text-indigo-700 dark:text-indigo-300">{st.label}</span>
          <span className="text-foreground-muted">첨부 없이 넘어감 · {shortDate(doc.date)} · {doc.done_by}{st.note ? ` — ${st.note}` : ""}</span>
          {!p.dropped && <button type="button" className="ml-auto text-[12px] text-foreground-subtle hover:underline" disabled={!!busy} onClick={onUndo}>되돌리기</button>}
        </p>
      )}
      {mode ? (
        <div className="space-y-1.5 rounded-lg border border-dashed border-indigo-500/50 bg-indigo-500/[0.04] px-3 py-2">
          <p className="text-[12.5px]"><b>{DOC_SKIP_MODES[mode]}</b>로 기록 — {DOC_SKIP_HELP[mode]}. 첨부 없이 넘어간 것으로 리스트에도 표시됩니다.</p>
          <div className="flex flex-wrap items-center gap-1.5">
            <input type="date" value={date} onChange={(e) => setDate(e.target.value)} className="h-8 rounded-lg border border-border bg-surface px-2 text-[12.5px]" />
            <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={300} placeholder="메모(예: 10월분 선결제, 선택)"
                   className="h-8 min-w-[160px] flex-1 rounded-lg border border-border bg-surface px-2 text-[12.5px]" />
            <button type="button" className={BTN} onClick={() => setMode(null)}>취소</button>
            <button type="button" className={PRIMARY} disabled={disabled || !date} onClick={() => { onSkip(mode, date, note); setMode(null); setNote(""); }}>
              {busy === kind ? "저장 중…" : "기록"}
            </button>
          </div>
        </div>
      ) : !p.dropped && (
        <div className="flex flex-wrap items-center gap-1.5">
          <UploadButton label={`${name} 첨부`} busy={busy === `file-${kind}`} disabled={disabled} onFile={onUpload} />
          {st.state === "none" && (
            <>
              <span className="text-[12px] text-foreground-subtle">못 끊는 경우</span>
              {(Object.keys(DOC_SKIP_MODES) as (keyof typeof DOC_SKIP_MODES)[]).map((k) => (
                <button key={k} type="button" className={BTN} disabled={disabled} title={DOC_SKIP_HELP[k]} onClick={() => setMode(k)}>{DOC_SKIP_MODES[k]}</button>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}

/** S/N 칸(사용자 2026-11-11 — 출하처럼 입고 품목마다). 수량만큼 칸, 여러 줄을 붙여 넣으면 그 칸부터 아래로 나눠 들어간다. 빈 칸은 미등록. */
function SnInputs({ qty, value, onChange, disabled }: { qty: number; value: string[]; onChange: (v: string[]) => void; disabled?: boolean }) {
  const n = Math.max(Math.ceil(qty || 0), value.length, 1);
  const cells = Array.from({ length: n }, (_, i) => value[i] ?? "");
  const set = (i: number, v: string) => onChange(cells.map((x, j) => (j === i ? v : x)));
  const paste = (i: number, text: string) => {
    const vals = text.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
    if (vals.length < 2) return false;
    const next = [...cells];
    vals.forEach((v, k) => { next[i + k] = v; });
    onChange(next);
    return true;
  };
  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-1.5">
      {cells.map((v, i) => (
        <input key={i} value={v} maxLength={80} disabled={disabled} placeholder={`${i + 1}번 S/N`} aria-label={`${i + 1}번 S/N`}
               onPaste={(e) => { if (paste(i, e.clipboardData.getData("text"))) e.preventDefault(); }}
               onChange={(e) => set(i, e.target.value)}
               className="h-8 rounded-lg border border-border bg-surface px-2 font-mono text-[12px] outline-none focus:border-accent" />
      ))}
    </div>
  );
}

const snCount = (xs: string[] | undefined) => (xs ?? []).filter((x) => x.trim()).length;

/** [S/N 등록 n/수량] — 입고 S/N 은 선택이라 다 못 적어도 빨갛게 하지 않는다 */
function SnToggle({ count, need, open, onClick, disabled }: { count: number; need: number; open: boolean; onClick: () => void; disabled?: boolean }) {
  const full = need > 0 && count >= need;
  return (
    <button type="button" onClick={onClick} disabled={disabled} aria-expanded={open}
            className={cn("inline-flex h-8 shrink-0 items-center gap-1 rounded-lg border px-2.5 text-[12px] font-semibold disabled:opacity-40",
              full ? "border-emerald-500/40 text-emerald-700 dark:text-emerald-300" : "border-border text-foreground-muted hover:bg-foreground/5")}>
      {full ? "✓ " : ""}S/N 등록 <span className="font-mono">{count}/{need}</span>
    </button>
  );
}

function FileList({ id, kind, files, currentLast, canRemove, onRemove }: {
  id: number; kind: FileKind; files: PurchaseFile[]; currentLast?: boolean; canRemove: boolean; onRemove: (n: number) => void;
}) {
  if (!files.length) return null;
  return (
    <ul className="space-y-1">
      {files.map((f, n) => {
        const old = currentLast && n < files.length - 1;
        return (
          <li key={n} className={cn("flex items-center gap-2 rounded-lg border border-border px-2.5 py-1.5", old && "opacity-60")}>
            {currentLast && <span className={cn("rounded px-1.5 text-[11px] font-semibold", old ? "bg-foreground/8" : "bg-accent/12 text-accent")}>{old ? "이전" : "지금 견적서"}</span>}
            <a href={fileUrl(id, kind, n)} target="_blank" rel="noreferrer" className={cn("min-w-0 truncate text-accent hover:underline", old && "line-through")}>{f.filename}</a>
            <span className="ml-auto shrink-0 text-[11.5px] text-foreground-subtle">{f.uploaded_by} · {stamp(f.uploaded_at)}</span>
            {canRemove && (
              <button type="button" onClick={() => { if (window.confirm(`'${f.filename}'을(를) 지울까요?`)) onRemove(n); }}
                      className="shrink-0 text-[11.5px] text-foreground-subtle hover:text-red-600">지우기</button>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function UploadButton({ label, busy, disabled, onFile, primary, accept }: {
  label: string; busy: boolean; disabled: boolean; onFile: (f: File) => void; primary?: boolean; accept?: string;
}) {
  const ref = useRef<HTMLInputElement>(null);
  return (
    <>
      <input ref={ref} type="file" hidden accept={accept ?? ".pdf,.png,.jpg,.jpeg,.webp,.xlsx,.xls,.docx,.doc,.hwp,.hwpx"}
             onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) onFile(f); }} />
      <button type="button" className={primary ? PRIMARY : BTN} disabled={disabled} onClick={() => ref.current?.click()}>
        {busy ? "올리는 중…" : label}
      </button>
    </>
  );
}

function DateAction({ label, busy, disabled, title, onRun }: { label: string; busy: boolean; disabled: boolean; title?: string; onRun: (d: string) => void }) {
  const [date, setDate] = useState(todayIso());
  return (
    <span className="inline-flex items-center gap-1.5">
      <input type="date" value={date} onChange={(e) => setDate(e.target.value)} className="h-8 rounded-lg border border-border bg-surface px-2 text-[12.5px]" />
      <button type="button" className={PRIMARY} disabled={disabled || !date} title={title} onClick={() => onRun(date)}>{busy ? "저장 중…" : label}</button>
    </span>
  );
}

const AFTER_ORDER = "발주서를 발행하고 입고 예정일을 적은 뒤에 기록합니다.";

/** 입고 예정일 — 발주 단계에서 꼭(사용자 2026-11-02). 발행한 뒤 바꾸면 이전 날짜·사유가 남는다. */
function ExpectedDateEditor({ p, over, busy, onSave }: { p: PurchaseRow; over: boolean; busy: boolean; onSave: (date: string, reason: string) => void }) {
  // 해외는 '납기일'(선택, 사용자 2026-11-11) — 비어 있어도 입력 칸을 펼쳐 재촉하지 않는다
  const name = over ? "납기일" : "입고 예정일";
  const [edit, setEdit] = useState(!p.expected_date && !over);
  const [date, setDate] = useState(p.expected_date ?? "");
  const [reason, setReason] = useState("");
  const late = !!p.expected_date && !p.receipt.done && p.expected_date < todayIso();
  if (!edit) {
    return (
      <div className={cn("flex flex-wrap items-center gap-2 rounded-lg px-3 py-2", late ? "bg-red-500/10" : "bg-foreground/[0.04]")}>
        <span className="font-semibold">{name}</span>
        <span className={cn("tabular-nums", late && "font-semibold text-red-600 dark:text-red-400")}>{p.expected_date ?? <Muted>안 적음(선택)</Muted>}</span>
        {late && <span className="text-[12px] text-red-600 dark:text-red-400">지났습니다 — 입고됐으면 입고 기록을, 늦어지면 날짜를 바꿔 주세요</span>}
        {p.expected_history.length > 1 && (
          <span className="text-[11.5px] text-foreground-subtle" title={p.expected_history.map((h) => `${h.before ?? "-"} → ${h.date} (${h.by}${h.reason ? `, ${h.reason}` : ""})`).join("\n")}>
            변경 {p.expected_history.length - 1}회
          </span>
        )}
        {!p.dropped && <button type="button" className={cn(BTN, "ml-auto bg-surface")} disabled={busy} onClick={() => setEdit(true)}>{p.expected_date ? `${name} 바꾸기` : `${name} 적기`}</button>}
      </div>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-1.5 rounded-lg border border-amber-500/50 bg-amber-500/[0.06] px-3 py-2">
      <span className="font-semibold">{name}{over ? "" : " *"}</span>
      <input type="date" value={date} onChange={(e) => setDate(e.target.value)} className="h-8 rounded-lg border border-border bg-surface px-2 text-[12.5px]" />
      {p.expected_date && <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="바꾸는 이유(예: 공급사 납기 지연)"
                                 className="h-8 min-w-[200px] flex-1 rounded-lg border border-border bg-surface px-2 text-[12.5px]" />}
      <button type="button" className={PRIMARY} disabled={busy || !date} onClick={() => { onSave(date, reason); setEdit(false); }}>저장</button>
      {p.expected_date && <button type="button" className={BTN} onClick={() => setEdit(false)}>취소</button>}
      {!p.expected_date && over && <button type="button" className={BTN} onClick={() => setEdit(false)}>취소</button>}
      {!p.expected_date && !over && <span className="w-full text-[11.5px] text-amber-800 dark:text-amber-200">입고 예정일을 적어야 다음 단계로 넘어갑니다.</span>}
    </div>
  );
}

function DateNoteAction({ label, placeholder, busy, disabled, title, onRun }: {
  label: string; placeholder: string; busy: boolean; disabled: boolean; title?: string; onRun: (date: string, note: string) => void;
}) {
  const [date, setDate] = useState(todayIso());
  const [note, setNote] = useState("");
  return (
    <span className="inline-flex flex-1 flex-wrap items-center gap-1.5">
      <input type="date" value={date} onChange={(e) => setDate(e.target.value)} className="h-8 rounded-lg border border-border bg-surface px-2 text-[12.5px]" />
      <input value={note} onChange={(e) => setNote(e.target.value)} placeholder={placeholder}
             className="h-8 min-w-[160px] flex-1 rounded-lg border border-border bg-surface px-2 text-[12.5px]" />
      <button type="button" className={PRIMARY} disabled={disabled || !date} title={title} onClick={() => onRun(date, note)}>{busy ? "저장 중…" : label}</button>
    </span>
  );
}

type ReceiptBody = {
  date: string; carrier: string; tracking: string; receiver: string; location: string; note: string; final: boolean;
  lines: { line_no: number; qty: number | null; photos: string[]; serials: string[] }[];
};

const photoUrl = (id: number, name: string) => `/api/purchases/${id}/receipt-photos/${name}`;

/** 입고 — 기록 목록(품목·수량·사진·S/N, [수정]·[S/N 등록]) + 새 입고 기록 칸(출하 기록 작성과 같은 꼴). */
function ReceiptSection({ id, p, admin, busy, ready, onSave, onSerials, onRemove }: {
  id: number; p: PurchaseRow; admin: boolean; busy: string | null; ready: boolean;
  onSave: (index: number | null, body: ReceiptBody) => void; onRemove: (index: number) => void;
  onSerials: (index: number, lines: { line_no: number; serials: string[] }[]) => void;
}) {
  const [editing, setEditing] = useState<number | null>(null);
  const [snEdit, setSnEdit] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);
  const recs = p.receipt.records;
  return (
    <div className="space-y-2">
      {p.receipt.lines.length > 0 && (
        <div className="flex flex-wrap gap-1.5 text-[12px]">
          {p.receipt.lines.map((ln) => {
            const full = ln.ordered != null && ln.received >= ln.ordered;
            return (
              <span key={ln.line_no} className={cn("rounded-md border px-2 py-0.5", full ? "border-emerald-500/40 text-emerald-700 dark:text-emerald-300" : "border-border")}>
                {ln.name} <b className="tabular-nums">{ln.received}</b>/{ln.ordered ?? "-"}
              </span>
            );
          })}
        </div>
      )}
      {recs.map((r, i) => (editing === i ? (
        <ReceiptForm key={i} p={p} id={id} initial={r} busy={busy === "receipt"} onCancel={() => setEditing(null)}
                     onSave={(b) => { onSave(i, b); setEditing(null); }} />
      ) : (
        <div key={i} className="rounded-lg border border-border px-3 py-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-semibold">{i + 1}차 입고 {shortDate(r.date)}</span>
            {r.final && <span className="rounded bg-emerald-500/12 px-1.5 text-[11px] font-semibold text-emerald-700 dark:text-emerald-300">입고 완료</span>}
            <span className="text-[12px] text-foreground-muted">
              {[r.carrier, r.tracking.join(", "), r.receiver && `받은 사람 ${r.receiver}`, r.location && `보관 ${r.location}`].filter(Boolean).join(" · ")}
            </span>
            <span className="ml-auto flex gap-1.5">
              {/* 입고 기록을 남긴 뒤에도(입고 완료 뒤에도) S/N 만 따로 등록(사용자 2026-11-11) */}
              {!p.dropped && r.lines.some((ln) => ln.qty > 0) && (
                <SnToggle count={r.lines.reduce((n, ln) => n + snCount(ln.serials), 0)} need={r.lines.reduce((n, ln) => n + Math.ceil(ln.qty), 0)}
                          open={snEdit === i} disabled={!!busy} onClick={() => setSnEdit(snEdit === i ? null : i)} />
              )}
              {!p.dropped && <button type="button" className={BTN} disabled={!!busy} onClick={() => setEditing(i)}>수정</button>}
              {admin && <button type="button" className={BTN} disabled={!!busy}
                                onClick={() => { if (window.confirm(`${i + 1}차 입고 기록을 지울까요?`)) onRemove(i); }}>지우기</button>}
            </span>
          </div>
          <ul className="mt-1 space-y-1 text-[12.5px]">
            {r.lines.map((ln) => (
              <li key={ln.line_no} className="flex flex-wrap items-center gap-2">
                <span>{ln.name}</span><b className="tabular-nums">× {ln.qty}</b>
                {(ln.serials ?? []).map((sn) => (
                  <span key={sn} className="rounded bg-foreground/[0.06] px-1.5 font-mono text-[11.5px]">{sn}</span>
                ))}
                {ln.photos.map((ph) => (
                  <a key={ph} href={photoUrl(id, ph)} target="_blank" rel="noreferrer">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={photoUrl(id, ph)} alt={`${ln.name} 입고 사진`} className="h-10 w-10 rounded object-cover" />
                  </a>
                ))}
              </li>
            ))}
          </ul>
          {snEdit === i && (
            <SerialEditor record={r} busy={busy === "receipt-sn"} onCancel={() => setSnEdit(null)}
                          onSave={(lines) => { onSerials(i, lines); setSnEdit(null); }} />
          )}
          {r.note && <p className="mt-1 text-[12px] text-foreground-muted">{r.note}</p>}
          <p className="mt-0.5 text-[11px] text-foreground-subtle">기록 {r.by}{r.edited_by ? ` · 수정 ${r.edited_by}` : ""}</p>
        </div>
      )))}
      {!p.receipt.done && !p.dropped && (adding ? (
        <ReceiptForm p={p} id={id} busy={busy === "receipt"} onCancel={() => setAdding(false)}
                     onSave={(b) => { onSave(null, b); setAdding(false); }} />
      ) : (
        <button type="button" className={PRIMARY} disabled={!ready || !!busy} onClick={() => setAdding(true)}>
          {recs.length ? "다음 입고 기록" : "입고 기록 작성"}
        </button>
      ))}
    </div>
  );
}

function ReceiptForm({ id, p, initial, busy, onSave, onCancel }: {
  id: number; p: PurchaseRow; initial?: PurchaseRow["receipt"]["records"][number]; busy: boolean;
  onSave: (b: ReceiptBody) => void; onCancel: () => void;
}) {
  // 기본 수량 = 남은 수량(새 기록) · 그 기록의 수량(수정)
  const [b, setB] = useState<ReceiptBody>(() => ({
    date: initial?.date ?? todayIso(), carrier: initial?.carrier ?? "", tracking: initial?.tracking.join(" ") ?? "",
    receiver: initial?.receiver ?? "", location: initial?.location ?? "", note: initial?.note ?? "", final: initial?.final ?? false,
    lines: p.receipt.lines.map((ln) => {
      const mine = initial?.lines.find((x) => x.line_no === ln.line_no);
      const left = Math.max(0, (ln.ordered ?? 0) - ln.received);
      return { line_no: ln.line_no, qty: initial ? mine?.qty ?? null : left || null, photos: mine?.photos ?? [], serials: mine?.serials ?? [] };
    }),
  }));
  const [uploading, setUploading] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [snOpen, setSnOpen] = useState<number | null>(null);
  const setLine = (i: number, patch: Partial<ReceiptBody["lines"][number]>) =>
    setB({ ...b, lines: b.lines.map((x, j) => (j === i ? { ...x, ...patch } : x)) });
  const addPhoto = async (i: number, f: File) => {
    setUploading(i);
    setErr(null);
    try {
      const fd = new FormData();
      fd.append("file", f);
      const r = await fetch(`/api/purchases/${id}/receipt-photos`, { method: "POST", body: fd });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof d?.detail === "string" ? d.detail : "사진을 올리지 못했습니다.");
      setLine(i, { photos: [...b.lines[i].photos, d.id as string] });
    } catch (e) {
      setErr(e instanceof Error ? e.message : "사진을 올리지 못했습니다.");
    } finally {
      setUploading(null);
    }
  };
  // 이번 기록까지 합쳐 발주 수량을 다 채우면 '입고 완료'가 자동으로 된다 — 체크는 일부만 받고 끝낼 때
  const fills = p.receipt.lines.every((ln, i) => {
    const prev = ln.received - (initial?.lines.find((x) => x.line_no === ln.line_no)?.qty ?? 0);
    return prev + (b.lines[i]?.qty ?? 0) >= (ln.ordered ?? 0);
  });
  return (
    <div className="space-y-2 rounded-lg border border-dashed border-accent/50 bg-accent/[0.03] px-3 py-2">
      <div className="grid grid-cols-2 gap-1.5 text-[12px] text-foreground-muted">
        <label className="grid gap-0.5">입고일 *<input type="date" className={cn(INPUT, "h-8")} value={b.date} onChange={(e) => setB({ ...b, date: e.target.value })} /></label>
        <label className="grid gap-0.5">운송사<input className={cn(INPUT, "h-8")} value={b.carrier} placeholder="예: 경동택배·DHL·포워더" onChange={(e) => setB({ ...b, carrier: e.target.value })} /></label>
        <label className="grid gap-0.5">운송장 번호(여러 개는 띄어서)<input className={cn(INPUT, "h-8")} value={b.tracking} onChange={(e) => setB({ ...b, tracking: e.target.value })} /></label>
        <label className="grid gap-0.5">받은 사람<input className={cn(INPUT, "h-8")} value={b.receiver} onChange={(e) => setB({ ...b, receiver: e.target.value })} /></label>
        <label className="col-span-2 grid gap-0.5">보관 위치<input className={cn(INPUT, "h-8")} value={b.location} placeholder="예: 본사 2층 창고" onChange={(e) => setB({ ...b, location: e.target.value })} /></label>
      </div>
      <div className="space-y-1.5">
        {p.receipt.lines.map((ln, i) => (
          <div key={ln.line_no} className="rounded-md bg-background px-2 py-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <span className="min-w-0 flex-1 truncate text-[12.5px] font-medium" title={ln.name}>{ln.name}</span>
            <span className="text-[11.5px] text-foreground-subtle">발주 {ln.ordered ?? "-"} · 받음 {ln.received}</span>
            <input className="h-8 w-20 rounded-lg border border-border bg-surface px-2 text-right outline-none focus:border-accent" inputMode="decimal" placeholder="수량" value={b.lines[i].qty ?? ""}
                   onChange={(e) => { const n = Number(e.target.value); setLine(i, { qty: e.target.value.trim() === "" || Number.isNaN(n) ? null : n }); }} />
            {b.lines[i].photos.map((ph) => (
              <span key={ph} className="relative">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={photoUrl(id, ph)} alt="입고 사진" className="h-9 w-9 rounded object-cover" />
                <button type="button" aria-label="사진 빼기" onClick={() => setLine(i, { photos: b.lines[i].photos.filter((x) => x !== ph) })}
                        className="absolute -right-1 -top-1 grid h-4 w-4 place-items-center rounded-full bg-black/70 text-[10px] text-white">×</button>
              </span>
            ))}
            <UploadButton label={uploading === i ? "올리는 중…" : "+ 사진"} busy={false} disabled={uploading != null} accept=".jpg,.jpeg,.png,.webp"
                          onFile={(f) => void addPhoto(i, f)} />
            <SnToggle count={snCount(b.lines[i].serials)} need={Math.ceil(b.lines[i].qty ?? 0)} open={snOpen === i}
                      disabled={!(b.lines[i].qty ?? 0)} onClick={() => setSnOpen(snOpen === i ? null : i)} />
          </div>
          {snOpen === i && (b.lines[i].qty ?? 0) > 0 && (
            <div className="mt-1.5">
              <SnInputs qty={b.lines[i].qty ?? 0} value={b.lines[i].serials} onChange={(v) => setLine(i, { serials: v })} />
            </div>
          )}
          </div>
        ))}
      </div>
      <textarea className={cn(INPUT, "h-14 py-1.5 text-[12.5px]")} placeholder="메모(포장 상태·불량 등)" value={b.note} onChange={(e) => setB({ ...b, note: e.target.value })} />
      <label className="flex items-center gap-1.5 text-[12.5px]">
        <input type="checkbox" checked={b.final || fills} disabled={fills} onChange={(e) => setB({ ...b, final: e.target.checked })} />
        이번 입고로 입고 완료 {fills ? "(발주 수량을 모두 받아 자동으로 완료)" : "(남은 수량이 있어도 더 받지 않을 때)"}
      </label>
      {err && <p className="text-[12px] text-red-600">{err}</p>}
      <div className="flex justify-end gap-1.5">
        <button type="button" className={BTN} onClick={onCancel}>취소</button>
        <button type="button" className={PRIMARY} disabled={busy || !b.date || uploading != null}
                onClick={() => onSave({ ...b, lines: b.lines.map((x) => ({ ...x, serials: x.serials.map((v) => v.trim()).filter(Boolean) })) })}>
          {busy ? "저장 중…" : initial ? "입고 기록 고치기" : "입고 기록 저장"}
        </button>
      </div>
    </div>
  );
}

/** 남긴 입고 기록 하나의 S/N 만 고치기 — 품목 줄마다 입고 수량만큼 칸(사용자 2026-11-11: 입고 시트 작성 뒤에도 따로 등록) */
function SerialEditor({ record, busy, onSave, onCancel }: {
  record: PurchaseRow["receipt"]["records"][number]; busy: boolean;
  onSave: (lines: { line_no: number; serials: string[] }[]) => void; onCancel: () => void;
}) {
  const lines = record.lines.filter((ln) => ln.qty > 0);
  const [v, setV] = useState<Record<number, string[]>>(() => Object.fromEntries(lines.map((ln) => [ln.line_no, ln.serials ?? []])));
  return (
    <div className="mt-2 space-y-2 rounded-lg border border-dashed border-accent/50 bg-accent/[0.03] px-3 py-2">
      <p className="text-[12px] text-foreground-muted">S/N 을 한 칸에 하나씩 적습니다. 여러 줄을 복사해 붙여 넣으면 아래 칸으로 나눠 들어갑니다. 비운 칸은 미등록.</p>
      {lines.map((ln) => (
        <div key={ln.line_no} className="space-y-1">
          <div className="text-[12.5px] font-medium">{ln.name} <span className="text-foreground-subtle">× {ln.qty} · {snCount(v[ln.line_no])}개 적음</span></div>
          <SnInputs qty={ln.qty} value={v[ln.line_no] ?? []} onChange={(x) => setV({ ...v, [ln.line_no]: x })} disabled={busy} />
        </div>
      ))}
      <div className="flex justify-end gap-1.5">
        <button type="button" className={BTN} onClick={onCancel}>취소</button>
        <button type="button" className={PRIMARY} disabled={busy}
                onClick={() => onSave(lines.map((ln) => ({ line_no: ln.line_no, serials: (v[ln.line_no] ?? []).map((x) => x.trim()).filter(Boolean) })))}>
          {busy ? "저장 중…" : "S/N 저장"}
        </button>
      </div>
    </div>
  );
}

function InfoEditor({ p, busy, onSave }: {
  p: PurchaseRow; busy: boolean; onSave: (v: { supplier: string; title: string; owner: string; note: string; region: PurchaseRow["region"] }) => void;
}) {
  const [edit, setEdit] = useState(false);
  const [v, setV] = useState({ supplier: p.supplier, title: p.title, owner: p.owner, note: p.note, region: p.region });
  if (!edit) {
    return (
      <div className="flex items-start gap-2 text-[12.5px] text-foreground-muted">
        <span className="min-w-0 flex-1 whitespace-pre-wrap">{p.note || "메모 없음"}</span>
        {!p.dropped && <button type="button" className={BTN} onClick={() => setEdit(true)}>기본 정보 수정</button>}
      </div>
    );
  }
  return (
    <div className="grid grid-cols-2 gap-2 rounded-lg border border-dashed border-border p-3">
      <input className={INPUT} value={v.supplier} placeholder="공급사" onChange={(e) => setV({ ...v, supplier: e.target.value })} />
      <input className={INPUT} value={v.owner} placeholder="담당자" onChange={(e) => setV({ ...v, owner: e.target.value })} />
      <input className={cn(INPUT, "col-span-2")} value={v.title} placeholder="품목·건명" onChange={(e) => setV({ ...v, title: e.target.value })} />
      <textarea className={cn(INPUT, "col-span-2 h-16 py-2")} value={v.note} placeholder="메모" onChange={(e) => setV({ ...v, note: e.target.value })} />
      {!p.po.no && (
        <div className="col-span-2 flex items-center gap-2 text-[12.5px]">
          국내 · 해외
          {(["domestic", "overseas"] as const).map((k) => (
            <label key={k} className="inline-flex items-center gap-1"><input type="radio" checked={v.region === k} onChange={() => setV({ ...v, region: k })} />{REGION_LABEL[k]}</label>
          ))}
          <span className="text-[11.5px] text-foreground-subtle">(발주서를 발행하면 바꿀 수 없습니다)</span>
        </div>
      )}
      <div className="col-span-2 flex justify-end gap-1.5">
        <button type="button" className={BTN} onClick={() => setEdit(false)}>취소</button>
        <button type="button" className={PRIMARY} disabled={busy || !v.supplier.trim()} onClick={() => { onSave(v); setEdit(false); }}>저장</button>
      </div>
    </div>
  );
}

// ── 결제 조건·회차별 송금 확인(사용자 2026-11-11 — 영업 건 관리의 결제 조건·입금 확인과 같게) ─────────────────────
type TermDraft = { label: string; pct: string; when: string; expected_date: string; fixed: boolean };
/** 항상 나오는 회차 — 영업 건과 같게 선금·중도금·잔금 고정, %·시점은 수기(시점은 비워도 됨) */
const FIXED_TERMS = ["선금", "중도금", "잔금"];

function termRows(saved: PurchaseRow["pay_terms"]): TermDraft[] {
  const rows: TermDraft[] = FIXED_TERMS.map((label) => {
    const t = saved.find((x) => x.label === label);
    return { label, pct: t ? String(t.pct) : "", when: t?.when ?? "", expected_date: t?.expected_date ?? "", fixed: true };
  });
  for (const t of saved) {
    if (!FIXED_TERMS.includes(t.label)) rows.push({ label: t.label, pct: String(t.pct), when: t.when ?? "", expected_date: t.expected_date ?? "", fixed: false });
  }
  return rows;
}

/** 결제 조건 편집 — 선금·중도금·잔금 세 줄, 줄마다 %·시점·송금 예정일. 0% 나 빈 줄은 저장 안 함, 합이 100% 여야 저장.
 *  송금 확인한 회차는 % 를 못 바꾼다. 금액은 공급가액 × %(마지막 회차가 끝전). */
function TermsEditor({ p, busy, onSave }: {
  p: PurchaseRow; busy: boolean; onSave: (t: { label: string; pct: number; when: string; expected_date: string | null }[]) => void;
}) {
  const saved = p.pay_terms;
  const doneLabels = new Set(p.terms.filter((t) => t.confirmed).map((t) => t.label));
  const [rows, setRows] = useState<TermDraft[]>(() => termRows(saved));
  const [editing, setEditing] = useState(!saved.length);
  const total = p.total ?? 0;
  const used = rows.filter((r) => Number(r.pct) > 0);
  const sum = Math.round(used.reduce((s, r) => s + Number(r.pct), 0) * 1000) / 1000;
  const okSum = Math.abs(sum - 100) < 0.01;
  const setRow = (i: number, patch: Partial<TermDraft>) => setRows((xs) => xs.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const amountOf = (r: TermDraft) => {
    const k = used.indexOf(r);
    if (k < 0) return null;
    if (k < used.length - 1) return Math.round(total * Number(r.pct) / 100);
    return Math.round(total - used.slice(0, -1).reduce((s, x) => s + Math.round(total * Number(x.pct) / 100), 0));
  };
  // 남은 % 를 마지막 빈 회차(잔금)에 채우기 — 30 넣으면 잔금 70 자동(합 100%)
  const fillRest = () => {
    const rest = Math.round((100 - sum) * 1000) / 1000;
    if (rest <= 0) return;
    const i = [...rows.keys()].reverse().find((j) => !(Number(rows[j].pct) > 0) && !doneLabels.has(rows[j].label));
    if (i != null) setRow(i, { pct: String(rest) });
  };

  if (!editing) {
    return (
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[12px] font-semibold text-foreground-muted">결제 조건</span>
        {saved.map((t, i) => (
          <span key={i} className="rounded-md bg-foreground/[0.04] px-2 py-1">
            <b>{t.label} {t.pct}%</b> <span className="text-foreground-subtle">{[t.when, t.expected_date ? `예정 ${shortDate(t.expected_date)}` : "예정일 미정"].filter(Boolean).join(" · ")}</span>
          </span>
        ))}
        <button type="button" className="ml-auto text-[12px] font-semibold text-accent hover:underline" disabled={busy} onClick={() => setEditing(true)}>고치기</button>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border bg-surface px-3 py-2.5">
      <table className="w-full table-fixed border-collapse">
        <colgroup><col className="w-[60px]" /><col className="w-[80px]" /><col /><col className="w-[150px]" /><col className="w-[110px]" /></colgroup>
        <thead>
          <tr className="border-b border-border text-left text-[12px] text-foreground-muted">
            {["구분", "비율(%)", "시점", "송금 예정일", "금액"].map((h) => (
              <th key={h} className={cn("whitespace-nowrap py-1.5 pr-1.5 font-bold", h === "금액" && "text-right")} title={h === "금액" ? "공급가액 기준" : undefined}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => {
            const locked = doneLabels.has(r.label);
            const off = !(Number(r.pct) > 0);
            const amt = amountOf(r);
            return (
              <tr key={r.label} className={cn("border-b border-border/60", off && "text-foreground-subtle")}>
                <td className="py-1.5 pr-1.5 font-bold">{r.label}{!r.fixed && <span className="ml-1 text-[11px] font-normal text-foreground-subtle">(예전)</span>}</td>
                <td className="py-1.5 pr-1.5">
                  <input value={r.pct} disabled={locked} inputMode="decimal" placeholder="0" aria-label={`${r.label} 비율`}
                         onChange={(e) => setRow(i, { pct: e.target.value.replace(/[^\d.]/g, "") })} className={cn(HEAD_FIELD, "text-right")} />
                </td>
                <td className="py-1.5 pr-1.5">
                  <input value={r.when} disabled={off} maxLength={30} placeholder="예: 발주 시" aria-label={`${r.label} 시점`}
                         onChange={(e) => setRow(i, { when: e.target.value })} className={HEAD_FIELD} />
                </td>
                <td className="py-1.5 pr-1.5">
                  <input type="date" value={r.expected_date} disabled={off} aria-label={`${r.label} 송금 예정일`}
                         onChange={(e) => setRow(i, { expected_date: e.target.value })} className={HEAD_FIELD} />
                </td>
                <td className="py-1.5 text-right font-bold tabular-nums">
                  {locked ? <span className="mr-2 text-[11px] text-emerald-700 dark:text-emerald-300">송금 확인됨</span> : null}
                  {amt != null ? won(amt, p.currency) : <Muted>-</Muted>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[12px] text-foreground-subtle">쓰지 않는 회차는 비워 두세요(0% 는 저장하지 않음).</span>
        {!okSum && sum < 100 && sum > 0 && (
          <button type="button" className="text-[12px] font-semibold text-accent hover:underline" onClick={fillRest}>남은 {Math.round((100 - sum) * 1000) / 1000}% 채우기</button>
        )}
        <span className={cn("ml-auto", okSum ? "text-foreground-muted" : "font-semibold text-red-600")}>합계 {sum}%{okSum ? "" : " — 100% 가 되게"}</span>
        {saved.length > 0 && <button type="button" className={secondaryBtn} disabled={busy} onClick={() => { setRows(termRows(saved)); setEditing(false); }}>취소</button>}
        <button type="button" className={primaryBtn} disabled={busy || !used.length || !okSum}
                onClick={() => onSave(used.map((r) => ({ label: r.label, pct: Number(r.pct), when: r.when.trim(), expected_date: r.expected_date || null })))}>
          <Icon name="check" className="h-4 w-4" /> 결제 조건 저장
        </button>
      </div>
    </div>
  );
}

/** 회차별 송금 확인 — 영업 건 [입금 확인]과 같은 줄. 송금일은 위에서 한 번 고르고 회차마다 [송금 확인].
 *  확인 취소는 누구나(사용자 2026-11-11 — 발주서가 바뀌며 금액이 바뀔 수 있어서, 이력에 남음). */
function TermList({ p, busy, ready, onConfirm, onUnconfirm }: {
  p: PurchaseRow; busy: string | null; ready: boolean; onConfirm: (index: number, date: string) => void; onUnconfirm: (index: number) => void;
}) {
  const [date, setDate] = useState(todayIso());
  if (!p.terms.length) return null;
  const today = todayIso();
  const next = p.terms.find((t) => !t.confirmed)?.index;
  return (
    <div className="flex flex-col gap-1.5">
      {next != null && (
        <label className="flex items-center gap-1.5 self-end text-[12px] text-foreground-muted">송금일
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)} className={cn(INPUT, "h-8 w-[140px]")} />
        </label>
      )}
      <ul className="flex flex-col gap-1">
        {p.terms.map((t) => {
          const late = !t.confirmed && !!t.expected_date && t.expected_date < today;
          const now = t.index === next;
          return (
            <li key={t.index} className={cn("flex flex-wrap items-center gap-2 rounded-md px-2 py-1.5",
              t.confirmed ? "bg-emerald-500/8" : late ? "bg-red-500/[0.08]" : now ? "bg-accent/8" : "bg-foreground/[0.03]")}>
              <b>{t.label} {t.pct}%</b>
              {t.when && <span className="text-foreground-subtle">{t.when}</span>}
              <b className="tabular-nums">{won(t.amount, p.currency)}</b>
              {t.expected_date && (
                <span className={cn(late ? "font-semibold text-red-600 dark:text-red-400" : "text-foreground-muted")}>
                  예정 {shortDate(t.expected_date)}{late ? " · 지남" : ""}
                </span>
              )}
              {t.mismatch && <span className="text-[11.5px] text-amber-700 dark:text-amber-300" title={t.mismatch_note ?? ""}>⚠ 예전 지출내역 불일치 기록</span>}
              <span className="ml-auto flex items-center gap-2">
                {t.confirmed ? (
                  <>
                    <span className="text-emerald-700 dark:text-emerald-300">✓ 송금 확인 {shortDate(t.paid_date)}</span>
                    {!p.dropped && <button type="button" disabled={!!busy}
                                          onClick={() => { if (window.confirm(`${t.label} 송금 확인을 취소할까요? 금액이 바뀌었으면 취소한 뒤 다시 확인하세요.`)) onUnconfirm(t.index); }}
                                      className="text-[11.5px] text-foreground-subtle hover:text-red-600 hover:underline">확인 취소</button>}
                  </>
                ) : (
                  <button type="button" disabled={!!busy || !ready || p.dropped || !date} onClick={() => onConfirm(t.index, date)}
                          className={cn(now || late ? primaryBtn : secondaryBtn, "h-8")}>
                    {busy === `confirm-${t.index}` ? "확인 중…" : "송금 확인"}
                  </button>
                )}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

