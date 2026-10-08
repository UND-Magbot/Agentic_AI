"use client";

import { useState } from "react";
import type { ChatMessage, DomainKey, DomainMeta } from "@/lib/shared/types";
import { ChatWindow } from "@/components/shared/chat/chat-window";
import { DomainHeader, type HeaderStatus } from "@/components/shared/layout/domain-header";

type Props = {
  /** 잠금 모드면 도메인 메타. 자동 모드면 undefined. */
  lockedDomain?: DomainMeta;
  allDomains: DomainMeta[];
  /** 사용자가 진입 가능한 도메인 키. 자동 모드 빠른 액션 칩 비활성화에 사용. */
  allowedDomains?: readonly DomainKey[];
  /** 자동 모드 빈 상태 환영 메시지(`{userName}님, 다시 오셨네요`)에 노출할 이름. */
  userName?: string;
  /** 사이드바 RECENT 클릭 시 복원할 conversation id (string). */
  conversationId?: string;
  /** 복원할 초기 메시지 — server 가 DB 에서 읽어 전달. */
  initialMessages?: ChatMessage[];
  /** 복원 시 페이지 상단에 표시할 제목(선택). */
  initialTitle?: string;
  /** 프로젝트 컨텍스트(상세 페이지에서 진입). instructions 는 system prompt 뒤에 부착되고, 새 대화는 자동으로 이 프로젝트에 매핑된다. */
  projectContext?: { id: string; instructions: string; domainKey: DomainKey };
  /** ?q=<...> 로 들어온 첫 발화. mount 후 자동으로 send. */
  initialUserMessage?: string;
};

/**
 * DomainHeader + ChatWindow 묶음.
 *
 * 헤더 라이프사이클 라벨(STANDBY/ACTIVE/STREAMING/ERROR)을 보여주려면
 * ChatWindow 의 streaming/에러 상태를 헤더로 흘려야 한다. server component 인 page 에서는
 * 공유 state 를 만들 수 없으므로 client 측 wrapper 가 필요.
 */
export function ChatPanel({
  lockedDomain,
  allDomains,
  allowedDomains,
  userName,
  conversationId,
  initialMessages,
  initialTitle,
  projectContext,
  initialUserMessage,
}: Props) {
  const [status, setStatus] = useState<HeaderStatus>(
    initialMessages && initialMessages.length > 0
      ? "active"
      : initialUserMessage
        ? "streaming"
        : "standby",
  );
  return (
    <>
      <DomainHeader domain={lockedDomain} status={status} />
      <div className="min-h-0 flex-1">
        <ChatWindow
          lockedDomain={lockedDomain}
          allDomains={allDomains}
          allowedDomains={allowedDomains}
          userName={userName}
          onStatusChange={setStatus}
          conversationId={conversationId}
          initialMessages={initialMessages}
          initialTitle={initialTitle}
          projectContext={projectContext}
          initialUserMessage={initialUserMessage}
        />
      </div>
    </>
  );
}
