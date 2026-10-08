// sales 전용 퀵액션 + 원클릭 작업.
// 다른 도메인 컴포넌트를 import 하지 않는다(공통 shared 타입만 참조).
"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import type { ComposerAttachment } from "@/components/shared/chat/composer";
import { Icon } from "@/components/shared/ui/icon";
import type { IconName } from "@/lib/shared/types";
import { cn } from "@/lib/shared/utils";

export function SalesQuickActions({ onPick }: { onPick: (text: string) => void }) {
  const actions = ["이번주 신규 리드", "다음 주 미팅 요약", "정체된 딜 진단"];
  return (
    <div className="flex flex-wrap gap-2">
      {actions.map((a) => (
        <button
          key={a}
          type="button"
          onClick={() => onPick(a)}
          className="rounded-full border border-border bg-surface px-3 py-1 text-xs text-foreground-muted hover:text-foreground"
        >
          {a}
        </button>
      ))}
    </div>
  );
}

// backend request_files.MAX_REQUEST_CHARS 와 같은 값.
const MAX_REQUEST_CHARS = 4000;
const CONCEPT_EXAMPLE =
  "한화로봇 14kg Class 100 되는지 확인. 기존 전용장비에서 센서 쓰던 것을 비전으로 대체, " +
  "보틀 목이 기울어진 경우 검출. 로봇 투입 → 비전으로 X,Y,Z 센터 잡은 후 너트러너로 뚜껑 풀기 → " +
  "액주입장비로 이동, 비전 XY 확인 후 병 놓기 → 필링 후 뚜껑 잠그기. 약액 주입 210초, 빈 병 1.5kg, 액 3.7kg.";
const PROPOSAL_EXAMPLE =
  "OO금속 소결 공정. 완성 파레트(최대 700kg)를 작업자가 핸드리프트로 F구간→D구간 이송, 1회 6분 소요. " +
  "바닥에 윤활유 누출, 문턱·경사 있음. ERP 연동 희망. AMR 도입 검토 요청, 대수·동선 제안 필요.";

/** 요청 원문을 .txt 첨부로 올린다 — 첨부 소유자가 결과 파일 소유자가 된다. */
async function uploadRequest(text: string, filename: string): Promise<ComposerAttachment> {
  const fd = new FormData();
  fd.append("file", new File([text], filename, { type: "text/plain" }));
  const r = await fetch("/api/attachments", { method: "POST", body: fd });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    throw new Error(typeof data?.detail === "string" ? data.detail : "요청 내용 업로드 실패");
  }
  return { id: data.id, filename: data.filename, mime: data.mime, size_bytes: data.size_bytes };
}

/** 요청 원문 붙여넣기형 원클릭 작업 한 종류의 문구. */
interface RequestTask {
  id: string;
  title: string;
  cardDescription: string;
  icon: IconName;
  modalDescription: string;
  example: string;
  filename: string;
  submitLabel: string;
}

const CONCEPT_TASK: RequestTask = {
  id: "concept-map",
  title: "공정 개념도 만들기",
  cardDescription:
    "고객 요청 내용을 붙여넣으면 공정·설비 배치·비전·검토 사항을 담은 개념도(PNG + draw.io 원본)를 만듭니다.",
  icon: "ruler",
  modalDescription:
    "고객 요청 내용을 그대로 붙여넣어 주세요. 요청에 없는 수치는 지어내지 않고 표에 \"확인 필요\"로 남깁니다. " +
    "품질 우선 모델이라 수 분(길면 10분 가까이) 걸립니다.",
  example: CONCEPT_EXAMPLE,
  filename: "개념도_요청.txt",
  submitLabel: "개념도 만들기",
};

const PROPOSAL_TASK: RequestTask = {
  id: "proposal-body",
  title: "제안서 빠른 초안 (기존 방식)",
  cardDescription:
    "고객 요청 내용을 붙여넣으면 요청 항목을 빠짐없이 반영하고 관련 사례만 참고해 제안서 초안(PowerPoint 디자인판·심플판)을 만듭니다.",
  icon: "edit",
  modalDescription:
    "고객 요청·현장 메모를 그대로 붙여넣어 주세요. 요청에 없는 수치는 \"(수치 확인 필요)\"로 가리고, " +
    "참고한 사례는 출처와 함께 표시합니다. 디자인판에는 적용 컨셉 이미지(외부 생성, 고객사명 등은 가려서 전송)가 들어갑니다. " +
    "요청 반영 검토·보완과 이미지 생성까지 보통 2~4분 걸립니다.",
  example: PROPOSAL_EXAMPLE,
  filename: "제안서_요청.txt",
  submitLabel: "초안 만들기",
};

