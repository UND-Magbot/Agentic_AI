// 발주서 작성 창(사용자 2026-11-02) — 회사 발주서 양식(Purchasing order) 칸 그대로 채우고 미리보기·발행.
// 화면 모양은 견적서 작성 화면과 같게(사용자 2026-11-11): 진행 상황 카드 · 머리 카드(번호 묶음, 남은 필수 칸) · 품목 카드 줄 ·
// 합계 막대 · [변경 저장]/[미리보기]/[고친 파일로 발행]/[발주서 발행] 버튼 줄, 오른쪽은 AI 대화(폭 조절 막대), 미리보기는 창으로.
// AI 대화 = 말로 품목·머리 칸을 고친다(숫자는 말한 것만 — 서버 po_chat). 화면의 저장 안 한 내용도 같이 보내 반영·저장한다.
// [견적서에서 불러오기] 버튼은 뺐다(사용자 2026-11-11). 품목은 직접 적거나 오른쪽 AI 대화로.
// [변경 저장]은 번호 없이 초안만, [발행]은 새 발주번호(PO-이니셜날짜-NN, 2026-11-17부터)를 붙여 확정한다. 금액은 VAT 별도.
// [고친 파일로 발행] = 엑셀에서 고친 발주서를 올려 그 파일로 발행(번호는 파일의 Order No., 견적서 '고친 파일로 발행'과 같은 창 모양).
"use client";

import { useEffect, useRef, useState } from "react";
import { RichText } from "@/components/domains/sales/agent-chat";
import { won } from "@/components/domains/sales/deal-format";
import { ConfirmDialog, POPUP_BTN_HOVER } from "@/components/shared/ui/confirm-dialog";
import { Icon } from "@/components/shared/ui/icon";
import {
  PurchaseApiError, purchaseRequest, purchaseUpload, reopenOf, type PoDraft, type PoHeader, type PoItem, type PoUploadRead, type PurchaseCurrency,
  type PurchaseRow, type ReopenLine,
} from "@/lib/shared/purchases";
import { fileBugReport, readFailureContent } from "@/lib/shared/bug-report";
import { cn, scrollChatToEnd } from "@/lib/shared/utils";
import { ITEM_BTN, primaryBtn, secondaryBtn } from "./proposal-actions";
import { AutoTextarea, LineArea } from "./quote-blocks";
import { AMOUNT_BOX, HEAD_AREA, HEAD_FIELD, ROW_CARD, ROW_FIELD, ROW_ICON_BTN, ROW_LABEL } from "./quote-styles";
import { SPLIT_GRID, SplitBar, splitStyle, useSplit } from "./split-pane";
import { req, REQ, REQ_SCOPE, ReqLabel, ReqMark, reqOr } from "@/components/domains/sales/required";

// 견적서 작성 화면과 같은 카드·칸 모양(quote-workspace 의 HEAD_CARD·HEAD_LABEL)
const HEAD_CARD = "flex flex-col gap-4 rounded-2xl border border-border bg-surface-raised p-5 shadow-[0_1px_2px_rgba(15,23,42,0.04),0_16px_36px_-24px_rgba(15,30,70,0.30)]";
const HEAD_LABEL = "flex min-w-0 flex-col gap-1.5 text-[12.5px] font-semibold text-foreground-muted";
const MAX_ITEMS = 30;
const EMPTY_ITEM: PoItem = { commodity: "", description: "", moq: null, qty: null, price: null };
const num = (v: string): number | null => {
  const n = Number(v.replace(/,/g, "").trim());
  return v.trim() === "" || Number.isNaN(n) ? null : n;
};
const kstToday = () => new Date().toLocaleDateString("sv-SE", { timeZone: "Asia/Seoul" });
const lineAmount = (it: PoItem, usd: boolean) => (usd ? Math.round((it.qty ?? 0) * (it.price ?? 0) * 100) / 100 : Math.round((it.qty ?? 0) * (it.price ?? 0)));
const itemName = (it: PoItem) => (it.commodity ?? "").trim() || it.description.trim();

