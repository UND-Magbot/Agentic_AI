// 제품 이미지 — 공정 컨셉 이미지를 그릴 때 대안에 나오는 회사 제품(AMR·그리퍼 등)의 외형 참고로 GPT 에 보낸다.
// 소개서에서 뽑은 후보·직접 올린 사진 모두 영업 관리자가 승인해야 쓰인다(사용자 결정 2026-09-30).
"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import type { ProductImage, ProductImageCard, ProductImageLibrary } from "@/lib/shared/proposal-projects";
import { ErrorBox, secondaryBtn } from "./proposal-actions";

async function postJson(url: string, body: unknown) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof d?.detail === "string" ? d.detail : "요청을 처리하지 못했습니다.");
}

function Thumb({ image, approver, onError }: { image: ProductImage; approver: boolean; onError: (e: string) => void }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const pending = image.review_status === "pending";

  async function review(action: "approve" | "reject") {
    setBusy(true);
    try {
      await postJson(`/api/product-images/review/${image.id}`, { action });
      router.refresh();
    } catch (e) {
      onError(e instanceof Error ? e.message : "요청을 처리하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <figure className="flex w-36 flex-col gap-1">
      <a href={`/api/product-images/file/${image.id}`} target="_blank" rel="noreferrer"
         className="flex h-28 items-center justify-center overflow-hidden rounded-lg border border-border bg-white">
        {/* eslint-disable-next-line @next/next/no-img-element -- 인증 쿠키가 필요한 프록시 이미지 */}
        <img src={`/api/product-images/file/${image.id}`} alt={image.source_ref} className="max-h-full max-w-full object-contain" />
      </a>
      <figcaption className="flex flex-col gap-1 text-[11px] text-foreground-subtle">
        <span className="flex items-center gap-1">
          <span className={pending
            ? "rounded-full bg-amber-500/15 px-1.5 py-0.5 font-medium text-amber-700 dark:text-amber-300"
            : "rounded-full bg-emerald-500/15 px-1.5 py-0.5 font-medium text-emerald-700 dark:text-emerald-300"}>
            {pending ? "승인 대기" : "사용 중"}
          </span>
          <span className="truncate" title={image.source_ref}>{image.source === "deck" ? image.source_ref.split(" ").slice(0, 2).join(" ") : "직접 올림"}</span>
        </span>
        {approver && (
          <span className="flex gap-1">
            {pending && (
              <button type="button" disabled={busy} onClick={() => void review("approve")}
                      className="rounded-md bg-accent px-2 py-0.5 text-[11px] font-medium text-white disabled:opacity-50">승인</button>
            )}
            <button type="button" disabled={busy} onClick={() => void review("reject")}
                    className="rounded-md border border-border px-2 py-0.5 text-[11px] text-foreground-muted disabled:opacity-50">
              {pending ? "반려" : "빼기"}
            </button>
          </span>
        )}
      </figcaption>
    </figure>
  );
}

function ProductRow({ card, approver }: { card: ProductImageCard; approver: boolean }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function upload(file: File) {
    setBusy(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const r = await fetch(`/api/product-images/${card.card_id}`, { method: "POST", body: form });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof d?.detail === "string" ? d.detail : "올리지 못했습니다.");
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "올리지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="flex flex-col gap-2 rounded-xl border border-border bg-surface p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-baseline gap-1.5">
          <span className="text-[14px] font-semibold text-foreground">{card.name}</span>
          <span className="text-[12px] text-foreground-subtle">
            {card.family}{card.slides.length > 0 && ` · 소개서 ${card.slides.join("·")}쪽`}
          </span>
        </div>
        <label className={`${secondaryBtn} cursor-pointer ${busy ? "pointer-events-none opacity-50" : ""}`}>
          사진 올리기
          <input type="file" accept="image/png,image/jpeg,image/webp" className="hidden" disabled={busy}
                 onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) void upload(f); }} />
        </label>
      </div>
      {card.images.length === 0 ? (
        <p className="text-[12px] text-foreground-muted">사진이 없습니다 — 컨셉 이미지에 이 제품의 외형 참고가 붙지 않습니다.</p>
      ) : (
        <div className="flex flex-wrap gap-3">
          {card.images.map((im) => <Thumb key={im.id} image={im} approver={approver} onError={setError} />)}
        </div>
      )}
      <ErrorBox error={error} />
    </li>
  );
}

type Filter = "pending" | "all" | "missing";

export function ProductImageLibraryView({ library }: { library: ProductImageLibrary | null }) {
  const [filter, setFilter] = useState<Filter>("pending");
  const products = useMemo(() => library?.products ?? [], [library]);
  const counts = useMemo(() => ({
    pending: products.filter((p) => p.images.some((i) => i.review_status === "pending")).length,
    ready: products.filter((p) => p.images.some((i) => i.review_status === "approved")).length,
    missing: products.filter((p) => !p.images.some((i) => i.review_status === "approved")).length,
  }), [products]);
  const shown = products.filter((p) =>
    filter === "all" ? true
      : filter === "pending" ? p.images.some((i) => i.review_status === "pending")
        : !p.images.some((i) => i.review_status === "approved"));

  if (library === null) {
    return <p className="rounded-lg border border-border px-4 py-3 text-[13px] text-foreground-muted">제품 이미지를 불러오지 못했습니다.</p>;
  }
  const tab = (key: Filter, label: string) => (
    <button type="button" onClick={() => setFilter(key)}
            className={`rounded-full px-3 py-1 text-[12px] ${filter === key ? "bg-accent text-white" : "border border-border text-foreground-muted"}`}>
      {label}
    </button>
  );

  return (
    <section className="flex flex-col gap-3">
      <div>
        <h2 className="text-[18px] font-semibold text-foreground">제품 이미지</h2>
        <p className="text-[13px] text-foreground-muted">
          공정 컨셉 이미지를 그릴 때 대안에 나오는 회사 제품(예: AMR)의 사진을 외형 참고로 AI 에 함께 보냅니다.
          실물과 똑같이 그려지지는 않고 비슷한 모양으로 그려집니다. 승인된 사진만 쓰이며, 제품마다 먼저 승인된 한 장이 쓰입니다.
          {library.approver ? "" : " 올린 사진은 영업 관리자가 승인하면 쓰입니다."}
        </p>
        <p className="mt-1 text-[12px] text-foreground-subtle">
          제품 {products.length}개 · 사진 사용 중 {counts.ready}개 · 승인 대기 {counts.pending}개
        </p>
      </div>
      <div className="flex flex-wrap gap-2">
        {tab("pending", `승인 대기 ${counts.pending}`)}
        {tab("missing", `사용 중인 사진 없음 ${counts.missing}`)}
        {tab("all", `전체 ${products.length}`)}
      </div>
      {shown.length === 0 ? (
        <p className="rounded-lg border border-border px-4 py-3 text-[13px] text-foreground-muted">해당하는 제품이 없습니다.</p>
      ) : (
        <ul className="flex flex-col gap-3">
          {shown.map((p) => <ProductRow key={p.card_id} card={p} approver={library.approver} />)}
        </ul>
      )}
    </section>
  );
}
