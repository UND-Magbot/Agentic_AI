// 회사 제품 추천 화면의 [영업 건 관리] 탭(사용자 2026-10-06: 별도 화면이 아니라 회사 제품 추천의 탭 하나로).
// 리스트는 클라이언트에서 받아 DealList 에 넘기고, 상태 변경 뒤에는 다시 받는다.
"use client";

import { useCallback, useEffect, useState } from "react";
import { DealList } from "@/components/domains/sales/deal-list";
import type { DealBiz, DealListData } from "@/lib/shared/sales-deals";

export function DealsTab({ biz = "sale", openDealId = null, onMeeting, onOrder, onQuote }: {
  /** 열자마자 상세를 띄울 건(제품·A/S 탭에서 넘어올 때) */
  openDealId?: number | null;
  /** sale = 제품 판매 관리, rental = 제품 대여 관리(대여 견적서로 낸 건, 사용자 2026-10-22) */
  biz?: DealBiz;
  onMeeting?: (proposalId: number) => void; onOrder?: (dealId: number) => void; onQuote?: (quoteId: number) => void;
}) {
  const [data, setData] = useState<DealListData | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch(`/api/sales-deals?biz=${biz}`, { cache: "no-store" });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof d?.detail === "string" ? d.detail : "제품 영업 건을 불러오지 못했습니다.");
      setData(d as DealListData);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "제품 영업 건을 불러오지 못했습니다.");
    }
  }, [biz]);

  useEffect(() => {
    let alive = true;
    fetch(`/api/sales-deals?biz=${biz}`, { cache: "no-store" })
      .then(async (r) => {
        const d = await r.json().catch(() => ({}));
        if (!alive) return;
        if (!r.ok) setError(typeof d?.detail === "string" ? d.detail : "제품 영업 건을 불러오지 못했습니다.");
        else setData(d as DealListData);
      })
      .catch(() => { if (alive) setError("서버에 연결하지 못했습니다."); });
    return () => { alive = false; };
  }, [biz]);

  if (error) return <p className="py-16 text-center text-[13px] text-foreground-muted">{error}</p>;
  if (!data) return <p className="py-16 text-center text-[13px] text-foreground-subtle">제품 영업 건을 불러오는 중…</p>;
  return <DealList data={data} biz={biz} initialOpenId={openDealId} onRefresh={() => void load()} onMeeting={onMeeting} onOrder={onOrder} onQuote={onQuote} />;
}
