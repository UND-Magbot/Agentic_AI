import { notFound } from "next/navigation";

import { ProposalIntakeForm } from "@/components/domains/sales/proposal-intake-form";
import { ProposalTabs } from "@/components/domains/sales/proposal-tabs";
import { getProposalCatalog } from "@/lib/shared/proposal-projects";

export default async function NewProposalPage() {
  const catalog = await getProposalCatalog();
  if (!catalog) notFound();
  return (
    <div className="flex h-full min-h-0 flex-col">
      <ProposalTabs />
      <div className="min-h-0 flex-1">
        <ProposalIntakeForm assetRoles={catalog.asset_roles} essential={catalog.essential} optional={catalog.optional} />
      </div>
    </div>
  );
}
