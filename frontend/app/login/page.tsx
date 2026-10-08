import type { Metadata } from "next";
import { LoginForm } from "@/components/shared/auth/login-form";
import { ThemeToggle } from "@/components/shared/ui/theme-toggle";
import Image from "next/image";

export const metadata: Metadata = {
  title: "로그인 — UND CORTEX",
};

type SearchParams = Promise<{ next?: string | string[] }>;

export default async function LoginPage({ searchParams }: { searchParams: SearchParams }) {
  const sp = await searchParams;
  const nextRaw = Array.isArray(sp.next) ? sp.next[0] : sp.next;
  // open redirect 방지: 절대경로(`/...`)만 허용. 그 외는 무시.
  const nextPath = nextRaw && nextRaw.startsWith("/") && !nextRaw.startsWith("//") ? nextRaw : "/";

  return (
    <div className="und-aurora relative grid min-h-svh place-items-center px-4 py-10">
      {/* 우상단 테마 토글 */}
      <div className="absolute right-4 top-4 z-[1]">
        <ThemeToggle />
      </div>

      <div className="anim-fade-in-up relative z-[1] w-full max-w-[400px]">
        {/* 브랜드 마크 — 글로우 플레이트 + 그라디언트 워드마크 */}
        <div className="mb-8 flex items-center justify-center gap-3">
          <span className="relative" aria-hidden>
            <span className="und-glow absolute -inset-3 rounded-full" />
            <span className="relative grid h-11 w-11 place-items-center overflow-hidden rounded-xl bg-surface-raised ring-1 ring-border shadow-[inset_0_1px_0_rgba(255,255,255,0.9),0_10px_24px_-12px_rgba(15,30,70,0.5)]">
              <Image
                src="/und-logo.jpg"
                alt="UND"
                width={48}
                height={24}
                className="h-auto w-9 object-contain"
                priority
              />
            </span>
          </span>
          <div className="flex flex-col">
            <span className="text-grad text-[16px] font-extrabold tracking-[0.01em]">
              UND Cortex
            </span>
            <span className="font-sans text-[11px] font-semibold uppercase tracking-[0.16em] text-foreground-subtle">
              사내 업무 에이전트
            </span>
          </div>
        </div>

        {/* 로그인 카드 — 글래스 */}
        <div className="und-glass rounded-2xl p-7 shadow-[0_24px_60px_-28px_rgba(15,30,70,0.45)]">
          <h1 className="text-[17px] font-semibold tracking-tight text-foreground">로그인</h1>
          <p className="mt-1 text-[13px] text-foreground-muted">
            사내 발급된 계정으로 접속합니다.
          </p>

          <div className="mt-6">
            <LoginForm nextPath={nextPath} />
          </div>
        </div>

        {/* 도메인 라벨 — 한글 가독성을 위해 sans 로. */}
        <div className="mt-6 flex flex-wrap items-center justify-center gap-x-3 gap-y-1.5 font-sans text-[11.5px] font-medium tracking-tight text-foreground-subtle">
          <span>도메인</span>
          <span aria-hidden>·</span>
          <span>재무관리</span>
          <span>기술영업</span>
          <span>기구설계</span>
          <span>선행개발</span>
        </div>
      </div>
    </div>
  );
}
