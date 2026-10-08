// 견적서 작성 — 제품 추천 [최종 제안 확정] 뒤 [견적서 작성 →] 으로 들어온다(사용자 2026-10-06, 영업부 팀장 요청).
// 왼쪽: 진행 상황(AI 가 지금 묻는 것·발행 전 확인할 것)·머리·품목(직접 수정 → [변경 저장])·발행 이력,
// 오른쪽: AI 와 대화 — 말로 품목 추가·수정·삭제, AI 가 빠진 것(고객·현장 작업 인원·일수·단가·담당자)을 순서대로 묻는다.
// 초안은 추천 결과(선정 모델·구성품·단가표)로 서버가 만든다. 발행하면 엑셀, 다시 발행하면 같은 번호에 사내 판(v2·v3 …)이 올라간다 — 견적서에는 번호만.
// 발행 전후 PDF 미리보기, 발행 뒤 덮어쓰기(같은 판), 내려받기는 PDF·엑셀 중 골라 다른 이름으로 저장(사용자 2026-10-06).
"use client";

import { useEffect, useRef, useState } from "react";
import type { QuoteDelivery, QuoteForm, QuoteLine, QuoteListItem, QuoteSession, QuoteWork } from "@/lib/shared/product-recommend";
import { RichText } from "@/components/domains/sales/agent-chat";
import { Icon } from "@/components/shared/ui/icon";
import { cn } from "@/lib/shared/utils";
import { ErrorBox, inputCls, primaryBtn, secondaryBtn } from "./proposal-actions";

const CONTACT_KEY = "atcQuoteContact";
const CONTACT_FIELDS = ["initials", "contact_name", "contact_title", "contact_mobile", "contact_email"] as const;

/** 브라우저에 기억한 담당자 정보 — 새 견적 초안에 미리 채운다(저장이 막혀 있어도 그대로 진행). */
export function savedContact(): Record<string, string> {
  try {
    const v = JSON.parse(window.localStorage.getItem(CONTACT_KEY) ?? "{}") as Record<string, unknown>;
    return Object.fromEntries(CONTACT_FIELDS.filter((k) => typeof v[k] === "string" && v[k]).map((k) => [k, v[k] as string]));
  } catch {
    return {};
  }
}

function rememberContact(f: QuoteForm) {
  try {
    window.localStorage.setItem(CONTACT_KEY, JSON.stringify(Object.fromEntries(CONTACT_FIELDS.map((k) => [k, f[k] ?? ""]))));
  } catch { /* 저장이 막혀도 계속 */ }
}

async function api<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(`/api/product-recommend${path}`, body === undefined ? { cache: "no-store" }
    : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof d?.detail === "string" ? d.detail : "요청을 처리하지 못했습니다.");
  return d as T;
}

const won = (n: number | null | undefined) => (n == null ? "-" : `${Math.round(n).toLocaleString("ko-KR")}원`);
const KIND_KO: Record<QuoteLine["kind"], string> = { product: "제품", option: "옵션", freight: "물류", work: "인건비", extra: "추가" };
/** 납품 방식(영업팀 기준) — 고르면 서버가 물류비·인건비 줄과 납품장소 문구를 맞춘다. */
const DELIVERY: { case: 1 | 2 | 3 | 4 | 5; label: string; freight: boolean; work: boolean }[] = [
  { case: 1, label: "택배 발송", freight: false, work: false },
  { case: 2, label: "화물 직납", freight: true, work: false },
  { case: 3, label: "UND 인원 설치 납품", freight: false, work: true },
  { case: 4, label: "화물 + UND 인원 설치", freight: true, work: true },
  { case: 5, label: "별도 협의", freight: false, work: false },
];
/** UND 설치 인건비 회사 기준 — 1인 8시간(1일) 80만원(사용자 2026-10-07, backend quote_session.WORK_RATE 와 같게). */
const WORK_RATE = 800_000;
const isAuto = (l: QuoteLine) => l.kind === "work" || l.kind === "freight";   // 납품 방식 칸에서만 바꾸는 줄

export type Fmt = "pdf" | "xlsx";
const FMT: Record<Fmt, { label: string; mime: string }> = {
  pdf: { label: "PDF", mime: "application/pdf" },
  xlsx: { label: "엑셀", mime: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" },
};
type SaveHandle = { createWritable: () => Promise<{ write: (b: Blob) => Promise<void>; close: () => Promise<void>; abort: () => Promise<void> }> };
type SavePicker = (o: { suggestedName: string; types: { description: string; accept: Record<string, string[]> }[] }) => Promise<SaveHandle>;

async function fetchBlob(url: string): Promise<Blob> {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    throw new Error(typeof d?.detail === "string" ? d.detail : "파일을 받지 못했습니다.");
  }
  return r.blob();
}

/** 다른 이름으로 저장 — 저장 창을 지원하는 브라우저(크롬·엣지)는 위치·이름을 고르는 창, 아니면 적은 이름으로 내려받기.
 *  저장 창은 클릭 직후에만 열 수 있어 창을 먼저 띄우고 파일을 받는다. 취소하면 false. */
