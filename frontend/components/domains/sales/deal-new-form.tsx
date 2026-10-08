// 새 영업 건(견적) 등록 — 고객사·담당·견적일·품목·결제조건. 합계와 결제 회차는 저장할 때 backend 가 계산한다.
"use client";

import { useState } from "react";
import { Field, INPUT, dealRequest, todayIso, won } from "@/components/domains/sales/deal-format";
import { InitialsPrompt } from "@/components/domains/sales/initials-prompt";
import type { DealRow } from "@/lib/shared/sales-deals";
import { cn } from "@/lib/shared/utils";

// 기존 관리 시트에서 가장 많이 쓰던 결제조건 문구
const PAY_TERM_PRESETS = [
  "선금 : 발주 시 50% / 잔금 : 납품 전 50% (잔금 입금 확인 후 출고)",
  "선금 : 발주 시 100% (입금 확인 후 출고)",
  "대금 지급 조건 : 납품 후 익월말",
  "별도 협의",
];

type Line = { key: number; name: string; unit_price: string; qty: string };

const num = (s: string) => {
  const n = Number(s.replaceAll(",", ""));
  return s.trim() && Number.isFinite(n) ? n : null;
};

export function DealNewForm({ owners, onClose, onCreated }: {
  owners: string[];
  onClose: () => void;
  onCreated: (row: DealRow) => void;
}) {
  const [customer, setCustomer] = useState("");
  const [contact, setContact] = useState("");
  const [owner, setOwner] = useState("");
  const [quoteDate, setQuoteDate] = useState(todayIso());
  const [title, setTitle] = useState("");
  const [lines, setLines] = useState<Line[]>([{ key: 1, name: "", unit_price: "", qty: "1" }]);
  const [vatIncluded, setVatIncluded] = useState(false);
  const [currency, setCurrency] = useState<"KRW" | "USD">("KRW");
  const [payTerms, setPayTerms] = useState(PAY_TERM_PRESETS[0]);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [needIni, setNeedIni] = useState(false);

  const total = lines.reduce((sum, l) => sum + (num(l.unit_price) ?? 0) * (num(l.qty) ?? 0), 0);
  const filled = lines.filter((l) => l.name.trim());
  const canSave = customer.trim() && quoteDate && filled.length > 0 && !busy;

  const setLine = (key: number, patch: Partial<Line>) =>
    setLines((ls) => ls.map((l) => (l.key === key ? { ...l, ...patch } : l)));

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const row = await dealRequest("", {
        customer: customer.trim(), contact: contact.trim() || null, owner: owner.trim() || null,
        title: title.trim() || null, quote_date: quoteDate, vat_included: vatIncluded, currency,
        pay_terms_text: payTerms.trim() || null, note: note.trim() || null,
        items: filled.map((l) => ({ name: l.name.trim(), unit_price: num(l.unit_price), qty: num(l.qty) })),
      });
      onCreated(row);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "저장하지 못했습니다.";
      // 새 건 번호 = 담당 이니셜 + 날짜 — 이니셜을 아직 모르는 담당자면 한 번 묻는다
      if (msg.includes("이니셜") && owner.trim()) setNeedIni(true);
      else setError(msg.includes("이니셜") ? "영업 담당을 적어 주세요 — 건 번호(담당 이니셜 + 날짜)에 씁니다." : msg);
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-40 grid place-items-center bg-black/30 p-4" onClick={onClose}>
      <div role="dialog" aria-label="새 제품 영업 건 등록" onClick={(e) => e.stopPropagation()}
           className="scroll-thin max-h-full w-full max-w-[760px] overflow-y-auto rounded-2xl bg-background p-5 shadow-2xl">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-[17px] font-bold">새 건 등록 (견적)</h2>
          <button type="button" onClick={onClose} aria-label="닫기"
                  className="grid h-8 w-8 place-items-center rounded-lg text-foreground-muted hover:bg-foreground/8">✕</button>
        </div>

        <div className="grid gap-3 text-[13px] md:grid-cols-2">
          <Field label="고객사 *"><input value={customer} onChange={(e) => setCustomer(e.target.value)} className={INPUT} /></Field>
          <Field label="고객 담당자"><input value={contact} onChange={(e) => setContact(e.target.value)} className={INPUT} placeholder="예: 박기영 이사" /></Field>
          <Field label="영업 담당">
            <input value={owner} onChange={(e) => setOwner(e.target.value)} list="deal-owners" className={INPUT} />
            <datalist id="deal-owners">{owners.map((o) => <option key={o} value={o} />)}</datalist>
          </Field>
          <Field label="견적일 *"><input type="date" value={quoteDate} onChange={(e) => setQuoteDate(e.target.value)} className={INPUT} /></Field>
          <Field label="건명 (비우면 첫 품목으로)" wide>
            <input value={title} onChange={(e) => setTitle(e.target.value)} className={INPUT} />
          </Field>
        </div>

        <h3 className="mb-2 mt-5 text-[12px] font-semibold uppercase tracking-[0.1em] text-foreground-subtle">견적 품목</h3>
        <div className="flex flex-col gap-2">
          {lines.map((l) => (
            <div key={l.key} className="grid grid-cols-[1fr_130px_80px_32px] gap-2">
              <input value={l.name} onChange={(e) => setLine(l.key, { name: e.target.value })} placeholder="품목·사양" className={INPUT} />
              <input value={l.unit_price} onChange={(e) => setLine(l.key, { unit_price: e.target.value })} placeholder="단가"
                     inputMode="numeric" className={cn(INPUT, "text-right")} />
              <input value={l.qty} onChange={(e) => setLine(l.key, { qty: e.target.value })} placeholder="수량"
                     inputMode="decimal" className={cn(INPUT, "text-right")} />
              <button type="button" aria-label="품목 삭제" disabled={lines.length === 1}
                      onClick={() => setLines((ls) => ls.filter((x) => x.key !== l.key))}
                      className="rounded-lg text-foreground-muted hover:bg-foreground/8 disabled:opacity-30">✕</button>
            </div>
          ))}
        </div>
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-[13px]">
          <button type="button" onClick={() => setLines((ls) => [...ls, { key: Date.now(), name: "", unit_price: "", qty: "1" }])}
                  className="rounded-lg border border-border px-3 py-1.5 hover:bg-foreground/5">+ 품목 추가</button>
          <div className="flex items-center gap-3">
            <label className="flex items-center gap-1.5 whitespace-nowrap">
              <input type="checkbox" checked={vatIncluded} onChange={(e) => setVatIncluded(e.target.checked)} /> 부가세 포함 단가
            </label>
            <select value={currency} onChange={(e) => setCurrency(e.target.value as "KRW" | "USD")} className={cn(INPUT, "w-auto")}>
              <option value="KRW">원(KRW)</option>
              <option value="USD">달러(USD)</option>
            </select>
            <span className="font-semibold">합계 {won(total, currency)}</span>
          </div>
        </div>

        <h3 className="mb-2 mt-5 text-[12px] font-semibold uppercase tracking-[0.1em] text-foreground-subtle">결제조건</h3>
        <div className="mb-2 flex flex-wrap gap-1.5">
          {PAY_TERM_PRESETS.map((p) => (
            <button key={p} type="button" onClick={() => setPayTerms(p)}
                    className={cn("rounded-full border px-2.5 py-1 text-[12px]",
                      payTerms === p ? "border-accent bg-accent/10 text-accent" : "border-border text-foreground-muted hover:text-foreground")}>
              {p}
            </button>
          ))}
        </div>
        <input value={payTerms} onChange={(e) => setPayTerms(e.target.value)} className={INPUT}
               placeholder="예: 선금 : 발주 시 50% / 잔금 : 납품 전 50%" />
        <p className="mt-1 text-[11.5px] text-foreground-subtle">
          &apos;선금·잔금·%&apos;를 읽어 결제 회차를 만듭니다. &apos;입금 확인 후 출고&apos;가 있으면 완납 확인 전 출하를 막습니다.
        </p>

        <Field label="비고" wide className="mt-4">
          <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} className={cn(INPUT, "h-auto py-2")} />
        </Field>

        {error && <p className="mt-3 rounded-lg bg-red-500/10 px-3 py-2 text-[13px] text-red-700 dark:text-red-300">{error}</p>}
        {needIni && (
          <div className="mt-3">
            <InitialsPrompt name={owner.trim()} onCancel={() => setNeedIni(false)} onSaved={() => { setNeedIni(false); void save(); }} />
          </div>
        )}
        <div className="mt-5 flex justify-end gap-2">
          <button type="button" onClick={onClose} className="h-9 rounded-lg border border-border px-4 text-[13px]">취소</button>
          <button type="button" onClick={save} disabled={!canSave}
                  className="h-9 rounded-lg bg-accent px-4 text-[13px] font-semibold text-white disabled:opacity-40">
            {busy ? "저장 중…" : "등록"}
          </button>
        </div>
      </div>
    </div>
  );
}
