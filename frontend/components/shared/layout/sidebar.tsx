"use client";

import { useEffect, useRef, useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import type { Conversation, DomainKey, DomainMeta } from "@/lib/shared/types";
import { cn, formatRelativeTime, groupRecentByTime } from "@/lib/shared/utils";
import { Icon } from "@/components/shared/ui/icon";
import { SearchModal } from "@/components/shared/layout/search-modal";
import { ChangePasswordModal } from "@/components/shared/auth/change-password-modal";
import { BugReportModal } from "@/components/shared/layout/bug-report-modal";
import { RecentItemMenu } from "@/components/shared/layout/recent-item-menu";
import { RenameConversationModal } from "@/components/shared/layout/rename-conversation-modal";
import { AddToProjectModal } from "@/components/shared/layout/add-to-project-modal";
import { getUserName } from "@/lib/shared/user";

type SidebarUser = {
  username: string;
  displayName: string;
  role: "superadmin" | "domain_admin" | "member" | "viewer";
  domain: "all" | "finance" | "sales" | "design" | "develop";
};

type Props = {
  domains: DomainMeta[];
  recentByDomain: Record<string, Conversation[]>;
  /**
   * 사용자가 진입 가능한 도메인 키.
   * superadmin/all → 5개 전체, 도메인 관리자 → 본인 + 'normal'.
   * 미전달 시 안전망으로 모두 허용.
   */
  allowedDomains?: readonly DomainKey[];
  user?: SidebarUser | null;
};

const ROLE_LABEL: Record<SidebarUser["role"], string> = {
  superadmin: "슈퍼관리자",
  domain_admin: "도메인관리자",
  member: "구성원",
  viewer: "뷰어",
};

const DOMAIN_LABEL: Record<SidebarUser["domain"], string> = {
  all: "전체",
  finance: "재무관리",
  sales: "기술영업",
  design: "기구설계",
  develop: "선행개발",
};

const COLLAPSED_KEY = "und_cortex_sidebar_collapsed";

export function Sidebar({ domains, recentByDomain, allowedDomains, user }: Props) {
  const domainMap = Object.fromEntries(domains.map((d) => [d.key, d])) as Record<DomainKey, DomainMeta>;
  const allowedSet = new Set<DomainKey>(
    allowedDomains ?? (["finance", "sales", "dev", "design", "normal"] as const),
  );
  const userName = user?.displayName ?? getUserName();
  const userMeta = user ? `${ROLE_LABEL[user.role]} · ${DOMAIN_LABEL[user.domain]}` : null;

  // 정렬: 즐겨찾기 먼저, 그 안에서 최근 활동 순. 다른 그룹(시간대) 들은 별도로 처리한다.
  const sortRecent = (items: Conversation[]) =>
    [...items].sort((a, b) => {
      const sa = a.starred ? 1 : 0;
      const sb = b.starred ? 1 : 0;
      if (sa !== sb) return sb - sa;
      return new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime();
    });

  // 사이드바 RECENT — 서버 prop 으로 받은 초기값을 로컬 상태로 가져와 항목 액션(별표/이름변경/삭제)
  // 시 즉시 반영. 다른 페이지로 이동·새로고침 시 서버 데이터로 자연 동기화.
  const initialRecent: Conversation[] = sortRecent(Object.values(recentByDomain).flat());
  const [recent, setRecent] = useState<Conversation[]>(initialRecent);
  // prop 의 ID set 이 바뀌면 (새 대화 생성/외부 변경) 로컬 재동기화. title/starred 같은 in-place
  // 변경은 로컬 우선이라 그대로 둔다.
  const propIdsKey = initialRecent.map((c) => c.id).sort().join(",");
  const lastPropKeyRef = useRef(propIdsKey);
  useEffect(() => {
    if (propIdsKey !== lastPropKeyRef.current) {
      lastPropKeyRef.current = propIdsKey;
      setRecent(initialRecent);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [propIdsKey]);

  // 항목 액션 모달 — 한 번에 한 개만.
  const [renameTarget, setRenameTarget] = useState<Conversation | null>(null);
  const [projectTarget, setProjectTarget] = useState<Conversation | null>(null);

  // 즐겨찾기 / 시간대 그룹 분리.
  const starredItems = recent.filter((c) => c.starred);
  const unstarredItems = recent.filter((c) => !c.starred);

  // SSR 안전: 초기엔 펼친 상태로 렌더 → mount 후 localStorage 동기화. hydration mismatch 회피.
  const [collapsed, setCollapsed] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (window.localStorage.getItem(COLLAPSED_KEY) === "1") setCollapsed(true);
  }, []);
  const toggle = () => {
    setCollapsed((prev) => {
      const next = !prev;
      try {
        window.localStorage.setItem(COLLAPSED_KEY, next ? "1" : "0");
      } catch {
        /* localStorage 미지원 환경(시크릿 등) — 무시. */
      }
      return next;
    });
  };

  // 검색 모달 — 사이드바 메뉴 항목 클릭 시 오픈. 1차 구현은 in-memory 필터.
  const [searchOpen, setSearchOpen] = useState(false);
  const openSearch = () => setSearchOpen(true);
  const closeSearch = () => setSearchOpen(false);

  // 사용자 알림 도트 — 1차 stub. 기본 표시, localStorage["und_cortex_user_alert"]==="0" 일 때 숨김.
  // §B "알림 시스템" 도입 시 store/props 로 자연 교체.
  const [hasUserAlert, setHasUserAlert] = useState(true);
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (window.localStorage.getItem("und_cortex_user_alert") === "0") setHasUserAlert(false);
  }, []);

  // 로그아웃 — /api/auth/logout 호출 후 /login 으로. 이중 클릭 방지로 loggingOut 플래그.
  const [loggingOut, setLoggingOut] = useState(false);
  async function handleLogout() {
    if (loggingOut) return;
    setLoggingOut(true);
    try {
      await fetch("/api/auth/logout", { method: "POST" });
    } catch {
      /* 서버 미응답이어도 쿠키 삭제는 클라이언트 측에서 강제. */
    }
    router.replace("/login");
    router.refresh();
  }

  // 사용자 메뉴 (위로 펼침) + 비밀번호 재설정 모달.
  const [menuOpen, setMenuOpen] = useState(false);
  const [pwOpen, setPwOpen] = useState(false);
  const [bugOpen, setBugOpen] = useState(false);
  const userMenuRef = useRef<HTMLDivElement>(null);

  // outside click / ESC 로 메뉴 닫기.
  useEffect(() => {
    if (!menuOpen) return;
    const onDown = (e: MouseEvent) => {
      if (!userMenuRef.current) return;
      if (!userMenuRef.current.contains(e.target as Node)) setMenuOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenuOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [menuOpen]);

  // RECENT 영역은 시간 의존(상대시간/그룹) 이라 SSR↔CSR mismatch 회피 위해 mount 이후에만 렌더.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  // 활성 도메인 — `/chat/<domain>` URL 패턴에서 추출. 도메인 잠금 모드 표시용.
  const pathname = usePathname() ?? "";
  const activeDomain: DomainKey | null = (() => {
    const m = pathname.match(/^\/chat\/([^/]+)/);
    if (!m) return null;
    const k = m[1] as DomainKey;
    return domainMap[k] ? k : null;
  })();
  const isChatsActive = pathname.startsWith("/chats");
  const isProjectsActive = pathname.startsWith("/projects");

  // 글로벌 단축키
  //   Ctrl/Cmd + B            — 사이드바 토글
  //   Ctrl/Cmd + K            — 검색 모달 오픈
  //   Ctrl/Cmd + Shift + O    — 새 채팅(`/` 자동 라우팅 모드로 이동)
  // input/textarea 안에서도 동작하도록 글로벌로 등록(Slack/Notion 패턴).
  const router = useRouter();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const ctrl = e.ctrlKey || e.metaKey;
      if (!ctrl) return;
      const key = e.key.toLowerCase();
      if (!e.shiftKey && !e.altKey && key === "b") {
        e.preventDefault();
        setCollapsed((prev) => {
          const next = !prev;
          try {
            window.localStorage.setItem(COLLAPSED_KEY, next ? "1" : "0");
          } catch {
            /* ignore */
          }
          return next;
        });
      } else if (!e.shiftKey && !e.altKey && key === "k") {
        e.preventDefault();
        setSearchOpen(true);
      } else if (e.shiftKey && !e.altKey && key === "o") {
        e.preventDefault();
        window.dispatchEvent(new CustomEvent("und_cortex:new_chat"));
        router.push("/");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [router]);

  return (
    <aside
      className={cn(
        "flex h-full shrink-0 flex-col border-r border-border bg-sidebar-bg text-sidebar-fg transition-[width] duration-200 ease-out",
        collapsed ? "w-[60px]" : "w-[260px]",
      )}
      data-collapsed={collapsed}
    >
      {/* 헤더 — 로고/워드마크 + 토글. collapsed 시 토글만 가운데 정렬. */}
      <div
        className={cn(
          "flex h-14 items-center border-b border-foreground/10",
          collapsed ? "justify-center px-2" : "justify-between gap-2 px-4",
        )}
      >
        {!collapsed && (
          <div className="flex min-w-0 items-center gap-2.5">
            <span className="grid h-8 w-8 shrink-0 place-items-center overflow-hidden rounded-md bg-surface-raised ring-1 ring-foreground/10">
              <Image
                src="/und-logo.jpg"
                alt="UND"
                width={48}
                height={24}
                className="h-auto w-7 object-contain"
                priority
              />
            </span>
            <span className="flex flex-col leading-none">
              <span className="text-[14px] font-bold tracking-[0.01em] text-sidebar-fg">UND Cortex</span>
              <span className="mt-1 text-[9.5px] font-semibold uppercase tracking-[0.16em] text-sidebar-fg-muted">
                Enterprise AI
              </span>
            </span>
          </div>
        )}
        <button
          type="button"
          onClick={toggle}
          aria-label={collapsed ? "사이드바 펼치기" : "사이드바 접기"}
          aria-expanded={!collapsed}
          aria-keyshortcuts="Control+B"
          title={collapsed ? "사이드바 펼치기 (Ctrl+B)" : "사이드바 접기 (Ctrl+B)"}
          className="grid h-8 w-8 shrink-0 place-items-center rounded-md text-sidebar-fg/80 hover:bg-sidebar-hover hover:text-sidebar-fg"
        >
          <Icon name="sidebar-toggle" className="h-4 w-4" />
        </button>
      </div>

      {/* 새 채팅 */}
      <div className={cn("pb-2 pt-3", collapsed ? "px-2" : "px-3")}>
        <Link
          href="/"
          // 같은 `/` 라우트에서 다시 누른 경우(URL 변화 없음)나 RSC 캐시로 인해 ChatPanel 이
          // unmount 되지 않는 케이스 대비 — custom event 로 ChatWindow 에 직접 reset 신호.
          onClick={(e) => {
            // 새 탭/미들 클릭/모디파이어 동반 클릭은 기본 동작 유지.
            if (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) return;
            if (typeof window !== "undefined") {
              window.dispatchEvent(new CustomEvent("und_cortex:new_chat"));
            }
            // URL 도 명시적으로 / 로 — 이미 / 이면 no-op.
            if (typeof window !== "undefined" && window.location.search) {
              // 검색 파라미터 있는 상태(예: `/?c=11`)면 router.push 가 효율적.
              e.preventDefault();
              router.push("/");
            }
          }}
          title={collapsed ? "새 채팅 (Ctrl+Shift+O)" : "새 채팅 (Ctrl+Shift+O)"}
          aria-label="새 채팅"
          aria-keyshortcuts="Control+Shift+O"
          className={cn(
            "und-grad flex items-center text-sm font-semibold text-white transition hover:brightness-105",
            "shadow-[0_8px_20px_-8px_rgba(37,99,235,0.6),inset_0_1px_0_rgba(255,255,255,0.35)]",
            collapsed ? "h-9 w-9 justify-center rounded-xl" : "gap-2 rounded-xl px-3 py-2.5",
          )}
        >
          <Icon name="plus" className="h-4 w-4" />
          {!collapsed && <span>새 채팅</span>}
        </Link>
      </div>

      {/* 검색 — 메뉴 버튼. 클릭 시 모달 오픈. */}
      <div className={cn("pb-2", collapsed ? "px-2" : "px-3")}>
        <button
          type="button"
          onClick={openSearch}
          aria-label="검색"
          aria-keyshortcuts="Control+K"
          title="검색 (Ctrl+K)"
          className={cn(
            "flex items-center rounded-md text-sidebar-fg/90 hover:bg-sidebar-hover",
            collapsed ? "h-9 w-9 justify-center" : "w-full gap-2 px-3 py-2 text-sm",
          )}
        >
          <Icon name="search" className="h-4 w-4" />
          {!collapsed && <span>검색</span>}
        </button>
      </div>

      {/* 채팅 — 메뉴 버튼. 클릭 시 /chats 라우트로 이동(메인 영역만 전환). */}
      <div className={cn("pb-2", collapsed ? "px-2" : "px-3")}>
        <Link
          href="/chats"
          aria-label="채팅"
          aria-current={isChatsActive ? "page" : undefined}
          title={collapsed ? "채팅" : undefined}
          className={cn(
            "relative flex items-center rounded-lg text-sm transition",
            collapsed ? "h-9 w-9 justify-center" : "gap-2 px-3 py-2",
            isChatsActive
              ? "und-grad-soft font-semibold text-accent before:absolute before:inset-y-1.5 before:left-0 before:w-[3px] before:rounded-full before:bg-accent before:content-['']"
              : "text-sidebar-fg/90 hover:bg-sidebar-hover",
          )}
        >
          <Icon name="chat" className="h-4 w-4" />
          {!collapsed && <span>채팅</span>}
        </Link>
      </div>

      {/* 프로젝트 — 대화 그룹화. 클릭 시 /projects 라우트(목록). */}
      <div className={cn("pb-2", collapsed ? "px-2" : "px-3")}>
        <Link
          href="/projects"
          aria-label="프로젝트"
          aria-current={isProjectsActive ? "page" : undefined}
          title={collapsed ? "프로젝트" : undefined}
          className={cn(
            "relative flex items-center rounded-lg text-sm transition",
            collapsed ? "h-9 w-9 justify-center" : "gap-2 px-3 py-2",
            isProjectsActive
              ? "und-grad-soft font-semibold text-accent before:absolute before:inset-y-1.5 before:left-0 before:w-[3px] before:rounded-full before:bg-accent before:content-['']"
              : "text-sidebar-fg/90 hover:bg-sidebar-hover",
          )}
        >
          <Icon name="folder" className="h-4 w-4" />
          {!collapsed && <span>프로젝트</span>}
        </Link>
      </div>

      {/* 스크롤 영역 — 도메인 + 최근 항목. 위쪽(헤더/새채팅/검색/채팅)은 고정, 이 영역만 스크롤. */}
      <div
        className={cn(
          "scroll-thin flex-1 overflow-y-auto pb-2",
          collapsed ? "px-2" : "px-3",
        )}
      >
        {/* 도메인 직링크 — 5개 도메인. /chat/<domain> 잠금 모드로 이동. */}
        <nav className="pb-2" aria-label="도메인 메뉴">
          {!collapsed && (
            <div className="mb-1.5 mt-4 px-2 text-[11px] font-semibold uppercase tracking-wider text-sidebar-fg-muted">
              도메인
            </div>
          )}
          <ul className={cn("flex", collapsed ? "flex-col items-center gap-1" : "flex-col gap-0.5")}>
            {domains
              .filter((d) => allowedSet.has(d.key))
              .map((d) => {
                const active = activeDomain === d.key;
                return (
                  <li key={d.key}>
                    <Link
                      href={`/chat/${d.key}`}
                      title={collapsed ? d.label : undefined}
                      aria-current={active ? "page" : undefined}
                      className={cn(
                        "relative flex items-center rounded-lg text-sm transition",
                        collapsed ? "h-9 w-9 justify-center" : "gap-2 px-3 py-2",
                        active
                          ? "und-grad-soft font-semibold text-accent before:absolute before:inset-y-1.5 before:left-0 before:w-[3px] before:rounded-full before:bg-accent before:content-['']"
                          : "text-sidebar-fg/90 hover:bg-sidebar-hover",
                      )}
                    >
                      <Icon name={d.icon} className={cn("h-4 w-4", active ? "text-accent" : "")} />
                      {!collapsed && <span className="truncate">{d.label}</span>}
                    </Link>
                  </li>
                );
              })}
          </ul>
        </nav>

        {/* 최근 대화 — 접힌 상태에선 숨김. mount 이후에만 그룹/상대시간 렌더(SSR mismatch 회피). */}
        {mounted && !collapsed && recent.length > 0 && (
          <>
            <div className="mb-1.5 mt-4 px-2 text-[11px] font-semibold uppercase tracking-wider text-sidebar-fg-muted">
              최근 항목
            </div>
            <div className="space-y-3">
              {/* 즐겨찾기 그룹 — 별도 헤더로 위로 분리. 비어있으면 미노출. */}
              {starredItems.length > 0 && (
                <div>
                  <div className="mb-1 flex items-center gap-1 px-1 text-[10px] font-medium tracking-tight text-foreground-subtle">
                    <Icon name="star" className="h-2.5 w-2.5 text-amber-500" />
                    즐겨찾기
                  </div>
                  <ul className="space-y-0.5">
                    {starredItems.map((c) => (
                      <RecentRow
                        key={c.id}
                        c={c}
                        d={domainMap[c.domain]}
                        onRequestRename={() => setRenameTarget(c)}
                        onRequestAddToProject={() => setProjectTarget(c)}
                        onUpdate={(next) =>
                          setRecent((prev) => sortRecent(prev.map((p) => (p.id === c.id ? next : p))))
                        }
                        onDelete={() => setRecent((prev) => prev.filter((p) => p.id !== c.id))}
                      />
                    ))}
                  </ul>
                </div>
              )}

              {/* 시간대 그룹 (즐겨찾기가 아닌 항목들). */}
              {groupRecentByTime(unstarredItems).map((group) => (
                <div key={group.key}>
                  <div className="mb-1 px-1 text-[10px] font-medium tracking-tight text-foreground-subtle">
                    {group.label}
                  </div>
                  <ul className="space-y-0.5">
                    {group.items.map((c) => (
                      <RecentRow
                        key={c.id}
                        c={c}
                        d={domainMap[c.domain]}
                        onRequestRename={() => setRenameTarget(c)}
                        onRequestAddToProject={() => setProjectTarget(c)}
                        onUpdate={(next) =>
                          setRecent((prev) => sortRecent(prev.map((p) => (p.id === c.id ? next : p))))
                        }
                        onDelete={() => setRecent((prev) => prev.filter((p) => p.id !== c.id))}
                      />
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          </>
        )}
      </div>

      {/* 사용자 영역 — 행 전체가 트리거. 클릭 시 위로 펼쳐지는 메뉴(비밀번호 재설정 / 버그 등록 / 로그아웃). */}
      <div
        ref={userMenuRef}
        className={cn(
          "relative border-t border-foreground/10 py-3",
          collapsed ? "px-2" : "px-3",
        )}
      >
        <button
          type="button"
          onClick={() => setMenuOpen((v) => !v)}
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          title={collapsed ? `${userName}${userMeta ? " · " + userMeta : ""}` : undefined}
          className={cn(
            "flex w-full items-center rounded-md py-2 text-sm transition",
            menuOpen ? "bg-sidebar-hover" : "hover:bg-sidebar-hover",
            collapsed ? "justify-center" : "gap-3 px-2",
          )}
        >
          <span className="und-grad relative grid h-9 w-9 shrink-0 place-items-center rounded-xl text-white shadow-[inset_0_1px_0_rgba(255,255,255,0.4),0_4px_10px_-4px_rgba(37,99,235,0.6)]">
            <Icon name="user" className="h-5 w-5" />
            {hasUserAlert && (
              <span
                className="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full bg-red-500 ring-2 ring-sidebar-bg"
                aria-label="새 알림 있음"
              />
            )}
          </span>
          {!collapsed && (
            <div className="flex min-w-0 flex-1 flex-col items-start text-left">
              <span className="truncate text-sm font-medium">{userName}</span>
              {userMeta && (
                <span className="truncate font-sans text-[11px] font-medium tracking-tight text-sidebar-fg-muted">
                  {userMeta}
                </span>
              )}
            </div>
          )}
          {!collapsed && (
            <Icon
              name="chevron-down"
              className={cn(
                "h-4 w-4 shrink-0 text-sidebar-fg/60 transition",
                menuOpen ? "rotate-0" : "-rotate-90",
              )}
            />
          )}
        </button>

        {/* 드롭다운 — 행 위로 펼침. collapsed 일 때는 아이콘만 표시되도록 우측에 띄움. */}
        {menuOpen && (
          <div
            role="menu"
            aria-label="사용자 메뉴"
            className={cn(
              "anim-fade-in-up absolute z-30 min-w-[180px] rounded-md border border-border bg-surface-raised p-1 shadow-lg",
              collapsed
                ? "bottom-1/2 left-full ml-2 translate-y-1/2"
                : "bottom-full left-3 right-3 mb-2",
            )}
          >
            <MenuButton
              icon="refresh"
              label="비밀번호 재설정"
              onClick={() => {
                setMenuOpen(false);
                setPwOpen(true);
              }}
            />
            <MenuButton
              icon="bug"
              label="버그 등록"
              onClick={() => {
                setMenuOpen(false);
                setBugOpen(true);
              }}
            />
            <MenuButton
              icon="logout"
              label={loggingOut ? "로그아웃 중..." : "로그아웃"}
              disabled={loggingOut}
              onClick={() => {
                setMenuOpen(false);
                void handleLogout();
              }}
              variant="danger"
            />
          </div>
        )}
      </div>

      {/* 비밀번호 재설정 모달 */}
      <ChangePasswordModal open={pwOpen} onClose={() => setPwOpen(false)} />
      {/* 버그 등록 — 누구나(사용자 2026-10-08) */}
      {bugOpen && <BugReportModal onClose={() => setBugOpen(false)} />}

      {/* 검색 모달 — 사이드바 외부에 portal 없이 fixed 로 렌더 */}
      <SearchModal
        open={searchOpen}
        onClose={closeSearch}
        recent={recent}
        domainMap={domainMap}
      />

      {/* RECENT 항목 액션 — 이름 변경 / 프로젝트에 추가 */}
      {renameTarget && (
        <RenameConversationModal
          open
          conversationId={renameTarget.id}
          initialTitle={renameTarget.title}
          onClose={() => setRenameTarget(null)}
          onRenamed={(t) => {
            setRecent((prev) =>
              sortRecent(prev.map((p) => (p.id === renameTarget.id ? { ...p, title: t } : p))),
            );
            setRenameTarget(null);
          }}
        />
      )}
      {projectTarget && (
        <AddToProjectModal
          open
          conversationId={projectTarget.id}
          conversationTitle={projectTarget.title}
          onClose={() => setProjectTarget(null)}
          onAdded={() => setProjectTarget(null)}
        />
      )}
    </aside>
  );
}

/**
 * 사이드바 RECENT 항목 한 줄. Link 본체 + 우상단 hover 시 노출되는 `…` 메뉴.
 *  - Link 와 menu 버튼이 nested 되면 anchor inside button 같은 시맨틱 문제가 생기므로,
 *    바깥 div 에 `group/relative` 를 두고 Link 와 메뉴를 sibling 으로 배치한다.
 */
function RecentRow({
  c,
  d,
  onRequestRename,
  onRequestAddToProject,
  onUpdate,
  onDelete,
}: {
  c: Conversation;
  d: DomainMeta;
  onRequestRename: () => void;
  onRequestAddToProject: () => void;
  onUpdate: (next: Conversation) => void;
  onDelete: () => void;
}) {
  return (
    <li className="group/recent relative">
      <Link
        href={`/?c=${c.id}`}
        className="relative flex flex-col gap-0.5 rounded-lg px-2.5 py-1.5 text-sm text-sidebar-fg/90 transition hover:bg-sidebar-hover"
      >
        <span className="flex items-center gap-1.5 truncate pr-7 font-medium">
          {c.starred && <Icon name="star" className="h-3 w-3 shrink-0 text-amber-500" />}
          <span className="truncate">{c.title}</span>
        </span>
        <span className="flex items-center gap-1.5 text-[11px] text-sidebar-fg-muted">
          <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", d.accent.bg)} aria-hidden />
          <span className="truncate">{d.shortLabel}</span>
          <span aria-hidden className="opacity-50">·</span>
          <span className="shrink-0 tabular-nums">{formatRelativeTime(c.updatedAt)}</span>
        </span>
      </Link>
      {/* 우상단 `…` 메뉴 — hover 시 노출. */}
      <div className="pointer-events-none absolute right-1.5 top-1.5 opacity-0 transition group-hover/recent:pointer-events-auto group-hover/recent:opacity-100 focus-within:pointer-events-auto focus-within:opacity-100">
        <RecentItemMenu
          conversation={c}
          align="right"
          onRequestRename={onRequestRename}
          onRequestAddToProject={onRequestAddToProject}
          onAction={(action) => {
            if (action.type === "deleted") onDelete();
            else if (action.type === "starred") onUpdate({ ...c, starred: action.starred });
            else if (action.type === "renamed") onUpdate({ ...c, title: action.title });
          }}
        />
      </div>
    </li>
  );
}

function MenuButton({
  icon,
  label,
  onClick,
  disabled,
  variant = "default",
}: {
  icon: "refresh" | "logout" | "bug";
  label: string;
  onClick: () => void;
  disabled?: boolean;
  variant?: "default" | "danger";
}) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "flex w-full items-center gap-2.5 rounded-sm px-2.5 py-2 text-left text-[13px] transition",
        "hover:bg-foreground/5 disabled:cursor-not-allowed disabled:opacity-50",
        variant === "danger"
          ? "text-red-600 dark:text-red-400"
          : "text-foreground",
      )}
    >
      <Icon name={icon} className="h-4 w-4" />
      <span className="flex-1 truncate">{label}</span>
    </button>
  );
}
