// 견적서 작성 — 제품 추천 [최종 제안 확정] 뒤 [견적서 작성 →] 으로 들어온다(사용자 2026-10-06, 영업부 팀장 요청).
// 왼쪽: 진행 상황(AI 가 지금 묻는 것·발행 전 확인할 것)·머리·품목(직접 수정 → [변경 저장])·발행 이력,
// 오른쪽: AI 와 대화 — 말로 품목 추가·수정·삭제, AI 가 빠진 것(고객·현장 작업 인원·일수·단가·담당자)을 순서대로 묻는다.
// 초안은 추천 결과(선정 모델·구성품·단가표)로 서버가 만든다. 발행하면 엑셀, 다시 발행하면 같은 번호에 사내 판(v2·v3 …)이 올라간다 — 견적서에는 번호만.
// 발행 전후 PDF 미리보기, 발행 뒤 덮어쓰기(같은 판), 내려받기는 PDF·엑셀 중 골라 다른 이름으로 저장(사용자 2026-10-06).
"use client";

import { useEffect, useRef, useState } from "react";
import type { QuoteBlock, QuoteDelivery, QuoteForm, QuoteLine, QuoteListItem, QuoteNego, QuoteHangulCell, QuotePurpose, QuoteSession, QuoteWork } from "@/lib/shared/product-recommend";
import { RichText } from "@/components/domains/sales/agent-chat";
import { ConfirmDialog } from "@/components/shared/ui/confirm-dialog";
import { Icon } from "@/components/shared/ui/icon";
import { useDragMove } from "@/components/shared/ui/use-drag-move";
import { cn, keepInPlace, scrollChatToEnd } from "@/lib/shared/utils";
import { AutoTextarea, LineArea, MAIN_BLOCK, MAIN_DEFAULT, QuoteBlocks, blockOf, monthFactor, negoCut, usd, usdOf } from "./quote-blocks";
import { QuotePurposeDialog } from "./quote-purpose-dialog";
import { QuoteUploadDialog } from "./quote-upload-dialog";
import { ErrorBox, ITEM_BTN, inputCls, primaryBtn, secondaryBtn } from "./proposal-actions";
import { AMOUNT_BOX, HEAD_AREA, HEAD_FIELD, ROW_BTN, ROW_CARD, ROW_CARD_AUTO, ROW_FIELD, ROW_ICON_BTN, ROW_LABEL } from "./quote-styles";
import { SPLIT_GRID, SplitBar, splitStyle, useSplit } from "./split-pane";

const CONTACT_KEY = "atcQuoteContact";

