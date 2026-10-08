// AI 와 대화하는 화면 공용 조각(제품 추천·수주 진행) — GPT 식: 생각 과정(흘러나오는 동안 펼침, 끝나면 접힘)·실제로 한 일·답.
"use client";

import { type ReactNode, useState } from "react";
import { Icon } from "@/components/shared/ui/icon";
import { cn } from "@/lib/shared/utils";

export type AgentStep = { ok: boolean; text: string };
/** 말풍선 하나 — think: 생각 과정, steps: 코드가 검증해 실제로 한 일(✓/✗), answer: 답, live: 아직 흘러오는 중. */
export type AgentView = { think: string; steps: AgentStep[]; answer: string; live: boolean };

/** 한 줄에 JSON 하나씩 흘러오는 응답(NDJSON)을 받는 대로 넘긴다. 실패하면 화면 문구로 Error. */
export async function streamNdjson<T>(url: string, body: unknown, onEvent: (ev: T) => void): Promise<void> {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok || !r.body) {
    const d = await r.json().catch(() => ({}));
    throw new Error(typeof d?.detail === "string" ? d.detail : "요청을 처리하지 못했습니다.");
  }
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    buf += dec.decode(value ?? new Uint8Array(), { stream: !done });
    let nl: number;
    while ((nl = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (line) onEvent(JSON.parse(line) as T);
    }
    if (done) break;
  }
}

export function AiBubble({ children, wide }: { children: ReactNode; wide?: boolean }) {
  return (
    <div className="flex items-start gap-2">
      <span className="und-grad mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full text-[10px] font-bold text-white" aria-hidden>AI</span>
      <div className={cn("rounded-2xl rounded-tl-md bg-foreground/[0.05] px-3.5 py-2 text-[13px] leading-relaxed text-foreground", wide ? "w-full max-w-[92%]" : "max-w-[88%]")}>
        {children}
      </div>
    </div>
  );
}

export function AgentBubble({ m }: { m: AgentView }) {
  const [open, setOpen] = useState(false);
  const showThink = m.live ? !m.answer : open;
  return (
    <AiBubble wide>
      {m.think && (
        <div className="mb-1.5">
          <button type="button" className="inline-flex items-center gap-1 text-[11.5px] font-medium text-foreground-subtle hover:text-foreground-muted"
                  onClick={() => setOpen((v) => !v)} disabled={m.live}>
            {m.live && !m.answer ? "생각하는 중…" : "생각 과정"}
            {!m.live && <Icon name="chevron-down" className={cn("h-3 w-3 transition-transform", open && "rotate-180")} />}
          </button>
          {showThink && (
            <p className="mt-1 whitespace-pre-wrap border-l-2 border-foreground/15 pl-2.5 text-[12px] leading-relaxed text-foreground-muted">
              {m.think.trim()}
            </p>
          )}
        </div>
      )}
      {!m.think && m.live && <p className="text-[12px] text-foreground-subtle">생각하는 중…</p>}
      {m.answer && (
        <div className="flex items-end gap-0.5">
          <RichText text={m.answer.trim()} className="min-w-0 flex-1" />
          {m.live && <span className="animate-pulse">▍</span>}
        </div>
      )}
      {m.steps.length > 0 && (
        <ul className="mt-1.5 flex flex-col gap-0.5 text-[12px]">
          {m.steps.map((x, i) => (
            <li key={i} className={x.ok ? "text-emerald-700 dark:text-emerald-300" : "text-amber-700 dark:text-amber-300"}>
              {x.ok ? "✓" : "✗"} {x.text}
            </li>
          ))}
        </ul>
      )}
    </AiBubble>
  );
}

/** **강조** → 볼드 + 밑줄(사용자 2026-10-07: 중요 단어). 짝이 안 맞는 ** 는 글자 그대로(흘러오는 중일 때). */
function Inline({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*\n]+?\*\*)/g);
  return (
    <>
      {parts.map((p, i) => (p.length > 4 && p.startsWith("**") && p.endsWith("**")
        ? <strong key={i} className="font-semibold underline decoration-accent/70 decoration-2 underline-offset-[3px]">{p.slice(2, -2)}</strong>
        : <span key={i}>{p}</span>))}
    </>
  );
}

type Block = { kind: "p"; lines: string[] } | { kind: "ul"; items: string[] } | { kind: "ol"; start: number; items: string[] };
const BULLET = /^\s*[-•·]\s+/;
const NUMBER = /^\s*(\d{1,2})[.)]\s+/;

/** AI 답을 보기 좋게 — 빈 줄 = 문단, '- ' = 글머리 목록, '1) '·'1. ' = 번호 목록, 그 밖의 줄바꿈은 그대로. HTML 은 쓰지 않는다. */
export function RichText({ text, className }: { text: string; className?: string }) {
  const blocks: Block[] = [];
  for (const raw of text.split("\n")) {
    const line = raw.trimEnd();
    const last = blocks[blocks.length - 1];
    if (!line.trim()) {
      blocks.push({ kind: "p", lines: [] });
      continue;
    }
    const num = NUMBER.exec(line);
    if (BULLET.test(line)) {
      if (last?.kind === "ul") last.items.push(line.replace(BULLET, ""));
      else blocks.push({ kind: "ul", items: [line.replace(BULLET, "")] });
    } else if (num) {
      if (last?.kind === "ol") last.items.push(line.replace(NUMBER, ""));
      else blocks.push({ kind: "ol", start: Number(num[1]), items: [line.replace(NUMBER, "")] });
    } else if (last?.kind === "p" && last.lines.length) {
      last.lines.push(line);
    } else {
      blocks.push({ kind: "p", lines: [line] });
    }
  }
  const shown = blocks.filter((b) => (b.kind === "p" ? b.lines.length > 0 : b.items.length > 0));
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      {shown.map((b, i) => b.kind === "p" ? (
        <p key={i}>{b.lines.map((l, j) => <span key={j}>{j > 0 && <br />}<Inline text={l} /></span>)}</p>
      ) : b.kind === "ul" ? (
        <ul key={i} className="flex list-disc flex-col gap-0.5 pl-5 marker:text-foreground-subtle">
          {b.items.map((it, j) => <li key={j}><Inline text={it} /></li>)}
        </ul>
      ) : (
        <ol key={i} start={b.start} className="flex list-decimal flex-col gap-0.5 pl-5 marker:font-semibold marker:text-accent">
          {b.items.map((it, j) => <li key={j}><Inline text={it} /></li>)}
        </ol>
      ))}
    </div>
  );
}
