import { KnowledgeReviewList } from "@/components/domains/sales/knowledge-review-list";
import { ProductImageLibraryView } from "@/components/domains/sales/product-image-library";
import { ProposalTabs } from "@/components/domains/sales/proposal-tabs";
import { getKnowledgeReviews, getProductImages } from "@/lib/shared/proposal-projects";

export default async function KnowledgeReviewPage() {
  const [items, library] = await Promise.all([getKnowledgeReviews(), getProductImages()]);
  return (
    <div className="flex h-full min-h-0 flex-col">
      <ProposalTabs />
      {/* 앱 본문은 스스로 스크롤하지 않는다 — 이 화면이 스크롤 칸을 갖는다 */}
      <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">
        <KnowledgeReviewList items={items} />
        <div className="mx-auto w-full max-w-3xl px-4 pb-10">
          <ProductImageLibraryView library={library} />
        </div>
      </div>
    </div>
  );
}