/** 대표 기능 카드 — 기능마다 색·흐름을 달리해 한눈에 구분되게(사용자 2026-10-01: 부가 기능처럼 보이고 서로 구별이 안 됨). */
const FEATURES: {
  href: string; title: string; desc: string; icon: IconName; steps: string[];
  tone: { band: string; icon: string; chip: string; cta: string; ring: string };
}[] = [
  {
    href: "/proposals/new",
    title: "제안서 만들기",
    desc: "고객 메일·자료를 넣으면 AI 가 필요한 정보를 뽑고, 빠진 것만 물어 가며 컨셉 이미지와 제안서(PPT)를 만듭니다.",
    icon: "edit",
    steps: ["자료 입력", "핵심 질문", "컨셉 확정", "PPT 완성"],
    tone: {
      band: "from-blue-600 to-indigo-500",
      icon: "bg-white/20 text-white",
      chip: "bg-blue-500/10 text-blue-700 dark:text-blue-300",
      cta: "text-blue-700 dark:text-blue-300",
      ring: "hover:ring-blue-500/40",
    },
  },
  {
    href: "/proposals/recommend",
    title: "회사 제품 추천",
    desc: "대상물·무게·로봇·현장 조건을 답하면 AI 가 회사 제품 DB 에서 가장 맞는 제품 하나를 이유와 함께 골라 줍니다.",
    icon: "search",
    steps: ["조건 Q&A", "AI 추천", "묻기·바로잡기"],
    tone: {
      band: "from-emerald-600 to-teal-500",
      icon: "bg-white/20 text-white",
      chip: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
      cta: "text-emerald-700 dark:text-emerald-300",
      ring: "hover:ring-emerald-500/40",
    },
  },
  {
    // 견적서 수기 작성(사용자 2026-10-08) — AI 추천이 아직 없는 제품(그리퍼·AMR 등)도 견적서부터 시작해 같은 흐름으로 관리
    href: "/proposals/recommend?tab=quote&new=manual",
    title: "견적서 수기 작성",
    desc: "AI 추천이 아직 없는 제품(그리퍼·AMR 등)도 견적서부터 바로 — 품목을 직접 넣거나 단가표에서 고르고, 발행하면 건 번호가 붙어 영업 건 관리로 이어집니다.",
    icon: "edit",
    steps: ["품목 입력", "견적 발행", "영업 건 관리"],
    tone: {
      band: "from-violet-600 to-fuchsia-500",
      icon: "bg-white/20 text-white",
      chip: "bg-violet-500/10 text-violet-700 dark:text-violet-300",
      cta: "text-violet-700 dark:text-violet-300",
      ring: "hover:ring-violet-500/40",
    },
  },
  {
    href: "/proposals/recommend?tab=deals",     // 회사 제품 추천 화면의 [제품 영업 건 관리] 탭(사용자 2026-10-06)
    title: "제품 영업 건 관리",
    desc: "제품 판매 건을 견적부터 수주·세금계산서·입금·출하까지 한 건씩 리스트로 관리합니다. 수주 확정·계산서·입금·출하를 기록하면 단계가 바뀝니다.",
    icon: "file-spreadsheet",
    steps: ["견적", "수주", "거래명세서·입금", "출하"],
    tone: {
      band: "from-amber-600 to-orange-500",
      icon: "bg-white/20 text-white",
      chip: "bg-amber-500/10 text-amber-700 dark:text-amber-300",
      cta: "text-amber-700 dark:text-amber-300",
      ring: "hover:ring-amber-500/40",
    },
  },
];

/**
 * 기술영업 첫 화면 — 대표 기능(제안서 만들기·회사 제품 추천)을 크게, 보조 도구는 작게 따로, 추천 질문은 맨 아래 칩으로.
 * 제안서 만들기는 자료 입력 화면(/proposals/new)에서 자료 읽기 → 핵심 질문 → 컨셉 이미지 승인 → 구성 확인 → 제작으로 이어진다.
 */