/** 내용이 길어지면 높이가 늘어나는 글 칸(규격 여러 줄 — 사용자 2026-10-12). Enter 는 줄바꿈. */
const CONTACT_FIELDS = ["initials", "contact_name", "contact_title", "contact_mobile", "contact_email", "contact_name_en", "contact_honorific"] as const;

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
const NEW_GROUP = "__new__";
const KIND_KO: Record<QuoteLine["kind"], string> = { product: "제품", option: "옵션", freight: "물류", work: "인건비", extra: "추가", note: "서술형" };
// 품목 줄 꼬리표 색(사용자 2026-10-23) — 제품·추가 파랑, 옵션 청록, 자동 줄(물류·인건비) 회색
const KIND_TONE: Record<QuoteLine["kind"], string> = {
  product: "bg-blue-500/10 text-blue-700 dark:text-blue-300", extra: "bg-blue-500/10 text-blue-700 dark:text-blue-300",
  option: "bg-cyan-600/10 text-cyan-800 dark:text-cyan-300", freight: "bg-foreground/[0.07] text-foreground-muted",
  work: "bg-foreground/[0.07] text-foreground-muted", note: "bg-foreground/[0.07] text-foreground-muted",
};
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
export function PreviewDialog({ url: src0, title, sub, onClose, onLoaded, movable = false }: {
  url: string; title: string; sub: string; onClose: () => void; /** PDF 를 받은 뒤(해외 견적서: 서버가 AI 번역을 저장했을 수 있음) */ onLoaded?: () => void;
  /** 머리를 잡아 끌어 옮기기(시리얼 등록 옆에 띄워 맞춰 볼 때, 사용자 2026-10-19) */ movable?: boolean;
}) {
  const drag = useDragMove();
  const [src, setSrc] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let url: string | null = null;
    let alive = true;
    fetchBlob(src0)
      .then((b) => { url = URL.createObjectURL(b); if (alive) { setSrc(url); onLoaded?.(); } })
      .catch((e: Error) => { if (alive) setErr(e.message); });
    return () => { alive = false; if (url) URL.revokeObjectURL(url); };
  }, [src0]);  // eslint-disable-line react-hooks/exhaustive-deps -- onLoaded 는 받을 때 한 번만
  return (
    // 옮길 수 있는 창은 뒤를 가리지 않는다 — 견적서를 옆에 띄워 둔 채 뒤 창(시리얼 등록)에 입력할 수 있게. 닫기는 ✕·Esc
    <div className={cn("fixed inset-0 z-50 grid place-items-center p-4", movable ? "pointer-events-none" : "bg-black/40")} onClick={movable ? undefined : onClose}>
      <div className={cn("flex h-[92svh] w-full max-w-4xl flex-col overflow-hidden rounded-xl border border-border bg-surface shadow-xl",
                         movable && "pointer-events-auto shadow-[0_30px_70px_-20px_rgba(15,30,70,0.6)]")} onClick={(e) => e.stopPropagation()}
           role="dialog" aria-label={title} style={movable ? drag.style : undefined}>
        <div className={cn("flex items-center gap-2 border-b border-border px-4 py-2.5", movable && drag.handleClass)} {...(movable ? drag.handle : {})}
             title={movable ? "잡고 끌어 옮기기 · 두 번 누르면 가운데로" : undefined}>
          <h3 className="text-[14px] font-semibold text-foreground">{title}</h3>
          <span className="text-[12px] text-foreground-subtle">{sub}</span>
          <button type="button" className="ml-auto grid h-7 w-7 place-items-center rounded-md hover:bg-foreground/[0.06]" onClick={onClose} aria-label="닫기">
            <Icon name="x" className="h-4 w-4" />
          </button>
        </div>
        {err ? <div className="p-4"><ErrorBox error={err} /></div>
          : src ? <iframe src={src} title={title} className={cn("w-full flex-1 bg-white", drag.dragging && "pointer-events-none")} />
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

/** 해외 견적서 'AI 번역' 목록(사용자 2026-10-14) — 칸에는 한글 원문, 견적서에는 AI 영어. 영어는 고칠 수 있고,
 *  꼭 한글로 둘 칸은 [한글 그대로]. 번역 전 칸은 미리보기·발행 때 AI 가 바꾼다. */
function EnCells({ cells, disabled, note, onSet }: {
  cells: QuoteHangulCell[]; disabled: boolean; note: string | null; onSet: (path: string, patch: { en?: string; keep?: boolean }) => void;
}) {
  const pending = cells.filter((c) => c.en == null && !c.keep).length;
  return (
    <div className="flex flex-col gap-1.5 rounded-lg border border-accent/25 bg-accent/[0.04] px-3 py-2 text-[12px]">
      <p className="font-semibold text-foreground">AI 번역 · 한글 칸 {cells.length}개
        <span className="ml-1.5 font-normal text-foreground-subtle">
          {pending ? `${pending}칸은 미리보기·발행 때 AI 가 영어로 바꿉니다` : "견적서에는 오른쪽 영어가 들어갑니다 — 고칠 칸은 바로 고쳐 주세요"}
        </span>
      </p>
      {note && <p className="text-accent">{note}</p>}
      <ul className="flex flex-col gap-1">
        {cells.map((c) => <EnRow key={`${c.path}|${c.en ?? ""}|${c.keep}`} c={c} disabled={disabled} onSet={onSet} />)}
      </ul>
    </div>
  );
}

function EnRow({ c, disabled, onSet }: { c: QuoteHangulCell; disabled: boolean; onSet: (path: string, patch: { en?: string; keep?: boolean }) => void }) {
  const [v, setV] = useState(c.en ?? "");
  const commit = () => { if (v.trim() !== (c.en ?? "")) onSet(c.path, { en: v }); };
  return (
    <li className="grid items-start gap-x-2 gap-y-0.5 border-t border-border/60 pt-1 sm:grid-cols-[minmax(0,9rem)_minmax(0,1fr)_minmax(0,1.3fr)_auto]">
      <span className="text-foreground-subtle">{c.label}
        {c.name && c.by === "ai" && !c.keep && <span className="ml-1 rounded bg-amber-500/15 px-1 text-[10.5px] font-semibold text-amber-700 dark:text-amber-300">이름 확인</span>}
      </span>
      <span className="whitespace-pre-wrap text-foreground-muted">{c.text}</span>
      {c.keep ? <span className="text-foreground-subtle">한글 그대로 들어갑니다</span> : (
        <textarea value={v} rows={Math.min(4, Math.max(1, v.split("\n").length))} disabled={disabled}
                  placeholder="미리보기·발행 때 AI 가 번역 (직접 적어도 됨)"
                  onChange={(e) => setV(e.target.value)} onBlur={commit}
                  className={cn("w-full resize-y rounded border bg-surface px-1.5 py-0.5 text-[12px] outline-none focus:border-accent",
                    c.en == null ? "border-dashed border-border" : "border-border")} />
      )}
      <label className="inline-flex items-center gap-1 whitespace-nowrap text-foreground-muted" title="이 칸은 번역하지 않고 한글로 둡니다(발행 가능)">
        <input type="checkbox" checked={c.keep} disabled={disabled} onChange={(e) => onSet(c.path, { keep: e.target.checked })} /> 한글 그대로
      </label>
    </li>
  );
}

/** 견적서 탭 — 목록 또는 한 건 작업 화면. */
export function QuoteTab({ openId, onOpen, askManual = false }: {
  openId: number | null; onOpen: (id: number | null) => void;
  /** 첫 화면 '견적서 수기 작성' 카드로 들어옴 — 목적(판매/대여) 고르는 창부터 */
  askManual?: boolean;
}) {
  if (openId) return <QuoteWorkspace key={openId} id={openId} onBack={() => onOpen(null)} />;
  // 첫 화면 카드 → 탭이 이미 열린 뒤에 들어와도 목적 창을 연다(key 가 바뀌면 목록을 새로 그려 처음 값으로)
  return <QuoteList key={askManual ? "ask" : "list"} onOpen={onOpen} askManual={askManual} />;
}

function QuoteList({ onOpen, askManual }: { onOpen: (id: number) => void; askManual: boolean }) {
  const [items, setItems] = useState<QuoteListItem[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [picking, setPicking] = useState(askManual);
  /** 견적서 수기 작성(사용자 2026-10-08) — AI 추천 없이 빈 견적서를 만들어 연다. 목적(판매/대여)은 창에서 고른다(2026-10-22). */
  async function createManual(purpose: QuotePurpose) {
    setCreating(true);
    setErr(null);
    try {
      onOpen((await api<QuoteSession>("/quotes/manual", { form: { ...savedContact(), purpose } })).id);
    } catch (e) {
      setErr((e as Error).message);
      setCreating(false);
      setPicking(false);
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
          AI 추천이 아직 없는 제품(그리퍼·AMR 등)은 <b>[견적서 수기 작성]</b>으로 빈 견적서에서 시작합니다 — 발행하면 건 번호가 붙어 제품 판매·대여 관리로 이어집니다.
        </p>
        <button type="button" className={primaryBtn} disabled={creating} onClick={() => setPicking(true)}>
          <Icon name="plus" className="h-4 w-4" /> {creating ? "만드는 중…" : "견적서 수기 작성"}
        </button>
      </div>
      <p className="text-[12.5px] text-foreground-muted">
        제품 추천에서 툴체인저 후보를 [최종 제안 확정]한 뒤 <b>[견적서 작성 →]</b>을 누르면 추천 결과로 초안이 만들어지고, AI 와 대화하며 채웁니다.
        이 목록에는 <b>작성 중(발행 전)</b>인 견적서만 보입니다 — 발행한 견적서는 [제품 판매·대여 관리]에서 건을 열어 다시 받거나 [견적서 고치기 →]로 고칩니다.
      </p>
      <ErrorBox error={err} />
      {picking && <QuotePurposeDialog busy={creating} onPick={(p) => void createManual(p)} onClose={() => setPicking(false)} />}
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
                  <td className="px-3 py-2">{x.manual ? <span className="text-violet-700 dark:text-violet-300">수기 작성</span> : x.model ?? "—"}
                    {x.purpose === "rental" && <span className="ml-1.5 rounded bg-emerald-500/12 px-1.5 text-[11px] font-semibold text-emerald-700 dark:text-emerald-300">대여</span>}</td>
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

type Draft = { form: QuoteForm; lines: QuoteLine[]; work: QuoteWork; delivery: QuoteDelivery; include: string[];
               blocks: QuoteBlock[]; nego: QuoteNego | null };
const toDraft = (q: QuoteSession): Draft => ({
  form: { ...q.state.form }, lines: q.state.lines.map((l) => ({ ...l })), work: { ...q.state.work },
  delivery: { ...(q.state.delivery ?? { case: null, freight: null }) }, include: [],
  // 묶음(사용자 2026-10-13) — 예전 견적은 기본 묶음 하나
  blocks: (q.state.blocks?.length ? q.state.blocks : [MAIN_DEFAULT]).map((b) => ({ ...b, rows: b.rows?.map((r) => ({ ...r })) })),
  nego: q.state.nego ? { ...q.state.nego } : null,
});

// 칸별 입력 예시 — 국내(한글)·해외(영문) 견적서를 따로(영업팀 요청 10번, 사용자 2026-10-13: 해외용은 영문 작성 예시).
// 납기·납품장소·결제조건·Comments 는 비우면 양식 기본 문구가 들어간다(quote_xlsx.DEFAULT_TERMS 와 같은 문구).
// 해외 견적서일 때 칸 이름은 영문(사용자 2026-10-13) — 견적서 양식의 영문 표기와 맞춤
const LABEL_EN: Partial<Record<keyof QuoteForm, string>> = {
  customer: "Customer", to: "To (Attention)", cc: "CC", initials: "Initials", contact_name: "Name", contact_title: "Title",
  contact_name_en: "Name (English)", contact_honorific: "Mr. / Ms.",
  contact_mobile: "Mobile", contact_email: "E-mail", delivery: "Delivery Schedule", place: "Delivery Place", payment: "Payment Terms",
  bank: "Bank", account_no: "Account No.", account_holder: "Account Holder", comments: "Comments", note: "Remarks",
};
// only — 그 견적서에서만 보이는 칸: 국내는 한글 이름·직함, 해외는 영문 이름·호칭('Mr. Alex Jeon', 해외 기준 견적서 — 사용자 2026-10-14)
// 해외 예시 문구 = 해외 기준 견적서 표기
// line — 견적서 칸 안에서 Alt+Enter 로 줄을 바꿀 수 있는 칸(사용자 2026-10-16)
const HEAD: { k: keyof QuoteForm; label: string; wide?: boolean; area?: boolean; line?: boolean; req?: boolean; only?: "ko" | "en"; ph?: { ko: string; en: string } }[] = [
  { k: "customer", label: "고객사", req: true, ph: { ko: "예: (주)오토기기", en: "예: ABC Robotics Co., Ltd." } },
  { k: "to", label: "받는 분(To)", ph: { ko: "예: 김철수 대표님", en: "예: Mr. Sean Huang" } },
  { k: "cc", label: "참조(CC)", ph: { ko: "예: 이영희 과장", en: "예: Ms. Jane Doe (Purchasing)" } },
  { k: "initials", label: "담당자 이니셜", req: true, ph: { ko: "예: VT", en: "예: VT" } },
  { k: "contact_name", label: "담당자 이름", req: true, only: "ko", ph: { ko: "예: 홍길동", en: "" } },
  { k: "contact_title", label: "직함", only: "ko", ph: { ko: "예: 이사", en: "" } },
  { k: "contact_name_en", label: "담당자 영문 이름", req: true, only: "en", ph: { ko: "", en: "예: Alex Jeon (내 정보의 영문 이름)" } },
  { k: "contact_honorific", label: "호칭", only: "en", ph: { ko: "", en: "" } },
  { k: "contact_mobile", label: "휴대폰", req: true, ph: { ko: "예: 010-1234-5678", en: "예: 010-1234-5678 — 견적서엔 82-10-1234-5678 로" } },
  { k: "contact_email", label: "이메일", req: true, ph: { ko: "예: name@company.com", en: "예: name@company.com" } },
  { k: "delivery", label: "납기", line: true, ph: { ko: "예: 통상 3 ~ 4주 (비우면 이 문구)", en: "예: 4 ~ 6 weeks after payment (비우면 이 문구)" } },
  { k: "place", label: "납품장소", line: true, ph: { ko: "예: 직납 및 배차 비용 별도 (비우면 이 문구)", en: "예: UND Korea Factory (비우면 이 문구)" } },
  { k: "payment", label: "결제조건", line: true, ph: { ko: "예: 발주시 50%, 납품전 50% (비우면 이 문구)",
                                          en: "예: 100% advance payment before delivery. (비우면 이 문구)" } },
  // 입금 계좌(사용자 2026-10-08) — 기본은 회사 계좌, 바꾸면 이 견적서에만
  { k: "bank", label: "입금 은행", ph: { ko: "비우면 회사 기본 계좌", en: "비우면 회사 해외 송금 계좌 — 바꿀 때는 영문 은행명" } },
  { k: "account_no", label: "입금 계좌번호", ph: { ko: "비우면 회사 기본 계좌", en: "비우면 회사 해외 송금 계좌" } },
  { k: "account_holder", label: "예금주", ph: { ko: "비우면 회사 기본 계좌", en: "비우면 회사 해외 송금 계좌 — 바꿀 때는 영문 예금주" } },
  { k: "comments", label: "Comments", wide: true, area: true,
    ph: { ko: "비우면 기본 문구\n예: www.magbot.kr\n- Auto Tool Changer 공급 건\n맥봇 로봇 툴체인저",
          en: "비우면 기본 문구\n예: www.magbot.kr\nMagbot Robot Automatic Tool Changer" } },
  { k: "note", label: "비고", wide: true, area: true,
    ph: { ko: "예: 설치 일정은 발주 후 협의", en: "예: Ex-work UND Korea Factory" } },
];

// 견적서 머리 묶음(사용자 2026-10-23: 작성하기 편하게) — 고객 · 우리 담당 · 거래 조건 · 입금 정보(평소엔 접힘). Comments·비고는 맨 아래
const HEAD_GROUPS: { n: number; title: string; en: string; tone: string; keys: (keyof QuoteForm)[]; cols: string; fold?: boolean }[] = [
  { n: 1, title: "고객", en: "Customer", tone: "bg-blue-500/10 text-blue-700 dark:text-blue-300", keys: ["customer", "to", "cc"], cols: "sm:grid-cols-3" },
  { n: 2, title: "우리 담당", en: "Our Contact", tone: "bg-slate-500/12 text-slate-700 dark:text-slate-300",
    keys: ["initials", "contact_name", "contact_title", "contact_name_en", "contact_honorific", "contact_mobile", "contact_email"], cols: "sm:grid-cols-3" },
  { n: 3, title: "거래 조건", en: "Terms", tone: "bg-cyan-600/10 text-cyan-800 dark:text-cyan-300", keys: ["delivery", "place", "payment"], cols: "sm:grid-cols-3" },
  { n: 4, title: "입금 정보", en: "Bank Account", tone: "bg-foreground/[0.07] text-foreground-muted", keys: ["bank", "account_no", "account_holder"],
    cols: "sm:grid-cols-3", fold: true },
];
const HEAD_CARD = "flex flex-col gap-4 rounded-2xl border border-border bg-surface-raised p-5 shadow-[0_1px_2px_rgba(15,23,42,0.04),0_16px_36px_-24px_rgba(15,30,70,0.30)]";
const HEAD_LABEL = "flex min-w-0 flex-col gap-1.5 text-[12.5px] font-semibold text-foreground-muted";
const SEG = "flex gap-0.5 rounded-[11px] bg-foreground/[0.06] p-[3px]";
const SEG_BTN = "h-8 rounded-[9px] px-3.5 text-[12.5px] font-semibold text-foreground-muted transition-colors hover:text-foreground disabled:opacity-60";
const SEG_ON = "bg-surface-raised font-bold shadow-[0_1px_3px_rgba(15,23,42,0.14)]";

const SPLIT_KEY = "und_cortex_quote_split";
const SPLIT_DEFAULT = 60;               // 예전 3:2 와 같은 폭

/** 품목 줄 크게 보기 — 넓은 칸에서 품목·규격·수량·단가·비고를 고치고, 오른쪽에 견적서 표에 찍히는 모양을 같이 보여 준다.
 *  고친 내용은 표와 같은 작성 중 값이라 [변경 저장]을 눌러야 저장된다. */
function LineDialog({ line, en, fx, busy, months, onChange, onClose }: {
  line: QuoteLine; en: boolean; fx: number | null; busy: boolean; months: number; onChange: (patch: Partial<QuoteLine>) => void; onClose: () => void;
}) {
  const mf = monthFactor(line, months);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const price = en ? usdOf(line, fx) : line.unit_price;
  const money = (n: number | null | undefined) => (n == null ? "-" : en ? usd(n) : `₩${Math.round(n).toLocaleString("ko-KR")}`);
  const qtyText = line.qty == null ? "-" : `${line.qty}${line.unit ? ` ${line.unit}` : ""}`;
  const cell = "border border-foreground/40 px-1.5 py-1 align-middle";
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/30 p-4" onClick={onClose}>
      <div role="dialog" aria-label="품목 줄 크게 보기" onClick={(e) => e.stopPropagation()}
           className="scroll-thin max-h-full w-full max-w-[1080px] overflow-y-auto rounded-2xl bg-background p-5 shadow-2xl">
        <div className="mb-3 flex items-center gap-2">
          <h2 className="text-[16px] font-bold">품목 줄 크게 보기</h2>
          <span className="text-[12px] text-foreground-muted">고친 내용은 표에 바로 반영되고, 닫은 뒤 [변경 저장]을 눌러야 저장됩니다.</span>
          <button type="button" onClick={onClose} aria-label="닫기"
                  className="ml-auto grid h-8 w-8 place-items-center rounded-lg text-foreground-muted hover:bg-foreground/8"><Icon name="x" className="h-4 w-4" /></button>
        </div>
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
          <div className="flex flex-col gap-3 text-[12.5px]">
            <label className="flex flex-col gap-1 text-foreground-muted">{en ? "Commodity" : "품목"}
              <LineArea className={cn(inputCls, "text-[13px]")} value={line.commodity ?? ""} disabled={busy}
                        onChange={(e) => onChange({ commodity: e.target.value })} />
            </label>
            {line.kind === "extra" && (
              <label className="flex flex-col gap-1 text-foreground-muted">{en ? "Specifications" : "규격"} <span className="text-[11px] text-foreground-subtle">Enter 로 줄 바꿈 · 8줄까지</span>
                <AutoTextarea className={cn(inputCls, "min-h-[96px] text-[13px]")} value={line.name} disabled={busy}
                              onChange={(e) => onChange({ name: e.target.value })} />
              </label>
            )}
            <div className="grid grid-cols-3 gap-2">
              <label className="flex flex-col gap-1 text-foreground-muted">수량
                <input type="number" min={1} className={cn(inputCls, "text-[13px]")} value={line.qty ?? ""} disabled={busy}
                       onChange={(e) => onChange({ qty: Math.max(0, Math.floor(Number(e.target.value) || 0)), ...(line.conditional ? { confirmed: true } : {}) })} />
              </label>
              <label className="flex flex-col gap-1 text-foreground-muted">단위
                <input className={cn(inputCls, "text-[13px]")} value={line.unit ?? ""} maxLength={8} placeholder="ea" disabled={busy}
                       onChange={(e) => onChange({ unit: e.target.value })} />
              </label>
              {!en && (
                <label className="flex flex-col gap-1 text-foreground-muted">{months ? "단가 (월 납입금 또는 1회 지불, 원)" : "단가(원)"}
                  <input inputMode="numeric" className={cn(inputCls, "text-[13px]")} value={line.unit_price ?? ""} disabled={busy}
                         onChange={(e) => onChange({ unit_price: Number(e.target.value.replace(/,/g, "")) || null })} />
                </label>
              )}
            </div>
            <label className="flex flex-col gap-1 text-foreground-muted">{en ? "Remark" : "비고"} <span className="text-[11px] text-foreground-subtle">Alt+Enter 로 줄 바꿈 · {(line.remark ?? "").length}자</span>
              <LineArea className={cn(inputCls, "min-h-[96px] text-[13px]")} value={line.remark ?? ""} disabled={busy}
                        onChange={(e) => onChange({ remark: e.target.value })} />
            </label>
          </div>
          <div className="flex flex-col gap-2">
            <p className="text-[12.5px] font-semibold text-foreground">견적서 표에는 이렇게 들어갑니다</p>
            <div className="overflow-x-auto rounded-lg border border-border bg-white p-3 text-black">
              <table className="w-full min-w-[460px] border-collapse text-center text-[11.5px] font-semibold leading-snug">
                <thead>
                  <tr className="bg-[#d9d9d9]">
                    <th className={cn(cell, "w-[16%]")}>Commodity</th><th className={cn(cell, "w-[30%]")}>Specifications</th>
                    <th className={cn(cell, "w-[10%]")}>Quantity</th><th className={cn(cell, "w-[13%]")}>Price{months ? <><br />(월 납입금)</> : null}</th>
                    <th className={cn(cell, "w-[13%]")}>Supply amount</th><th className={cn(cell, "w-[18%]")}>Remark</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td className={cn(cell, "whitespace-pre-line")}>{line.commodity || " "}</td>
                    <td className={cn(cell, "whitespace-pre-line")}>{line.name || " "}</td>
                    <td className={cell}>{qtyText}</td>
                    <td className={cell}>{money(price)}</td>
                    <td className={cell}>{money(price == null || line.qty == null ? null : price * line.qty * mf)}{mf > 1 && <span className="block text-[10px] font-normal">× {mf}개월</span>}</td>
                    <td className={cn(cell, "whitespace-pre-line")}>{line.remark || " "}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p className="text-[11.5px] text-foreground-subtle">
              칸 폭은 실제 견적서 비율에 맞춘 것이고, 글이 길면 그만큼 칸이 높아집니다. 정확한 모양은 저장한 뒤 [미리보기]로 확인하세요.
            </p>
          </div>
        </div>
        <div className="mt-4 flex justify-end">
          <button type="button" onClick={onClose} className={primaryBtn}>완료</button>
        </div>
      </div>
    </div>
  );
}

export function QuoteWorkspace({ id, onBack }: { id: number; onBack: () => void }) {
  const [q, setQ] = useState<QuoteSession | null>(null);
  // 불러올 때마다 +1 — 달러 단가 칸(소수점 입력 중 글자를 지키려고 비제어 입력)을 새 값으로 다시 그린다
  const [draftVer, setDraftVer] = useState(0);
  // 납품 방식 칸 '+ 별도 묶음을 만들어 넣기' — 적는 중인 새 묶음 이름(null = 안 만드는 중)
  const [newGroup, setNewGroup] = useState<string | null>(null);
  const [d, setD] = useState<Draft | null>(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [preview, setPreview] = useState(false);
  // 품목 줄 크게 보기(사용자 2026-10-22) — 비고·규격이 길 때 넓은 칸에서 고치고 견적서에 찍히는 모양을 같이 본다
  const [bigLine, setBigLine] = useState<number | null>(null);
  // 입금 정보 묶음 — 평소엔 한 줄 요약으로 접어 둔다(거의 안 바꾸는 칸)
  const [bankOpen, setBankOpen] = useState(false);
  const split = useSplit(SPLIT_KEY, SPLIT_DEFAULT);
  // 해외 견적서: 번역 전 칸이 있는데 [발행]을 누르면 미리보기로 먼저 확인하게 한다(사용자 2026-10-14: 사람이 더블체크)
  const [enNote, setEnNote] = useState<string | null>(null);
  const [saving, setSaving] = useState<{ revision: number; fileName: string; title: string } | null>(null);
  const [uploading, setUploading] = useState(false);
  // 완납한 건에 발행 — 한 번 확인(사용자 2026-10-26)
  const [paidAsk, setPaidAsk] = useState(false);
  const chatEnd = useRef<HTMLDivElement>(null);

  function load(s: QuoteSession) {
    setQ(s);
    setD(toDraft(s));
    setDraftVer((v) => v + 1);
    setDirty(false);
  }
  useEffect(() => {
    let alive = true;
    api<QuoteSession>(`/quotes/${id}`).then((s) => { if (alive) load(s); }).catch((e: Error) => { if (alive) setErr(e.message); });
    return () => { alive = false; };
  }, [id]);
  useEffect(() => { scrollChatToEnd(chatEnd.current); }, [q?.chat.length, busy]);

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

  /** 저장 — formPatch 는 저장하면서 같이 바꿀 머리 값(국내↔해외 전환: 서버가 그 언어 기본 문구·계좌로 바꿔 바로 보이게, 사용자 2026-10-13) */
  async function save(formPatch: Partial<QuoteForm> = {}): Promise<QuoteSession | null> {
    if (!d) return null;
    const body = {
      form: { ...d.form, ...formPatch }, work: { people: d.work.people, days: d.work.days, rate: d.work.rate }, delivery: d.delivery,
      include_options: d.include, blocks: d.blocks, nego: d.nego?.value ? d.nego : null,
      // 물류비·인건비 줄은 납품 방식으로, 화면에서 넣은 옵션 줄은 include_options 로 보낸다(두 번 들어가지 않게)
      lines: d.lines.filter((l) => !isAuto(l) && !l.id.startsWith("new-opt-")).map((l) => ({
        id: l.id.startsWith("new") ? null : l.id, name: l.name, commodity: l.commodity ?? "", qty: l.qty, unit_price: l.unit_price, remark: l.remark ?? "",
        usd: l.usd ?? null, block: blockOf(l, d.blocks), unit: l.unit ?? "", in_total: !!l.in_total, ...(l.kind === "note" ? { kind: "note" } : {}),
        ...(l.conditional && l.confirmed ? { confirmed: true } : {}),
      })),
    };
    const s = await run("저장 중", () => api<QuoteSession>(`/quotes/${id}/edit`, body));
    if (s) load(s);
    return s ?? null;
  }

  /** [사진] — 저장 안 한 변경·새 줄이면 먼저 저장하고(새 줄은 저장 뒤 받은 줄 번호로) 올린다(사용자 2026-10-13: 클릭이 안 되던 것) */
  async function attachPhoto(l: QuoteLine, f: File) {
    if (!d || !q) return;
    let lineId: string | undefined = l.id;
    if (dirty || l.id.startsWith("new")) {
      const oldIds = new Set(q.state.lines.map((x) => x.id));
      const newOnes = d.lines.filter((x) => !isAuto(x) && x.id.startsWith("new") && !x.id.startsWith("new-opt-"));
      const k = newOnes.findIndex((x) => x.id === l.id);
      const s = await save();
      if (!s) return;
      if (l.id.startsWith("new")) {   // 서버는 새 줄을 보낸 순서대로 뒤에 붙인다
        lineId = s.state.lines.filter((x) => !oldIds.has(x.id) && !isAuto(x))[k]?.id;
        if (!lineId) return;
      }
    }
    await uploadPhoto(lineId, f);
  }

  /** Remark 칸의 사진 — 기본 품목 표와 묶음 표가 같이 쓴다 */
  function photoCell(l: QuoteLine) {
    if (!q) return null;
    if (l.photo) return (
      <span className="relative inline-flex w-fit">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={`/api/product-recommend/quotes/${q.id}/photo/${l.photo.split("/").pop()}`} alt={`${l.name} 사진`}
             className="h-10 w-auto max-w-[96px] rounded border border-border object-contain" />
        <button type="button" aria-label="사진 빼기" disabled={!!busy} title="사진 빼기"
                onClick={async () => { if (dirty && !(await save())) return; await removePhoto(l.id); }}
                className="absolute -right-1.5 -top-1.5 grid h-4 w-4 place-items-center rounded-full bg-black/60 text-[10px] text-white disabled:opacity-40">✕</button>
      </span>
    );
    const optPending = l.id.startsWith("new-opt-");
    return (
      // relative — 숨긴 파일 칸이 버튼 안에 있어야 눌렀을 때 화면이 위로 튀지 않는다(사용자 2026-10-23 ui_error01)
      <label className={cn(ROW_BTN, "relative cursor-pointer", (busy || optPending) && "pointer-events-none opacity-50")}
             title={optPending ? "옵션을 넣은 뒤 [변경 저장]을 먼저 눌러 주세요" : dirty || l.id.startsWith("new")
               ? "저장하지 않은 변경을 먼저 저장하고 사진을 올립니다(JPG·PNG·WEBP)" : "견적서 Remark 칸에 작게 들어갑니다(JPG·PNG·WEBP)"}>
        <Icon name="paperclip" className="h-4 w-4" /> 사진
        <input type="file" accept="image/jpeg,image/png,image/webp" className="sr-only"
               onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) void attachPhoto(l, f); }} />
      </label>
    );
  }

  /** 품목 사진 올리기·빼기 — 서버 상태로 바로 저장(견적서 Remark 칸, 사용자 2026-10-12). */
  async function uploadPhoto(lineId: string, f: File) {
    const fd = new FormData();
    fd.append("file", f, f.name);
    const s = await run("사진 올리는 중", async () => {
      const r = await fetch(`/api/product-recommend/quotes/${id}/lines/${lineId}/photo`, { method: "POST", body: fd });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof d?.detail === "string" ? d.detail : "사진을 올리지 못했습니다.");
      return d as QuoteSession;
    });
    if (s) load(s);
  }
  async function removePhoto(lineId: string) {
    const s = await run("사진 빼는 중", () => api<QuoteSession>(`/quotes/${id}/lines/${lineId}/photo/remove`, {}));
    if (s) load(s);
  }

  /** 'AI 번역' 목록 — 칸 하나의 영어 고치기('' = 다음 미리보기 때 AI 가 다시) · [한글 그대로] */
  async function setEnCell(path: string, patch: { en?: string; keep?: boolean }) {
    const s = await run("저장 중", () => api<QuoteSession>(`/quotes/${id}/en-cell`, { path, ...patch }));
    if (s) load(s);
  }
  /** 미리보기 PDF 를 받은 뒤 — 서버가 그사이 AI 번역을 저장했으면 목록에 보이게 다시 불러온다. */
  async function reloadAfterPreview() {
    if (!q || !(q.hangul ?? []).some((c) => c.en == null && !c.keep)) return;
    try { load(await api<QuoteSession>(`/quotes/${id}`)); } catch { /* 미리보기는 이미 열림 — 목록은 다음에 */ }
  }

  /** 발행·재발행(늘 새 발행번호, 사용자 2026-10-12) → 바로 내려받기 창(PDF·엑셀 고르기, 다른 이름으로 저장). */
  async function issue(paidConfirmed = false) {
    if (!q) return;
    if (q.deal_paid_full && !paidConfirmed) { setPaidAsk(true); return; }   // 완납한 건 — 발행 전에 한 번 확인(사용자 2026-10-26)
    if ((q.hangul ?? []).some((c) => c.en == null && !c.keep)) {
      setEnNote("한글 칸을 AI 가 영어로 바꿨습니다 — 미리보기와 아래 'AI 번역' 목록을 확인한 뒤 다시 [발행]을 눌러 주세요.");
      setPreview(true);
      return;
    }
    setEnNote(null);
    const s = await run("발행 중", () => api<QuoteSession>(`/quotes/${id}/issue`, {}));
    if (s) {
      load(s);
      rememberContact(s.state.form);
      const h = s.history[s.history.length - 1];
      if (h) setSaving({ revision: h.revision, fileName: h.file_name, title: `발행했습니다${h.quote_no ? ` — ${h.quote_no}` : ""} · 내려받기` });
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
  const setForm = (k: keyof QuoteForm, v: string) => edit({ form: { ...d.form,
    [k]: k === "fx_rate" || k === "rental_months" ? (Number(v.replace(/,/g, "")) || null) : v } });
  const setLine = (i: number, patch: Partial<QuoteLine>) => edit({ lines: d.lines.map((l, j) => (j === i ? { ...l, ...patch } : l)) });
  const setWork = (patch: Partial<QuoteWork>) => edit({ work: { ...d.work, ...patch } });
  const setDelivery = (patch: Partial<QuoteDelivery>) => edit({ delivery: { ...d.delivery, ...patch } });
  const dv = DELIVERY.find((x) => x.case === d.delivery.case);
  // 고치는 중 합계 — 물류비·인건비는 납품 방식 칸 값으로 다시 계산(저장하면 서버가 같은 식으로 줄을 만든다)
  // 합계에 들어가는 줄 = 옵션 묶음 줄은 [반영]한 것만(사용자 2026-10-13 → 2026-10-19). NEGO 는 아래에서 뺀다
  const optionIds = new Set(d.blocks.filter((b) => b.type === "option").map((b) => b.id));
  const autoBlock = d.blocks.some((b) => b.id === d.delivery.block && b.type === "items") ? d.delivery.block as string : MAIN_BLOCK;
  /** 납품 방식 칸에서 별도 묶음을 만들고 물류비·인건비를 거기에 — 소계 켬. 저장하면 서버가 B1… 로 바꾼다 */
  function makeAutoGroup() {
    const title = (newGroup ?? "").trim().slice(0, 60);
    if (!d || !title) return;
    const hit = d.blocks.find((b) => b.type === "items" && b.id !== MAIN_BLOCK && b.title === title);
    const id = hit?.id ?? `new-${Date.now()}`;
    edit({ blocks: hit ? d.blocks : [...d.blocks, { id, type: "items", title, subtotal: true }], delivery: { ...d.delivery, block: id } });
    setNewGroup(null);
  }
  const billed = d.lines.filter((l) => !isAuto(l) && l.kind !== "note" && (!optionIds.has(blockOf(l, d.blocks)) || l.in_total));
  // 대여 견적서 — 단가 = 1대당 월 납입금, 금액 = 수량 × 단가 × 대여 개월(사용자 2026-10-22)
  const rentMonths = d.form.purpose === "rental" ? Number(d.form.rental_months) || 0 : 0;
  const baseKrw = billed.reduce((s, l) => s + (l.qty ?? 0) * (l.unit_price ?? 0) * monthFactor(l, rentMonths), 0)
    + (dv?.freight ? d.delivery.freight ?? 0 : 0) + (dv?.work ? (d.work.people ?? 0) * (d.work.days ?? 0) * (d.work.rate ?? WORK_RATE) : 0);
  const total = baseKrw - negoCut(d.nego, baseKrw, false);
  // 해외 견적 달러 합계(고치는 중) — 물류비·인건비는 원화라 환율이 있어야 더한다. 달러 단가 없는 줄이 있으면 null
  const fx = d.form.fx_rate;
  const autoKrw = (dv?.freight ? d.delivery.freight ?? 0 : 0) + (dv?.work ? (d.work.people ?? 0) * (d.work.days ?? 0) * (d.work.rate ?? WORK_RATE) : 0);
  const manualUsd = billed.map((l) => ({ q: (l.qty ?? 0) * monthFactor(l, rentMonths), u: usdOf(l, fx) }));
  const missingUsd = d.lines.some((l) => !isAuto(l) && l.kind !== "note" && usdOf(l, fx) == null);
  const baseUsd = missingUsd || (autoKrw > 0 && !(fx && fx > 0)) ? null
    : Math.round((manualUsd.reduce((s, x) => s + x.q * (x.u ?? 0), 0) + (autoKrw && fx ? autoKrw / fx : 0)) * 100) / 100;
  const totalUsd = baseUsd == null ? null : Math.round((baseUsd - negoCut(d.nego, baseUsd, true)) * 100) / 100;
  const en = d.form.lang === "en";
  const hangul = q.hangul ?? [];
  // 번역 전 칸은 [발행] 때 미리보기로 먼저 번역·확인 — 발행을 막는 건 채울 것(problems)뿐
  const blocked = q.problems.length > 0;
  const issued = q.status === "issued";
  const optionsLeft = q.state.options.filter((o) => !d.include.includes(o.name));
  // 견적서 머리 — 남은 필수 칸 수와 칸 하나 그리기
  const headMissing = HEAD.filter((h) => h.req && (!h.only || h.only === (en ? "en" : "ko")) && !String(d.form[h.k] ?? "").trim()).length
    + (d.form.purpose === "rental" && !String(d.form.rental_months ?? "").trim() ? 1 : 0);
  const headInput = (h: (typeof HEAD)[number]) => {
    const ph = h.ph?.[en ? "en" : "ko"];
    if (h.k === "contact_honorific") {
      return (
        <select className={HEAD_FIELD} value={d.form.contact_honorific ?? ""} disabled={!!busy} onChange={(e) => setForm("contact_honorific", e.target.value)}>
          <option value="">안 씀</option><option value="Mr.">Mr.</option><option value="Ms.">Ms.</option>
        </select>
      );
    }
    if (h.area) {
      return <textarea className={cn(HEAD_AREA, h.k === "comments" ? "min-h-[104px] text-center" : "min-h-[104px]")} value={String(d.form[h.k] ?? "")}
                       placeholder={ph} disabled={!!busy} onChange={(e) => setForm(h.k, e.target.value)} />;
    }
    if (h.line) {
      return <LineArea className={HEAD_AREA} value={String(d.form[h.k] ?? "")} placeholder={ph} disabled={!!busy} onChange={(e) => setForm(h.k, e.target.value)} />;
    }
    return <input className={cn(HEAD_FIELD, h.k === "initials" && "font-bold tracking-wider")} value={String(d.form[h.k] ?? "")} placeholder={ph} disabled={!!busy}
                  onChange={(e) => setForm(h.k, h.k === "initials" ? e.target.value.toUpperCase().slice(0, 2) : e.target.value)} />;
  };

  // 왼쪽(견적 내용)을 넓게 — 품목 규격을 여러 줄로 적어도 잘 보이게(사용자 2026-10-12)
  return (
    <div style={splitStyle(split)} className={SPLIT_GRID}>
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
            {q.on_file && (
              <span className="rounded-md bg-sky-500/12 px-1.5 py-0.5 text-[11.5px] font-semibold text-sky-700 dark:text-sky-300"
                    title="미리보기·발행 파일을 회사 양식으로 새로 그리지 않고, 올린 엑셀 파일에서 바뀐 칸만 고칩니다(엑셀에서 넣은 서식 유지).">
                올린 파일({q.on_file}) 위에서 고치는 중
              </span>
            )}
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
          {en && hangul.length > 0 && (
            <EnCells cells={hangul} disabled={!!busy || dirty} note={enNote} onSet={(path, patch) => void setEnCell(path, patch)} />
          )}
          {!q.question && !blocked && !dirty && (
            <p className="text-[12.5px] text-emerald-700 dark:text-emerald-300">필요한 내용을 다 채웠습니다 — [견적서 발행]을 누르면 엑셀이 만들어집니다.</p>
          )}
        </section>

        {/* 머리 — 네 묶음(고객·우리 담당·거래 조건·입금 정보)으로 나눠 무엇이 남았는지 바로 보이게(사용자 2026-10-23: 작성하기 편하게) */}
        <section className={HEAD_CARD}>
          <div className="flex flex-wrap items-center gap-3">
            <div className="min-w-0">
              <h3 className="text-[17px] font-extrabold tracking-tight text-foreground">{en ? "Quotation Header" : "견적서 머리"}</h3>
              <p className={cn("mt-0.5 text-[12.5px] font-medium", headMissing ? "text-amber-700 dark:text-amber-300" : "text-emerald-700 dark:text-emerald-300")}>
                {headMissing ? `필수 칸 ${headMissing}개 남음 — 빨간 별(*) 칸을 채우면 발행할 수 있습니다` : "필수 칸을 모두 채웠습니다"}
              </p>
            </div>
            <div className="ml-auto flex flex-wrap gap-2">
              <div role="group" aria-label="견적서 언어" className={SEG}>
                {(["ko", "en"] as const).map((l) => (
                  <button key={l} type="button" disabled={!!busy} aria-pressed={d.form.lang === l} onClick={() => { if (d.form.lang !== l) void save({ lang: l }); }}
                          title={l === "ko" ? "국내 견적서로 — 조건 문구·입금 계좌가 국내 기본으로 바뀝니다(바로 저장)" : "해외 견적서로 — 조건 문구·입금 계좌가 영문 기본으로 바뀝니다(바로 저장)"}
                          className={cn(SEG_BTN, d.form.lang === l && cn(SEG_ON, "text-foreground"))}>
                    {l === "ko" ? "국내 · 원" : "해외 · $"}
                  </button>
                ))}
              </div>
              {/* 판매 / 대여(사용자 2026-10-22) — 대여면 계약 개월 수·월 납입금·중도 해지 주석. 판매 = 파랑, 대여 = 에메랄드 */}
              <div role="group" aria-label="견적서 목적" className={SEG}>
                {(["sale", "rental"] as const).map((p) => {
                  const on = (d.form.purpose ?? "sale") === p;
                  return (
                    <button key={p} type="button" disabled={!!busy} aria-pressed={on} onClick={() => { if (!on) void save({ purpose: p }); }}
                            title={p === "sale" ? "판매 견적서로(바로 저장)" : "대여 견적서로 — 대여 개월 수·월 납입금·중도 해지 문구(바로 저장)"}
                            className={cn(SEG_BTN, on && cn(SEG_ON, p === "rental" ? "text-emerald-700 dark:text-emerald-300" : "text-blue-700 dark:text-blue-300"))}>
                      {p === "sale" ? "제품 판매" : "제품 대여"}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>

          {d.form.purpose === "rental" && (
            <div className="flex flex-col gap-3 rounded-xl border border-emerald-500/35 bg-gradient-to-b from-emerald-500/[0.07] to-emerald-500/[0.02] p-4">
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded-full bg-emerald-600 px-2.5 py-0.5 text-[11.5px] font-bold text-white">대여</span>
                <h4 className="text-[14px] font-extrabold text-emerald-900 dark:text-emerald-200">대여 조건</h4>
                <span className="text-[12px] text-emerald-800/80 dark:text-emerald-300/80">월 납입금 = 수량 × 월 단가 × 대여 개월 (설치·물류 등 1회 비용은 한 번만)</span>
              </div>
              <div className="grid gap-3 sm:grid-cols-3">
                <label className={cn(HEAD_LABEL, "text-emerald-900/80 dark:text-emerald-200/80")}>
                  <span>대여 개월 수<span className="text-red-600"> *</span></span>
                  <input className={cn(HEAD_FIELD, "border-emerald-500/40")} inputMode="numeric" value={d.form.rental_months ?? ""} placeholder="예: 36 (3년)"
                         disabled={!!busy} onChange={(e) => setForm("rental_months", e.target.value)} />
                </label>
                <label className={cn(HEAD_LABEL, "text-emerald-900/80 dark:text-emerald-200/80")}>
                  <span>대여 시작일 <span className="font-normal text-foreground-subtle">· 선택</span></span>
                  <input type="date" className={cn(HEAD_FIELD, "border-emerald-500/40")} value={d.form.rental_start ?? ""} disabled={!!busy}
                         onChange={(e) => setForm("rental_start", e.target.value)} />
                </label>
                <div className={cn(HEAD_LABEL, "text-emerald-900/80 dark:text-emerald-200/80")}>
                  <span>월 납입금 <span className="font-normal text-foreground-subtle">· 천 원 올림</span></span>
                  {!en && d.form.rental_months && (dirty ? total : q.subtotal) ? (
                    <span className="flex h-10 items-center whitespace-nowrap rounded-[10px] bg-emerald-600 px-3.5 text-[16px] font-extrabold tracking-tight text-white"
                          title={`Total ÷ ${d.form.rental_months}개월, 천 원 올림`}>
                      월 {(Math.ceil((dirty ? total : q.subtotal) / Number(d.form.rental_months) / 1000) * 1000).toLocaleString("ko-KR")}원
                    </span>
                  ) : (
                    <span className="flex h-10 items-center rounded-[10px] border border-dashed border-emerald-500/40 px-3 text-[12.5px] font-medium text-emerald-700 dark:text-emerald-300">
                      {d.form.rental_months ? "품목 단가를 넣으면 계산" : "개월 수를 넣으면 계산"}
                    </span>
                  )}
                </div>
              </div>
              <details className="text-[11.5px] text-foreground-subtle">
                <summary className="cursor-pointer select-none font-semibold text-emerald-800 dark:text-emerald-300">단가 넣는 법</summary>
                <p className="mt-1">
                  단가 칸에는 <b>1대당 월 납입금</b> 또는 <b>1회 지불 금액</b>을 넣으세요. 월 납입금 줄은 <b>수량 × 월 납입금 × 대여 개월</b>로 계산되고, 품목을 <b>Service</b> 로 적거나 단위가 <b>식</b>인 줄, 배송비·운반비 줄은 1회 지불로 한 번만 더합니다. 표 아래 중도 해지 문구는 아래 [표 아래 주석]에서 고칠 수 있습니다.
                </p>
              </details>
            </div>
          )}
          {en && (
            <p className="rounded-xl bg-accent/[0.07] px-3.5 py-2.5 text-[12px] text-foreground">
              해외 견적서는 <b>모든 칸이 영문</b>이어야 발행됩니다. 한글로 적은 칸은 위의 <b>[AI로 영문 바꾸기]</b>로 한 번에 바꾸고 미리보기로 확인하세요.
              비워 둔 납기·납품장소·결제조건·Comments 는 영문 기본 문구로, 한글 단위(식·개월·대·개)는 lot·months·unit·ea 로 들어갑니다.
            </p>
          )}

          {HEAD_GROUPS.map((g) => {
            const fields = HEAD.filter((h) => g.keys.includes(h.k) && (!h.only || h.only === (en ? "en" : "ko")));
            const left = fields.filter((h) => h.req && !String(d.form[h.k] ?? "").trim()).length;
            const hasReq = fields.some((h) => h.req);
            const folded = g.fold && !bankOpen;
            const summary = g.fold ? fields.map((h) => String(d.form[h.k] ?? "").trim()).filter(Boolean).join(" · ") : "";
            return (
              <div key={g.n} className={cn("rounded-xl border border-border", g.fold ? "bg-foreground/[0.02]" : "bg-surface-raised")}>
                <div className={cn("flex flex-wrap items-center gap-2.5 px-4", g.fold ? "py-3" : "pt-3.5")}>
                  <span className={cn("grid h-[26px] w-[26px] place-items-center rounded-lg text-[12.5px] font-extrabold", g.tone)}>{g.n}</span>
                  <h4 className="text-[14px] font-extrabold text-foreground">{en ? g.en : g.title}</h4>
                  {g.fold ? (
                    <>
                      {!summary && (
                        <span className="rounded-full border border-border bg-surface-raised px-2 py-0.5 text-[11.5px] font-semibold text-foreground-muted">기본값</span>
                      )}
                      <span className="min-w-0 truncate text-[12.5px] text-foreground-muted">{summary || (en ? "회사 해외 송금 계좌" : "회사 기본 계좌")}</span>
                      <button type="button" aria-expanded={bankOpen} onClick={() => setBankOpen(!bankOpen)}
                              className="ml-auto text-[12.5px] font-bold text-accent hover:underline">
                        {bankOpen ? "접기" : "펼쳐서 고치기"}
                      </button>
                    </>
                  ) : left > 0 ? (
                    <span className="ml-auto rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-[11.5px] font-semibold text-amber-800 dark:text-amber-300">{left}칸 남음</span>
                  ) : hasReq ? (
                    <span className="ml-auto rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-[11.5px] font-semibold text-emerald-700 dark:text-emerald-300">완료</span>
                  ) : (
                    <span className="ml-auto text-[11.5px] text-foreground-subtle">비우면 기본 문구</span>
                  )}
                </div>
                {!folded && (
                  <div className={cn("grid gap-3 px-4 pb-4", g.fold ? "pt-0" : "pt-3", g.cols)}>
                    {fields.map((h) => (
                      <label key={h.k} className={HEAD_LABEL}>
                        <span>{en ? LABEL_EN[h.k] ?? h.label : h.label}{h.req && <span className="text-red-600"> *</span>}</span>
                        {headInput(h)}
                      </label>
                    ))}
                    {g.n === 3 && en && (
                      <label className={HEAD_LABEL}>
                        <span>Exchange Rate (KRW/USD) <span className="font-normal text-foreground-subtle">· 선택</span></span>
                        <input className={HEAD_FIELD} inputMode="decimal" value={d.form.fx_rate ?? ""} placeholder="예: 1380 — 넣으면 단가표 가격을 달러로"
                               disabled={!!busy} onChange={(e) => setForm("fx_rate", e.target.value)} />
                      </label>
                    )}
                  </div>
                )}
              </div>
            );
          })}

          {/* 견적서 아래쪽 문구 */}
          <div className="grid gap-3 sm:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
            {HEAD.filter((h) => h.k === "comments" || h.k === "note").map((h) => (
              <label key={h.k} className={HEAD_LABEL}>
                <span>{en ? LABEL_EN[h.k] ?? h.label : h.label}
                  {h.k === "comments" && <span className="ml-1 font-normal text-foreground-subtle">· 견적서에 가운데 정렬 · 6줄까지</span>}</span>
                {headInput(h)}
              </label>
            ))}
          </div>
        </section>

        {/* 품목 */}
        <section className={cn(HEAD_CARD, "gap-3")}>
          <div className="flex flex-wrap items-baseline gap-2">
            <h3 className="text-[17px] font-extrabold tracking-tight text-foreground">{en ? "Goods Description & Details" : "품목"}</h3>
            <span className="text-[12.5px] text-foreground-muted">
              {en ? "단가는 달러(VAT 별도) — 비운 칸은 원화 단가표 가격 ÷ 환율 · 견적서엔 $ 로만 표기" : "금액은 원화(VAT 별도) · 제품 단가는 회사 단가표"}
            </span>
          </div>
          {d.blocks.length > 1 && (() => {   // 다른 묶음이 있을 때 — 기본 품목 표에도 소제목·소계(사용자 2026-10-13)
            const main = d.blocks.find((b) => b.id === MAIN_BLOCK) ?? MAIN_DEFAULT;
            const setMain = (patch: Partial<QuoteBlock>) =>
              edit({ blocks: [{ ...main, ...patch }, ...d.blocks.filter((b) => b.id !== MAIN_BLOCK)] });
            return (
              <div className="flex flex-wrap items-center gap-2 text-[12px]">
                <span className="rounded bg-accent/10 px-1.5 py-0.5 text-[11px] font-semibold text-accent">기본 품목 묶음</span>
                <input className={cn(inputCls, "min-w-0 flex-1 basis-40 py-0.5 text-[12.5px]")} value={main.title} maxLength={60} disabled={!!busy}
                       aria-label="기본 묶음 소제목" placeholder="소제목(선택) — 비우면 소제목 줄 없이" onChange={(e) => setMain({ title: e.target.value })} />
                <span className="text-[11.5px] text-foreground-subtle">
                  {d.blocks.some((x) => x.type === "items" && x.id !== MAIN_BLOCK) ? "품목 묶음이 둘 이상 — 묶음마다 Sub total 자동" : ""}
                  {" "}· 단락마다 회색 구분줄 자동
                </span>
              </div>
            );
          })()}
          {/* 품목 줄 — 한 줄 = 두 단 카드(가로 스크롤 없이 금액·버튼이 늘 보이게, 사용자 2026-10-23) */}
          <ul className="flex flex-col gap-2.5">
            {d.lines.map((l, i) => blockOf(l, d.blocks, autoBlock) !== MAIN_BLOCK ? null : (
              <li key={l.id} className={isAuto(l) ? ROW_CARD_AUTO : ROW_CARD}>
                <div className="grid gap-2.5 sm:grid-cols-[190px_minmax(0,1fr)]">
                  <div className={ROW_LABEL}>
                    <span className="flex items-center gap-1.5">{en ? "Commodity" : "품목"}
                      <span className={cn("rounded-md px-1.5 py-px text-[11px] font-bold", KIND_TONE[l.kind])}>
                        {monthFactor(l, rentMonths) === 1 && rentMonths && l.kind !== "note" ? "1회" : KIND_KO[l.kind]}
                      </span>
                    </span>
                    {isAuto(l) ? <span className="flex h-10 items-center text-[13.5px] font-semibold text-foreground-muted">{l.commodity}</span> : (
                      <LineArea className={HEAD_AREA} value={l.commodity ?? ""} aria-label="품목" placeholder={en ? "영문 — 예: Magbot ATC" : "예: Magbot ATC"}
                                disabled={!!busy} onChange={(e) => setLine(i, { commodity: e.target.value })} />
                    )}
                  </div>
                  <div className={ROW_LABEL}>
                    <span>{en ? "Specifications" : "규격"}</span>
                    {l.kind === "extra"
                      ? <AutoTextarea className={HEAD_AREA} value={l.name} aria-label="규격" placeholder={en ? "모델·사양(영문) — 여러 줄 가능\n예: TCV1\n- Master T.C (Payload 10Kg)" : "모델·사양 — 여러 줄 가능\n예: TCV1\n- Master T.C"} disabled={!!busy} onChange={(e) => setLine(i, { name: e.target.value })} />
                      : <span className="flex min-h-10 items-center whitespace-pre-line text-[13.5px] font-semibold text-foreground">{l.name}</span>}
                  </div>
                </div>
                <div className="mt-2.5 flex flex-wrap items-end gap-2.5">
                  <div className={ROW_LABEL}>
                    <span>{en ? "Qty · Unit" : "수량 · 단위"}</span>
                    {isAuto(l) ? <span className="flex h-10 items-center text-[13.5px] font-semibold">{l.qty}{l.unit ? ` ${l.unit}` : ""}</span> : (
                      <span className="flex">
                        <input className={cn(ROW_FIELD, "w-[64px] rounded-r-none text-right", (l.qty == null || (l.conditional && !l.confirmed)) && "border-amber-500")}
                               type="number" min={1} value={l.qty ?? ""} aria-label="수량" disabled={!!busy}
                               onChange={(e) => setLine(i, { qty: Math.max(0, Math.floor(Number(e.target.value) || 0)), ...(l.conditional ? { confirmed: true } : {}) })} />
                        <input className={cn(ROW_FIELD, "-ml-px w-[64px] rounded-l-none bg-foreground/[0.03] font-normal")} value={l.unit ?? ""} maxLength={8}
                               list="quote-units" aria-label="수량 단위" disabled={!!busy} placeholder="ea"
                               title="수량 단위 — 예: MD·식·개월·대 (비우면 ea)" onChange={(e) => setLine(i, { unit: e.target.value })} />
                      </span>
                    )}
                  </div>
                  <div className={ROW_LABEL}>
                    <span>{en ? (rentMonths ? "Price (Monthly)" : "Price") : rentMonths ? (monthFactor(l, rentMonths) > 1 ? "월 단가" : "금액 (1회)") : "단가"}</span>
                    {isAuto(l) ? <span className="flex h-10 items-center text-[13.5px] font-semibold">{en ? (usdOf(l, fx) == null ? "환율 필요" : usd(usdOf(l, fx))) : won(l.unit_price)}</span> : en ? (
                      <span className="flex flex-col gap-0.5">
                        <input className={cn(ROW_FIELD, "w-[140px] text-right", usdOf(l, fx) == null && "border-amber-500")} inputMode="decimal"
                               key={`usd-${l.id}-${draftVer}`} defaultValue={l.usd ?? ""} aria-label="달러 단가" disabled={!!busy}
                               placeholder={usdOf(l, fx) != null ? `${usd(usdOf(l, fx))} (환율 환산)` : "$ 단가"}
                               onChange={(e) => { const v = Number(e.target.value.replace(/[,$\s]/g, "")); setLine(i, { usd: v > 0 ? v : null }); }} />
                        {l.unit_price ? <span className="text-[10.5px] font-normal text-foreground-subtle">단가표 {won(l.unit_price)}</span> : null}
                      </span>
                    ) : (
                      <input className={cn(ROW_FIELD, "w-[140px] text-right", l.unit_price == null && "border-amber-500")} inputMode="numeric"
                             value={l.unit_price ?? ""} aria-label="단가" placeholder={l.price_status === "unset" ? "가격 미정" : rentMonths ? "월 납입금 또는 1회 지불" : "단가"} disabled={!!busy}
                             onChange={(e) => setLine(i, { unit_price: Number(e.target.value.replace(/,/g, "")) || null })} />
                    )}
                  </div>
                  {l.conditional && (l.confirmed
                    ? <span className="flex h-10 items-center text-[12px] font-semibold text-emerald-700 dark:text-emerald-300">✓ 수량 확인함</span>
                    : <button type="button" disabled={!!busy || l.qty == null} title={l.basis || "조건부 수량 — 근거를 확인한 뒤 누르세요"}
                              onClick={() => setLine(i, { confirmed: true })}
                              className={cn(ROW_BTN, "border-amber-500/60 text-amber-700 hover:bg-amber-500/10 dark:text-amber-300")}>
                        조건부 · 수량 확인
                      </button>)}
                  <div className={cn(ROW_LABEL, "ml-auto flex-1 items-end")}>
                    <span>{en ? "Amount" : "금액"}{rentMonths ? <span className="font-normal text-foreground-subtle"> · {monthFactor(l, rentMonths) > 1 ? `× ${rentMonths}개월` : "1회 지불"}</span> : null}</span>
                    <span className={AMOUNT_BOX}>
                      {en ? (usdOf(l, fx) == null ? "-" : usd((l.qty ?? 0) * (usdOf(l, fx) ?? 0) * monthFactor(l, rentMonths)))
                        : (l.unit_price == null ? <span className="text-[12.5px] font-medium text-foreground-subtle">단가를 넣으면 계산</span>
                          : won((l.qty ?? 0) * (l.unit_price ?? 0) * monthFactor(l, rentMonths)))}
                    </span>
                  </div>
                </div>
                <div className="mt-2.5 flex flex-wrap items-start gap-2">
                  {isAuto(l) ? (l.remark ? <span className="text-[12px] text-foreground-muted">비고 · {l.remark}</span> : null) : (
                    <>
                      <LineArea className={cn(HEAD_AREA, "min-w-0 flex-1 basis-64 text-[12.5px]")} value={l.remark ?? ""} aria-label="비고" disabled={!!busy}
                                placeholder={en ? "Remark — 예: Control box & 4m cable included" : "비고 — 예: CONTROLBOX·4m 케이블 기본 포함"}
                                onChange={(e) => setLine(i, { remark: e.target.value })} />
                      {/* 품목 사진 — 견적서 Remark 칸에 작게(사용자 2026-10-12). 저장 안 한 변경은 먼저 저장하고 올림 */}
                      {photoCell(l)}
                      <button type="button" aria-label={`${l.name || "품목"} 크게 보기`} title="크게 보기 — 넓은 칸에서 고치고 견적서 모양 확인"
                              disabled={!!busy} className={ROW_ICON_BTN} onClick={() => setBigLine(i)}>
                        <Icon name="eye" className="h-4 w-4" />
                      </button>
                      <button type="button" aria-label={`${l.name} 빼기`} title="이 줄 빼기" disabled={!!busy}
                              className={cn(ROW_ICON_BTN, "hover:border-red-400/60 hover:bg-red-500/5 hover:text-red-600")} onClick={() => edit({ lines: d.lines.filter((_, j) => j !== i) })}>
                        <Icon name="trash" className="h-4 w-4" />
                      </button>
                    </>
                  )}
                </div>
              </li>
            ))}
          </ul>
          <datalist id="quote-units">{["ea", "식", "개월", "대", "MD", "set"].map((u) => <option key={u} value={u} />)}</datalist>
          <div className="flex flex-wrap items-center gap-1.5 text-[12px]">
            <button type="button" disabled={!!busy} className={ITEM_BTN}
                    onClick={() => edit({ lines: [...d.lines, { id: `new${Date.now()}`, kind: "extra", key: "extra", name: "", commodity: "", qty: 1, unit_price: null, remark: "" }] })}>
              <Icon name="plus" className="h-3.5 w-3.5 text-blue-600 dark:text-blue-400" /> 항목 추가
            </button>
            {q.manual && <PricePicker disabled={!!busy}
                                      onPick={(p) => edit({ lines: [...d.lines, { id: `new${Date.now()}`, kind: "extra", key: "extra",
                                        name: `${p.model} ${p.item}`.slice(0, 60), qty: 1, unit_price: p.unit_price, remark: "" }] })} />}
            {optionsLeft.map((o) => (
              <button key={o.name} type="button" disabled={!!busy}
                      className={ITEM_BTN}
                      onClick={() => edit({ include: [...d.include, o.name],
                        lines: [...d.lines, { ...o, id: `new-opt-${o.name}`, kind: "option" } as QuoteLine] })}>
                <Icon name="plus" className="h-3.5 w-3.5 text-cyan-700 dark:text-cyan-300" /> 옵션 {o.name} ({won(o.unit_price)})
              </button>
            ))}
          </div>
          {/* 묶음(옵션·소제목·사양·문구)·NEGO — 영업팀 요청 14·9번(사용자 2026-10-13) */}
          <QuoteBlocks blocks={d.blocks} lines={d.lines} nego={d.nego} en={en} fx={fx} busy={!!busy} draftVer={draftVer} autoBlock={autoBlock} months={rentMonths}
                       totalNote={d.form.total_note ?? ""}
                       footNotes={Array.isArray(d.form.foot_notes) ? d.form.foot_notes : (d.form.foot_notes ?? "").split("\n").filter((x) => x.trim())}
                       pricePicker={(onPick) => <PricePicker disabled={!!busy} onPick={onPick} />}
                       photoCell={photoCell}
                       onChange={({ total_note, foot_notes, ...rest }) => edit({
                         ...rest,
                         // 추천 옵션(IB 등)을 화면에서 넣었다가 뺀 줄은 포함 목록에서도 뺀다 — 저장 때 다시 들어오지 않게
                         ...(rest.lines ? { include: d.include.filter((n) => rest.lines!.some((l) => l.id === `new-opt-${n}`)) } : {}),
                         ...(total_note !== undefined || foot_notes !== undefined ? { form: {
                           ...d.form, ...(total_note !== undefined ? { total_note } : {}), ...(foot_notes !== undefined ? { foot_notes } : {}) } } : {}),
                       })}>
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
              {/* 물류비·인건비 줄을 넣을 곳 — 기본 품목 / 있는 사용자 지정 묶음 / 새 묶음을 만들어 넣기(사용자 2026-10-13) */}
              {dv && (dv.freight || dv.work) && (
                <div className="flex flex-wrap items-center gap-1.5 text-foreground-muted">
                  <span>{dv.freight && dv.work ? "물류비·설치 인건비" : dv.work ? "설치 인건비" : "물류비"} 넣을 곳</span>
                  <select className={cn(inputCls, "w-auto py-0.5 text-[12.5px]")} value={newGroup !== null ? NEW_GROUP : autoBlock} disabled={!!busy}
                          aria-label="물류비·인건비 넣을 곳"
                          onChange={(e) => {
                            if (e.target.value === NEW_GROUP) { setNewGroup(dv.work ? "설치 및 시운전" : "물류"); return; }
                            setNewGroup(null);
                            setDelivery({ block: e.target.value });
                          }}>
                    <option value={MAIN_BLOCK}>기본 품목에 넣기</option>
                    {d.blocks.filter((b) => b.type === "items" && b.id !== MAIN_BLOCK).map((b) => (
                      <option key={b.id} value={b.id}>{b.title || "이름 없는 묶음"} 묶음에 넣기</option>
                    ))}
                    <option value={NEW_GROUP}>+ 별도 묶음을 만들어 넣기</option>
                  </select>
                  {newGroup !== null && (
                    <>
                      <input className={cn(inputCls, "w-[160px] py-0.5 text-[12.5px]")} value={newGroup} maxLength={60} autoFocus disabled={!!busy}
                             aria-label="새 묶음 이름" placeholder="묶음 이름 — 예: 설치 및 시운전" onChange={(e) => setNewGroup(e.target.value)}
                             onKeyDown={(e) => { if (e.key === "Enter" && !e.nativeEvent.isComposing) { e.preventDefault(); makeAutoGroup(); } }} />
                      <button type="button" disabled={!!busy || !newGroup.trim()} onClick={makeAutoGroup}
                              className="rounded-md border border-accent/60 px-2 py-0.5 text-[12px] font-semibold text-accent hover:bg-accent/5 disabled:opacity-40">
                        만들기
                      </button>
                      <button type="button" className="text-[12px] text-foreground-subtle hover:underline" onClick={() => setNewGroup(null)}>취소</button>
                    </>
                  )}
                  <span className="basis-full text-[11px] text-foreground-subtle">
                    별도 묶음에 넣으면 견적서에 그 묶음 이름(소제목)과 소계가 따로 나옵니다 — 예: 설치 및 시운전 / 배송비.
                  </span>
                </div>
              )}
            </div>
          </QuoteBlocks>
          {/* 합계 막대 — 이 화면의 결론이라 가장 크게(판매 파랑 · 대여 에메랄드, 사용자 2026-10-23) */}
          <div className={cn("-mx-5 flex flex-wrap items-center gap-x-6 gap-y-2 border-y px-5 py-4",
            rentMonths ? "border-emerald-500/25 bg-gradient-to-b from-emerald-500/[0.07] to-emerald-500/[0.03]" : "border-blue-500/20 bg-gradient-to-b from-blue-500/[0.06] to-blue-500/[0.02]")}>
            {!en && rentMonths && (dirty ? total : q.subtotal) ? (
              <div className="flex flex-col gap-0.5">
                <span className="text-[12px] font-semibold text-emerald-800/80 dark:text-emerald-300/80">월 납입금 · 천 원 올림</span>
                <span className="text-[16px] font-extrabold text-emerald-700 dark:text-emerald-300">
                  월 {(Math.ceil((dirty ? total : q.subtotal) / rentMonths / 1000) * 1000).toLocaleString("ko-KR")}원
                </span>
              </div>
            ) : null}
            <div className="ml-auto flex flex-col items-end gap-0.5">
              <span className="text-[12px] font-semibold text-foreground-muted">합계(VAT 별도){d.lines.some((l) => optionIds.has(blockOf(l, d.blocks)) && l.kind !== "note") ? (d.lines.some((l) => optionIds.has(blockOf(l, d.blocks)) && l.kind !== "note" && !l.in_total) ? " · 미반영 옵션 제외" : " · 옵션 포함") : ""}{d.nego?.value ? " · NEGO 차감" : ""}</span>
              <span className="text-[24px] font-extrabold tracking-tight text-foreground">
                {en ? ((dirty ? totalUsd : q.subtotal_usd) == null ? "달러 단가가 빈 줄이 있습니다" : usd(dirty ? totalUsd : q.subtotal_usd)) : won(dirty ? total : q.subtotal)}
              </span>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" className={dirty ? primaryBtn : secondaryBtn} disabled={!dirty || !!busy} onClick={(e) => void keepInPlace(e.currentTarget, () => save())}>
              <Icon name="check" className="h-4 w-4" /> 변경 저장
            </button>
            {dirty && <button type="button" className="text-[12px] text-foreground-subtle hover:underline" disabled={!!busy} onClick={() => load(q)}>되돌리기</button>}
            <button type="button" className={cn(secondaryBtn, "ml-auto")} disabled={dirty || !!busy || q.problems.length > 0} onClick={() => setPreview(true)}
                    title={q.problems.length ? "먼저 위의 확인할 것을 채워 주세요" : dirty ? "먼저 변경을 저장해 주세요" : "지금 내용 그대로 견적서를 봅니다"}>
              <Icon name="eye" className="h-4 w-4" /> 미리보기
            </button>
            <button type="button" className={secondaryBtn} disabled={dirty || !!busy} onClick={() => setUploading(true)}
                    title={dirty ? "먼저 변경을 저장해 주세요" : "엑셀에서 고쳤거나 따로 만든 견적서 파일로 발행합니다(새 번호)"}>
              <Icon name="paperclip" className="h-4 w-4" /> 고친 파일로 발행
            </button>
            <button type="button" className={primaryBtn} disabled={dirty || !!busy || blocked} onClick={() => void issue()}
                    title={q.problems.length ? "발행 전에 위의 확인할 것을 채워 주세요"
                      : hangul.some((c) => c.en == null && !c.keep) ? "한글 칸을 AI 가 영어로 바꾼 뒤 미리보기로 먼저 보여 드립니다"
                      : dirty ? "먼저 변경을 저장해 주세요" : ""}>
              <Icon name="file-spreadsheet" className="h-4 w-4" /> {issued ? "재발행 (새 번호)" : "견적서 발행"}
            </button>
          </div>
          {issued && !dirty && (
            <p className="text-[11.5px] text-foreground-subtle">발행 뒤에도 고칠 수 있습니다 — 고치고 저장한 뒤 [재발행]하면 새 발행번호(QT-이니셜+오늘 날짜-순번)가 붙고, 예전 판은 아래 발행 이력에 남습니다.</p>
          )}
          {/* 자리는 늘 잡아 둔다 — 저장 뒤 이 줄이 사라지며 페이지가 짧아져 화면이 밀리지 않게(사용자 2026-10-23) */}
          <p className={cn("min-h-[17px] text-[11.5px]", dirty ? "text-amber-700 dark:text-amber-300" : "invisible")} aria-hidden={!dirty}>
            직접 고친 내용이 있습니다 — [변경 저장] 후 대화·발행할 수 있습니다.
          </p>
        </section>

        {/* 발행 이력 */}
        {q.history.length > 0 && (
          <section className="flex flex-col gap-1.5 rounded-xl border border-border bg-surface p-4 text-[12.5px]">
            <h3 className="text-[14px] font-semibold text-foreground">발행 이력</h3>
            {[...q.history].reverse().map((h) => (
              <div key={h.revision} className="flex flex-wrap items-center gap-2">
                <span className="font-mono font-medium">{h.quote_no ?? `v${h.revision + 1}`}</span>
                {!h.revision && <span className="text-foreground-subtle">(최초 발행)</span>}
                {h.uploaded && <span className="rounded bg-amber-500/12 px-1.5 text-[11px] font-semibold text-amber-700 dark:text-amber-300">외부 수정본</span>}
                <span className="text-foreground-subtle">{new Date(h.issued_at).toLocaleString("ko-KR")}</span>
                <span>{h.currency === "USD" ? `$${h.total.toLocaleString("en-US", { minimumFractionDigits: 2 })}` : won(h.total)}</span>
                <button type="button" className="ml-auto inline-flex items-center gap-1 text-accent hover:underline"
                        onClick={() => setSaving({ revision: h.revision, fileName: h.file_name, title: `${h.quote_no ?? `v${h.revision + 1}`} 다시 다운로드` })}>
                  <Icon name="download" className="h-3.5 w-3.5" /> 다시 다운로드
                </button>
              </div>
            ))}
          </section>
        )}
        <ErrorBox error={err} />
      </div>
      {preview && <PreviewDialog url={`/api/product-recommend/quotes/${q.id}/preview`} title="견적서 미리보기"
                                  sub={en && hangul.length ? "한글 칸은 AI 영어로 · 발행 전이면 견적번호는 발행 때 정해집니다" : "지금 저장된 내용 그대로 · 발행 전이면 견적번호는 발행 때 정해집니다"}
                                  onClose={() => setPreview(false)} onLoaded={() => void reloadAfterPreview()} />}
      <ConfirmDialog open={paidAsk} icon="help" variant="default" title="완납한 건입니다"
                     description="완납한 건입니다. 새로 발행해도 이 건의 견적 금액·품목은 바뀌지 않고 견적서 목록에만 남습니다. 추가 주문이면 새 영업 건으로 만들어 주세요."
                     confirmLabel="그래도 발행" onClose={() => setPaidAsk(false)}
                     onConfirm={() => { setPaidAsk(false); void issue(true); }} />
      {uploading && (
        <QuoteUploadDialog title="고친 견적서 파일로 발행" uploadUrl={`/api/product-recommend/quotes/${q.id}/upload`}
                           confirmUrl={`/api/product-recommend/quotes/${q.id}/upload/confirm`} paidFull={!!q.deal_paid_full}
                           onDone={(d) => { setUploading(false); load(d as QuoteSession); }} onClose={() => setUploading(false)} />
      )}
      {saving && <DownloadDialog urlFor={(fmt) => `/api/product-recommend/quotes/${q.id}/file?fmt=${fmt}&revision=${saving.revision}`}
                                 fileName={saving.fileName} title={saving.title} onClose={() => setSaving(null)} />}

      {bigLine != null && d.lines[bigLine] && (
        <LineDialog line={d.lines[bigLine]} en={en} fx={fx} busy={!!busy} months={rentMonths} onChange={(patch) => setLine(bigLine, patch)}
                    onClose={() => setBigLine(null)} />
      )}

      {/* 견적서 ↔ AI 대화 폭 조절(사용자 2026-10-22) — 끌어서 조절, 두 번 누르면 기본 폭, 화살표 키로도 */}
      <SplitBar split={split} label="견적서와 AI 대화 폭 조절" />

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
              className={ITEM_BTN}>
        <Icon name="search" className="h-3.5 w-3.5 text-blue-600 dark:text-blue-400" /> 단가표에서 추가
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
