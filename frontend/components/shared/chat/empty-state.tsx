"use client";

import type { ReactNode } from "react";
import type { DomainAccent, IconName } from "@/lib/shared/types";
import { cn } from "@/lib/shared/utils";
import { Icon } from "@/components/shared/ui/icon";

type Props = {
  icon: IconName;
  accent: DomainAccent;
  title: string;
  tagline: string;
  description?: string;
  /** 도메인별 추천 프롬프트. 클릭 시 onPickExample 호출(보통 즉시 send). */
  examples?: string[];
  onPickExample?: (text: string) => void;
  /** 추천 질문 아래에 렌더할 도메인 전용 슬롯(예: 재무 원클릭 작업 버튼). */
  children?: ReactNode;
};

export function EmptyState({ icon, accent, title, tagline, description, examples, onPickExample, children }: Props) {
  return (
    // 세로 가운데는 my-auto 로 — justify-center 는 내용이 화면보다 길면 위쪽이 스크롤로도 안 보이게 잘린다
    <div className="und-aurora anim-fade-in flex min-h-full flex-col items-center px-6 py-12 text-center">
      <div className="relative z-[1] my-auto flex w-full flex-col items-center">
      <span className="relative" aria-hidden>
        <span className="und-glow absolute -inset-5 rounded-full" />
        <span
          className={cn(
            "relative grid h-14 w-14 place-items-center rounded-2xl text-white ring-1 ring-black/10 dark:ring-white/10",
            "shadow-[inset_0_1px_0_rgba(255,255,255,0.35),0_14px_30px_-14px_rgba(15,30,70,0.6)]",
            accent.bg,
          )}
        >
          <Icon name={icon} className="h-6 w-6" />
        </span>
      </span>
      <h1 className="mt-5 text-2xl font-bold tracking-tight">{title}</h1>
      <p className="mt-2 text-sm text-foreground-muted">{tagline}</p>
      {description && (
        <p className="mt-1 max-w-md text-sm text-foreground-subtle">{description}</p>
      )}

      {/* 추천 질문 — 클릭 시 즉시 발화. 첫 진입에서 어떤 식으로 묻는 게 좋은지 가이드한다. */}
      {examples && examples.length > 0 && onPickExample && (
        <div className="mt-7 flex w-full max-w-2xl flex-col gap-2">
          <p className="font-sans text-[12px] font-medium tracking-tight text-foreground-subtle">
            추천 질문
          </p>
          <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {examples.map((ex) => (
              <li key={ex}>
                <button
                  type="button"
                  onClick={() => onPickExample(ex)}
                  className={cn(
                    "group relative flex w-full items-start gap-2.5 overflow-hidden rounded-xl und-glass und-card-border px-3.5 py-3 text-left text-[13px] text-foreground/90 transition",
                    "shadow-[0_10px_24px_-16px_rgba(15,30,70,0.4)] hover:-translate-y-0.5 hover:shadow-[0_18px_36px_-18px_rgba(15,30,70,0.5)]",
                  )}
                >
                  <span className={cn("mt-1 h-1.5 w-1.5 shrink-0 rounded-full", accent.dot)} aria-hidden />
                  <span className="leading-snug">{ex}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {children}
      </div>
    </div>
  );
}