export function PurchasePoEditor({ id, reissue = false, currentNo = null, currentExpected = null, onClose, onIssued, onGoto }: {
  id: number; onClose: () => void; onIssued: (row: PurchaseRow) => void;
  /** 막힌 팝업의 [○○ 칸으로 가기] — 작성 창을 닫고 상세의 그 단계로(사용자 2026-11-17). receipt·pay */
  onGoto?: (step: "receipt" | "pay") => void;
  /** 이미 발행한 발주서를 고쳐 다시 발행 */
  reissue?: boolean;
  /** 지금 발주서 번호·입고 예정일 — 고친 파일로 발행할 때 같은 번호인지 보고, 예정일을 채운다 */
  currentNo?: string | null;
  currentExpected?: string | null;
}) {
  const [d, setD] = useState<PoDraft | null>(null);
  const [saved, setSaved] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [pdf, setPdf] = useState<string | null>(null);
  const [bankOpen, setBankOpen] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [issueAsk, setIssueAsk] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [text, setText] = useState("");
  // 입고를 마친 건을 다시 발행할 때 다시 열리는 품목(팝업) — 훅은 아래 '불러오는 중' 반환보다 위에(훅 순서 오류 수정 2026-11-16)
  const [reopen, setReopen] = useState<ReopenLine[] | null>(null);
  // 발행이 막힌 이유(팝업) — code 가 있으면 해결할 칸으로 안내(입고 기록·송금 확인)
  const [blocked, setBlocked] = useState<{ message: string; code?: string } | null>(null);
  const blockedGo = blocked?.code === "RECEIPT_LOCK"
    ? { step: "receipt" as const, ask: "입고 칸으로 안내해 드릴까요?", label: "입고 칸으로 가기",
        sub: "작성 창을 닫고 구매 건의 입고 칸으로 갑니다. 고친 발주서 내용은 [변경 저장]해 두면 그대로 남습니다." }
    : blocked?.code === "PAID_LOCK"
    ? { step: "pay" as const, ask: "송금 칸으로 안내해 드릴까요?", label: "송금 칸으로 가기",
        sub: "작성 창을 닫고 구매 건의 송금 칸으로 갑니다. [확인 취소] 뒤 다시 발행하면 됩니다." }
    : null;
  const chatEnd = useRef<HTMLDivElement>(null);
  const split = useSplit("und_cortex_po_split", 62);

  useEffect(() => {
    purchaseRequest<PoDraft>(`/${id}/po/draft`)
      .then((x) => {
        const v = { ...x, items: x.items.length ? x.items : [{ ...EMPTY_ITEM }] };
        setD(v);
        setSaved(JSON.stringify(v));
      })
      .catch((e) => setMsg({ ok: false, text: e instanceof Error ? e.message : "불러오지 못했습니다." }));
  }, [id]);
  useEffect(() => () => { if (pdf) URL.revokeObjectURL(pdf); }, [pdf]);
  useEffect(() => { scrollChatToEnd(chatEnd.current); }, [d?.chat?.length, busy]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !busy && !uploading && !issueAsk && !previewOpen) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, uploading, issueAsk, previewOpen, onClose]);

  if (!d) {
    return <Shell><p className="m-auto text-[13px] text-foreground-muted">{msg?.text ?? "불러오는 중…"}</p></Shell>;
  }
  const h = d.header;
  const setH = (patch: Partial<PoHeader>) => setD({ ...d, header: { ...h, ...patch } });
  const setItem = (i: number, patch: Partial<PoItem>) => setD({ ...d, items: d.items.map((it, j) => (j === i ? { ...it, ...patch } : it)) });
  // 해외(사용자 2026-11-02) — Purchasing order 양식·달러(센트 단위)·Swift Code·Remark
  const en = d.lang === "en";
  const cur = d.currency;
  const total = Math.round(d.items.reduce((s, it) => s + lineAmount(it, en), 0) * 100) / 100;
  const body = () => ({ initials: d.initials, expected_date: d.expected_date, header: h, items: d.items });
  const dirty = JSON.stringify({ ...d, chat: undefined }) !== JSON.stringify({ ...(saved ? JSON.parse(saved) as PoDraft : {}), chat: undefined });

  // 발행 전에 채울 것 — 견적서 화면의 '확인할 것' 목록처럼
  const problems = [
    !/^[A-Za-z]{2}$/.test(d.initials) && "발행자 이니셜(영문 2자)",
    !d.expected_date && !en && "입고 예정일",          // 해외 납기일은 선택(사용자 2026-11-11)
    !h.supplier.name.trim() && "공급사",
    !h.issuer.name.trim() && "우리 담당 이름",
    !d.items.some((it) => itemName(it)) && "품목",
    ...d.items.flatMap((it, i) => itemName(it) ? [
      (!it.qty || it.qty <= 0) && `${i + 1}번 품목 수량`,
      it.price == null && `${i + 1}번 품목 단가`,
    ] : []),
  ].filter((x): x is string => !!x);

  const run = async (label: string, fn: () => Promise<void>) => {
    setBusy(label);
    setMsg(null);
    try {
      await fn();
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : "처리하지 못했습니다." });
    } finally {
      setBusy(null);
    }
  };

  // AI 대화 — 지금 화면의 초안(저장 안 한 것 포함)을 같이 보낸다. 서버가 반영·저장한 초안으로 바꾼다
  const send = async (say?: string) => {
    const m = (say ?? text).trim();
    if (!m || busy) return;
    if (say === undefined) setText("");
    const before = d;
    setD({ ...d, chat: [...(d.chat ?? []), { role: "user", text: m }] });
    setBusy("발주서 반영 중");
    setMsg(null);
    try {
      const r = await purchaseRequest<PoDraft & { reply: string }>(`/${id}/po/chat`, { text: m, draft: body() });
      const v = { ...r, items: r.items.length ? r.items : [{ ...EMPTY_ITEM }] };
      setD(v);
      setSaved(JSON.stringify(v));
    } catch (e) {
      setD(before);
      setText(m);
      setMsg({ ok: false, text: e instanceof Error ? e.message : "AI 가 처리하지 못했습니다." });
    } finally {
      setBusy(null);
    }
  };

  const preview = () => run("미리보기 만드는 중", async () => {
    setPreviewOpen(true);
    const r = await fetch(`/api/purchases/${id}/po/preview`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body()) });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      setPreviewOpen(false);
      throw new Error(typeof e?.detail === "string" ? e.detail : "미리보기를 만들지 못했습니다.");
    }
    setPdf(URL.createObjectURL(await r.blob()));
  });

  const save = () => run("저장 중", async () => {
    await purchaseRequest(`/${id}/po/draft`, body());
    setSaved(JSON.stringify(d));
    setMsg({ ok: true, text: "작성 중인 발주서를 저장했습니다(번호는 발행할 때 붙습니다)." });
  });

  // 입고를 마친 건에 품목을 더하거나 수량을 늘리면 입고가 다시 열린다 — 서버가 묻고(409 + reopen) 팝업 확인 뒤 다시 보낸다(사용자 2026-11-16)
  const issue = (reopenOk = false) => run("발행 중", async () => {
    try {
      onIssued(await purchaseRequest(`/${id}/po/issue`, { ...body(), reopen_ok: reopenOk }));
    } catch (e) {
      const r = reopenOf(e);
      if (r) { setReopen(r); return; }
      // 막힘은 팝업으로(사용자 2026-11-17 — 안내가 화면 맨 위에 떠서 [재발행] 누른 자리에서 안 보였음)
      const code = e instanceof PurchaseApiError ? e.data?.code : undefined;
      setBlocked({ message: e instanceof Error ? e.message : "발행하지 못했습니다.", code });
    }
  });

  // 머리 묶음 — 견적서 화면처럼 번호·색·남은 칸. 공급사 계좌는 평소 접어 둔다
  const field = (label: string, value: string, onChange: (v: string) => void, opt: { req?: boolean; ph?: string; type?: string; max?: number; wide?: boolean } = {}) => (
    <label className={cn(HEAD_LABEL, opt.wide && "sm:col-span-full", opt.req && REQ_SCOPE)}>
      {opt.req ? <ReqLabel>{label}</ReqLabel> : <span>{label}</span>}
      <input {...req(!!opt.req)} className={HEAD_FIELD} type={opt.type ?? "text"} value={value} maxLength={opt.max}
             placeholder={opt.ph} disabled={!!busy} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
  // 우리 담당·이니셜은 내 정보(프로필 [내 정보])로 자동으로 채운다(서버 po_draft — 비어 있는 칸만, 사용자 2026-11-11: 버튼 없이).
  // 내 정보가 비어 있으면 어디서 넣는지만 알려 준다
  const mine = d.mine;
  const hasMine = !!mine && (!!mine.issuer.name.trim() || !!mine.initials);
  const groups: { n: number; title: string; tone: string; req: string[]; fold?: boolean; summary?: string; action?: React.ReactNode;
                  body: React.ReactNode }[] = [
    { n: 1, title: "발주 정보", tone: "bg-blue-500/10 text-blue-700 dark:text-blue-300", req: en ? [d.initials.trim()] : [d.initials.trim(), d.expected_date],
      body: (<>
        {field("Order Date", h.date, (v) => setH({ date: v }), { type: "date" })}
        {field("발행자 이니셜(영문 2자)", d.initials, (v) => setD({ ...d, initials: v.toUpperCase() }), { req: true, ph: "LU", max: 2 })}
        <label className={cn(HEAD_LABEL, !en && REQ_SCOPE)}>
          {/* 해외는 '납기일'(선택 — 사용자 2026-11-11: 안 적어도 발행·다음 단계로) */}
          <span className="flex flex-wrap items-center gap-1">
            {en ? <>납기일<span className="font-normal text-foreground-subtle"> (선택)</span></> : <ReqLabel>입고 예정일</ReqLabel>}
            <span className="font-normal text-foreground-subtle">· 발주서엔 안 찍힘</span>
          </span>
          <input type="date" {...req(!en)} className={HEAD_FIELD} value={d.expected_date} disabled={!!busy}
                 onChange={(e) => setD({ ...d, expected_date: e.target.value })} />
        </label>
      </>) },
    { n: 2, title: en ? "Supplier and Shipper by" : "공급사", tone: "bg-violet-500/10 text-violet-700 dark:text-violet-300", req: [h.supplier.name.trim()],
      body: (<>
        {field("공급사", h.supplier.name, (v) => setH({ supplier: { ...h.supplier, name: v } }), { req: true })}
        {field("담당자(호칭 포함)", h.supplier.contact, (v) => setH({ supplier: { ...h.supplier, contact: v } }), { ph: en ? "(선택) Ms. Echo" : "예: 강종구 팀장" })}
        {/* 국내도 공급사 Tel 을 적는다(사용자 2026-11-11, 에이딘로보틱스 발주서 예시) */}
        {field("Tel", h.supplier.tel ?? "", (v) => setH({ supplier: { ...h.supplier, tel: v } }), { ph: en ? "(+86)13670013044" : "010-0000-0000" })}
        {field("e-mail", h.supplier.email, (v) => setH({ supplier: { ...h.supplier, email: v } }), { ph: "선택" })}
      </>) },
    { n: 3, title: en ? "Issued the order by" : "우리 담당", tone: "bg-slate-500/12 text-slate-700 dark:text-slate-300", req: [h.issuer.name.trim()],
      action: hasMine ? undefined : (
        <span className="text-[11.5px] text-foreground-subtle">프로필 [내 정보]에 이름·{en ? "영문 이름·" : ""}이니셜을 저장하면 자동으로 채워집니다</span>
      ),
      body: (<>
        {field(en ? "이름(영문)" : "이름", h.issuer.name, (v) => setH({ issuer: { ...h.issuer, name: v } }), { req: true })}
        {field(en ? "호칭(Mr./Ms.)" : "직함", h.issuer.title, (v) => setH({ issuer: { ...h.issuer, title: v } }))}
        {field("Tel", h.issuer.tel, (v) => setH({ issuer: { ...h.issuer, tel: v } }))}
        {field("주소", h.issuer.address, (v) => setH({ issuer: { ...h.issuer, address: v } }), { wide: true })}
        {!en && field("Fax", h.issuer.fax, (v) => setH({ issuer: { ...h.issuer, fax: v } }))}
        {field(en ? "담당 표기(양식 마지막 줄)" : "ATTN", h.issuer.attn, (v) => setH({ issuer: { ...h.issuer, attn: v } }),
               { ph: en ? "Mr. Alex Jeon (alex@unde.co.kr)" : "Victor (victor@unde.co.kr)" })}
      </>) },
    { n: 4, title: "공급사 계좌", tone: "bg-foreground/[0.07] text-foreground-muted", req: [], fold: true,
      summary: [h.bank, h.account_no, h.account_holder].map((x) => (x ?? "").trim()).filter(Boolean).join(" · "),
      body: (<>
        {field("Bank", h.bank, (v) => setH({ bank: v }))}
        {field("Account No.", h.account_no, (v) => setH({ account_no: v }))}
        {field("Account Holder", h.account_holder, (v) => setH({ account_holder: v }))}
        {field(en ? "Address" : "Address(비우면 -)", h.address, (v) => setH({ address: v }))}
        {en && field("Swift Code", h.swift ?? "", (v) => setH({ swift: v }))}
      </>) },
  ];
  const headMissing = groups.reduce((s, g) => s + g.req.filter((x) => !x).length, 0);

  return (
    <Shell>
      <div style={splitStyle(split)} className={cn(SPLIT_GRID, "w-full")}>
        <div className="flex min-w-0 flex-col gap-4">
          {/* 진행 상황 — 견적서 화면과 같은 카드 */}
          <section className="flex flex-col gap-2 rounded-xl border border-accent/30 bg-surface p-4">
            <div className="flex flex-wrap items-center gap-2">
              <button type="button" className={secondaryBtn} disabled={!!busy} onClick={onClose}><Icon name="back" className="h-4 w-4" /> 닫기</button>
              <h2 className="text-[15px] font-semibold text-foreground">
                {currentNo ? `${currentNo} 고쳐 다시 발행` : en ? "해외 발주서(Purchasing order)" : "발주서 작성"} · {h.supplier.name || "공급사"}
              </h2>
              <span className={cn("rounded-md px-1.5 py-0.5 text-[11.5px] font-semibold",
                currentNo ? "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300" : "bg-amber-500/15 text-amber-700 dark:text-amber-300")}>
                {currentNo ? "발행" : d.source === "draft" ? "작성 중 저장본" : "작성 중"}
              </span>
              <span className="rounded-md bg-foreground/[0.06] px-1.5 py-0.5 text-[11.5px] font-semibold text-foreground-muted">{en ? "해외 · US$" : "국내 · 원 · VAT 별도"}</span>
            </div>
            {problems.length > 0 ? (
              <ul className="flex flex-col gap-0.5 text-[12px] text-amber-700 dark:text-amber-300">
                {problems.slice(0, 6).map((p) => <li key={p}>· {p}을(를) 채워 주세요</li>)}
                {problems.length > 6 && <li>· 외 {problems.length - 6}개</li>}
              </ul>
            ) : (
              <p className="text-[12.5px] text-emerald-700 dark:text-emerald-300">필요한 내용을 다 채웠습니다 — [발주서 발행]을 누르면 발주번호가 붙고 엑셀·PDF 가 만들어집니다.</p>
            )}
            {msg && (
              <p className={cn("rounded-lg px-3 py-2 text-[12.5px]", msg.ok ? "bg-emerald-500/10 text-emerald-800 dark:text-emerald-200" : "bg-amber-500/10 text-amber-800 dark:text-amber-200")}>{msg.text}</p>
            )}
          </section>

          {/* 머리 — 견적서 머리처럼 번호 묶음 */}
          <section className={HEAD_CARD}>
            <div className="min-w-0">
              <h3 className="text-[17px] font-extrabold tracking-tight text-foreground">{en ? "Purchasing Order Header" : "발주서 머리"}</h3>
              <p className={cn("mt-0.5 text-[12.5px] font-medium", headMissing ? "text-amber-700 dark:text-amber-300" : "text-emerald-700 dark:text-emerald-300")}>
                {headMissing ? `필수 칸 ${headMissing}개 남음 — 빨간 별(*) 칸을 채우면 발행할 수 있습니다` : "필수 칸을 모두 채웠습니다"}
              </p>
            </div>
            {groups.map((g) => {
              const left = g.req.filter((x) => !x).length;
              const folded = g.fold && !bankOpen;
              return (
                <div key={g.n} className={cn("rounded-xl border border-border", g.fold ? "bg-foreground/[0.02]" : "bg-surface-raised")}>
                  <div className={cn("flex flex-wrap items-center gap-2.5 px-4", g.fold ? "py-3" : "pt-3.5")}>
                    <span className={cn("grid h-[26px] w-[26px] place-items-center rounded-lg text-[12.5px] font-extrabold", g.tone)}>{g.n}</span>
                    <h4 className="text-[14px] font-extrabold text-foreground">{g.title}</h4>
                    {g.action}
                    {g.fold ? (
                      <>
                        {!g.summary && <span className="rounded-full border border-border bg-surface-raised px-2 py-0.5 text-[11.5px] font-semibold text-foreground-muted">비어 있음</span>}
                        <span className="min-w-0 truncate text-[12.5px] text-foreground-muted">{g.summary || "견적서에 계좌가 있으면 적습니다(선택)"}</span>
                        <button type="button" aria-expanded={bankOpen} onClick={() => setBankOpen(!bankOpen)} className="ml-auto text-[12.5px] font-bold text-accent hover:underline">
                          {bankOpen ? "접기" : "펼쳐서 고치기"}
                        </button>
                      </>
                    ) : left > 0 ? (
                      <span className="ml-auto rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-[11.5px] font-semibold text-amber-800 dark:text-amber-300">{left}칸 남음</span>
                    ) : (
                      <span className="ml-auto rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-[11.5px] font-semibold text-emerald-700 dark:text-emerald-300">완료</span>
                    )}
                  </div>
                  {!folded && <div className={cn("grid gap-3 px-4 pb-4 sm:grid-cols-3", g.fold ? "pt-0" : "pt-3")}>{g.body}</div>}
                </div>
              );
            })}
            <label className={HEAD_LABEL}>
              <span>Comments <span className="ml-1 font-normal text-foreground-subtle">· 발주서에 가운데 정렬 — 예: 구매 건명, 납품처</span></span>
              <AutoTextarea className={HEAD_AREA} value={h.comments} disabled={!!busy}
                            placeholder={"예: 6축 F/T 센서 구매의 건\n납품처 : 유엔디로보틱스 구미로봇사업장"} onChange={(e) => setH({ comments: e.target.value })} />
            </label>
          </section>

          {/* 품목 — 견적서 품목처럼 한 줄 = 카드 */}
          <section className={cn(HEAD_CARD, "gap-3")}>
            <div className="flex flex-wrap items-baseline gap-2">
              <h3 className="text-[17px] font-extrabold tracking-tight text-foreground">Goods Description &amp; Details</h3>
              <span className="text-[12.5px] text-foreground-muted">{en ? "단가는 달러(US$)" : "단가는 원화 · VAT 별도"} · Commodity(모델명)와 Description(설명)은 발주서에 두 칸으로 나갑니다</span>
            </div>
            <ul className="flex flex-col gap-2.5">
              {d.items.map((it, i) => (
                <li key={i} className={ROW_CARD}>
                  <div className="grid gap-2.5 sm:grid-cols-[190px_minmax(0,1fr)]">
                    <div className={ROW_LABEL}>
                      <span><ReqMark>Commodity</ReqMark></span>
                      <LineArea className={HEAD_AREA} value={it.commodity ?? ""} aria-label="Commodity" placeholder="예: AFT2000-D80-C" {...reqOr(!!itemName(it), false)} disabled={!!busy}
                                onChange={(e) => setItem(i, { commodity: e.target.value })} />
                    </div>
                    <div className={ROW_LABEL}>
                      <span>Description</span>
                      <AutoTextarea className={HEAD_AREA} value={it.description} aria-label="Description" placeholder="예: 200N 15Nm, CAN Protocol" disabled={!!busy}
                                    onChange={(e) => setItem(i, { description: e.target.value })} />
                    </div>
                  </div>
                  <div className="mt-2.5 flex flex-wrap items-end gap-2.5">
                    <div className={ROW_LABEL}>
                      <span><ReqMark>Q&apos;ty (pcs)</ReqMark></span>
                      <input className={cn(ROW_FIELD, "w-[90px] text-right")} {...req(!!itemName(it))} inputMode="numeric"
                             value={it.qty ?? ""} aria-label="수량" disabled={!!busy} onChange={(e) => setItem(i, { qty: num(e.target.value) })} />
                    </div>
                    {/* 국내 = MOQ 칸, 해외 = Remark 칸(양식마다 다름) */}
                    {en ? (
                      <div className={ROW_LABEL}>
                        <span>Remark</span>
                        <input className={cn(ROW_FIELD, "w-[160px]")} value={it.remark ?? ""} placeholder="예: Sea" disabled={!!busy}
                               onChange={(e) => setItem(i, { remark: e.target.value })} />
                      </div>
                    ) : (
                      <div className={ROW_LABEL}>
                        <span>MOQ <span className="font-normal text-foreground-subtle">· 선택</span></span>
                        <input className={cn(ROW_FIELD, "w-[90px] text-right")} inputMode="numeric" value={it.moq ?? ""} disabled={!!busy}
                               onChange={(e) => setItem(i, { moq: num(e.target.value) })} />
                      </div>
                    )}
                    <div className={ROW_LABEL}>
                      <span><ReqMark>{en ? "Price (US$)" : "Price"}</ReqMark></span>
                      <input className={cn(ROW_FIELD, "w-[140px] text-right")} {...req(!!itemName(it))} inputMode="decimal"
                             value={it.price ?? ""} aria-label="단가" placeholder="단가" disabled={!!busy} onChange={(e) => setItem(i, { price: num(e.target.value) })} />
                    </div>
                    <div className={cn(ROW_LABEL, "ml-auto flex-1 items-end")}>
                      <span>Amount</span>
                      <span className={AMOUNT_BOX}>
                        {it.qty != null && it.price != null ? won(lineAmount(it, en), cur) : <span className="text-[12.5px] font-medium text-foreground-subtle">수량·단가를 넣으면 계산</span>}
                      </span>
                    </div>
                    <button type="button" aria-label={`${i + 1}번 품목 빼기`} title="이 줄 빼기" disabled={!!busy}
                            className={cn(ROW_ICON_BTN, "hover:border-red-400/60 hover:bg-red-500/5 hover:text-red-600")}
                            onClick={() => setD({ ...d, items: d.items.length > 1 ? d.items.filter((_, j) => j !== i) : [{ ...EMPTY_ITEM }] })}>
                      <Icon name="trash" className="h-4 w-4" />
                    </button>
                  </div>
                </li>
              ))}
            </ul>
            <div className="flex flex-wrap items-center gap-1.5 text-[12px]">
              <button type="button" className={ITEM_BTN} disabled={!!busy || d.items.length >= MAX_ITEMS}
                      onClick={() => setD({ ...d, items: [...d.items, { ...EMPTY_ITEM }] })}>
                <Icon name="plus" className="h-3.5 w-3.5 text-blue-600 dark:text-blue-400" /> 항목 추가
              </button>
            </div>
            {/* 합계 막대 — 견적서 화면처럼 이 화면의 결론이라 가장 크게 */}
            <div className="-mx-5 flex flex-wrap items-center gap-x-6 gap-y-2 border-y border-blue-500/20 bg-gradient-to-b from-blue-500/[0.06] to-blue-500/[0.02] px-5 py-4">
              <div className="ml-auto flex flex-col items-end gap-0.5">
                <span className="text-[12px] font-semibold text-foreground-muted">TOTAL{en ? " (US$)" : " (VAT 별도)"}</span>
                <span className="text-[24px] font-extrabold tracking-tight text-foreground">{won(total, cur)}</span>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <button type="button" className={dirty ? primaryBtn : secondaryBtn} disabled={!dirty || !!busy} onClick={() => void save()}>
                <Icon name="check" className="h-4 w-4" /> 변경 저장
              </button>
              {dirty && <button type="button" className="text-[12px] text-foreground-subtle hover:underline" disabled={!!busy}
                                onClick={() => setD(JSON.parse(saved) as PoDraft)}>되돌리기</button>}
              <button type="button" className={cn(secondaryBtn, "ml-auto")} disabled={!!busy} onClick={() => void preview()}
                      title="지금 내용 그대로 발주서를 봅니다(번호는 발행 때 붙음)">
                <Icon name="eye" className="h-4 w-4" /> 미리보기
              </button>
              <button type="button" className={secondaryBtn} disabled={!!busy} onClick={() => setUploading(true)}
                      title="엑셀에서 고친 발주서 파일로 발행합니다(번호는 파일의 Order No.)">
                <Icon name="paperclip" className="h-4 w-4" /> 고친 파일로 발행
              </button>
              <button type="button" className={primaryBtn} disabled={!!busy || problems.length > 0} onClick={() => setIssueAsk(true)}
                      title={problems.length ? "발행 전에 위의 채울 것을 채워 주세요" : undefined}>
                <Icon name="file-spreadsheet" className="h-4 w-4" /> {reissue ? "재발행 (새 번호)" : "발주서 발행"}
              </button>
            </div>
            {reissue && !dirty && (
              <p className="text-[11.5px] text-foreground-subtle">고쳐서 [재발행]하면 새 발주번호(PO-이니셜+오늘 날짜-순번)가 붙고, 예전 판은 상세의 이전 발주서에 남습니다.</p>
            )}
            <p className={cn("min-h-[17px] text-[11.5px]", dirty ? "text-amber-700 dark:text-amber-300" : "invisible")} aria-hidden={!dirty}>
              고친 내용이 있습니다 — [변경 저장]으로 작성 중 저장하거나 바로 [발주서 발행]할 수 있습니다.
            </p>
          </section>
        </div>

        <SplitBar split={split} label="발주서와 AI 대화 폭 조절" />

        {/* 오른쪽: AI 와 대화 — 견적서 화면과 같은 칸 */}
        <aside className="flex flex-col overflow-hidden rounded-xl border border-border bg-surface lg:sticky lg:top-0 lg:h-[calc(100svh-3rem)]">
          <div className="flex items-center gap-2 border-b border-border px-4 py-3">
            <span className="und-grad grid h-7 w-7 place-items-center rounded-full text-[11px] font-bold text-white" aria-hidden>AI</span>
            <div className="min-w-0">
              <h2 className="text-[14px] font-semibold leading-tight text-foreground">AI 와 같이 발주서 만들기</h2>
              <p className="truncate text-[11.5px] text-foreground-subtle">말로 품목 추가·수정·삭제 · 숫자는 말씀하신 것만 반영합니다</p>
            </div>
          </div>
          <div className="scroll-thin flex min-h-[320px] flex-1 flex-col gap-3 overflow-y-auto px-4 py-3" aria-live="polite">
            {!(d.chat ?? []).length && (
              <div className="max-w-[90%] rounded-2xl rounded-bl-md bg-foreground/[0.05] px-3.5 py-2 text-[13px] leading-relaxed text-foreground">
                무엇을 발주할지 말씀해 주세요. 예: <b>“견적서에서 불러와 줘”</b>, <b>“AFT2000-D80-C 1개 150만원 추가”</b>,
                <b> “공급사 담당자 강종구 팀장, 010-9099-9438”</b>, <b>“입고 예정일 11월 20일”</b>
              </div>
            )}
            {(d.chat ?? []).map((m, i) => m.role === "user" ? (
              <div key={i} className="flex justify-end">
                <div className="bubble-user max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md px-3.5 py-2 text-[13px] leading-relaxed text-white">{m.text}</div>
              </div>
            ) : (
              <div key={i} className="max-w-[90%] rounded-2xl rounded-bl-md bg-foreground/[0.05] px-3.5 py-2 text-[13px] leading-relaxed text-foreground"><RichText text={m.text} /></div>
            ))}
            {busy && busy !== "미리보기 만드는 중" && (
              <div className="flex items-center gap-2 text-[12.5px] text-foreground-subtle">
                <Icon name="refresh" className="h-4 w-4 animate-spin text-accent" /> {busy}…
              </div>
            )}
            <div ref={chatEnd} />
          </div>
          <div className="border-t border-border p-3">
            <div className="flex flex-col rounded-xl border border-foreground/15 bg-background focus-within:ring-1 focus-within:ring-accent/40">
              <textarea rows={3} maxLength={1000} value={text} disabled={!!busy}
                        onChange={(e) => setText(e.target.value)}
                        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(); } }}
                        aria-label="AI 에게 보내기"
                        placeholder="예: 견적서에서 불러와 줘 / 2번 수량 3개로 / 입고 예정일 11월 20일 / Comments 에 납품처 구미 (Enter 보내기)"
                        className="w-full resize-none bg-transparent px-3 pt-2.5 text-[13px] leading-relaxed text-foreground placeholder:text-foreground-subtle/70 focus:outline-none" />
              <div className="flex items-center px-2 pb-2">
                <button type="button" aria-label="보내기" disabled={!text.trim() || !!busy} onClick={() => void send()}
                        className="und-grad ml-auto grid h-8 w-8 place-items-center rounded-full text-white transition hover:brightness-105 disabled:opacity-40">
                  <Icon name="send" className="h-4 w-4" />
                </button>
              </div>
            </div>
          </div>
        </aside>
      </div>

      {/* 미리보기 — 견적서 미리보기 창과 같은 모양 */}
      {previewOpen && (
        <div className="fixed inset-0 z-[60] grid place-items-center bg-black/40 p-4" onClick={() => setPreviewOpen(false)}>
          <div className="flex h-[92svh] w-full max-w-4xl flex-col overflow-hidden rounded-xl border border-border bg-surface shadow-xl"
               onClick={(e) => e.stopPropagation()} role="dialog" aria-label="발주서 미리보기">
            <div className="flex items-center gap-2 border-b border-border px-4 py-2.5">
              <h3 className="text-[14px] font-semibold text-foreground">발주서 미리보기</h3>
              <span className="text-[12px] text-foreground-subtle">지금 화면 내용 그대로 · 발주번호는 발행할 때 붙습니다</span>
              <button type="button" className="ml-auto grid h-7 w-7 place-items-center rounded-md hover:bg-foreground/[0.06]" onClick={() => setPreviewOpen(false)} aria-label="닫기">
                <Icon name="x" className="h-4 w-4" />
              </button>
            </div>
            {busy === "미리보기 만드는 중" || !pdf
              ? <p className="flex flex-1 items-center justify-center gap-2 text-[13px] text-foreground-subtle"><Icon name="refresh" className="h-4 w-4 animate-spin text-accent" /> 발주서를 PDF 로 만드는 중…</p>
              : <iframe src={pdf} title="발주서 미리보기" className="w-full flex-1 bg-white" />}
          </div>
        </div>
      )}

      <ConfirmDialog open={issueAsk} icon="help" variant="default" title={reissue ? "발주서를 고쳐 다시 발행할까요?" : "발주서를 발행할까요?"}
                     description={`새 발주번호가 붙고 합계 ${won(total, cur)}${en ? "" : "(VAT 별도)"}, ${d.expected_date ? `${en ? "납기일" : "입고 예정일"} ${d.expected_date}로` : "납기일 없이"} 확정됩니다.`}
                     confirmLabel={reissue ? "재발행" : "발행"} onClose={() => setIssueAsk(false)}
                     onConfirm={() => { setIssueAsk(false); void issue(); }} />
      <ConfirmDialog open={!!blocked} icon="help" variant="default" title="발주서를 발행할 수 없습니다"
                     description={<>
                       <span className="block rounded-lg bg-foreground/[0.04] px-3 py-2 text-foreground">{blocked?.message}</span>
                       {blockedGo && onGoto && <>
                         <b className="mt-3 block text-foreground">{blockedGo.ask}</b>
                         <span className="mt-0.5 block">{blockedGo.sub}</span>
                       </>}
                     </>}
                     confirmLabel={blockedGo && onGoto ? blockedGo.label : "확인"} cancelLabel="닫기" hideCancel={!(blockedGo && onGoto)}
                     onClose={() => setBlocked(null)}
                     onConfirm={() => { setBlocked(null); if (blockedGo && onGoto) onGoto(blockedGo.step); }} />
      {reopen && <ReopenConfirm lines={reopen} onOk={() => { setReopen(null); void issue(true); }} onCancel={() => setReopen(null)} />}
      {uploading && <PoUploadDialog id={id} currentNo={currentNo} currentExpected={currentExpected ?? d.expected_date ?? null} currency={cur}
                                    initials={d.initials}
                                    onClose={() => setUploading(false)} onIssued={(row) => { setUploading(false); onIssued(row); }} />}
    </Shell>
  );
}

