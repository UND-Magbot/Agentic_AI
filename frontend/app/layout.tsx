import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
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
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body className="min-h-full bg-background text-foreground">{children}</body>
    </html>
  );
}
