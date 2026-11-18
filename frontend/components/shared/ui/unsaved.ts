"use client";

// 저장 안 한 작성 칸 지키기(사용자 2026-11-18 — 입고 기록을 쓰다 실수로 상세가 닫혀 적던 내용이 사라짐).
// 1) useUnsaved: 작성 중인 칸이 "저장 안 한 내용이 있음"을 알린다 → 상세를 닫을 때(Esc·바깥 누름·×) confirmLeave 가 묻는다.
// 2) readDraft·writeDraft·clearDraft: 손이 많이 가는 칸(입고·출하 기록)은 적던 내용을 이 PC 브라우저에 잠깐 보관했다가
//    다시 열면 "이어서 쓸까요?" — DB 에는 저장하지 않는다(저장은 그 칸의 [저장] 버튼).
// 3) 닫을 때 [변경사항 저장](사용자 2026-11-18): 칸마다 넘긴 save(그 칸의 [저장] 버튼과 같은 일)를 차례로 부르고,
//    저장된 칸이 목록에서 빠지면 닫는다. 빠지지 않은 칸(필수 칸 비어 있음·저장 실패)이 있으면 알리고 상세는 열어 둔다.
import { useEffect, useRef } from "react";

import { askChoice, askConfirm } from "@/components/shared/ui/confirm-host";

/** 칸의 [저장] — 지금 저장할 수 없으면(버튼이 막힌 상태) null. false 를 돌려주면 저장하지 않은 것(확인 창에서 취소 등). */
export type UnsavedSave = (() => unknown) | null;

interface Entry {
  label: string;
  save: () => UnsavedSave;
}

const open = new Map<symbol, Entry>();
/** 저장한 칸이 닫히거나(다시 불러온 내용과 같아져) 목록에서 빠지기를 기다리는 최대 시간 */
const SAVE_WAIT_MS = 5000;

/** dirty 인 동안 label(예: "입고 기록")을 '저장 안 한 칸'으로 올려 둔다. 칸이 닫히거나 저장되면 빠진다. */
export function useUnsaved(label: string, dirty: boolean, save: UnsavedSave = null): void {
  const saveRef = useRef(save);
  useEffect(() => { saveRef.current = save; });
  useEffect(() => {
    if (!dirty) return;
    const k = Symbol(label);
    open.set(k, { label, save: () => saveRef.current });
    return () => { open.delete(k); };
  }, [label, dirty]);
}

const labels = (es: Entry[]) => [...new Set(es.map((e) => e.label))].join(", ");

/** 상세를 닫아도 되는지 — 저장 안 한 칸이 있으면 UND Cortex 팝업으로 묻는다(Esc·[계속 작성]이면 false). */
export async function confirmLeave(): Promise<boolean> {
  if (!open.size) return true;
  const choice = await askChoice({
    title: "작성 중인 내용이 저장되지 않았습니다",
    message: `${labels([...open.values()])} — 저장하지 않고 닫을까요? 입고·출하 기록은 다시 열면 이어서 쓸 수 있습니다.`,
    confirmLabel: "저장하지 않고 닫기", cancelLabel: "계속 작성", extraLabel: "변경사항 저장", danger: true,
  });
  if (choice === "extra") return saveAll();
  return choice === "confirm";
}

async function saveAll(): Promise<boolean> {
  const failed = new Set<Entry>();
  for (const e of [...open.values()]) {
    const save = e.save();
    try {
      if (!save || (await save()) === false) failed.add(e);
    } catch {
      failed.add(e);
    }
  }
  // 저장에 성공한 칸은 닫히거나 저장된 내용과 같아져 목록에서 빠진다 — 그때까지 잠깐 기다린다
  const waiting = () => [...open.values()].some((e) => !failed.has(e));
  for (let t = 0; waiting() && t < SAVE_WAIT_MS; t += 100) await new Promise((r) => setTimeout(r, 100));
  if (!open.size) return true;
  await askConfirm({
    title: "저장하지 못한 칸이 있습니다",
    message: `${labels([...open.values()])} — 빨간 칸(필수)이나 안내를 확인한 뒤 그 칸에서 저장해 주세요.`,
    confirmLabel: "확인", alert: true,
  });
  return false;
}

const PREFIX = "und-draft:";
/** 보관 기간 — 오래된 작성 내용은 버린다 */
const KEEP_MS = 7 * 24 * 60 * 60 * 1000;

export interface Draft<T> {
  value: T;
  /** 마지막으로 적은 시각(ISO) */
  at: string;
}

export function readDraft<T>(key: string): Draft<T> | null {
  try {
    const raw = localStorage.getItem(PREFIX + key);
    if (!raw) return null;
    const d = JSON.parse(raw) as Draft<T>;
    if (!d?.at || Date.now() - Date.parse(d.at) > KEEP_MS) {
      localStorage.removeItem(PREFIX + key);
      return null;
    }
    return d;
  } catch {
    return null;
  }
}

export function writeDraft<T>(key: string, value: T): void {
  try {
    localStorage.setItem(PREFIX + key, JSON.stringify({ value, at: new Date().toISOString() }));
  } catch { /* 보관 못 해도 작성은 계속 */ }
}

export function clearDraft(key: string): void {
  try {
    localStorage.removeItem(PREFIX + key);
  } catch { /* 무시 */ }
}

/** 보관 시각 → '11.18 14:05' */
export function draftTime(at: string): string {
  const d = new Date(at);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}.${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
