import { notFound, redirect } from "next/navigation";
import { listDomainMetas, tryGetDomain } from "@/lib/agents/registry";
import { ChatPanel } from "@/components/shared/chat/chat-panel";
import { canAccessDomain, displayName, getAllowedDomains, getCurrentUser } from "@/lib/shared/auth";
import type { DomainKey } from "@/lib/shared/types";

type Params = { domain: string };

export default async function DomainChatPage({ params }: { params: Promise<Params> }) {
  const { domain } = await params;
  const mod = tryGetDomain(domain);
  if (!mod) notFound();

  // 도메인 접근 권한 가드 — UI 칩/사이드바 비활성화로 1차 차단되지만, URL 직접 입력에도 대응.
  // 권한 없으면 자동 라우팅 모드(`/`)로 돌려보낸다(404 보다 친절).
  const user = await getCurrentUser();
  if (!canAccessDomain(user, mod.meta.key as DomainKey)) {
    redirect("/");
  }

  const allDomains = listDomainMetas();
  const allowedDomains = getAllowedDomains(user);
  const userName = displayName(user);
  // 도메인 전환(`/chat/finance` → `/chat/sales` 또는 `/chat/finance` → `/`)간
  // ChatPanel 인스턴스 재사용으로 messages 가 잔존하는 문제 방지.
  return (
    <ChatPanel
      key={`locked:${mod.meta.key}`}
      lockedDomain={mod.meta}
      allDomains={allDomains}
      allowedDomains={allowedDomains}
      userName={userName}
    />
  );
}
