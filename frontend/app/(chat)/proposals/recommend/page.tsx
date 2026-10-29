import { ProductRecommend, type RecommendTab } from "@/components/domains/sales/product-recommend";
import { displayName, getCurrentUser } from "@/lib/shared/auth";
import { getProductCorrections } from "@/lib/shared/proposal-projects";

const TABS: RecommendTab[] = ["recommend", "quote", "order", "deals", "rentals", "units", "memory"];

// ?tab=deals 처럼 탭을 골라 열 수 있다(첫 화면 '영업 건 관리' 카드 → [영업 건 관리] 탭)
// ?tab=quote&new=manual — 첫 화면 '견적서 수기 작성' 카드 → 빈 견적서를 만들어 바로 연다
// ?tab=memory&sub=approvals — 승인 요청 알림 팝업 → [검토·승인 > 승인 요청]
// 채팅 답의 이동 버튼(사용자 2026-10-28) — ?tab=deals|rentals&deal=건 · ?tab=quote&quote=견적서 · ?tab=order&deal=건(&focus=ship 출하 칸)
export default async function ProductRecommendPage({ searchParams }: { searchParams: Promise<{ tab?: string; new?: string; sub?: string; deal?: string; quote?: string; focus?: string }> }) {
  const { tab, new: start, sub, deal, quote, focus } = await searchParams;
  const num = (v?: string) => (v && /^\d+$/.test(v) ? Number(v) : undefined);
  const [corrections, user] = await Promise.all([getProductCorrections(), getCurrentUser()]);
  return (
    // 같은 화면에 있어도 다른 이동 버튼을 누르면 그 건으로 다시 연다
    <ProductRecommend key={`${tab}-${deal}-${quote}-${focus}`} initial={corrections ?? { items: [], approver: false }}
                      initialTab={TABS.includes(tab as RecommendTab) ? (tab as RecommendTab) : undefined}
                      startManual={start === "manual"}
                      initialSub={sub === "approvals" ? "approvals" : undefined}
                      initialDeal={num(deal)} initialQuote={num(quote)} initialFocus={focus === "ship" ? "ship" : undefined}
                      writer={user ? displayName(user) : ""} />
  );
}