async function saveAs(url: string, name: string, fmt: Fmt): Promise<boolean> {
  const picker = (window as unknown as { showSaveFilePicker?: SavePicker }).showSaveFilePicker;
  if (picker) {
    let handle: SaveHandle;
    try {
      handle = await picker({ suggestedName: name, types: [{ description: FMT[fmt].label, accept: { [FMT[fmt].mime]: [`.${fmt}`] } }] });
    } catch (e) {
      if ((e as Error).name === "AbortError") return false;
      throw e;
    }
    const w = await handle.createWritable();
    try {
      await w.write(await fetchBlob(url));
      await w.close();
    } catch (e) {
      await w.abort().catch(() => {});
      throw e;
    }
    return true;
  }
  const href = URL.createObjectURL(await fetchBlob(url));
  const a = document.createElement("a");
  a.href = href;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(href), 10_000);
  return true;
}

/** 내려받기 창 — PDF·엑셀 고르기 + 파일 이름 바꾸기(견적서·거래명세서 같이 씀). urlFor: 형식별 받을 주소. */
export function DownloadDialog({ urlFor, fileName, title, onClose }: {
  urlFor: (fmt: Fmt) => string; fileName: string; title: string; onClose: () => void;
}) {
  const [fmt, setFmt] = useState<Fmt>("pdf");
  const [name, setName] = useState(fileName.replace(/\.(xlsx|pdf)$/i, ""));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const clean = name.replace(/[\\/:*?"<>|]+/g, " ").trim();

  async function go() {
    if (!clean) return;
    setBusy(true);
    setErr(null);
    try {
      if (await saveAs(urlFor(fmt), `${clean}.${fmt}`, fmt)) onClose();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-40 grid place-items-center bg-black/30 p-4" onClick={busy ? undefined : onClose}>
      <div className="flex w-full max-w-lg flex-col gap-3 rounded-xl border border-border bg-surface p-5 shadow-xl" onClick={(e) => e.stopPropagation()}
           role="dialog" aria-label="견적서 내려받기">
        <div>
          <h3 className="text-[15px] font-semibold text-foreground">{title}</h3>
          <p className="text-[12px] text-foreground-subtle">형식을 고르고, 필요하면 파일 이름을 바꿔 저장하세요.</p>
        </div>
        <div className="flex gap-2">
          {(Object.keys(FMT) as Fmt[]).map((k) => (
            <button key={k} type="button" onClick={() => setFmt(k)} aria-pressed={fmt === k}
                    className={cn("flex-1 rounded-lg border px-3 py-2 text-[13px] font-semibold",
                      fmt === k ? "border-accent bg-accent/10 text-accent" : "border-border text-foreground-muted hover:bg-foreground/[0.04]")}>
              {FMT[k].label} (.{k})
            </button>
          ))}
        </div>
        <label className="flex flex-col gap-1 text-[12.5px] text-foreground-muted">
          파일 이름
          <div className="flex items-center gap-1.5">
            <input value={name} onChange={(e) => setName(e.target.value)} maxLength={180} className={cn(inputCls, "flex-1")}
                   onKeyDown={(e) => { if (e.key === "Enter" && !e.nativeEvent.isComposing) void go(); }} />
            <span className="text-foreground-subtle">.{fmt}</span>
          </div>
        </label>
        <ErrorBox error={err} />
        <div className="flex justify-end gap-2">
          <button type="button" className={secondaryBtn} disabled={busy} onClick={onClose}>닫기</button>
          <button type="button" className={primaryBtn} disabled={busy || !clean} onClick={() => void go()}>
            <Icon name="download" className="h-4 w-4" /> {busy ? (fmt === "pdf" ? "PDF 만드는 중…" : "받는 중…") : "다른 이름으로 저장"}
          </button>
        </div>
      </div>
    </div>
  );
}

/** 미리보기(PDF, 파일로 남기지 않음) — 견적서·거래명세서 같이 씀. url: PDF 를 주는 주소. */
export function PreviewDialog({ url: src0, title, sub, onClose }: { url: string; title: string; sub: string; onClose: () => void }) {
  const [src, setSrc] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let url: string | null = null;
    let alive = true;
    fetchBlob(src0)
      .then((b) => { url = URL.createObjectURL(b); if (alive) setSrc(url); })
      .catch((e: Error) => { if (alive) setErr(e.message); });
    return () => { alive = false; if (url) URL.revokeObjectURL(url); };
  }, [src0]);
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4" onClick={onClose}>
      <div className="flex h-[92svh] w-full max-w-4xl flex-col overflow-hidden rounded-xl border border-border bg-surface shadow-xl" onClick={(e) => e.stopPropagation()}
           role="dialog" aria-label={title}>
        <div className="flex items-center gap-2 border-b border-border px-4 py-2.5">
          <h3 className="text-[14px] font-semibold text-foreground">{title}</h3>
          <span className="text-[12px] text-foreground-subtle">{sub}</span>
          <button type="button" className="ml-auto grid h-7 w-7 place-items-center rounded-md hover:bg-foreground/[0.06]" onClick={onClose} aria-label="닫기">
            <Icon name="x" className="h-4 w-4" />
          </button>
        </div>
        {err ? <div className="p-4"><ErrorBox error={err} /></div>
          : src ? <iframe src={src} title={title} className="w-full flex-1 bg-white" />
            : <p className="flex flex-1 items-center justify-center gap-2 text-[13px] text-foreground-subtle"><Icon name="refresh" className="h-4 w-4 animate-spin text-accent" /> 견적서를 PDF 로 만드는 중…</p>}
      </div>
    </div>
  );
}

/** AI 질문의 선택지 — 누르면 그 글이 답으로 간다. */
function Choices({ items, disabled, onPick }: { items: string[]; disabled: boolean; onPick: (c: string) => void }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map((c) => (
        <button key={c} type="button" disabled={disabled} onClick={() => onPick(c)}
                className="rounded-full border border-accent/40 bg-surface px-2.5 py-1 text-[12px] text-accent transition hover:bg-accent hover:text-white disabled:opacity-50">
          {c}
        </button>
      ))}
    </div>
  );
}

