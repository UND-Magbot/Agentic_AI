import { ProductRecommend, type RecommendTab } from "@/components/domains/sales/product-recommend";
import { getProductCorrections } from "@/lib/shared/proposal-projects";

const TABS: RecommendTab[] = ["recommend", "quote", "order", "deals", "memory"];

// ?tab=deals 처럼 탭을 골라 열 수 있다(첫 화면 '영업 건 관리' 카드 → [영업 건 관리] 탭)
// ?tab=quote&new=manual — 첫 화면 '견적서 수기 작성' 카드 → 빈 견적서를 만들어 바로 연다
export default async function ProductRecommendPage({ searchParams }: { searchParams: Promise<{ tab?: string; new?: string }> }) {
  const { tab, new: start } = await searchParams;
  const corrections = await getProductCorrections();
  return (
    <ProductRecommend initial={corrections ?? { items: [], approver: false }}
                      initialTab={TABS.includes(tab as RecommendTab) ? (tab as RecommendTab) : undefined}
                      startManual={start === "manual"} />
  );
}
