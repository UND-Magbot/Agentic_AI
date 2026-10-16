"use client";

// 모든 여러 줄 칸에서 Alt+Enter = 줄바꿈(사용자 2026-10-16, 엑셀처럼). Enter 로 보내는 대화 칸에서도 보내지 않고 줄만 바꾼다.
// 창 전체에서 먼저(capture) 받아 칸의 onKeyDown(보내기)까지 가지 않게 막는다. 한 줄 입력(<input>)은 줄을 담을 수 없어 그대로.
import { useEffect } from "react";

export function AltEnterNewline() {
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key !== "Enter" || !e.altKey || e.ctrlKey || e.metaKey || e.isComposing) return;
      const el = e.target;
      if (!(el instanceof HTMLTextAreaElement) || el.disabled || el.readOnly) return;
      e.preventDefault();
      e.stopPropagation();
      // insertText 는 되돌리기(Ctrl+Z)에 남고 input 이벤트로 React onChange 도 부른다. 안 되는 브라우저는 직접 넣는다
      if (!document.execCommand("insertText", false, "\n")) {
        el.setRangeText("\n", el.selectionStart, el.selectionEnd, "end");
        el.dispatchEvent(new Event("input", { bubbles: true }));
      }
    }
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, []);
  return null;
}