export function SalesHome({ disabled, examples, onPickExample, onRunBody, onRunConcept }: {
  disabled?: boolean;
  examples?: string[];
  onPickExample: (text: string) => void;
  onRunBody: (attachments: ComposerAttachment[]) => void;
  onRunConcept: (attachments: ComposerAttachment[]) => void;
}) {
  return (
    // 아래 여백 — 화면 하단에 떠 있는 입력창과 카드가 붙거나 겹치지 않게
    // 대표 기능 4개가 한 줄에(사용자 2026-10-08: 제품 추천 옆에 견적서 수기 작성) — 넓은 화면 4열, 좁으면 2열
    <div className="mt-6 flex w-full max-w-6xl flex-col gap-8 pb-36 text-left">
      <section aria-labelledby="sales-main">
        <h2 id="sales-main" className="mb-3 text-[12px] font-semibold uppercase tracking-[0.14em] text-foreground-subtle">
          대표 기능
        </h2>
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          {FEATURES.map((f) => (
            <Link
              key={f.href}
              href={f.href}
              aria-disabled={disabled}
              className={cn(
                "group flex flex-col overflow-hidden rounded-2xl bg-surface-raised ring-1 ring-border transition",
                "shadow-[0_14px_34px_-22px_rgba(15,30,70,0.45)]",
                disabled ? "pointer-events-none opacity-60" : cn("hover:-translate-y-1 hover:shadow-[0_24px_44px_-24px_rgba(15,30,70,0.55)]", f.tone.ring),
              )}
            >
              <div className={cn("flex items-center gap-3 bg-gradient-to-r px-5 py-4 text-white", f.tone.band)}>
                <span className={cn("grid h-10 w-10 place-items-center rounded-xl", f.tone.icon)}>
                  <Icon name={f.icon} className="h-5 w-5" />
                </span>
                <span className="text-[17px] font-bold tracking-tight">{f.title}</span>
              </div>
              <div className="flex flex-1 flex-col gap-4 px-5 py-4">
                <p className="text-[13px] leading-relaxed text-foreground-muted">{f.desc}</p>
                <ol className="flex flex-wrap items-center gap-1.5 text-[11.5px] font-medium">
                  {f.steps.map((s, i) => (
                    <li key={s} className="flex items-center gap-1.5">
                      {i > 0 && <span className="text-foreground-subtle" aria-hidden>›</span>}
                      <span className={cn("rounded-full px-2 py-0.5", f.tone.chip)}>{s}</span>
                    </li>
                  ))}
                </ol>
                <span className={cn("mt-auto inline-flex items-center gap-1 text-[13px] font-semibold", f.tone.cta)}>
                  시작하기 <span className="transition group-hover:translate-x-0.5" aria-hidden>→</span>
                </span>
              </div>
            </Link>
          ))}
        </div>
      </section>

      <section aria-labelledby="sales-tools">
        <h2 id="sales-tools" className="mb-3 text-[12px] font-semibold uppercase tracking-[0.14em] text-foreground-subtle">
          보조 도구
        </h2>
        <div className="grid gap-3 md:grid-cols-2">
          <RequestTaskCard task={PROPOSAL_TASK} onRun={onRunBody} disabled={disabled} />
          <RequestTaskCard task={CONCEPT_TASK} onRun={onRunConcept} disabled={disabled} />
        </div>
      </section>

      {examples && examples.length > 0 && (
        <section aria-labelledby="sales-ask">
          <h2 id="sales-ask" className="mb-3 text-[12px] font-semibold uppercase tracking-[0.14em] text-foreground-subtle">
            AI 에게 바로 물어보기
          </h2>
          <div className="flex flex-wrap gap-2">
            {examples.map((ex) => (
              <button key={ex} type="button" disabled={disabled} onClick={() => onPickExample(ex)}
                      className="rounded-full border border-border bg-surface px-3 py-1.5 text-[12.5px] text-foreground-muted transition hover:border-accent/40 hover:text-foreground disabled:opacity-60">
                {ex}
              </button>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

function RequestTaskCard({
  task,
  onRun,
  disabled,
}: {
  task: RequestTask;
  onRun: (attachments: ComposerAttachment[]) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);

  // 보조 도구 — 대표 기능보다 작고 차분하게(색 띠 없이 회색 톤)
  return (
    <div className="w-full text-left">
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen(true)}
        className={cn(
          "flex h-full w-full items-start gap-3 rounded-xl border border-border bg-surface px-4 py-3 text-left transition",
          disabled ? "cursor-not-allowed opacity-60" : "hover:border-foreground/25 hover:bg-foreground/[0.03]",
        )}
      >
        <span className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-foreground/[0.06] text-foreground-muted">
          <Icon name={task.icon} className="h-4 w-4" />
        </span>
        <span className="flex min-w-0 flex-col gap-0.5">
          <span className="text-[13.5px] font-semibold text-foreground">{task.title}</span>
          <span className="text-[12px] leading-snug text-foreground-subtle">{task.cardDescription}</span>
        </span>
      </button>

      {open && <RequestModal task={task} onClose={() => setOpen(false)} onRun={onRun} />}
    </div>
  );
}

function RequestModal({
  task,
  onClose,
  onRun,
}: {
  task: RequestTask;
  onClose: () => void;
  onRun: (attachments: ComposerAttachment[]) => void;
}) {
  const [text, setText] = useState("");
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const trimmed = text.trim();
  const tooLong = trimmed.length > MAX_REQUEST_CHARS;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !uploading) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [uploading, onClose]);

  async function start() {
    if (!trimmed || tooLong || uploading) return;
    setUploading(true);
    setError(null);
    try {
      onRun([await uploadRequest(trimmed, task.filename)]);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "업로드 중 오류가 발생했습니다.");
      setUploading(false);
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby={`${task.id}-title`}
      className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/30 p-4 backdrop-blur-sm"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !uploading) onClose();
      }}
    >
      <div className="anim-fade-in-up w-full max-w-lg rounded-2xl border border-border bg-surface-raised p-6 shadow-[0_30px_70px_-30px_rgba(15,30,70,0.55)]">
        <div className="flex items-center justify-between">
          <h2
            id={`${task.id}-title`}
            className="text-[18px] font-semibold tracking-tight text-foreground"
          >
            {task.title}
          </h2>
          <button
            type="button"
            onClick={onClose}
            disabled={uploading}
            aria-label="닫기"
            className="grid h-8 w-8 place-items-center rounded-md text-foreground-subtle hover:bg-foreground/5 hover:text-foreground disabled:opacity-40"
          >
            <span className="text-[18px] leading-none">×</span>
          </button>
        </div>

        <p className="mt-1.5 text-[13px] text-foreground-subtle">{task.modalDescription}</p>

        <textarea
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setError(null);
          }}
          disabled={uploading}
          rows={9}
          placeholder={`예) ${task.example}`}
          aria-label="고객 요청 내용"
          className="mt-4 w-full resize-y rounded-xl border border-foreground/15 bg-background px-3 py-2.5 text-[13px] leading-relaxed text-foreground placeholder:text-foreground-subtle/70 focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/40 disabled:opacity-60"
        />
        <div
          className={cn(
            "mt-1 text-right text-[11px]",
            tooLong ? "text-red-600 dark:text-red-400" : "text-foreground-subtle",
          )}
        >
          {trimmed.length.toLocaleString()} / {MAX_REQUEST_CHARS.toLocaleString()}자
        </div>

        {error && (
          <div className="mt-2 rounded-md border border-red-500/30 bg-red-500/5 px-3 py-2 text-[12px] text-red-600 dark:text-red-400">
            {error}
          </div>
        )}

        <div className="mt-5 flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={uploading}
            className="rounded-md px-4 py-2 text-[13px] text-foreground hover:bg-foreground/5 disabled:opacity-50"
          >
            취소
          </button>
          <button
            type="button"
            onClick={() => void start()}
            disabled={!trimmed || tooLong || uploading}
            className="und-grad inline-flex items-center gap-2 rounded-xl px-4 py-2 text-[13px] font-semibold text-white shadow-[0_8px_18px_-8px_rgba(37,99,235,0.6),inset_0_1px_0_rgba(255,255,255,0.35)] transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {uploading ? (
              <>
                <Icon name="refresh" className="h-3.5 w-3.5 animate-spin" />
                <span>업로드 중…</span>
              </>
            ) : (
              <>
                <Icon name="check" className="h-4 w-4" />
                <span>{task.submitLabel}</span>
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
