import { notFound } from "next/navigation";

import { ProjectDetailView } from "@/components/shared/projects/project-detail-view";
import { listDomainMetas } from "@/lib/agents/registry";
import { displayName, getCurrentUser } from "@/lib/shared/auth";
import { getProjectDetail } from "@/lib/shared/projects";

type PageProps = { params: Promise<{ id: string }> };

export default async function ProjectDetailPage({ params }: PageProps) {
  const { id } = await params;
  const detail = await getProjectDetail(id);
  if (!detail) notFound();
  const domains = listDomainMetas();
  const user = await getCurrentUser();
  const userName = displayName(user);
  return (
    <ProjectDetailView
      project={detail}
      domains={domains}
      userName={userName}
    />
  );
}
