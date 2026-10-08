import { listDomainMetas } from "@/lib/agents/registry";
import { ChatPanel } from "@/components/shared/chat/chat-panel";
import { displayName, getAllowedDomains, getCurrentUser } from "@/lib/shared/auth";
import { getConversationDetail } from "@/lib/shared/conversations";
import { getProjectDetail } from "@/lib/shared/projects";
import type { ChatMessage, DomainKey } from "@/lib/shared/types";

type SearchParams = { c?: string; p?: string; q?: string };

const UI_DOMAIN_BY_DB: Record<string, DomainKey> = {
  finance: "finance",
  sales: "sales",
  develop: "dev",
  design: "design",
  all: "normal",
};

export default async function AutoChatHome({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const domains = listDomainMetas();
  const user = await getCurrentUser();
  const allowedDomains = getAllowedDomains(user);
  const userName = displayName(user);
  const sp = await searchParams;
  const cid = sp?.c?.trim();
  const pid = sp?.p?.trim();
  const initialUserMessage = sp?.q?.trim() || undefined;

  // key — `/?c=5` ↔ `/` ↔ `/?c=8` 사이를 client-side navigation 으로 이동할 때
  // ChatPanel/ChatWindow 가 같은 인스턴스로 재사용되어 messages state 가 잔존하는 문제를 막는다.
  // conversationId 가 바뀔 때(또는 새 채팅 진입 시) key 가 달라져 강제 unmount → 새 mount,
  // 진행 중 fetch 도 컴포넌트 cleanup 에서 abort 된다.

  // ?c=<id> 가 있으면 해당 대화 복원 (RECENT 클릭 진입).
  if (cid) {
    const detail = await getConversationDetail(cid);
    if (detail) {
      const initialMessages: ChatMessage[] = detail.messages.map((m) => ({
        id: String(m.id),
        role: m.role,
        content: m.content,
        createdAt: m.created_at,
        domains: m.domain ? [UI_DOMAIN_BY_DB[m.domain] ?? "normal"] : [],
      }));
      return (
        <ChatPanel
          key={`conv:${detail.id}`}
          allDomains={domains}
          allowedDomains={allowedDomains}
          userName={userName}
          conversationId={String(detail.id)}
          initialMessages={initialMessages}
          initialTitle={detail.title}
        />
      );
    }
  }

  // ?p=<projectId>&q=<firstMessage> 가 있으면 프로젝트 컨텍스트로 채팅 시작.
  // sessionStorage 대신 URL 로 전달하는 이유:
  //   React StrictMode 의 dev 더블마운트에서 첫 mount 가 storage 를 consume → 두 번째 mount
  //   는 빈 상태가 되는 문제가 있었다. URL 은 두 mount 가 동일하게 읽고, 첫 mount 의 fetch 는
  //   언마운트 정리에서 abort 되고 두 번째 mount 가 실제 스트림을 받아 정상 표시된다.
  if (pid) {
    const proj = await getProjectDetail(pid);
    if (proj) {
      const projectContext = {
        id: String(proj.id),
        instructions: proj.system_prompt ?? "",
        domainKey: (proj.domain_key ?? "normal") as DomainKey,
      };
      // key 에 firstMessage 해시까지 포함해, 같은 프로젝트 안에서 다른 첫 발화로 재진입 시
      // 강제 remount 가 일어나도록 한다.
      const k = `proj:${proj.id}:${initialUserMessage ?? ""}`;
      return (
        <ChatPanel
          key={k}
          allDomains={domains}
          allowedDomains={allowedDomains}
          userName={userName}
          projectContext={projectContext}
          initialUserMessage={initialUserMessage}
        />
      );
    }
  }

  return (
    <ChatPanel
      key="new"
      allDomains={domains}
      allowedDomains={allowedDomains}
      userName={userName}
    />
  );
}
