import type { Metadata } from "next";
import { Geist, Geist_Mono, Noto_Sans_KR } from "next/font/google";
import { AltEnterNewline } from "@/components/shared/alt-enter-newline";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

// 한글 글꼴 — 지정하지 않으면 Windows 기본(맑은 고딕)으로 나와 흐릿하고 굵기 단계가 적다(사용자 2026-10-23: 더 선명하게).
// 영문·숫자는 Geist, 한글은 Noto Sans KR 로 이어서 쓴다.
const notoKr = Noto_Sans_KR({
  variable: "--font-kr",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700", "800"],
  display: "swap",
  preload: false,
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "UND CORTEX — 사내 업무 에이전트",
  description:
    "사내 업무 에이전트. 재무관리, 기술영업, 선행개발, 기구설계 도메인을 한 인터페이스에서.",
};

// 첫 페인트 전에 테마를 결정해 <html> 에 .dark 를 박아 FOUC(라이트→다크 깜빡임)를 방지.
// 우선순위: localStorage("und_cortex_theme") = "dark" | "light" → 없으면 prefers-color-scheme.
// 인라인 IIFE 라 React hydration 전에 동작.
const THEME_INIT_SCRIPT = `(() => { try {
  var s = localStorage.getItem('und_cortex_theme');
  var prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  var dark = s === 'dark' || (s !== 'light' && prefersDark);
  if (dark) document.documentElement.classList.add('dark');
} catch (e) { /* localStorage 접근 실패(시크릿 등) — 라이트 기본. */ } })();`;

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="ko"
      className={`${geistSans.variable} ${geistMono.variable} ${notoKr.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body className="min-h-full bg-background text-foreground">
        <AltEnterNewline />
        {children}
      </body>
    </html>
  );
}
