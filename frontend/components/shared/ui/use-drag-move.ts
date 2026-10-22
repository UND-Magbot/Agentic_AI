"use client";

import { useRef, useState, type CSSProperties, type PointerEvent } from "react";

const KEEP = 80;   // 창 머리가 화면 안에 이만큼(px)은 남게

/** 창 머리를 마우스로 잡아 끌어 옮기기(사용자 2026-10-19). 창에 style, 머리에 handle 을 붙인다.
 *  CSS translate 로 옮겨 등장 애니메이션(transform)과 겹치지 않는다. 버튼·입력 칸 위에서 누르면 끌지 않는다.
 *  dragging 동안은 iframe(PDF 미리보기)이 마우스를 삼키지 않게 막는 데 쓴다. */
export function useDragMove() {
  const [pos, setPos] = useState({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const start = useRef<{ px: number; py: number; x: number; y: number; rect: DOMRect } | null>(null);

  const onPointerDown = (e: PointerEvent<HTMLElement>) => {
    if (e.button !== 0 || (e.target as HTMLElement).closest("button, a, input, select, textarea, label")) return;
    start.current = { px: e.clientX, py: e.clientY, x: pos.x, y: pos.y, rect: e.currentTarget.getBoundingClientRect() };
    e.currentTarget.setPointerCapture(e.pointerId);
    setDragging(true);
    e.preventDefault();
  };
  const onPointerMove = (e: PointerEvent<HTMLElement>) => {
    const s = start.current;
    if (!s) return;
    const dx = Math.min(Math.max(e.clientX - s.px, KEEP - s.rect.right), window.innerWidth - KEEP - s.rect.left);
    const dy = Math.min(Math.max(e.clientY - s.py, -s.rect.top), window.innerHeight - KEEP / 2 - s.rect.top);
    setPos({ x: s.x + dx, y: s.y + dy });
  };
  const end = (e: PointerEvent<HTMLElement>) => {
    if (!start.current) return;
    start.current = null;
    setDragging(false);
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
  };

  const style: CSSProperties = { translate: `${pos.x}px ${pos.y}px` };
  const handle = { onPointerDown, onPointerMove, onPointerUp: end, onPointerCancel: end, onDoubleClick: () => setPos({ x: 0, y: 0 }) };
  return { style, handle, dragging, handleClass: "cursor-move select-none touch-none" };
}
