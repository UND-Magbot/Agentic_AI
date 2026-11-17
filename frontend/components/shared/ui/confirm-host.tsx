"use client";

// 브라우저 기본 확인 창(window.confirm) 대신 쓰는 확인 팝업(사용자 2026-11-17 — 기본 창은 투박하고 맨 위에
// '192.168.0.6:3010' 같은 주소가 보였다). 화면 틀에 <ConfirmHost /> 를 한 번 두고, 어디서든 askConfirm(...) 으로 연다.
//   if (await askConfirm({ title: "선금 송금 확인을 취소할까요?", message: "…", confirmLabel: "확인 취소" })) …
import { useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/shared/ui/confirm-dialog";

export interface ConfirmAsk {
  /** 굵은 질문 한 줄 */
  title: string;
  /** 설명(선택) */
  message?: string;
  /** 확인 버튼 글자 — 기본 "확인" */
  confirmLabel?: string;
  /** 지우기처럼 되돌리기 어려운 일 — 빨간 버튼 */
  danger?: boolean;
}

type Pending = ConfirmAsk & { resolve: (ok: boolean) => void };
const EVENT = "und-confirm";

/** 확인 팝업을 띄우고 [확인]이면 true, [취소]·바깥 누름·Esc 면 false. ConfirmHost 가 없으면 기본 창으로 대신한다. */
export function askConfirm(ask: ConfirmAsk): Promise<boolean> {
  return new Promise((resolve) => {
    const ev = new CustomEvent<Pending>(EVENT, { detail: { ...ask, resolve }, cancelable: true });
    // 받아 줄 ConfirmHost 가 없으면(preventDefault 안 됨) 예전처럼 기본 창
    if (window.dispatchEvent(ev)) resolve(window.confirm([ask.title, ask.message].filter(Boolean).join("\n\n")));
  });
}

export function ConfirmHost() {
  const [cur, setCur] = useState<Pending | null>(null);
  useEffect(() => {
    const on = (e: Event) => {
      e.preventDefault();
      setCur((e as CustomEvent<Pending>).detail);
    };
    window.addEventListener(EVENT, on);
    return () => window.removeEventListener(EVENT, on);
  }, []);
  const done = (ok: boolean) => {
    cur?.resolve(ok);
    setCur(null);
  };
  return (
    <ConfirmDialog open={!!cur} brand="UND Cortex" icon={cur?.danger ? "trash" : "help"} variant={cur?.danger ? "danger" : "default"}
                   title={cur?.title ?? ""} description={cur?.message} confirmLabel={cur?.confirmLabel ?? "확인"} cancelLabel="취소"
                   onClose={() => done(false)} onConfirm={() => done(true)} />
  );
}
