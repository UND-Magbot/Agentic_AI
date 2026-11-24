"use client";

// 기술영업 조회 전용(사용자 2026-11-24) — 재무팀 계정은 기술영업 화면을 볼 수만 있다.
// 서버(api_sales_deals.deny_readonly_write)가 GET 말고는 모두 막고, 화면은 작성을 시작하는 버튼을 숨기고 안내 띠를 보여 준다.
// 상세 안의 버튼(입금 확인·출하 기록 등)을 눌러도 서버가 '조회만 할 수 있습니다'로 막는다.

import { createContext, useContext } from "react";

export const SalesReadOnlyContext = createContext(false);

/** 지금 화면이 기술영업 조회 전용인지(재무팀) */
export function useSalesReadOnly(): boolean {
  return useContext(SalesReadOnlyContext);
}

export function SalesReadOnlyBanner() {
  return (
    <p role="status" className="rounded-xl border border-sky-500/30 bg-sky-500/[0.07] px-4 py-2.5 text-[12.5px] text-sky-800 dark:text-sky-200">
      <b>조회 전용</b> — 재무팀 계정은 기술영업 내용을 볼 수만 있습니다. 고치기·발행·확인은 영업팀에 요청해 주세요.
      (엑셀 내려받기는 그대로 쓸 수 있습니다)
    </p>
  );
}
