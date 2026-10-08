"use client";

import Image from "next/image";
import { usePathname, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ChatAttachment, ChatMessage, DomainKey, DomainMeta, IconName } from "@/lib/shared/types";
import { NEUTRAL_ACCENT } from "@/lib/shared/neutral";
import { cn } from "@/lib/shared/utils";
import { getUserName } from "@/lib/shared/user";
import { Icon } from "@/components/shared/ui/icon";
import { Composer, type ComposerAttachment } from "./composer";
import { EmptyState } from "./empty-state";
import { Message } from "./message";
import {
  FinanceFundReconcile,
  FinanceFundPlanGenerate,
  FinanceExpenseReconcile,
} from "@/components/domains/finance/quick-actions";
import { SalesHome } from "@/components/domains/sales/quick-actions";

// 일일자금수지 비교·검증 fast-path 트리거 표준 프롬프트(backend _detect_fund_reconcile_intent 와 매칭).
const FUND_RECONCILE_PROMPT = "자금계획 및 자금실적 비교, 검증 기능 수행";
const FUND_PLAN_PROMPT = "월마감 자료로 자금계획 자동작성";
// 개인카드 영수증 대조·검증 fast-path 트리거 표준 프롬프트(backend _detect_expense_reconcile_intent 와 매칭).
const EXPENSE_RECONCILE_PROMPT = "개인카드 영수증 대조·검증 기능 수행";
// 공정 개념도 fast-path 트리거 표준 프롬프트(backend _detect_concept_map_intent 와 매칭).
const CONCEPT_MAP_PROMPT = "공정 개념도 작성 기능 수행";
// 제안서 본문 fast-path 트리거 표준 프롬프트(backend _detect_proposal_body_intent 와 매칭).
const PROPOSAL_BODY_PROMPT = "제안서 본문 작성 기능 수행";

type HeaderStatus = "standby" | "active" | "streaming" | "error";

// 자동 모드 환영 화면의 '핵심 기능' 진입 카드. 클릭 시 예시 프롬프트를 즉시 발화(자동 라우팅).
// 빈 화면을 채우면서, 처음 온 사용자에게 무엇을 할 수 있는지 보여주는 역할.
// 각 카드는 채팅에서 **실제로 동작하는** 기능을 대표하고, prompt 는 신뢰성 있게 답이 나오는
// 구체적 예시로 둔다(모호한 질의는 RAG 가 '명시된 내용 없음'으로 응답할 수 있어 데모에 부적합).
const WELCOME_CARDS: { icon: IconName; title: string; desc: string; prompt: string }[] = [
  {
    icon: "spark",
    title: "사내 사칙 Q&A",
    desc: "사칙·규정을 근거 출처와 함께 정확히 답합니다.",
    // 구체적 질문 — RAG 가 준수사항 chunk 를 직접 매칭해 안정적으로 답한다.
    prompt: "연차 사용 보고는 누구에게 어떻게 해야 해?",
  },
  {
    icon: "file-spreadsheet",
    title: "Expense 보고 작성",
    desc: "영수증을 첨부하면 금액·날짜를 자동 추출해 양식을 채웁니다.",
    prompt: "이번 달 Expense 보고서 작성을 도와줘.",
  },
  {
    icon: "send",
    title: "연차 메일 작성",
    desc: "연차 사용 보고 메일을 양식·참조에 맞게 자동 작성합니다.",
    prompt: "다음 주 수요일 연차 쓸게, 보고 메일 작성해줘.",
  },
];

type Props = {
  /** 자동 라우팅 모드일 때 비워두고, 도메인 잠금 모드면 해당 도메인 전달. */
  lockedDomain?: DomainMeta;
  /** auto 모드에서 응답을 도메인별로 매핑하기 위해 모든 도메인 메타가 필요. */
  allDomains: DomainMeta[];
  /**
   * 사용자가 진입 가능한 도메인 키. 자동 모드 빈 상태의 빠른 액션 칩에서
   * 권한 없는 도메인은 disabled 처리한다. 미전달 시 모두 허용으로 간주.
   */
  allowedDomains?: readonly DomainKey[];
  /** 자동 모드 빈 상태 환영 메시지(`{userName}님, 다시 오셨네요`)용. */
  userName?: string;
  /** 라이프사이클 상태 변경 콜백(헤더 상태 라벨/도트 갱신용). */
  onStatusChange?: (status: HeaderStatus) => void;
  /** 사이드바 RECENT 에서 복원한 conversation id (string). 새 메시지는 이 대화에 append. */
  conversationId?: string;
  /** 복원 시 채워질 초기 메시지(서버에서 DB 로 읽어 전달). */
  initialMessages?: ChatMessage[];
  /** 복원 시 채워질 초기 제목. 비어있으면 첫 응답 후 자동 생성 시도. */
  initialTitle?: string;
  /**
   * 프로젝트 컨텍스트(프로젝트 상세 페이지에서 진입).
   * instructions 는 system prompt 뒤에 부착되고, 새 대화 영속화 시 자동으로 매핑된다.
   * 한 번 set 되면 ChatWindow 인스턴스 동안 유지(같은 프로젝트 안에서 후속 발화도 컨텍스트 유지).
   */
  projectContext?: { id: string; instructions: string; domainKey: DomainKey };
  /** `?q=<...>` 로 들어온 첫 발화. mount 후 자동 send. */
  initialUserMessage?: string;
};