/** 견적서 탭 — 목록 또는 한 건 작업 화면. */
export function QuoteTab({ openId, onOpen }: { openId: number | null; onOpen: (id: number | null) => void }) {
  if (openId) return <QuoteWorkspace key={openId} id={openId} onBack={() => onOpen(null)} />;
  return <QuoteList onOpen={onOpen} />;
}

function QuoteList({ onOpen }: { onOpen: (id: number) => void }) {
  const [items, setItems] = useState<QuoteListItem[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  /** 견적서 수기 작성(사용자 2026-10-08) — AI 추천 없이 빈 견적서를 만들어 연다. */
  async function createManual() {
    setCreating(true);
    setErr(null);
    try {
      onOpen((await api<QuoteSession>("/quotes/manual", { form: savedContact() })).id);
    } catch (e) {
      setErr((e as Error).message);
      setCreating(false);
    }
  }
  useEffect(() => {
    let alive = true;
    api<{ items: QuoteListItem[] }>("/quotes")
      .then((d) => { if (alive) setItems(d.items.filter((x) => x.status !== "issued")); })
      .catch((e: Error) => { if (alive) setErr(e.message); });
    return () => { alive = false; };
  }, []);
  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <p className="min-w-0 flex-1 text-[12.5px] text-foreground-muted">
          AI 추천이 아직 없는 제품(그리퍼·AMR 등)은 <b>[견적서 수기 작성]</b>으로 빈 견적서에서 시작합니다 — 발행하면 건 번호가 붙어 제품 영업 건 관리로 이어집니다.
        </p>
        <button type="button" className={primaryBtn} disabled={creating} onClick={() => void createManual()}>
          <Icon name="plus" className="h-4 w-4" /> {creating ? "만드는 중…" : "견적서 수기 작성"}
        </button>
      </div>
      <p className="text-[12.5px] text-foreground-muted">
        제품 추천에서 툴체인저 후보를 [최종 제안 확정]한 뒤 <b>[견적서 작성 →]</b>을 누르면 추천 결과로 초안이 만들어지고, AI 와 대화하며 채웁니다.
        이 목록에는 <b>작성 중(발행 전)</b>인 견적서만 보입니다 — 발행한 견적서는 [제품 영업 건 관리]에서 건을 열어 다시 받거나 [견적서 고치기 →]로 고칩니다.
      </p>
      <ErrorBox error={err} />
      {items === null && !err && <p className="text-[12.5px] text-foreground-subtle">불러오는 중…</p>}
      {items && items.length === 0 && <p className="rounded-xl border border-dashed border-border p-6 text-center text-[13px] text-foreground-subtle">작성 중인 견적서가 없습니다.</p>}
      {items && items.length > 0 && (
        <div className="overflow-hidden rounded-xl border border-border bg-surface">
          <table className="w-full text-[13px]">
            <thead>
              <tr className="border-b border-border text-left text-[12px] text-foreground-subtle">
                <th className="px-3 py-2 font-medium">견적번호</th><th className="px-3 py-2 font-medium">고객사</th>
                <th className="px-3 py-2 font-medium">모델</th><th className="px-3 py-2 font-medium">상태</th>
                <th className="px-3 py-2 text-right font-medium">합계</th><th className="px-3 py-2 font-medium">수정</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {items.map((x) => (
                <tr key={x.id} className="cursor-pointer hover:bg-foreground/[0.03]" onClick={() => onOpen(x.id)}>
                  <td className="px-3 py-2 font-medium text-foreground">{x.quote_no ? `${x.quote_no} (v${x.revision + 1})` : "— (초안)"}</td>
                  <td className="px-3 py-2">{x.customer ?? "—"}</td>
                  <td className="px-3 py-2">{x.manual ? <span className="text-violet-700 dark:text-violet-300">수기 작성</span> : x.model ?? "—"}</td>
                  <td className="px-3 py-2">
                    <span className={cn("rounded-md px-1.5 py-0.5 text-[11.5px] font-semibold",
                      x.status === "issued" ? "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300" : "bg-amber-500/15 text-amber-700 dark:text-amber-300")}>
                      {x.status === "issued" ? "발행" : "작성 중"}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right">{x.total != null ? x.total.toLocaleString("ko-KR") : "—"}</td>
                  <td className="px-3 py-2 text-foreground-subtle">{new Date(x.updated_at).toLocaleString("ko-KR")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

type Draft = { form: QuoteForm; lines: QuoteLine[]; work: QuoteWork; delivery: QuoteDelivery; include: string[] };
const toDraft = (q: QuoteSession): Draft => ({
  form: { ...q.state.form }, lines: q.state.lines.map((l) => ({ ...l })), work: { ...q.state.work },
  delivery: { ...(q.state.delivery ?? { case: null, freight: null }) }, include: [],
});

const HEAD: { k: keyof QuoteForm; label: string; wide?: boolean; area?: boolean; req?: boolean; ph?: string }[] = [
  { k: "customer", label: "고객사", req: true }, { k: "to", label: "받는 분(To)" }, { k: "cc", label: "참조(CC)" },
  { k: "subject", label: "파일명 내용", ph: "magbot툴체인저" }, { k: "initials", label: "담당자 이니셜", req: true, ph: "예: VT" },
  { k: "contact_name", label: "담당자 이름", req: true }, { k: "contact_title", label: "직함" },
  { k: "contact_mobile", label: "휴대폰", req: true }, { k: "contact_email", label: "이메일", req: true },
  { k: "delivery", label: "납기" }, { k: "place", label: "납품장소" }, { k: "payment", label: "결제조건" },
  // 입금 계좌(사용자 2026-10-08) — 기본은 회사 계좌, 바꾸면 이 견적서에만
  { k: "bank", label: "입금 은행", ph: "비우면 회사 기본 계좌" }, { k: "account_no", label: "입금 계좌번호", ph: "비우면 회사 기본 계좌" },
  { k: "account_holder", label: "예금주", ph: "비우면 회사 기본 계좌" },
  { k: "comments", label: "Comments", wide: true, area: true }, { k: "note", label: "비고", wide: true, area: true },
];

export function QuoteWorkspace({ id, onBack }: { id: number; onBack: () => void }) {
  const [q, setQ] = useState<QuoteSession | null>(null);
  const [d, setD] = useState<Draft | null>(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [preview, setPreview] = useState(false);
  const [saving, setSaving] = useState<{ revision: number; fileName: string; title: string } | null>(null);
  const chatEnd = useRef<HTMLDivElement>(null);

  function load(s: QuoteSession) {
    setQ(s);
    setD(toDraft(s));
    setDirty(false);
  }
  useEffect(() => {
    let alive = true;
    api<QuoteSession>(`/quotes/${id}`).then((s) => { if (alive) load(s); }).catch((e: Error) => { if (alive) setErr(e.message); });
    return () => { alive = false; };
  }, [id]);
  useEffect(() => { chatEnd.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [q?.chat.length, busy]);

  async function run<T>(label: string, f: () => Promise<T>): Promise<T | null> {
    setBusy(label);
    setErr(null);
    try {
      return await f();
    } catch (e) {
      setErr((e as Error).message);
      return null;
    } finally {
      setBusy(null);
    }
  }

  async function send(say?: string) {
    const m = (say ?? text).trim();
    if (!m || busy || dirty) return;
    if (say === undefined) setText("");
    setQ((s) => (s ? { ...s, chat: [...s.chat, { role: "user", text: m }] } : s));
    const r = await run("견적 반영 중", () => api<{ quote: QuoteSession }>(`/quotes/${id}/chat`, { message: m }));
    if (r) load(r.quote);
    else setQ((s) => (s ? { ...s, chat: s.chat.slice(0, -1) } : s));
  }

  async function save() {
    if (!d) return;
    const body = {
      form: d.form, work: { people: d.work.people, days: d.work.days, rate: d.work.rate }, delivery: d.delivery,
      include_options: d.include,
      // 물류비·인건비 줄은 납품 방식으로, 화면에서 넣은 옵션 줄은 include_options 로 보낸다(두 번 들어가지 않게)
      lines: d.lines.filter((l) => !isAuto(l) && !l.id.startsWith("new-opt-")).map((l) => ({
        id: l.id.startsWith("new") ? null : l.id, name: l.name, qty: l.qty, unit_price: l.unit_price, remark: l.remark ?? "",
        ...(l.conditional && l.confirmed ? { confirmed: true } : {}),
      })),
    };
    const s = await run("저장 중", () => api<QuoteSession>(`/quotes/${id}/edit`, body));
    if (s) load(s);
  }

  /** 발행(또는 덮어쓰기) → 바로 내려받기 창(PDF·엑셀 고르기, 다른 이름으로 저장). */
  async function issue(overwrite = false) {
    if (!q) return;
    const s = await run(overwrite ? "덮어쓰는 중" : "발행 중", () => api<QuoteSession>(`/quotes/${id}/issue`, { overwrite }));
    if (s) {
      load(s);
      rememberContact(s.state.form);
      const h = s.history[s.history.length - 1];
      if (h) setSaving({ revision: h.revision, fileName: h.file_name, title: `${overwrite ? "덮어썼습니다" : "발행했습니다"} — 내려받기` });
    }
  }

  if (!q || !d) {
    return (
      <section className="flex flex-col gap-3">
        <button type="button" className={cn(secondaryBtn, "self-start")} onClick={onBack}><Icon name="back" className="h-4 w-4" /> 견적서 목록</button>
        {err ? <ErrorBox error={err} /> : <p className="text-[12.5px] text-foreground-subtle">불러오는 중…</p>}
      </section>
    );
  }

  const edit = (patch: Partial<Draft>) => { setD((x) => (x ? { ...x, ...patch } : x)); setDirty(true); };
  const setForm = (k: keyof QuoteForm, v: string) => edit({ form: { ...d.form, [k]: k === "fx_rate" ? (Number(v.replace(/,/g, "")) || null) : v } });
  const setLine = (i: number, patch: Partial<QuoteLine>) => edit({ lines: d.lines.map((l, j) => (j === i ? { ...l, ...patch } : l)) });
  const setWork = (patch: Partial<QuoteWork>) => edit({ work: { ...d.work, ...patch } });
  const setDelivery = (patch: Partial<QuoteDelivery>) => edit({ delivery: { ...d.delivery, ...patch } });
  const dv = DELIVERY.find((x) => x.case === d.delivery.case);
  // 고치는 중 합계 — 물류비·인건비는 납품 방식 칸 값으로 다시 계산(저장하면 서버가 같은 식으로 줄을 만든다)
  const total = d.lines.filter((l) => !isAuto(l)).reduce((s, l) => s + (l.qty ?? 0) * (l.unit_price ?? 0), 0)
    + (dv?.freight ? d.delivery.freight ?? 0 : 0) + (dv?.work ? (d.work.people ?? 0) * (d.work.days ?? 0) * (d.work.rate ?? WORK_RATE) : 0);
  const en = d.form.lang === "en";
  const issued = q.status === "issued";
  const optionsLeft = q.state.options.filter((o) => !d.include.includes(o.name));

  return (
    <div className="grid items-start gap-4 lg:grid-cols-2">
      <div className="flex min-w-0 flex-col gap-4">
        {/* 진행 상황 */}
        <section className="flex flex-col gap-2 rounded-xl border border-accent/30 bg-surface p-4">
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" className={secondaryBtn} onClick={onBack}><Icon name="back" className="h-4 w-4" /> 목록</button>
            <h2 className="text-[15px] font-semibold text-foreground">
              {q.quote_no ? `${q.quote_no} (v${q.revision + 1})` : "견적서 초안"} · {q.manual ? "수기 견적" : q.state.model}
            </h2>
            <span className={cn("rounded-md px-1.5 py-0.5 text-[11.5px] font-semibold",
              issued ? "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300" : "bg-amber-500/15 text-amber-700 dark:text-amber-300")}>
              {issued ? "발행" : "작성 중"}
            </span>
          </div>
          {q.question && !dirty && (
            <div className="flex flex-col gap-1.5 rounded-lg bg-accent/[0.07] px-3 py-2 text-[12.5px] text-foreground">
              <p><span className="font-semibold text-accent">AI 가 묻는 중 · </span>{q.question.text}</p>
              {q.question.choices && <Choices items={q.question.choices} disabled={!!busy} onPick={(c) => void send(c)} />}
            </div>
          )}
          {q.problems.length > 0 && (
            <ul className="flex flex-col gap-0.5 text-[12px] text-amber-700 dark:text-amber-300">
              {q.problems.map((p) => <li key={p}>· {p}</li>)}
            </ul>
          )}
          {!q.question && q.problems.length === 0 && !dirty && (
            <p className="text-[12.5px] text-emerald-700 dark:text-emerald-300">필요한 내용을 다 채웠습니다 — [견적서 발행]을 누르면 엑셀이 만들어집니다.</p>
          )}
        </section>

        {/* 머리 */}
        <section className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-4">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-[14px] font-semibold text-foreground">견적서 머리</h3>
            <div className="ml-auto flex overflow-hidden rounded-md border border-border text-[12px]">
              {(["ko", "en"] as const).map((l) => (
                <button key={l} type="button" disabled={!!busy} onClick={() => setForm("lang", l)}
                        className={cn("px-2.5 py-1", d.form.lang === l ? "bg-accent text-white" : "text-foreground-muted")}>
                  {l === "ko" ? "국내(한글·원)" : "해외(영문·$)"}
                </button>
              ))}
            </div>
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            {HEAD.map((h) => (
              <label key={h.k} className={cn("flex flex-col gap-1 text-[12px] text-foreground-muted", h.wide && "sm:col-span-2")}>
                <span>{h.label}{h.req && <span className="text-red-600"> *</span>}</span>
                {h.area
                  ? <textarea className={cn(inputCls, "min-h-[56px] text-[12.5px]")} value={String(d.form[h.k] ?? "")} disabled={!!busy}
                              onChange={(e) => setForm(h.k, e.target.value)} />
                  : <input className={cn(inputCls, "text-[12.5px]")} value={String(d.form[h.k] ?? "")} placeholder={h.ph} disabled={!!busy}
                           onChange={(e) => setForm(h.k, h.k === "initials" ? e.target.value.toUpperCase().slice(0, 2) : e.target.value)} />}
              </label>
            ))}
            {en && (
              <label className="flex flex-col gap-1 text-[12px] text-foreground-muted">
                <span>환율(원/달러) <span className="text-red-600">*</span></span>
                <input className={cn(inputCls, "text-[12.5px]")} inputMode="decimal" value={d.form.fx_rate ?? ""} placeholder="예: 1380"
                       disabled={!!busy} onChange={(e) => setForm("fx_rate", e.target.value)} />
              </label>
            )}
          </div>
        </section>

        {/* 품목 */}
        <section className="flex flex-col gap-2 rounded-xl border border-border bg-surface p-4">
          <div className="flex flex-wrap items-baseline gap-2">
            <h3 className="text-[14px] font-semibold text-foreground">품목</h3>
            <span className="text-[11.5px] text-foreground-subtle">금액은 원화(VAT 별도){en ? " — 발행 때 환율로 달러 환산" : ""} · 제품 단가는 회사 단가표</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] text-[12.5px]">
              <thead>
                <tr className="text-left text-[11.5px] text-foreground-subtle">
                  <th className="px-1.5 py-1 font-medium">구분</th><th className="px-1.5 py-1 font-medium">품목</th>
                  <th className="w-[70px] px-1.5 py-1 font-medium">수량</th><th className="w-[120px] px-1.5 py-1 font-medium">단가</th>
                  <th className="px-1.5 py-1 text-right font-medium">금액</th><th className="px-1.5 py-1 font-medium">비고</th><th />
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {d.lines.map((l, i) => (
                  <tr key={l.id}>
                    <td className="px-1.5 py-1 text-[11.5px] text-foreground-subtle">{KIND_KO[l.kind]}</td>
                    <td className="px-1.5 py-1">
                      {l.kind === "extra"
                        ? <input className={cn(inputCls, "text-[12.5px]")} value={l.name} aria-label="품목명" disabled={!!busy} onChange={(e) => setLine(i, { name: e.target.value })} />
                        : <span className="font-medium text-foreground">{l.name}</span>}
                    </td>
                    <td className="px-1.5 py-1">
                      {isAuto(l) ? <span>{l.qty}{l.unit ? ` ${l.unit}` : ""}</span> : (
                        <div className="flex flex-col gap-0.5">
                          <input className={cn(inputCls, "text-[12.5px]", (l.qty == null || (l.conditional && !l.confirmed)) && "border-amber-500")}
                                 type="number" min={1} value={l.qty ?? ""} aria-label="수량" disabled={!!busy}
                                 onChange={(e) => setLine(i, { qty: Math.max(0, Math.floor(Number(e.target.value) || 0)), ...(l.conditional ? { confirmed: true } : {}) })} />
                          {l.conditional && (l.confirmed
                            ? <span className="text-[11px] text-emerald-700 dark:text-emerald-300">✓ 확인함</span>
                            : <button type="button" disabled={!!busy || l.qty == null} title={l.basis || "조건부 수량 — 근거를 확인한 뒤 누르세요"}
                                      onClick={() => setLine(i, { confirmed: true })}
                                      className="rounded border border-amber-500/60 px-1 text-[11px] font-semibold text-amber-700 hover:bg-amber-500/10 dark:text-amber-300">
                                조건부 · 수량 확인
                              </button>)}
                        </div>
                      )}
                    </td>
                    <td className="px-1.5 py-1">
                      {isAuto(l) ? <span>{won(l.unit_price)}</span> : (
                        <input className={cn(inputCls, "text-[12.5px]", l.unit_price == null && "border-amber-500")} inputMode="numeric"
                               value={l.unit_price ?? ""} aria-label="단가" placeholder={l.price_status === "unset" ? "가격 미정" : "단가"} disabled={!!busy}
                               onChange={(e) => setLine(i, { unit_price: Number(e.target.value.replace(/,/g, "")) || null })} />
                      )}
                    </td>
                    <td className="whitespace-nowrap px-1.5 py-1 text-right font-semibold">{won((l.qty ?? 0) * (l.unit_price ?? 0))}</td>
                    <td className="px-1.5 py-1">
                      {isAuto(l) ? <span className="text-foreground-muted">{l.remark}</span> : (
                        <input className={cn(inputCls, "text-[12px]")} value={l.remark ?? ""} aria-label="비고" disabled={!!busy}
                               onChange={(e) => setLine(i, { remark: e.target.value })} />
                      )}
                    </td>
                    <td className="px-1 py-1">
                      {!isAuto(l) && (
                        <button type="button" aria-label={`${l.name} 빼기`} title="빼기" disabled={!!busy}
                                className="text-foreground-subtle hover:text-red-600" onClick={() => edit({ lines: d.lines.filter((_, j) => j !== i) })}>
                          <Icon name="trash" className="h-4 w-4" />
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex flex-wrap items-center gap-1.5 text-[12px]">
            <button type="button" disabled={!!busy} className="inline-flex items-center gap-1 rounded border border-border px-2 py-0.5 text-foreground-muted hover:text-foreground"
                    onClick={() => edit({ lines: [...d.lines, { id: `new${Date.now()}`, kind: "extra", key: "extra", name: "", qty: 1, unit_price: null, remark: "" }] })}>
              <Icon name="plus" className="h-3.5 w-3.5" /> 항목 추가
            </button>
            {q.manual && <PricePicker disabled={!!busy}
                                      onPick={(p) => edit({ lines: [...d.lines, { id: `new${Date.now()}`, kind: "extra", key: "extra",
                                        name: `${p.model} ${p.item}`.slice(0, 60), qty: 1, unit_price: p.unit_price, remark: "" }] })} />}
            {optionsLeft.map((o) => (
              <button key={o.name} type="button" disabled={!!busy}
                      className="inline-flex items-center gap-1 rounded border border-border px-2 py-0.5 text-foreground-muted hover:text-foreground"
                      onClick={() => edit({ include: [...d.include, o.name],
                        lines: [...d.lines, { ...o, id: `new-opt-${o.name}`, kind: "option" } as QuoteLine] })}>
                <Icon name="plus" className="h-3.5 w-3.5" /> 옵션 {o.name} ({won(o.unit_price)})
              </button>
            ))}
          </div>
          {/* 납품 방식 — 1 택배(그대로) · 2 화물 직납(물류비) · 3 설치 납품(인건비 = 인원 × 일수 × 1인 1일 단가) · 4 화물 + 설치 · 5 별도 협의(그대로) */}
          <div className="flex flex-col gap-2 rounded-md bg-foreground/[0.03] px-2.5 py-2 text-[12.5px]">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="mr-1 font-semibold text-foreground">납품 방식</span>
              {DELIVERY.map((x) => (
                <button key={x.case} type="button" disabled={!!busy} onClick={() => setDelivery({ case: x.case })}
                        className={cn("rounded-full border px-2.5 py-0.5",
                          d.delivery.case === x.case ? "border-accent bg-accent text-white" : "border-border text-foreground-muted hover:text-foreground")}>
                  {x.case}. {x.label}
                </button>
              ))}
            </div>
            {dv && (dv.freight || dv.work) && (
              <div className="flex flex-wrap items-center gap-2">
                {dv.freight && (
                  <label className="flex items-center gap-1 text-foreground-muted">
                    물류비(원)
                    <input className={cn(inputCls, "w-[110px] py-0.5 text-[12.5px]")} inputMode="numeric" value={d.delivery.freight ?? ""}
                           disabled={!!busy} onChange={(e) => setDelivery({ freight: Number(e.target.value.replace(/,/g, "")) || null })} />
                  </label>
                )}
                {dv.work && ([["people", "설치 인원(명)"], ["days", "일수"], ["rate", "1인 1일(8H) 단가(원)"]] as const).map(([k, lab]) => (
                  <label key={k} className="flex items-center gap-1 text-foreground-muted">
                    {lab}
                    <input className={cn(inputCls, "w-[96px] py-0.5 text-[12.5px]")} inputMode="numeric" value={d.work[k] ?? ""} disabled={!!busy}
                           placeholder={k === "rate" ? WORK_RATE.toLocaleString("ko-KR") : undefined}
                           onChange={(e) => setWork({ [k]: Number(e.target.value.replace(/,/g, "")) || null })} />
                  </label>
                ))}
              </div>
            )}
            {dv && !dv.freight && !dv.work && <span className="text-[11.5px] text-foreground-subtle">{dv.label} — 품목 그대로(물류비·인건비 없음)</span>}
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-2">
            <span className="text-[13px] text-foreground-muted">합계(VAT 별도)</span>
            <span className="text-[16px] font-bold text-foreground">{won(dirty ? total : q.subtotal)}</span>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" className={secondaryBtn} disabled={!dirty || !!busy} onClick={() => void save()}>
              <Icon name="check" className="h-4 w-4" /> 변경 저장
            </button>
            {dirty && <button type="button" className="text-[12px] text-foreground-subtle hover:underline" disabled={!!busy} onClick={() => load(q)}>되돌리기</button>}
            <button type="button" className={cn(secondaryBtn, "ml-auto")} disabled={dirty || !!busy || q.problems.length > 0} onClick={() => setPreview(true)}
                    title={q.problems.length ? "먼저 위의 확인할 것을 채워 주세요" : dirty ? "먼저 변경을 저장해 주세요" : "지금 내용 그대로 견적서를 봅니다"}>
              <Icon name="eye" className="h-4 w-4" /> 미리보기
            </button>
            {issued && (
              <button type="button" className={secondaryBtn} disabled={dirty || !!busy || q.problems.length > 0} onClick={() => void issue(true)}
                      title={`판 번호를 올리지 않고 v${q.revision + 1}을 지금 내용으로 바꿉니다`}>
                <Icon name="refresh" className="h-4 w-4" /> 덮어쓰기 (v{q.revision + 1})
              </button>
            )}
            <button type="button" className={primaryBtn} disabled={dirty || !!busy || q.problems.length > 0} onClick={() => void issue()}
                    title={q.problems.length ? "발행 전에 위의 확인할 것을 채워 주세요" : dirty ? "먼저 변경을 저장해 주세요" : ""}>
              <Icon name="file-spreadsheet" className="h-4 w-4" /> {issued ? `수정본 발행 (v${q.revision + 2})` : "견적서 발행"}
            </button>
          </div>
          {issued && !dirty && (
            <p className="text-[11.5px] text-foreground-subtle">발행 뒤에도 고칠 수 있습니다 — 고치고 저장한 뒤 [덮어쓰기]는 같은 판을 바꾸고, [수정본 발행]은 다음 판을 만듭니다.</p>
          )}
          {dirty && <p className="text-[11.5px] text-amber-700 dark:text-amber-300">직접 고친 내용이 있습니다 — [변경 저장] 후 대화·발행할 수 있습니다.</p>}
        </section>

        {/* 발행 이력 */}
        {q.history.length > 0 && (
          <section className="flex flex-col gap-1.5 rounded-xl border border-border bg-surface p-4 text-[12.5px]">
            <h3 className="text-[14px] font-semibold text-foreground">발행 이력</h3>
            {[...q.history].reverse().map((h) => (
              <div key={h.revision} className="flex flex-wrap items-center gap-2">
                <span className="font-medium">v{h.revision + 1}{h.revision ? "" : " (최초 발행)"}</span>
                <span className="text-foreground-subtle">{new Date(h.issued_at).toLocaleString("ko-KR")}</span>
                <span>{h.currency === "USD" ? `$${h.total.toLocaleString("en-US", { minimumFractionDigits: 2 })}` : won(h.total)}</span>
                <button type="button" className="ml-auto inline-flex items-center gap-1 text-accent hover:underline"
                        onClick={() => setSaving({ revision: h.revision, fileName: h.file_name, title: `v${h.revision + 1} 다시 다운로드` })}>
                  <Icon name="download" className="h-3.5 w-3.5" /> 다시 다운로드
                </button>
              </div>
            ))}
          </section>
        )}
        <ErrorBox error={err} />
      </div>
      {preview && <PreviewDialog url={`/api/product-recommend/quotes/${q.id}/preview`} title="견적서 미리보기"
                                  sub="지금 저장된 내용 그대로 · 발행 전이면 견적번호는 발행 때 정해집니다" onClose={() => setPreview(false)} />}
      {saving && <DownloadDialog urlFor={(fmt) => `/api/product-recommend/quotes/${q.id}/file?fmt=${fmt}&revision=${saving.revision}`}
                                 fileName={saving.fileName} title={saving.title} onClose={() => setSaving(null)} />}

      {/* 오른쪽: AI 와 대화 */}
      <aside className="flex flex-col overflow-hidden rounded-xl border border-border bg-surface lg:sticky lg:top-4 lg:h-[calc(100svh-2rem)]">
        <div className="flex items-center gap-2 border-b border-border px-4 py-3">
          <span className="und-grad grid h-7 w-7 place-items-center rounded-full text-[11px] font-bold text-white" aria-hidden>AI</span>
          <div className="min-w-0">
            <h2 className="text-[14px] font-semibold leading-tight text-foreground">AI 와 같이 견적서 만들기</h2>
            <p className="truncate text-[11.5px] text-foreground-subtle">말로 품목 추가·수정·삭제 · 숫자는 말씀하신 것만 반영합니다</p>
          </div>
        </div>
        <div className="scroll-thin flex min-h-[320px] flex-1 flex-col gap-3 overflow-y-auto px-4 py-3" aria-live="polite">
          {q.chat.map((m, i) => m.role === "user" ? (
            <div key={i} className="flex justify-end">
              <div className="bubble-user max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md px-3.5 py-2 text-[13px] leading-relaxed text-white">{m.text}</div>
            </div>
          ) : (
            <div key={i} className="max-w-[90%] rounded-2xl rounded-bl-md bg-foreground/[0.05] px-3.5 py-2 text-[13px] leading-relaxed text-foreground"><RichText text={m.text} /></div>
          ))}
          {q.question?.choices && !busy && !dirty && (
            <Choices items={q.question.choices} disabled={!!busy} onPick={(c) => void send(c)} />
          )}
          {busy && (
            <div className="flex items-center gap-2 text-[12.5px] text-foreground-subtle">
              <Icon name="refresh" className="h-4 w-4 animate-spin text-accent" /> {busy}…
            </div>
          )}
          <div ref={chatEnd} />
        </div>
        <div className="border-t border-border p-3">
          <div className="flex flex-col rounded-xl border border-foreground/15 bg-background focus-within:ring-1 focus-within:ring-accent/40">
            <textarea rows={3} maxLength={1000} value={text} disabled={!!busy || dirty}
                      onChange={(e) => setText(e.target.value)}
                      onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(); } }}
                      aria-label="AI 에게 보내기"
                      placeholder={dirty ? "직접 고친 내용을 먼저 저장해 주세요" : "예: 화물로 보내고 2명이 3일 설치 / 물류비 15만원 / 1인 하루 50만원 / IB 넣어 줘 / T.P 3개로 (Enter 보내기)"}
                      className="w-full resize-none bg-transparent px-3 pt-2.5 text-[13px] leading-relaxed text-foreground placeholder:text-foreground-subtle/70 focus:outline-none" />
            <div className="flex items-center px-2 pb-2">
              <button type="button" aria-label="보내기" disabled={!text.trim() || !!busy || dirty} onClick={() => void send()}
                      className="und-grad ml-auto grid h-8 w-8 place-items-center rounded-full text-white transition hover:brightness-105 disabled:opacity-40">
                <Icon name="send" className="h-4 w-4" />
              </button>
            </div>
          </div>
        </div>
      </aside>
    </div>
  );
}


/** 수기 견적의 [단가표에서 추가] — 회사 단가표(고객사가, 국내)에서 골라 품목 줄로. 가격은 단가표 그대로(고칠 수 있음). */
function PricePicker({ disabled, onPick }: { disabled: boolean; onPick: (p: { model: string; item: string; unit_price: number }) => void }) {
  const [items, setItems] = useState<{ model: string; item: string; unit_price: number }[] | null>(null);
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  async function load() {
    setOpen((v) => !v);
    if (items) return;
    try {
      setItems((await api<{ items: { model: string; item: string; unit_price: number }[] }>("/quotes/price-list")).items);
    } catch {
      setItems([]);
    }
  }
  const shown = (items ?? []).filter((x) => !q.trim() || `${x.model} ${x.item}`.toLowerCase().includes(q.trim().toLowerCase())).slice(0, 40);
  return (
    <span className="relative">
      <button type="button" disabled={disabled} onClick={() => void load()} aria-expanded={open}
              className="inline-flex items-center gap-1 rounded border border-violet-500/50 px-2 py-0.5 text-violet-700 hover:bg-violet-500/5 dark:text-violet-300">
        <Icon name="search" className="h-3.5 w-3.5" /> 단가표에서 추가
      </button>
      {open && (
        <div className="absolute left-0 z-20 mt-1 flex w-[320px] flex-col gap-1.5 rounded-lg border border-border bg-background p-2 shadow-xl">
          <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="모델·품목 검색 (예: MG, TCV1)"
                 className="h-8 rounded border border-border bg-surface px-2 text-[12.5px] outline-none focus:border-accent" />
          <ul className="scroll-thin max-h-64 overflow-y-auto text-[12.5px]">
            {items === null && <li className="px-2 py-1 text-foreground-subtle">불러오는 중…</li>}
            {items !== null && shown.length === 0 && <li className="px-2 py-1 text-foreground-subtle">맞는 품목이 없습니다</li>}
            {shown.map((x) => (
              <li key={`${x.model}-${x.item}`}>
                <button type="button" onClick={() => { onPick(x); setOpen(false); }}
                        className="flex w-full items-center gap-2 rounded px-2 py-1 text-left hover:bg-accent/8">
                  <span className="font-medium">{x.model}</span><span className="text-foreground-muted">{x.item}</span>
                  <span className="ml-auto tabular-nums">{x.unit_price.toLocaleString("ko-KR")}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </span>
  );
}
