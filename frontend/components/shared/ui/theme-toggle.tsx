"use client";

import { useEffect, useState } from "react";
import { cn } from "@/lib/shared/utils";
import { Icon } from "@/components/shared/ui/icon";

const THEME_KEY = "und_cortex_theme";

/**
 * 라이트/다크 토글 버튼.
 * - 첫 렌더(SSR/CSR) 모두 라이트 가정 + `suppressHydrationWarning` 으로 hydration mismatch 회피.
 * - 마운트 후 실제 `<html>`의 `.dark` 여부를 읽어 상태 동기화 (layout.tsx 의 inline init 스크립트가 이미 적용).
 * - 토글 시 localStorage("und_cortex_theme") 에 "dark" | "light" 저장 + `<html>` class 갱신.
 *   사용자가 명시적 선택을 한 후로는 OS 설정 변경에 따라가지 않는다(흔한 패턴).
 */
export function ThemeToggle({ className }: { className?: string }) {
  const [dark, setDark] = useState(false);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setDark(document.documentElement.classList.contains("dark"));
    setMounted(true);
  }, []);

  function toggle() {
    const next = !dark;
    setDark(next);
    const root = document.documentElement;
    if (next) root.classList.add("dark");
    else root.classList.remove("dark");
    try {
      window.localStorage.setItem(THEME_KEY, next ? "dark" : "light");
    } catch {
      /* localStorage 접근 실패 — 무시. */
    }
  }

  // 마운트 전엔 라이트 아이콘으로 고정해 SSR 결과와 일치 → mount 후 실제 상태에 맞춰 교체.
  const showSun = mounted && dark;
  const label = showSun ? "라이트 모드로 전환" : "다크 모드로 전환";

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={label}
      aria-pressed={dark}
      title={label}
      suppressHydrationWarning
      className={cn(
        "grid h-8 w-8 place-items-center rounded-md text-foreground-subtle hover:bg-foreground/5 hover:text-foreground",
        className,
      )}
    >
      <Icon name={showSun ? "sun" : "moon"} className="h-4 w-4" />
    </button>
  );
}
