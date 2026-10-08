import { notFound } from "next/navigation";

import { ProposalWorkspace } from "@/components/domains/sales/proposal-workspace";
import { getProposalCatalog, getProposalProject } from "@/lib/shared/proposal-projects";

type PageProps = { params: Promise<{ id: string }> };

export default async function ProposalProjectPage({ params }: PageProps) {
  const { id } = await params;
  const [project, catalog] = await Promise.all([getProposalProject(id), getProposalCatalog()]);
  if (!project || !catalog) notFound();
  return <ProposalWorkspace project={project} catalog={catalog} />;
}