export function ChatWindow({
  lockedDomain,
  allDomains,
  allowedDomains,
  userName,
  onStatusChange,
  conversationId,
  initialMessages,
  initialTitle,
  projectContext,
  initialUserMessage,
}: Props) {
  const allowedSet = useMemo(
    () => new Set<DomainKey>(allowedDomains ?? (["finance", "sales", "dev", "design", "normal"] as const)),
    [allowedDomains],
  );
  const [messages, setMessages] = useState<ChatMessage[]>(initialMessages ?? []);
  const [streaming, setStreaming] = useState(false);
  const [title, setTitle] = useState<string>(initialTitle ?? "");
  // 복원된 대화는 이미 제목이 있으므로 자동 생성 스킵.
  const titleRequestedRef = useRef(Boolean(initialTitle));
  const scrollRef = useRef<HTMLDivElement>(null);
  // 진행 중인 fetch 를 사용자가 정지할 수 있도록 보관. 새 send 시 교체된다.
  const abortRef = useRef<AbortController | null>(null);
  // 현재 대화 식별자. /?c=<id> 진입 시 props 로 받고, 새 대화 첫 응답 직후 생성된 id 를 보관.
  const conversationIdRef = useRef<string | null>(conversationId ?? null);
  // 프로젝트 컨텍스트(있을 때만). 이 ChatWindow 인스턴스 동안 유지되며 모든 send 에 instructions 가 부착된다.
  // 대화가 영속화될 때 project_conversations 매핑도 자동으로 추가한다.
  // 초기값은 props 에서 받아 동기화 — server 컴포넌트가 `?p=<id>` 를 읽어 채워준다.
  const projectIdRef = useRef<string | null>(projectContext?.id ?? null);
  const projectInstructionsRef = useRef<string>(projectContext?.instructions ?? "");
  // backend 가 현재 사용 중인 LLM 모델명(예: gemma3:12b). /health 에서 한 번 받아
  // 헤더 라벨과 메시지 저장 메타에 사용한다. 미수신 시 빈 값.
  const [activeModel, setActiveModel] = useState<string>("");

  const domainMap = useMemo(
    () => Object.fromEntries(allDomains.map((d) => [d.key, d])) as Record<DomainKey, DomainMeta>,
    [allDomains],
  );

  // 모드 전환(잠금 ↔ 자동, 잠금 도메인 변경) 시 새 대화로 초기화.
  // 진행 중인 fetch 가 있으면 함께 abort 하여 좀비 스트림이 새 도메인 메시지에 끼어들지 않게.
  useEffect(() => {
    abortRef.current?.abort();
    setMessages(initialMessages ?? []);
    setTitle(initialTitle ?? "");
    titleRequestedRef.current = Boolean(initialTitle);
    conversationIdRef.current = conversationId ?? null;
    // initialMessages/initialTitle/conversationId 가 바뀐 경우(다른 RECENT 클릭)에도 동기화.
    // 의존성에 lockedDomain?.key 만 두면 같은 도메인 상태에서 다른 conv 클릭 시 반영이 안 됨.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lockedDomain?.key, conversationId]);

  // 모드 전환과 동시에 에러 상태도 초기화 — 새 대화로 시작했는데 헤더가 ERROR 로 남아있는 경우 방지.
  useEffect(() => {
    setErrored(false);
  }, [lockedDomain?.key]);

  // 컴포넌트 언마운트 시 안전망 — 페이지 이탈로 응답이 더 이상 필요 없을 때 fetch 해제.
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  // 현재 backend 사용 모델명 1회 조회 — 헤더 표시/메시지 메타 기록용.
  useEffect(() => {
    let aborted = false;
    fetch("/api/health")
      .then((r) => (r.ok ? r.json() : null))
      .then((d: { active_model?: string; model?: string } | null) => {
        const name = d?.active_model ?? d?.model;
        if (!aborted && name) setActiveModel(name);
      })
      .catch(() => {});
    return () => {
      aborted = true;
    };
  }, []);

  // URL 직접 추적 — Next.js 16 의 client-side RSC 캐시가 stale props 를 흘리는 케이스 대비.
  // `/?c=11` → `/` 처럼 c 가 사라지거나 다른 값으로 바뀌면 ChatWindow 가 즉시 reset.
  // (server component 의 key prop 만으로 처리 안 되는 경우의 client-side 안전망.)
  const pathname = usePathname() ?? "/";
  const searchParams = useSearchParams();
  const urlCid = searchParams?.get("c") ?? null;
  useEffect(() => {
    // 도메인 잠금 페이지(`/chat/<domain>`) 에서는 c 추적 의미 없음 — 잠금 도메인 변경은
    // 별도 effect(`[lockedDomain?.key, conversationId]`) 가 처리.
    if (pathname.startsWith("/chat/")) return;

    const current = conversationIdRef.current;
    if (urlCid !== current) {
      abortRef.current?.abort();
      setMessages([]);
      setTitle("");
      titleRequestedRef.current = false;
      conversationIdRef.current = urlCid;
      setErrored(false);
    }
    // 의존성: URL 의 c 값. SSR 시점에는 useSearchParams 가 비어있을 수 있어 첫 렌더에는 영향 없음.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname, urlCid]);

  // 사이드바/페이지 어디서든 "새 채팅" 트리거 시 강제 reset.
  // URL 변화 없이도(이미 `/` 상태에서 다시 누른 경우) 즉시 빈 상태로 돌아가도록 하는 안전망.
  useEffect(() => {
    const onNewChat = () => {
      abortRef.current?.abort();
      setMessages([]);
      setTitle("");
      titleRequestedRef.current = false;
      conversationIdRef.current = null;
      // 프로젝트 컨텍스트도 새 채팅 시 초기화 — 사용자가 명시적으로 일반 새 채팅을 시작했으므로.
      projectIdRef.current = null;
      projectInstructionsRef.current = "";
      setErrored(false);
    };
    window.addEventListener("und_cortex:new_chat", onNewChat);
    return () => window.removeEventListener("und_cortex:new_chat", onNewChat);
  }, []);

  // 프로젝트 컨텍스트로 진입한 경우, URL 의 `?q=<...>` 가 있으면 첫 발화를 자동 진행.
  // sessionStorage 가 아니라 URL 로 전달하는 이유:
  //   React StrictMode dev 더블마운트에서 한쪽이 storage 를 consume → 실제 mount 가 빈
  //   상태가 되는 회귀가 있었다. URL 은 두 mount 가 동일하게 읽고, 첫 mount 의 fetch 는
  //   언마운트 정리에서 abort 되어 자연 폐기된다(send 의 try/catch + AbortController).
  //
  // 자동 fire 후엔 history.replaceState 로 q/p 를 정리해, 페이지 새로고침으로 인한 중복
  // 발화를 방지한다(p 도 같이 제거 — 이후 후속 발화는 projectInstructionsRef 가 유지).
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (conversationId) return;
    if (!initialUserMessage?.trim()) return;

    const text = initialUserMessage.trim();
    const target: DomainKey | undefined =
      projectContext?.domainKey && projectContext.domainKey !== "normal"
        ? (projectContext.domainKey as DomainKey)
        : undefined;
    void send(text, target ? { overrideDomain: target } : undefined);

    // 800ms 뒤 URL 정리 — StrictMode 의 두 번째 mount 도 동일 props 를 보고 fetch 를 한 번 더
    // 시도하지만, 첫 fetch 가 unmount 정리로 abort 되었기 때문에 두 번째가 실제 stream 을 받는다.
    // 양쪽 mount 가 끝난 뒤 URL 을 비워, 같은 페이지를 새로고침해도 다시 발화하지 않게 한다.
    const cleanupTimer = setTimeout(() => {
      if (typeof window === "undefined") return;
      const url = new URL(window.location.href);
      let changed = false;
      if (url.searchParams.has("q")) {
        url.searchParams.delete("q");
        changed = true;
      }
      if (url.searchParams.has("p")) {
        url.searchParams.delete("p");
        changed = true;
      }
      if (changed) {
        const next = url.pathname + (url.search || "") + url.hash;
        window.history.replaceState(window.history.state, "", next);
      }
    }, 800);
    return () => clearTimeout(cleanupTimer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 시간대 기반 인사 — Claude.ai 패턴(Good morning/afternoon/evening). SSR 시 서버 시간으로
  // 계산하면 클라이언트 타임존(KST)과 어긋날 수 있어, mount 이후에만 시간 기반으로 교체.
  // 첫 paint 는 보수적인 "안녕하세요" 로 시작 → hydration mismatch 방지.
  const [greeting, setGreeting] = useState("안녕하세요");
  useEffect(() => {
    const h = new Date().getHours();
    if (h >= 5 && h < 12) setGreeting("좋은 아침이에요");
    else if (h >= 12 && h < 18) setGreeting("좋은 오후예요");
    else if (h >= 18 && h < 23) setGreeting("좋은 저녁이에요");
    else setGreeting("안녕하세요");
  }, []);

  // 첫 user+assistant 페어가 완성되고 스트리밍 종료되면 제목 1회 자동 생성.
  useEffect(() => {
    if (titleRequestedRef.current || streaming) return;
    if (messages.length < 2) return;
    const first = messages[0];
    const second = messages[1];
    if (first?.role !== "user" || second?.role !== "assistant") return;
    if (!second.content?.trim()) return;

    // 제목 생성에는 본문만 — 끊김 안내 sentinel(`␟`) 이후 텍스트는 제거.
    // (정지로 본문이 거의 비어있는 경우엔 제목 호출 자체를 미루고 다음 응답 때 다시 시도.)
    const NOTICE_SENTINEL = "␟";
    const sentinelIdx = second.content.indexOf(NOTICE_SENTINEL);
    // 원클릭 작업의 진행 표시(␜{…}␜)는 제목 재료가 아니다 — 빼고 600자(제목이 JSON 조각이 되던 문제).
    const assistantBody = (sentinelIdx >= 0 ? second.content.slice(0, sentinelIdx) : second.content)
      .replace(/␜[\s\S]*?␜/g, " ")
      .replace(/␜[\s\S]*$/, " ")
      .trim()
      .slice(0, 600);
    if (!assistantBody.trim()) return;
    titleRequestedRef.current = true;

    const payload = {
      messages: [
        { role: "user" as const, content: first.content },
        { role: "assistant" as const, content: assistantBody },
      ],
    };
    void fetch("/api/title", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((data: { title?: string } | null) => {
        if (!data?.title) return;
        const t = data.title.trim();
        setTitle(t);
        // DB 에 conversation 이 이미 만들어졌다면 제목 동기화. 실패는 silent.
        const cid = conversationIdRef.current;
        if (cid) {
          void fetch(`/api/conversations/${encodeURIComponent(cid)}`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ title: t }),
          }).catch(() => undefined);
        }
      })
      .catch(() => {
        /* 제목 생성 실패는 silent — 본문엔 영향 없음. */
      });
  }, [messages, streaming]);

  useEffect(() => {
    // 메시지가 없을 때(도메인 첫 화면)는 위에서 시작한다 — 아래로 내리면 머리·대표 기능이 가려진다
    if (messages.length === 0) return;
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  // 헤더 라이프사이클 상태 broadcast.
  // - streaming 중: "streaming"
  // - 마지막 호출 실패: "error" (다음 send 또는 모드 전환 시 자동 리셋)
  // - 메시지 있고 idle: "active"
  // - 그 외: "standby"
  const [errored, setErrored] = useState(false);
  useEffect(() => {
    if (!onStatusChange) return;
    const status: HeaderStatus = streaming
      ? "streaming"
      : errored
        ? "error"
        : messages.length > 0
          ? "active"
          : "standby";
    onStatusChange(status);
  }, [messages.length, streaming, errored, onStatusChange]);

  async function send(
    text: string,
    opts?: {
      overrideDomain?: DomainKey;
      baseHistory?: ChatMessage[];
      regenerate?: boolean;
      attachments?: ComposerAttachment[];
    },
  ) {
    const requested: DomainKey | "auto" = opts?.overrideDomain ?? lockedDomain?.key ?? "auto";
    // baseHistory 명시 시(재생성/재라우팅): 그 history 뒤에 새 user+assistant 추가.
    // 미지정 시: 현재 messages 뒤에 추가(일반 신규 발화).
    const baseHistory: ChatMessage[] = opts?.baseHistory ?? messages;

    const atts: ChatAttachment[] | undefined =
      opts?.attachments && opts.attachments.length > 0
        ? opts.attachments.map((a) => ({
            id: a.id,
            filename: a.filename,
            mime: a.mime,
            size_bytes: a.size_bytes,
          }))
        : undefined;

    const userMsg: ChatMessage = {
      id: cryptoId(),
      role: "user",
      content: text,
      createdAt: new Date().toISOString(),
      domains: requested === "auto" ? [] : [requested],
      attachments: atts,
    };
    const assistantId = cryptoId();
    const assistantMsg: ChatMessage = {
      id: assistantId,
      role: "assistant",
      content: "",
      createdAt: new Date().toISOString(),
      domains: [],
    };
    setMessages([...baseHistory, userMsg, assistantMsg]);
    setStreaming(true);
    setErrored(false);

    // 이전 요청이 남아있으면 정리하고 새 controller 발급.
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const projectInstructions = projectInstructionsRef.current?.trim() || undefined;
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          domain: requested,
          history: baseHistory,
          text,
          // 재생성 신호 — backend 가 sampling 을 일시적으로 풀어 다른 답이 나오게 한다.
          ...(opts?.regenerate ? { regenerate: true } : {}),
          // 프로젝트 컨텍스트 — 프로젝트에서 시작된 대화에서만 채워짐.
          ...(projectInstructions ? { projectInstructions } : {}),
          // 첨부 메타(업로드 순) — /api/chat 가 system prompt 에 inject 하여 LLM 이 도구 호출
          // 시 receipt_attachment_ids 로 활용. 빈 배열은 inject 생략.
          ...(atts && atts.length > 0
            ? {
                attachments: atts.map((a) => ({
                  id: a.id,
                  filename: a.filename,
                  mime: a.mime,
                })),
              }
            : {}),
        }),
        signal: controller.signal,
      });
      if (!res.ok) {
        // 권한/검증 실패 등 비-스트림 응답. 본문(에러 문자열)을 읽어 친절한 메시지로 변환.
        const detail = await res.text().catch(() => "");
        if (res.status === 403) {
          throw new Error(
            `이 도메인에 접근 권한이 없습니다.${detail ? ` (${detail})` : ""}`,
          );
        }
        throw new Error(detail || `요청 실패 (${res.status})`);
      }
      if (!res.body) throw new Error("응답 본문 없음");

      // 응답 도메인을 헤더에서 읽어 assistant 메시지에 즉시 반영.
      const headerDomain = res.headers.get("X-Domain") ?? "";
      const respondedDomains = headerDomain
        .split(",")
        .map((s) => s.trim())
        .filter((s): s is DomainKey => s in domainMap);
      // X-Sources: base64(JSON) 형태의 RAG 출처 메타데이터.
      let sources: import("@/lib/shared/types").RagSource[] | undefined;
      const rawSources = res.headers.get("X-Sources");
      if (rawSources) {
        try {
          const decoded = atob(rawSources);
          const parsed = JSON.parse(
            // atob 은 latin1 출력 — UTF-8 한글 보존 위해 escape/decodeURIComponent 보정.
            decodeURIComponent(escape(decoded)),
          );
          if (Array.isArray(parsed) && parsed.length > 0) sources = parsed;
        } catch {
          /* 헤더 파싱 실패는 silent — sources 없이 진행. */
        }
      }
      if (respondedDomains.length > 0 || sources) {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? {
                  ...m,
                  domains: respondedDomains.length > 0 ? respondedDomains : m.domains,
                  sources: sources ?? m.sources,
                }
              : m,
          ),
        );
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let assistantBody = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value, { stream: true });
        assistantBody += chunk;
        setMessages((prev) =>
          prev.map((m) => (m.id === assistantId ? { ...m, content: m.content + chunk } : m)),
        );
      }

      // 영속화 — fire-and-forget. 실패해도 화면 흐름은 유지.
      void persistTurn({
        userText: text,
        userDomain: requested === "auto" ? null : requested,
        assistantText: assistantBody,
        assistantDomains: respondedDomains,
      });
    } catch (err) {
      // 사용자가 정지한 경우엔 누적된 본문은 유지하고 끊김 안내만 sentinel 로 덧붙인다.
      // (message.tsx 가 sentinel 을 보고 별도 박스로 렌더링)
      const aborted =
        controller.signal.aborted ||
        (err instanceof DOMException && err.name === "AbortError") ||
        (err instanceof Error && err.name === "AbortError");
      if (aborted) {
        const NOTICE_SENTINEL = "␟";
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? {
                  ...m,
                  content: `${m.content}${NOTICE_SENTINEL}응답을 정지했습니다. 다시 시도하려면 ‘재생성’ 버튼을 눌러 주세요.`,
                }
              : m,
          ),
        );
      } else {
        const msg = err instanceof Error ? err.message : String(err);
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId ? { ...m, content: `⚠ 응답 생성 실패: ${msg}` } : m,
          ),
        );
        setErrored(true);
      }
    } finally {
      setStreaming(false);
      // 이번 요청의 controller 가 여전히 현재 ref 라면 비워준다.
      if (abortRef.current === controller) abortRef.current = null;
    }
  }

  // 사용자 정지 — 진행 중인 fetch 를 abort. backend 는 stream 을 그냥 끝내면 되고
  // 별도 정리 신호는 필요 없다(요청이 도구 루프 중이어도 next yield 시점에 자연 종결).
  function stop() {
    abortRef.current?.abort();
  }

  /**
   * 한 턴(user + assistant) 영속화.
   *  - 기존 conversation 이 있으면 messages 2건을 append.
   *  - 없으면 새 conversation 을 만들고 첫 메시지 페어를 같이 보낸다.
   * 첫 user 메시지 일부를 임시 제목으로 잡고, 자동 제목 생성이 끝나면 PATCH 로 갱신한다(별도 useEffect 가 처리).
   */
  async function persistTurn(args: {
    userText: string;
    userDomain: DomainKey | null;
    assistantText: string;
    assistantDomains: DomainKey[];
  }) {
    const NOTICE_SENTINEL = "␟";
    const idx = args.assistantText.indexOf(NOTICE_SENTINEL);
    const noNotice = idx >= 0 ? args.assistantText.slice(0, idx) : args.assistantText;
    // 진행 마커(␜…␜)는 표시 전용 — DB 에 박제되면 구버전 렌더러/미가공 표시에서 새어나오므로
    // 영속화 전에 제거(짝수=완성블록 첫~마지막, 홀수=첫~끝). message.tsx extractProgress 와 동일.
    const PROGRESS_SENTINEL = "␜";
    let stripped = noNotice;
    {
      const pos: number[] = [];
      for (let p = stripped.indexOf(PROGRESS_SENTINEL); p >= 0; p = stripped.indexOf(PROGRESS_SENTINEL, p + 1)) {
        pos.push(p);
      }
      if (pos.length > 0) {
        const first = pos[0];
        const closing = pos.length % 2 === 0 ? pos[pos.length - 1] : -1;
        stripped =
          closing >= 0 ? stripped.slice(0, first) + stripped.slice(closing + 1) : stripped.slice(0, first);
      }
      stripped = stripped.replace(/\u200B/g, "").replace(/\n{3,}/g, "\n\n");
    }
    const cleanAssistant = stripped.trim();
    if (!cleanAssistant) return; // 비어있으면 영속화 의미 없음.

    // UI 5종 도메인 키 → 그대로 backend 에 보냄(/from-ui 가 변환).
    const userDomainKey = args.userDomain ?? null;
    const assistantDomainKey = args.assistantDomains[0] ?? null;

    const messages = [
      {
        role: "user" as const,
        content: args.userText,
        domain: userDomainKey,
      },
      {
        role: "assistant" as const,
        content: cleanAssistant,
        domain: assistantDomainKey,
        ...(activeModel ? { model: activeModel } : {}),
      },
    ];

    try {
      if (conversationIdRef.current) {
        // 기존 대화 → messages 두 건 append.
        for (const m of messages) {
          await fetch(`/api/conversations/${encodeURIComponent(conversationIdRef.current)}/messages`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(m),
          });
        }
      } else {
        // 신규 대화 — 임시 제목(첫 user 의 앞 30자). 자동 제목 생성 후 PATCH 로 교체.
        const tempTitle = args.userText.trim().slice(0, 30) || "새 대화";
        const r = await fetch("/api/conversations", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            title: tempTitle,
            domain_key: assistantDomainKey ?? userDomainKey ?? "normal",
            messages,
          }),
        });
        if (r.ok) {
          const data = (await r.json()) as { id?: number };
          if (data?.id) {
            conversationIdRef.current = String(data.id);
            // 프로젝트 컨텍스트로 시작된 대화면 매핑 추가. 실패는 silent.
            const pid = projectIdRef.current;
            if (pid) {
              void fetch(`/api/projects/${encodeURIComponent(pid)}/conversations`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ conversation_id: data.id }),
              }).catch(() => undefined);
            }
          }
        }
      }
    } catch {
      /* 영속화 실패는 silent — 화면 메시지는 이미 표시되어 있다. */
    }
  }

  // 어떤 assistant 메시지에 대해 다른 도메인으로 다시 묻기.
  // 직전 user/assistant 페어를 history 에서 제거한 뒤 같은 user 텍스트로 새 응답을 생성.
  function reroute(assistantId: string, target: DomainKey) {
    const idx = messages.findIndex((m) => m.id === assistantId);
    const userMsg = idx > 0 ? messages[idx - 1] : null;
    if (!userMsg || userMsg.role !== "user") return;
    const trimmed = messages.slice(0, idx - 1);
    void send(userMsg.content, { overrideDomain: target, baseHistory: trimmed });
  }

  // 같은 도메인으로 동일 user 메시지 재시도(재생성).
  // history 에 이전 user/assistant 페어가 남아있으면 모델이 "다른 답을 만들려"다가 다국어 fallback 으로
  // 흐를 수 있어, 그 페어를 잘라낸 history 로 다시 보낸다.
  // 일반 응답은 temperature 0.1 로 보수적이라 같은 입력에 같은 답이 나오므로,
  // regenerate 시점에만 backend sampling 을 풀어 다양성을 확보한다.
  function regenerate(assistantId: string) {
    const idx = messages.findIndex((m) => m.id === assistantId);
    const userMsg = idx > 0 ? messages[idx - 1] : null;
    if (!userMsg || userMsg.role !== "user") return;
    const trimmed = messages.slice(0, idx - 1);
    void send(userMsg.content, { baseHistory: trimmed, regenerate: true });
  }

  // 좋아요/싫어요 — 1차 stub. §B DB 도입 시 message_feedback 테이블 저장으로 교체.
  function handleFeedback(assistantId: string, score: "up" | "down") {
    if (typeof window !== "undefined") {
      console.info(`[und_cortex] feedback: msg=${assistantId} score=${score} (곧 지원 예정)`);
    }
  }

  const isAuto = !lockedDomain;
  const isEmpty = messages.length === 0;
  // 프로젝트에서 첫 발화로 진입한 경우, mount 시점에 send() 가 즉시 호출되며 messages 가 채워진다.
  // 그 한 프레임 사이에 환영(빈) 화면이 깜빡이는 걸 방지하기 위해 별도 placeholder 를 렌더한다.
  const isStartingFromProject = isEmpty && Boolean(initialUserMessage?.trim());

  // 모드별 표시 props.
  const accent = lockedDomain?.accent ?? NEUTRAL_ACCENT;
  // placeholder — 도메인이 placeholder 를 명시했으면 그걸, 아니면 도메인 라벨로 만든 fallback.
  const placeholder = lockedDomain
    ? lockedDomain.placeholder ?? `${lockedDomain.label}에 대해 무엇이든 물어보세요…`
    : "무엇이든 물어보세요…";
  // 모델/모드 라벨 — backend가 자동 라우팅하므로 사용자에겐 정보성 표시(클릭 비활성).
  const modelDisplay = activeModel || "AI";
  const modelLabel = lockedDomain
    ? `${modelDisplay} · ${lockedDomain.shortLabel}`
    : `${modelDisplay} · 자동`;

  return (
    <div className="flex h-full flex-col">
      <div ref={scrollRef} className="scroll-thin flex-1 overflow-y-auto">
        {isStartingFromProject ? (
          // 프로젝트 컨텍스트 첫 발화의 짧은 부트 구간 — 사용자가 입력한 첫 메시지를
          // 미리 보여주어 환영 화면이 잠깐 깜빡이는 것을 막는다.
          <div className="mx-auto flex max-w-3xl flex-col gap-5 px-4 py-8">
            <div className="anim-fade-in-up rounded-2xl bg-foreground/5 px-4 py-3 text-[14px] leading-relaxed text-foreground">
              {initialUserMessage}
            </div>
            <div className="flex items-center gap-2 text-[12px] text-foreground-subtle">
              <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-foreground/40" />
              응답을 준비하는 중…
            </div>
          </div>
        ) : isEmpty ? (
          lockedDomain ? (
            <EmptyState
              icon={lockedDomain.icon}
              accent={lockedDomain.accent}
              title={lockedDomain.label}
              tagline={lockedDomain.tagline}
              description={lockedDomain.description}
              // 기술영업은 추천 질문을 대표 기능·보조 도구 아래 칩으로 따로 그린다(SalesHome)
              examples={lockedDomain.key === "sales" ? undefined : lockedDomain.examples}
              onPickExample={(t) => void send(t)}
            >
              {lockedDomain.key === "finance" && (
                <>
                  <FinanceFundReconcile
                    disabled={streaming}
                    onRun={(atts) =>
                      void send(FUND_RECONCILE_PROMPT, { attachments: atts })
                    }
                  />
                  <FinanceFundPlanGenerate
                    disabled={streaming}
                    onRun={(atts) =>
                      void send(FUND_PLAN_PROMPT, { attachments: atts })
                    }
                  />
                  <FinanceExpenseReconcile
                    disabled={streaming}
                    onRun={(atts) =>
                      void send(EXPENSE_RECONCILE_PROMPT, { attachments: atts })
                    }
                  />
                </>
              )}
              {lockedDomain.key === "sales" && (
                <SalesHome
                  disabled={streaming}
                  examples={lockedDomain.examples}
                  onPickExample={(t) => void send(t)}
                  onRunBody={(atts) => void send(PROPOSAL_BODY_PROMPT, { attachments: atts })}
                  onRunConcept={(atts) => void send(CONCEPT_MAP_PROMPT, { attachments: atts })}
                />
              )}
            </EmptyState>
          ) : (
            // auto 모드 메인: 오로라 배경 + 중앙 히어로(로고 글로우/그라디언트 워드마크) + 기능 카드.
            <div className="und-aurora anim-fade-in flex h-full flex-col">
              <div className="relative z-[1] mx-auto flex max-w-3xl flex-1 flex-col items-center justify-center px-6 py-8 text-center">
                {/* 로고 — 글로우 헤일로 + 글로시 플레이트 */}
                <div className="relative">
                  <div className="und-glow absolute -inset-9 rounded-full" aria-hidden />
                  <Image
                    src="/und-logo.jpg"
                    alt="UND"
                    width={160}
                    height={80}
                    priority
                    className="relative h-auto w-[130px] rounded-2xl bg-surface-raised p-2.5 ring-1 ring-border shadow-[inset_0_2px_0_rgba(255,255,255,0.95),0_26px_54px_-22px_rgba(15,30,70,0.5)] dark:shadow-[inset_0_1px_0_rgba(255,255,255,0.06),0_26px_54px_-22px_rgba(0,0,0,0.85)]"
                  />
                </div>

                {/* 워드마크 — 브랜드 그라디언트 텍스트 */}
                <h1 className="text-grad font-sans mt-7 whitespace-nowrap text-5xl font-extrabold leading-none tracking-[0.01em]">
                  UND&nbsp;CORTEX
                </h1>

                <p className="mt-3 text-[11px] font-semibold uppercase tracking-[0.22em] text-foreground-subtle">
                  사내 업무 에이전트
                </p>

                {/* 그라디언트 디바이더 */}
                <div className="mt-6 flex items-center gap-3" aria-hidden>
                  <span className="h-px w-16 bg-gradient-to-r from-transparent to-accent/50" />
                  <span className="und-grad h-2 w-2 rounded-full ring-4 ring-accent-soft" />
                  <span className="h-px w-16 bg-gradient-to-l from-transparent to-accent/50" />
                </div>

                {/* 환영 문구 — 시간대 인사 + 사용자명(그라디언트 강조). */}
                <h2 className="mt-6 text-2xl font-bold tracking-tight text-foreground">
                  {greeting}, <span className="text-grad">{userName ?? getUserName()}</span>님
                </h2>
                <p className="mt-2.5 max-w-lg text-[14px] leading-relaxed text-foreground-muted">
                  단순 질의부터 재무 문서 대조·Expense 보고까지, 무엇이든 자연어로 지시하면 알맞은
                  도메인으로 자동 라우팅됩니다.
                </p>

                {/* 핵심 기능 카드 — 클릭 시 예시 프롬프트 발화. */}
                <div className="mt-8 grid w-full max-w-2xl grid-cols-1 gap-3 sm:grid-cols-3">
                  {WELCOME_CARDS.map((card) => (
                    <button
                      key={card.title}
                      type="button"
                      onClick={() => void send(card.prompt)}
                      aria-label={`${card.title} — 예시로 시작`}
                      className={cn(
                        "und-card-border und-glass group relative overflow-hidden rounded-2xl p-4 text-left",
                        "shadow-[0_12px_30px_-18px_rgba(15,30,70,0.35)] transition",
                        "hover:-translate-y-0.5 hover:shadow-[0_22px_44px_-20px_rgba(15,30,70,0.45)]",
                      )}
                    >
                      <span className="und-grad grid h-9 w-9 place-items-center rounded-xl text-white shadow-[inset_0_1px_0_rgba(255,255,255,0.4),0_6px_14px_-6px_rgba(37,99,235,0.6)]">
                        <Icon name={card.icon} className="h-[18px] w-[18px]" />
                      </span>
                      <h3 className="mt-3 text-[14px] font-bold tracking-tight text-foreground">
                        {card.title}
                      </h3>
                      <p className="mt-1 text-[12.5px] leading-snug text-foreground-subtle">
                        {card.desc}
                      </p>
                    </button>
                  ))}
                </div>
              </div>
              <div className="relative z-[1] mx-auto max-w-3xl px-6 pb-3 text-center">
                <p className="text-[11px] text-foreground-subtle">
                  질문을 입력하면 자동으로 라우팅되거나, 사이드바에서 도메인을 직접 선택할 수 있어요.
                </p>
              </div>
            </div>
          )
        ) : (
          <div className="anim-fade-in-up mx-auto flex max-w-3xl flex-col gap-5 px-4 py-8">
            {title && (
              <div className="-mt-2 mb-1 text-[15px] font-semibold tracking-tight text-foreground">
                {title}
              </div>
            )}
            {messages.map((m, idx) => {
              const showReroute = isAuto && m.role === "assistant" && m.domains.length > 0 && !streaming;
              // 재라우팅 옵션은 (a) 현재 응답 도메인 제외 + (b) 사용자 권한 도메인만.
              const others = showReroute
                ? allDomains.filter((d) => !m.domains.includes(d.key) && allowedSet.has(d.key))
                : undefined;
              // 현재 스트리밍 중이고 마지막 어시스턴트 메시지면 isStreaming=true.
              // → Message 가 trail-off "…" 부착을 보류하고, 본문은 그대로 자라게 둔다.
              const isLastAssistantStreaming =
                streaming && m.role === "assistant" && idx === messages.length - 1;
              return (
                <Message
                  key={m.id}
                  message={m}
                  domainMap={domainMap}
                  onReroute={showReroute ? (k) => reroute(m.id, k) : undefined}
                  rerouteOptions={others}
                  onRegenerate={
                    m.role === "assistant" && !streaming ? () => regenerate(m.id) : undefined
                  }
                  onFeedback={
                    m.role === "assistant" ? (s) => handleFeedback(m.id, s) : undefined
                  }
                  isStreaming={isLastAssistantStreaming}
                />
              );
            })}
          </div>
        )}
      </div>
      <Composer
        accent={accent}
        placeholder={placeholder}
        disabled={streaming}
        compact={!isEmpty}
        modelLabel={modelLabel}
        streaming={streaming}
        onSubmit={(t, atts) => send(t, { attachments: atts })}
        onStop={stop}
      />
    </div>
  );
}

function cryptoId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return Math.random().toString(36).slice(2);
}
