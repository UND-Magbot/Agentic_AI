"use client";

// 브라우저 기본 확인·입력 창(window.confirm·prompt) 대신 쓰는 확인 팝업(사용자 2026-11-17 — 기본 창은 투박하고 맨 위에
// '192.168.0.6:3010' 같은 주소가 보였다). 화면 틀에 <ConfirmHost /> 를 한 번 두고, 어디서든 askConfirm·askText 로 연다.
//   if (await askConfirm({ title: "선금 송금 확인을 취소할까요?", message: "…", confirmLabel: "확인 취소" })) …
//   const reason = await askText({ title: "구매를 취소할까요?", placeholder: "취소 이유" })   // [취소]면 null
// 완료로 기록하는 버튼·취소(되돌리기) 버튼은 모두 한 번 더 묻는다(사용자 2026-11-18 — 실수로 눌려 완료·취소되지 않게).
import { useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/shared/ui/confirm-dialog";
import { cn } from "@/lib/shared/utils";

export interface ConfirmAsk {
  /** 굵은 질문 한 줄 */
  title: string;
  /** 설명(선택) */
  message?: string;
  /** 확인 버튼 글자 — 기본 "확인" */
  confirmLabel?: string;
  /** 취소 버튼 글자 — 기본 "취소" */
  cancelLabel?: string;
  /** 지우기·취소처럼 되돌리기 어려운 일 — 빨간 버튼 */
  danger?: boolean;
  /** 알림만 — [확인] 하나 */
  alert?: boolean;
}

export interface ChoiceAsk extends ConfirmAsk {
  /** 세 번째 버튼 글자(맨 오른쪽, 파란 버튼) */
  extraLabel: string;
}

export type Choice = "confirm" | "extra" | "cancel";

export interface TextAsk extends ConfirmAsk {
  /** 입력 칸 안내 */
  placeholder?: string;
  /** 비어 있으면 확인 버튼을 막는다 */
  required?: boolean;
  /** 처음 채워 둘 값(고치기) */
  initial?: string;
}

type Pending = (ConfirmAsk & { kind: "confirm"; extraLabel?: string; resolve: (v: boolean | "extra") => void })
  | (TextAsk & { kind: "text"; resolve: (v: string | null) => void });
const EVENT = "und-confirm";

function open(p: Pending): boolean {
  return !window.dispatchEvent(new CustomEvent<Pending>(EVENT, { detail: p, cancelable: true }));   // ConfirmHost 가 받으면 false
}

/** 확인 팝업 — [확인]이면 true, [취소]·바깥 누름·Esc 면 false. ConfirmHost 가 없으면 기본 창으로 대신한다. */
export function askConfirm(ask: ConfirmAsk): Promise<boolean> {
  return new Promise((resolve) => {
    if (!open({ ...ask, kind: "confirm", resolve: (v) => resolve(v === true) })) resolve(window.confirm([ask.title, ask.message].filter(Boolean).join("\n\n")));
  });
}

/** 버튼 셋 팝업 — [확인] "confirm", 세 번째 버튼 "extra", [취소]·바깥 누름·Esc "cancel". */
export function askChoice(ask: ChoiceAsk): Promise<Choice> {
  return new Promise((resolve) => {
    const r = (v: boolean | "extra") => resolve(v === "extra" ? "extra" : v ? "confirm" : "cancel");
    if (!open({ ...ask, kind: "confirm", resolve: r })) resolve(window.confirm([ask.title, ask.message].filter(Boolean).join("\n\n")) ? "confirm" : "cancel");
  });
}

/** 글을 받는 팝업(사유 등) — [확인]이면 적은 글(앞뒤 빈칸 정리), [취소]면 null. */
export function askText(ask: TextAsk): Promise<string | null> {
  return new Promise((resolve) => {
    if (!open({ ...ask, kind: "text", resolve })) {
      const v = window.prompt([ask.title, ask.message].filter(Boolean).join("\n\n"));
      resolve(v === null ? null : v.trim());
    }
  });
}

export function ConfirmHost() {
  const [cur, setCur] = useState<Pending | null>(null);
  const [text, setText] = useState("");
  useEffect(() => {
    const on = (e: Event) => {
      e.preventDefault();
      const d = (e as CustomEvent<Pending>).detail;
      setText(d.kind === "text" ? d.initial ?? "" : "");
      setCur(d);
    };
    window.addEventListener(EVENT, on);
    return () => window.removeEventListener(EVENT, on);
  }, []);
  const done = (ok: boolean | "extra") => {
    if (cur?.kind === "text") cur.resolve(ok === true ? text.trim() : null);
    else cur?.resolve(ok);
    setCur(null);
  };
  const needText = cur?.kind === "text" && !!cur.required && !text.trim();
  return (
    <ConfirmDialog open={!!cur} brand="UND Cortex" icon={cur?.danger ? "trash" : "help"} variant={cur?.danger ? "danger" : "default"}
                   title={cur?.title ?? ""}
                   description={cur?.kind === "text" ? (
                     <>
                       {cur.message && <span className="block">{cur.message}</span>}
                       <textarea autoFocus value={text} maxLength={500} onChange={(e) => setText(e.target.value)} placeholder={cur.placeholder}
                                 className={cn("mt-2 h-20 w-full rounded-lg border bg-surface px-3 py-2 text-[13px] text-foreground outline-none focus:border-accent",
                                   needText ? "border-red-400" : "border-border")} />
                     </>
                   ) : cur?.message}
                   confirmLabel={cur?.confirmLabel ?? "확인"} cancelLabel={cur?.cancelLabel ?? "취소"} confirmDisabled={needText}
                   hideCancel={cur?.kind === "confirm" && !!cur.alert}
                   extraLabel={cur?.kind === "confirm" ? cur.extraLabel : undefined} onExtra={() => done("extra")}
                   onClose={() => done(false)} onConfirm={() => done(true)} />
  );
}
