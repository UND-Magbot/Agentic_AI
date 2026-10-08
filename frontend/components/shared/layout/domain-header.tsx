"use client";

import type { DomainMeta } from "@/lib/shared/types";
import { cn } from "@/lib/shared/utils";
import { Icon } from "@/components/shared/ui/icon";
import { ThemeToggle } from "@/components/shared/ui/theme-toggle";

/**
 * 헤더에 표시할 라이프사이클 상태.
 *  - "standby"  : 빈 대화(메시지 0개) 또는 아이들. 도트 = accent 정상색.
 *  - "active"   : 대화 진행 중, 스트리밍 아님. 도트 = 정상색(연한 부각).
 *  - "streaming": 응답 스트리밍 중. 도트 펄스 애니메이션.
 *  - "error"    : 마지막 호출 실패. 도트 = 빨강. 다음 송신 시 자동 리셋.
 */
export type HeaderStatus = "standby" | "active" | "streaming" | "error";

type Props = {
  /** 잠금 모드면 해당 도메인. 자동 모드면 undefined. */
  domain?: DomainMeta;
  /** 라이프사이클 상태. 미지정 시 standby. */
  status?: HeaderStatus;
};

const STATUS_LABEL: Record<HeaderStatus, string> = {
  standby: "STANDBY",
  active: "ACTIVE",
  streaming: "STREAMING",
  error: "ERROR",
};

/**
 * 좌측 모드/도메인 라벨 옆에 붙는 상태 도트 + 모노 라벨.
 * 자동/잠금 모드 양쪽에서 동일 컴포넌트 사용.
 */
function StatusBadge({ status, accentDot }: { status: HeaderStatus; accentDot: string }) {
  const dotClass =
    status === "error"
      ? "bg-red-500"
      : status === "streaming"
        ? cn(accentDot, "animate-pulse")
        : accentDot;
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-surface px-2.5 py-1 font-sans text-[10.5px] font-semibold tracking-[0.06em] text-foreground-subtle ring-1 ring-border">
      <span className={cn("h-1.5 w-1.5 rounded-full", dotClass)} aria-hidden />
      {STATUS_LABEL[status]}
    </span>
  );
}

/**
 * 헤더 우상단 액세서리 — 테마 토글 / 알림 / 도움말.
 *  - 테마 토글: 라이트↔다크. localStorage 영속.
 *  - 알림: §B "알림 시스템" 도입 후 도트/배지 활성.
 *  - 도움말: 향후 단축키/사용 가이드 모달로 확장.
 */
function HeaderAccessories() {
  function notImplemented(feature: string) {
    if (typeof window !== "undefined") {
      console.info(`[und_cortex] ${feature}: 곧 지원 예정`);
    }
  }
  return (
    <div className="flex items-center gap-1">
      <ThemeToggle />
      <button
        type="button"
        onClick={() => notImplemented("notifications")}
        aria-label="알림"
        title="알림 (곧 지원 예정)"
        className="grid h-8 w-8 place-items-center rounded-md text-foreground-subtle hover:bg-foreground/5 hover:text-foreground"
      >
        <Icon name="bell" className="h-4 w-4" />
      </button>
      <button
        type="button"
        onClick={() => notImplemented("help")}
        aria-label="도움말"
        title="도움말 / 단축키 (곧 지원 예정)"
        className="grid h-8 w-8 place-items-center rounded-md text-foreground-subtle hover:bg-foreground/5 hover:text-foreground"
      >
        <Icon name="help" className="h-4 w-4" />
      </button>
    </div>
  );
}

export function DomainHeader({ domain, status = "standby" }: Props) {
  // 자동 모드: 좌·우 양 끝으로 분리해 헤더 시각 균형 확보.
  // "자동 라우팅" 라벨은 사용자 요청으로 숨김 — 상태 도트만 좌측에 노출.
  if (!domain) {
    return (
      <header className="flex items-center justify-between border-b border-border bg-surface/40 px-5 py-3 backdrop-blur">
        <StatusBadge status={status} accentDot="bg-accent" />
        <HeaderAccessories />
      </header>
    );
  }

  // 도메인 잠금: 도메인 정체성 + 상태 + 우상단 액세서리.
  return (
    <header className="flex items-center gap-3 border-b border-border bg-surface/40 px-5 py-3 backdrop-blur">
      <span
        className={cn("grid h-7 w-7 place-items-center rounded-md text-white ring-1 ring-border-strong", domain.accent.bg)}
        aria-hidden
      >
        <Icon name={domain.icon} className="h-3.5 w-3.5" />
      </span>
      <span className="text-sm font-semibold tracking-tight">{domain.label}</span>
      <span className="text-[11px] font-medium tracking-tight text-foreground-subtle">도메인 잠금</span>
      <span className="h-3 w-px bg-border" aria-hidden />
      <StatusBadge status={status} accentDot={domain.accent.dot} />
      <div className="flex-1" />
      <HeaderAccessories />
    </header>
  );
}
