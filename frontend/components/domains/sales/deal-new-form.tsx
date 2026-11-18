// 새 영업 건 등록 — 뼈대만(사용자 2026-11-18: 새 구매 건과 같은 형식). 고객사·프로젝트명·담당·메모만 적고 '견적 전' 단계로 만든다.
// 품목·금액·결제조건은 받지 않는다 — 견적서는 등록한 건의 상세에서 [견적서 수기 작성]으로 만들어 발행하고, 첫 발행번호가 건 번호가 된다
// (예전에는 여기서 품목을 적어 저장하면 발행번호 없는 견적이 생겨, 견적서를 고쳐도 건이 따라가지 않았음).
"use client";

import { useState } from "react";
import { dealRequest } from "@/components/domains/sales/deal-format";
import { secondaryBtn } from "@/components/domains/sales/proposal-actions";
import { REQ, REQ_SCOPE, ReqLabel } from "@/components/domains/sales/required";
import { Icon } from "@/components/shared/ui/icon";
import type { DealRow } from "@/lib/shared/sales-deals";
import { cn } from "@/lib/shared/utils";

const INPUT = "h-10 w-full rounded-lg border border-border bg-surface px-3 text-[13px] outline-none focus:border-accent";

export function DealNewForm({ owners, onClose, onCreated }: {
  owners: string[];
  onClose: () => void;
  onCreated: (row: DealRow) => void;
}) {
  const [v, setV] = useState({ customer: "", title: "", contact: "", owner: "", note: "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const canSave = !busy && !!v.customer.trim() && !!v.title.trim();

  async function save() {
    setBusy(true);
    setErr(null);
    try {
      onCreated(await dealRequest("/new", {
        customer: v.customer.trim(), title: v.title.trim(), contact: v.contact.trim() || null,
        owner: v.owner.trim() || null, note: v.note.trim() || null,
      }));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "등록하지 못했습니다.");
      setBusy(false);
    }
  }

  return (
    <section className="flex flex-col gap-3">
      <button type="button" className={cn(secondaryBtn, "self-start")} disabled={busy} onClick={onClose}>
        <Icon name="back" className="h-4 w-4" /> 영업 건 목록
      </button>
      <div className="w-full max-w-[720px] rounded-xl border border-border bg-surface p-5">
        <h2 className="text-[16px] font-bold">새 영업 건 작성</h2>
        <p className="mt-1 text-[12.5px] text-foreground-muted">
          고객사·프로젝트명만 적어 등록합니다. 등록한 뒤 상세의 <b>[견적서 수기 작성]</b>으로 견적서를 만들어 발행하면
          발행번호가 건 번호가 되고 견적 단계로 넘어갑니다.
        </p>
        <div className="mt-4 grid gap-3 text-[13px]">
          <label className={cn("grid gap-1", REQ_SCOPE)}><ReqLabel>고객사</ReqLabel>
            <input {...REQ} className={INPUT} value={v.customer} autoFocus placeholder="예: LG이노텍"
                   onChange={(e) => setV({ ...v, customer: e.target.value })} /></label>
          <label className={cn("grid gap-1", REQ_SCOPE)}><ReqLabel>프로젝트명</ReqLabel>
            <input {...REQ} className={INPUT} value={v.title} placeholder="예: Roller C AMR(RC1) 부자재"
                   onChange={(e) => setV({ ...v, title: e.target.value })} />
            <span className="text-[11.5px] text-foreground-subtle">리스트·상세에 보이는 이름입니다. 나중에 상세에서 고칠 수 있고, 견적서 내용과는 따로 관리됩니다.</span>
          </label>
          <label className="grid gap-1">고객 담당자<input className={INPUT} value={v.contact} placeholder="예: 박기영 이사"
                                                    onChange={(e) => setV({ ...v, contact: e.target.value })} /></label>
          <label className="grid gap-1">영업 담당
            <input className={INPUT} value={v.owner} list="deal-owners" onChange={(e) => setV({ ...v, owner: e.target.value })} />
            <datalist id="deal-owners">{owners.map((o) => <option key={o} value={o} />)}</datalist>
          </label>
          <label className="grid gap-1">메모<textarea className={cn(INPUT, "h-28 py-2")} maxLength={2000} value={v.note}
                                                 onChange={(e) => setV({ ...v, note: e.target.value })} /></label>
        </div>
        {err && <p className="mt-3 text-[12.5px] text-red-600">{err}</p>}
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" onClick={onClose} disabled={busy} className="h-9 rounded-lg border border-border px-4 text-[13px]">취소</button>
          <button type="button" onClick={() => void save()} disabled={!canSave}
                  className="h-9 rounded-lg bg-accent px-4 text-[13px] font-semibold text-white disabled:opacity-40">{busy ? "등록 중…" : "등록"}</button>
        </div>
      </div>
    </section>
  );
}
