import { ProjectsList } from "@/components/shared/projects/projects-list";
import { listDomainMetas } from "@/lib/agents/registry";
import { getProjects } from "@/lib/shared/projects";

/**
 * 프로젝트 목록 페이지.
 * - 서버 컴포넌트에서 1차 데이터 prefetch 후 클라이언트 컴포넌트에 hydrate.
 * - 검색/정렬/생성 등 상호작용은 클라이언트 측에서 `/api/projects` 를 다시 호출.
 */
export default async function ProjectsPage() {
  const initial = await getProjects({ sortBy: "activity", limit: 100 });
  const domains = listDomainMetas();
  return <ProjectsList initial={initial} domains={domains} />;
}