/** 전체 화면 작성 창 — 견적서 작성 화면처럼 페이지 바탕 위에 카드들 */
function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div role="dialog" aria-modal="true" aria-label="발주서 작성" className="anim-fade-in fixed inset-0 z-50 flex bg-background"
         onClick={(e) => e.stopPropagation()}>
      <div className="scroll-thin mx-auto flex w-full max-w-[1440px] overflow-y-auto px-4 py-6">{children}</div>
    </div>
  );
}

// 고친 발주서 파일로 발행 — 견적서 '고친 견적서 파일로 발행' 창과 같은 모양(사용자 2026-11-11).
// ① 파일(엑셀·PDF)을 고르거나 끌어 놓고 [올리고 읽기] → ② Order No.·발주일·품목을 확인·수정 → [이 내용으로 발행].
// 발주번호는 파일의 Order No.(고칠 수 있음), 비우면 이니셜로 새 번호. 지금 발주서와 같은 번호면 교체를 확인받는다(사용자 2026-11-16 —
// 번호 하나 = 발주서 하나): 판 그대로 이 파일로 바꾸고, 교체 전 파일은 보관(다시 받을 수 있음). 따로 남기려면 번호를 바꿔 올린다.
function PoUploadDialog({ id, currentNo, currentExpected, currency, initials: myInitials, onClose, onIssued }: {
  id: number; currentNo: string | null; currentExpected: string | null; currency: PurchaseCurrency;
  /** 발행자 이니셜 기본값(작성 창 값) — 번호를 비우면 이 이니셜로 새 번호 */
  initials?: string;
  onClose: () => void; onIssued: (row: PurchaseRow) => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [over, setOver] = useState(false);
  const [read, setRead] = useState<PoUploadRead | null>(null);
  const [docNo, setDocNo] = useState("");
  const [initials, setInitials] = useState(myInitials ?? "");
  // [버그 신고 후 넘어가기](사용자 2026-11-11) — AI 판독이 실패·이상해도 버그를 신고하고 이 파일 그대로 발행(품목은 나중에 [발주서 고치기])
  const [forced, setForced] = useState<string | null>(null);
  const [date, setDate] = useState(kstToday());
  const [expected, setExpected] = useState(currentExpected ?? "");
  const [items, setItems] = useState<PoItem[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [askSame, setAskSame] = useState(false);
  const usd = currency === "USD";
  const total = Math.round(items.reduce((s, it) => s + lineAmount(it, usd), 0) * 100) / 100;
  const same = !!docNo.trim() && docNo.trim().toUpperCase() === (currentNo ?? "").toUpperCase();
  const filled = items.length > 0 && items.every((x) => itemName(x) && x.qty && x.qty > 0 && x.price != null);
  const why = !filled && !forced ? "Commodity·수량·단가를 모두 채워 주세요" : !expected && !usd ? "입고 예정일을 적어 주세요"
    : !docNo.trim() && !/^[A-Za-z]{2}$/.test(initials) ? "발주번호를 적거나, 비우면 이니셜(영문 2자)을 적어 주세요" : "";
  const setItem = (i: number, patch: Partial<PoItem>) => setItems(items.map((it, j) => (j === i ? { ...it, ...patch } : it)));
  const ymd = kstToday().replace(/-/g, "");

  const pick = (f: File | undefined) => {
    if (!f) return;
    if (!/\.(xlsx|pdf)$/i.test(f.name)) { setError("발주서는 엑셀(xlsx)·PDF 파일만 올릴 수 있습니다."); return; }
    setError(null);
    setFile(f);
  };
  const send = async () => {
    if (!file) return;
    setBusy("올리고 읽는 중…");
    setError(null);
    try {
      const r = (await purchaseUpload(`/${id}/po/upload`, file)) as unknown as PoUploadRead;
      setRead(r);
      setDocNo(r.doc_no ?? "");
      if (r.date) setDate(r.date);
      setItems(r.items.length ? r.items : [{ ...EMPTY_ITEM, qty: 1 }]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "파일을 올리지 못했습니다.");
    } finally {
      setBusy(null);
    }
  };
  const reportAndSkip = async () => {
    if (!read) return;
    setBusy("버그 신고 중…");
    setError(null);
    try {
      const no = await fileBugReport(`[자동] 고친 발주서 파일 AI 판독 실패 — ${read.upload.filename}`, readFailureContent({
        where: `제품 구매 건 관리 > 구매 건 #${id} > 발주서 작성 > 고친 파일로 발행`, filename: read.upload.filename,
        storageKey: String(read.upload.key ?? ""), status: read.status, summary: read.summary,
        lines: read.items.map((x) => [x.commodity, x.description?.replace(/\n/g, " / "), `수량 ${x.qty ?? "?"}`, `단가 ${x.price ?? "?"}`]
          .filter(Boolean).join(" · ")),
      }));
      setForced(no || "신고됨");
    } catch (e) {
      setError(e instanceof Error ? e.message : "버그를 신고하지 못했습니다.");
    } finally {
      setBusy(null);
    }
  };
  const [reopen, setReopen] = useState<ReopenLine[] | null>(null);
  const [sameNoOk, setSameNoOk] = useState<"" | "replace">("");
  const confirm = async (sameNo: "" | "replace" = "", reopenOk = false) => {
    if (!read) return;
    setAskSame(false);
    setReopen(null);
    setSameNoOk(sameNo);
    setBusy("발행 중…");
    setError(null);
    try {
      onIssued(await purchaseRequest(`/${id}/po/upload/confirm`, { upload: read.upload, doc_no: docNo.trim(), initials, date, expected_date: expected, items, header: read.header ?? {},
                                                                       forced: !!forced, bug_report: forced ?? "", same_no: sameNo,
                                                                       reopen_ok: reopenOk }));
    } catch (e) {
      const r = reopenOf(e);
      if (r) { setReopen(r); setBusy(null); return; }
      setError(e instanceof Error ? e.message : "발행하지 못했습니다.");
      setBusy(null);
    }
  };

  const inputCls = "h-8 w-full min-w-0 rounded-md border border-border bg-surface px-2 text-[12.5px] outline-none focus:border-accent";
  return (
    <div role="dialog" aria-modal="true" aria-labelledby="poup-title"
         className="anim-fade-in fixed inset-0 z-[60] grid place-items-center bg-black/40 px-4"
         onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}
         onDragOver={(e) => e.preventDefault()} onDrop={(e) => e.preventDefault()}>
      <div className="anim-fade-in-up max-h-[90vh] w-full max-w-[760px] overflow-y-auto rounded-2xl border border-border bg-surface-raised p-5 text-[13px] shadow-[0_30px_70px_-30px_rgba(15,30,70,0.55)]">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 id="poup-title" className="text-[15px] font-semibold text-foreground">고친 발주서 파일로 발행</h2>
            <p className="mt-1 text-[12.5px] text-foreground-muted">
              엑셀에서 고친 발주서를, 공급사에 보낼 파일 그대로 올립니다. 발주서에 적힌 Order No. 로 발행되고, 내려받는 발주서도 이 파일이 됩니다.
            </p>
          </div>
          <button type="button" onClick={onClose} disabled={!!busy} aria-label="닫기"
                  className="grid h-7 w-7 shrink-0 place-items-center rounded-sm text-foreground-subtle hover:bg-foreground/5 hover:text-foreground">
            <span aria-hidden className="text-[15px] leading-none">×</span>
          </button>
        </div>

        {!read ? (
          <div className="mt-4 flex flex-col gap-3">
            <label
              onDragOver={(e) => { e.preventDefault(); if (!busy) setOver(true); }}
              onDragLeave={() => setOver(false)}
              onDrop={(e) => { e.preventDefault(); setOver(false); if (!busy) pick(e.dataTransfer.files?.[0]); }}
              className={cn("relative flex cursor-pointer flex-col items-center gap-1.5 rounded-xl border-2 border-dashed px-4 py-7 text-center transition-colors",
                over ? "border-accent bg-accent/[0.06]" : file ? "border-accent/60 bg-accent/[0.04]" : "border-border hover:border-foreground/25")}>
              <input type="file" accept=".xlsx,.pdf" className="sr-only" disabled={!!busy}
                     onChange={(e) => { pick(e.target.files?.[0]); e.target.value = ""; }} />
              <Icon name="paperclip" className="h-6 w-6 text-foreground-subtle" />
              {file ? <><b className="text-[14px] text-foreground">{file.name}</b><span className="text-[12px] text-foreground-subtle">{(file.size / 1024).toFixed(0)}KB · 다른 파일을 고르거나 끌어 놓으면 바꿉니다</span></>
                : <><b className="text-[14px] text-foreground">발주서 파일을 고르거나 여기에 끌어 놓으세요</b><span className="text-[12px] text-foreground-subtle">엑셀(xlsx) · PDF, 20MB 까지</span></>}
            </label>
            <p className="text-[11.5px] text-foreground-subtle">
              회사 발주서 양식 엑셀은 칸을 그대로 읽고, PDF 는 AI 가 품목을 읽습니다. 읽은 번호·품목은 다음 화면에서 확인·수정합니다.
            </p>
          </div>
        ) : (
          <div className="mt-4 flex flex-col gap-3">
            <div className={cn("rounded-lg border px-3 py-2 text-[12.5px]",
              read.status === "ok" ? "border-border bg-surface" : "border-amber-500/50 bg-amber-500/[0.06] text-amber-800 dark:text-amber-200")}>
              <b>{read.upload.filename}</b> — {read.summary}
              {read.header && (read.header.supplier?.name || read.header.bank || read.header.account_no) && (
                <div className="mt-1 text-[12px] text-foreground-muted">
                  머리 칸도 읽었습니다 — {[read.header.supplier?.name, read.header.bank && "Bank", read.header.swift && "Swift Code",
                    read.header.account_no && "Account No.", read.header.comments && "Comments"].filter(Boolean).join(" · ")}.
                  발행한 뒤 [발주서 고치기]에서 고쳐 다시 발행할 수 있습니다.
                </div>
              )}
            </div>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[620px] text-[12.5px]">
                <thead>
                  <tr className="text-left text-[11.5px] text-foreground-subtle">
                    <th className="w-[150px] px-1 py-1 font-medium"><ReqMark>Commodity</ReqMark></th><th className="px-1 py-1 font-medium">Description</th>
                    <th className="w-[80px] px-1 py-1 font-medium"><ReqMark>수량</ReqMark></th>
                    <th className="w-[120px] px-1 py-1 font-medium"><ReqMark>단가{usd ? "(US$)" : "(공급가)"}</ReqMark></th>
                    <th className="w-[110px] px-1 py-1 text-right font-medium">금액</th><th className="w-6" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {items.map((x, i) => (
                    <tr key={i}>
                      <td className="px-1 py-1"><input className={inputCls} {...REQ} value={x.commodity ?? ""} aria-label={`${i + 1}번 Commodity`}
                                                       placeholder="예: AFT2000-D80-C" onChange={(e) => setItem(i, { commodity: e.target.value })} /></td>
                      <td className="px-1 py-1"><input className={inputCls} value={x.description} aria-label={`${i + 1}번 Description`}
                                                       placeholder="예: 200N 15Nm, CAN Protocol" onChange={(e) => setItem(i, { description: e.target.value })} /></td>
                      <td className="px-1 py-1"><input className={inputCls} {...REQ} inputMode="decimal" value={x.qty ?? ""}
                                                       aria-label={`${i + 1}번 수량`} onChange={(e) => setItem(i, { qty: num(e.target.value) })} /></td>
                      <td className="px-1 py-1"><input className={inputCls} {...REQ} inputMode="decimal" value={x.price ?? ""}
                                                       aria-label={`${i + 1}번 단가`} onChange={(e) => setItem(i, { price: num(e.target.value) })} /></td>
                      <td className="whitespace-nowrap px-1 py-1 text-right tabular-nums">{won(lineAmount(x, usd), currency)}</td>
                      <td className="px-1 py-1">
                        <button type="button" aria-label={`${i + 1}번 품목 빼기`} className="text-foreground-subtle hover:text-red-600"
                                onClick={() => setItems((xs) => xs.filter((_, j) => j !== i))}><Icon name="trash" className="h-4 w-4" /></button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <button type="button" className="inline-flex items-center gap-1 rounded border border-border px-2 py-0.5 text-[12px] text-foreground-muted hover:text-foreground"
                      disabled={items.length >= MAX_ITEMS} onClick={() => setItems((xs) => [...xs, { ...EMPTY_ITEM, qty: 1 }])}>
                <Icon name="plus" className="h-3.5 w-3.5" /> 품목 추가
              </button>
              <span className="text-[13px]">합계{usd ? "" : "(공급가액)"} <b className="tabular-nums">{won(total, currency)}</b></span>
            </div>
            <div className="flex flex-col gap-1.5 rounded-lg border border-border bg-surface px-3 py-2 text-[12.5px]">
              <label className="flex flex-wrap items-center gap-2">
                <span className="font-medium">발주번호</span>
                <input value={docNo} maxLength={40} disabled={!!busy} onChange={(e) => setDocNo(e.target.value)}
                       placeholder="발주서에 적힌 Order No.(예: PO-VT20261117-01)" className={cn(inputCls, "w-64 font-mono")} aria-label="발주번호" />
                <span className="text-[11.5px] text-foreground-subtle">
                  {same ? "지금 발주서와 같은 번호 — 지금 발주서를 이 파일로 교체합니다(따로 남기려면 번호를 바꿔 주세요)"
                    : read.doc_no ? "발주서에서 읽은 번호 — 다르면 고쳐 주세요" : "발주서에서 번호를 찾지 못했습니다 — 적거나 비워 두면 새 번호"}
                </span>
              </label>
              {!docNo.trim() && (
                <label className={cn("flex flex-wrap items-center gap-2", REQ_SCOPE)}>
                  <ReqLabel>발행자 이니셜</ReqLabel>
                  <input {...REQ} value={initials} maxLength={2} disabled={!!busy} onChange={(e) => setInitials(e.target.value.toUpperCase())}
                         className={cn(inputCls, "w-24 uppercase")} aria-label="발행자 이니셜" />
                  <span className="text-[11.5px] text-foreground-subtle">→ 새 번호 PO-{(initials || "??").toUpperCase()}{ymd}-NN</span>
                </label>
              )}
              <div className="flex flex-wrap items-center gap-4">
                <label className="flex items-center gap-2">
                  <span className="font-medium">Order Date</span>
                  <input type="date" value={date} disabled={!!busy} onChange={(e) => setDate(e.target.value)} className={cn(inputCls, "w-40")} />
                </label>
                <label className={cn("flex items-center gap-2", !usd && REQ_SCOPE)}>
                  {usd ? <span className="font-medium">납기일 (선택)</span> : <ReqLabel>입고 예정일</ReqLabel>}
                  <input type="date" {...req(!usd)} value={expected} disabled={!!busy} onChange={(e) => setExpected(e.target.value)}
                         className={cn(inputCls, "w-40")} />
                </label>
              </div>
            </div>
          </div>
        )}

        {error && <p className="mt-3 text-[12.5px] text-red-600">{error}</p>}
        <div className="mt-4 flex flex-wrap items-center justify-end gap-2">
          {busy && <span className="mr-auto text-[12.5px] text-foreground-muted">{busy}</span>}
          {read && !busy && forced && (
            <span className="mr-auto text-[12px] text-amber-700 dark:text-amber-300">
              버그 신고 {forced} 접수 — 품목이 덜 채워져도 이 파일 그대로 발행할 수 있습니다(품목은 나중에 [발주서 고치기]에서).{why ? ` ${why}` : ""}
            </span>
          )}
          {read && !busy && !forced && why && <span className="mr-auto text-[12px] text-amber-700 dark:text-amber-300">{why}</span>}
          <button type="button" onClick={onClose} disabled={!!busy} className="h-9 rounded-lg border border-border px-4 text-[13px]">취소</button>
          {!read ? (
            <button type="button" disabled={!file || !!busy} onClick={() => void send()}
                    className="h-9 rounded-lg bg-accent px-5 text-[13px] font-semibold text-white hover:brightness-105 disabled:opacity-50">
              올리고 읽기
            </button>
          ) : (
            <>
              {/* AI 판독이 실패하거나 이상할 때 — 버그를 신고하고 발행 버튼을 연다(이 파일은 그대로 저장, 품목은 나중에 [발주서 고치기]) */}
              {!forced && (
                <button type="button" disabled={!!busy} onClick={() => void reportAndSkip()}
                        title="AI 가 품목을 잘못 읽었거나 못 읽었으면 — 버그를 신고하고, 품목이 덜 채워져도 이 파일 그대로 발행할 수 있게 합니다"
                        className="h-9 rounded-lg border border-amber-500/60 px-4 text-[13px] font-semibold text-amber-700 hover:bg-amber-500/10 disabled:opacity-50 dark:text-amber-300">
                  버그 신고 후 넘어가기
                </button>
              )}
              <button type="button" disabled={!!busy || !!why} onClick={() => (same ? setAskSame(true) : void confirm())}
                      className="h-9 rounded-lg bg-accent px-5 text-[13px] font-semibold text-white hover:brightness-105 disabled:opacity-50">
                이 내용으로 발행
              </button>
            </>
          )}
        </div>
      </div>
      {askSame && (
        <SameNoConfirm no={docNo.trim()} onReplace={() => void confirm("replace")} onCancel={() => setAskSame(false)} />
      )}
      {reopen && <ReopenConfirm lines={reopen} onOk={() => void confirm(sameNoOk, true)} onCancel={() => setReopen(null)} />}
    </div>
  );
}

/** 같은 발주번호로 올렸을 때 교체 확인(사용자 2026-11-16) — 번호 하나 = 발주서 하나. 견적서 '덮어쓰기' 확인과 같은 뜻. */
function SameNoConfirm({ no, onReplace, onCancel }: { no: string; onReplace: () => void; onCancel: () => void }) {
  return (
    <div role="alertdialog" aria-modal="true" aria-labelledby="sameno-title" className="fixed inset-0 z-[70] grid place-items-center bg-black/30 px-4"
         onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel(); }}>
      <div className="anim-fade-in-up w-full max-w-[440px] rounded-2xl border border-border bg-surface-raised p-5 text-[13px] shadow-[0_30px_70px_-30px_rgba(15,30,70,0.55)]">
        <h3 id="sameno-title" className="text-[15px] font-semibold text-amber-700 dark:text-amber-300">같은 발주번호의 발주서가 있습니다</h3>
        <p className="mt-2 text-[13px] leading-relaxed text-foreground">
          <span className="font-mono">{no}</span> 는 지금 발주서의 번호입니다. 지금 발주서를 이 파일로 교체할까요?
        </p>
        <ul className="mt-2 list-disc space-y-0.5 pl-5 text-[12px] text-foreground-muted">
          <li>번호·판은 그대로이고, 그 판을 받으면 올린 파일이 나가며 품목·금액도 올린 파일 기준으로 바뀝니다.</li>
          <li>교체 전 파일은 보관되어 발주서 카드의 &lsquo;교체 전&rsquo;에서 다시 받을 수 있습니다.</li>
          <li>따로 남기려면 [취소]를 누르고 발주번호를 바꿔 올려 주세요(새 판이 됩니다).</li>
        </ul>
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" onClick={onCancel} className={cn("h-9 rounded-lg border border-border px-4 text-[13px]", POPUP_BTN_HOVER)}>취소</button>
          <button type="button" onClick={onReplace} autoFocus className="h-9 rounded-lg bg-amber-600 px-5 text-[13px] font-semibold text-white hover:bg-blue-700!">교체</button>
        </div>
      </div>
    </div>
  );
}

/** 입고를 마친 건을 다시 발행하면 입고가 다시 열리는 품목 알림(사용자 2026-11-16) — 품목 추가·수량 늘림만, 단가·설명만 바뀌면 묻지 않는다. */
function ReopenConfirm({ lines, onOk, onCancel }: { lines: ReopenLine[]; onOk: () => void; onCancel: () => void }) {
  return (
    <div role="alertdialog" aria-modal="true" aria-labelledby="reopen-title" className="fixed inset-0 z-[70] grid place-items-center bg-black/30 px-4"
         onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel(); }}>
      <div className="anim-fade-in-up w-full max-w-[460px] rounded-2xl border border-border bg-surface-raised p-5 text-[13px] shadow-[0_30px_70px_-30px_rgba(15,30,70,0.55)]">
        <h3 id="reopen-title" className="text-[15px] font-semibold text-amber-700 dark:text-amber-300">입고가 다시 진행됩니다</h3>
        <p className="mt-2 text-[13px] leading-relaxed text-foreground">입고를 마친 건입니다. 이 발주서로 발행하면 아래 품목의 입고가 남아 입고 대기로 돌아갑니다.</p>
        <ul className="mt-2 flex flex-col gap-1 rounded-lg border border-border bg-surface px-3 py-2 text-[12.5px]">
          {lines.map((l, i) => (
            <li key={i} className="flex flex-wrap items-center gap-2">
              <b className="text-foreground">{l.name}</b>
              <span className="tabular-nums text-foreground-muted">받음 {l.got.toLocaleString("ko-KR")} / 발주 {l.ordered.toLocaleString("ko-KR")}개</span>
              <span className="rounded bg-amber-500/12 px-1.5 text-[11px] font-semibold text-amber-700 dark:text-amber-300">{l.kind}</span>
            </li>
          ))}
        </ul>
        <p className="mt-2 text-[12px] text-foreground-muted">추가로 받은 뒤 입고 칸에서 기록하면 다시 입고 완료가 됩니다. 따로 사는 물건이면 [취소] 후 새 구매 건으로 등록해 주세요.</p>
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" onClick={onCancel} className={cn("h-9 rounded-lg border border-border px-4 text-[13px]", POPUP_BTN_HOVER)}>취소</button>
          <button type="button" autoFocus onClick={onOk} className="h-9 rounded-lg bg-amber-600 px-5 text-[13px] font-semibold text-white hover:bg-blue-700!">발행</button>
        </div>
      </div>
    </div>
  );
}
