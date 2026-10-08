import { Sidebar } from "@/components/shared/layout/sidebar";
import { listDomainMetas } from "@/lib/agents/registry";
import { displayName, getAllowedDomains, getCurrentUser } from "@/lib/shared/auth";
import {
  getRecentConversations,
  groupByUiDomain,
  toConversation,
} from "@/lib/shared/conversations";

export default async function ChatLayout({ children }: { children: React.ReactNode }) {
  const domains = listDomainMetas();
  const user = await getCurrentUser();
  const allowedDomains = getAllowedDomains(user);

  // RECENT — 항상 실제 DB 에서 로드. backend startup 의 ensure_seed_conversations 가
  // 7건의 가상 대화(제목/도메인은 mock-recent.ts 와 동일)를 멱등 시드하므로,
  // 기존/신규 Postgres 볼륨 어느 쪽이든 RECENT 가 비어있는 일이 없다.
  const dtos = user ? await getRecentConversations(50) : [];
  const recentList = dtos.map(toConversation);
  const recent = groupByUiDomain(recentList);

  return (
    <div className="flex h-svh overflow-hidden">
      <Sidebar
        domains={domains}
        recentByDomain={recent}
        allowedDomains={allowedDomains}
        user={
          user
            ? {
                username: user.username,
                displayName: displayName(user),
                role: user.role,
                domain: user.domain,
              }
            : null
        }
      />
      <main className="flex flex-1 flex-col bg-background">{children}</main>
    </div>
  );
}
