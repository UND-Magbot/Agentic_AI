"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { ConfirmDialog } from "@/components/shared/ui/confirm-dialog";
import { Icon } from "@/components/shared/ui/icon";
import { InstructionsModal } from "@/components/shared/projects/instructions-modal";
import { NEUTRAL_ACCENT } from "@/lib/shared/neutral";
import type { ProjectDetailDTO } from "@/lib/shared/projects";
import type { DomainKey, DomainMeta } from "@/lib/shared/types";
import { cn, formatRelativeTime } from "@/lib/shared/utils";

type Props = {
  project: ProjectDetailDTO;
  domains: DomainMeta[];
  userName: string;
};

/**
 * 프로젝트 상세 페이지.
 *
 * 좌측 메인: 백 링크 → 프로젝트 헤더 → 새 대화 입력창 → 소속 대화 목록.
 * 우측 패널: Memory(placeholder) / Instructions(편집) / Files(placeholder).
 *
 * 새 대화 시작:
 *  - 입력창에서 발화 → `/?p=<id>&q=<encoded>` 로 이동.
 *  - server 컴포넌트가 `?p` 로 프로젝트 instructions/domain 을 prefetch 해서
 *    ChatPanel 에 prop 으로 내려주고, ChatWindow 가 mount 시점에 `?q` 의 첫 발화를
 *    자동으로 send 한다. send 가 끝나면 history.replaceState 로 URL 을 정리해
 *    페이지 새로고침이 발생해도 같은 발화가 두 번 일어나지 않게 한다.
 *  - URL 로 전달하는 이유: React StrictMode dev 더블마운트에서 sessionStorage 를
 *    한 번 consume 하면 두 번째 mount 가 빈 상태가 되어 환영 화면이 그대로 남는 회귀가
 *    있었다. URL 은 두 mount 가 같은 값을 보고, 첫 fetch 가 cleanup 에서 abort 되며
 *    두 번째 fetch 가 실제 stream 을 받는다.
 */
export function ProjectDetailView({ project: initial, domains, userName }: Props) {
  const router = useRouter();
  const [project, setProject] = useState<ProjectDetailDTO>(initial);
  const [draft, setDraft] = useState("");
  const [editingInstructions, setEditingInstructions] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const draftRef = useRef<HTMLTextAreaElement>(null);

  const domainMap = useMemo(
    () =>
      Object.fromEntries(domains.map((d) => [d.key, d])) as Record<DomainKey, DomainMeta>,
    [domains],
  );
  const accent = project.domain_key ? domainMap[project.domain_key]?.accent : undefined;
  const a = accent ?? NEUTRAL_ACCENT;

  // outside click / ESC → menu close
  useEffect(() => {
    if (!menuOpen) return;
    const onDown = (e: MouseEvent) => {
      if (!menuRef.current) return;
      if (!menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenuOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [menuOpen]);

  // 즐겨찾기 토글.
  async function toggleStarred() {
    const next = !project.starred;
    setProject((p) => ({ ...p, starred: next }));
    try {
      const r = await fetch(`/api/projects/${project.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ starred: next }),
      });
      if (!r.ok) throw new Error("star toggle failed");
    } catch {
      // 실패 시 원복.
      setProject((p) => ({ ...p, starred: !next }));
    }
  }

  // 프로젝트 삭제 — 모달의 확인 버튼이 호출. 실패 시 throw 해서 모달이 인라인 에러 표시.
  async function performDeleteProject() {
    const r = await fetch(`/api/projects/${project.id}`, { method: "DELETE" });
    if (!r.ok && r.status !== 204) {
      const detail = await r.text().catch(() => "");
      throw new Error(detail || `삭제 실패 (${r.status})`);
    }
    setConfirmingDelete(false);
    router.push("/projects");
    router.refresh();
  }

  // Instructions 저장.
  async function saveInstructions(next: string) {
    const trimmed = next.trim();
    setProject((p) => ({ ...p, system_prompt: trimmed || null }));
    try {
      await fetch(`/api/projects/${project.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ system_prompt: trimmed }),
      });
    } catch {
      /* 실패는 silent — 사용자가 다시 저장하도록 둠. */
    }
  }

  // 새 대화 시작.
  // URL 로 전달(`/?p=<id>&q=<encoded>`) — server 컴포넌트가 읽어 ChatWindow 에 prop 으로
  // 내려준다. sessionStorage 가 아니라 URL 인 이유: React StrictMode dev 더블마운트에서
  // storage consume 이 첫 mount 에서 발생해 두 번째 mount 가 빈 상태가 되는 회귀 회피.
  // ChatWindow 가 자동 fire 한 뒤 history.replaceState 로 q/p 를 정리해 새로고침 중복 발화도 차단.
  function startNewConversation(text: string) {
    const trimmed = text.trim();
    if (!trimmed) return;
    const params = new URLSearchParams();
    params.set("p", String(project.id));
    params.set("q", trimmed);
    router.push(`/?${params.toString()}`);
  }

  function onComposerKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      startNewConversation(draft);
    }
  }

  return (
    <div className="scroll-thin flex h-full overflow-y-auto">
      <div className="mx-auto flex w-full max-w-6xl gap-8 px-6 pb-12">
        {/* ────────────────────────── 좌측 메인 ────────────────────────── */}
        <div className="min-w-0 flex-1 pt-8">
          {/* 백 링크 */}
          <Link
            href="/projects"
            className="inline-flex items-center gap-1.5 text-[12px] font-medium text-foreground-muted hover:text-foreground"
          >
            <Icon name="back" className="h-3.5 w-3.5" />
            <span>모든 프로젝트</span>
          </Link>

          {/* 헤더 — 이름 + 액션 */}
          <div className="mt-5 flex items-center gap-3">
            <h1 className="min-w-0 truncate text-[28px] font-semibold tracking-tight text-foreground">
              {project.name}
            </h1>
            <div className="ml-auto flex items-center gap-1">
              <button
                type="button"
                onClick={toggleStarred}
                aria-pressed={project.starred}
                aria-label={project.starred ? "즐겨찾기 해제" : "즐겨찾기"}
                title={project.starred ? "즐겨찾기 해제" : "즐겨찾기"}
                className={cn(
                  "grid h-9 w-9 place-items-center rounded-md transition",
                  project.starred
                    ? "text-amber-500 hover:bg-amber-500/10"
                    : "text-foreground-subtle hover:bg-foreground/5 hover:text-foreground",
                )}
              >
                <Icon name="star" className="h-4 w-4" />
              </button>
              <div ref={menuRef} className="relative">
                <button
                  type="button"
                  onClick={() => setMenuOpen((v) => !v)}
                  aria-haspopup="menu"
                  aria-expanded={menuOpen}
                  aria-label="프로젝트 액션"
                  title="프로젝트 액션"
                  className="grid h-9 w-9 place-items-center rounded-md text-foreground-subtle hover:bg-foreground/5 hover:text-foreground"
                >
                  <Icon name="more" className="h-4 w-4" />
                </button>
                {menuOpen && (
                  <div
                    role="menu"
                    className="anim-fade-in-up absolute right-0 top-full z-20 mt-1 min-w-[180px] rounded-md border border-border bg-surface-raised p-1 shadow-lg"
                  >
                    <button
                      type="button"
                      role="menuitem"
                      onClick={() => {
                        setMenuOpen(false);
                        setEditingInstructions(true);
                      }}
                      className="flex w-full items-center gap-2.5 rounded-sm px-2.5 py-2 text-left text-[13px] text-foreground hover:bg-foreground/5"
                    >
                      <Icon name="edit" className="h-4 w-4" />
                      <span>Instructions 편집</span>
                    </button>
                    <button
                      type="button"
                      role="menuitem"
                      onClick={() => {
                        setMenuOpen(false);
                        setConfirmingDelete(true);
                      }}
                      className="flex w-full items-center gap-2.5 rounded-sm px-2.5 py-2 text-left text-[13px] text-red-600 hover:bg-foreground/5 dark:text-red-400"
                    >
                      <Icon name="trash" className="h-4 w-4" />
                      <span>프로젝트 삭제</span>
                    </button>
                  </div>
                )}
              </div>
            </div>
          </div>

          {project.description && (
            <p className="mt-2 text-[13px] text-foreground-muted">{project.description}</p>
          )}

          {/* 새 대화 입력창 */}
          <div
            className={cn(
              "mt-6 rounded-xl border border-foreground/15 bg-surface-raised px-4 py-3 shadow-sm transition focus-within:ring-2",
              a.focusRing,
            )}
          >
            <textarea
              ref={draftRef}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={onComposerKeyDown}
              rows={2}
              placeholder={`${userName}님, 무엇을 도와드릴까요?`}
              aria-label="이 프로젝트에서 새 대화 시작"
              className="block max-h-48 w-full resize-none bg-transparent text-[14px] text-foreground placeholder:text-foreground-subtle/70 focus:outline-none"
            />
            <div className="mt-2 flex items-center justify-between gap-2 text-[11px] text-foreground-subtle">
              <span className="inline-flex items-center gap-1.5">
                <Icon name="folder" className="h-3.5 w-3.5" />
                {project.name} 컨텍스트로 시작
              </span>
              <button
                type="button"
                onClick={() => startNewConversation(draft)}
                disabled={!draft.trim()}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-lg px-3.5 py-2 text-[12px] font-semibold transition",
                  draft.trim()
                    ? "und-grad text-white shadow-[0_8px_18px_-8px_rgba(37,99,235,0.6),inset_0_1px_0_rgba(255,255,255,0.35)] hover:brightness-105"
                    : "bg-foreground/10 text-foreground-subtle",
                )}
              >
                <Icon name="send" className="h-3.5 w-3.5" />
                <span>대화 시작</span>
              </button>
            </div>
          </div>

          {/* 소속 대화 목록 */}
          <div className="mt-8">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-[13px] font-semibold tracking-tight text-foreground">
                대화 ({project.conversations.length}개)
              </h2>
            </div>
            {project.conversations.length === 0 ? (
              <div className="rounded-lg border border-dashed border-foreground/15 bg-surface-raised/40 py-10 text-center text-[13px] text-foreground-subtle">
                아직 이 프로젝트에 속한 대화가 없습니다.
                <br />
                위 입력창에서 첫 대화를 시작해 보세요.
              </div>
            ) : (
              <ul className="flex flex-col gap-1.5">
                {project.conversations.map((c) => {
                  const cAccent = domainMap[c.domain_key]?.accent ?? NEUTRAL_ACCENT;
                  return (
                    <li key={c.id}>
                      <Link
                        href={`/?c=${c.id}`}
                        className="group relative flex items-center gap-3 overflow-hidden rounded-xl border border-border bg-surface-raised py-3 pl-5 pr-4 transition hover:-translate-y-0.5 hover:border-accent/30 hover:shadow-[0_14px_30px_-18px_rgba(15,30,70,0.45)]"
                      >
                        <span
                          className={cn("absolute inset-y-2 left-0 w-[3px] rounded-full", cAccent.bg)}
                          aria-hidden
                        />
                        <span className="min-w-0 flex-1 truncate text-[14px] font-medium text-foreground">
                          {c.title}
                        </span>
                        <span className="shrink-0 text-[11px] text-foreground-subtle">
                          마지막 메시지 {formatRelativeTime(c.updated_at)}
                        </span>
                      </Link>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </div>

        {/* ────────────────────────── 우측 패널 ────────────────────────── */}
        <aside className="hidden w-80 shrink-0 flex-col gap-3 pt-8 lg:flex">
          {/* Memory — 1차는 placeholder. RAG/메모리 도입 시 채움. */}
          <div className="rounded-xl border border-foreground/10 bg-surface-raised p-4">
            <div className="flex items-center justify-between">
              <h3 className="text-[13px] font-semibold tracking-tight text-foreground">
                Memory
              </h3>
              <span className="inline-flex items-center gap-1 rounded-sm bg-foreground/5 px-1.5 py-0.5 text-[10px] text-foreground-subtle">
                나만 보기
              </span>
            </div>
            <p className="mt-2 text-[12px] leading-relaxed text-foreground-subtle">
              몇 번의 대화 후 자동으로 정리된 프로젝트 메모리가 여기에 표시됩니다.
            </p>
          </div>

          {/* Instructions */}
          <div className="rounded-xl border border-foreground/10 bg-surface-raised p-4">
            <div className="flex items-center justify-between">
              <h3 className="text-[13px] font-semibold tracking-tight text-foreground">
                Instructions
              </h3>
              <button
                type="button"
                onClick={() => setEditingInstructions(true)}
                aria-label="Instructions 편집"
                title="Instructions 편집"
                className="grid h-7 w-7 place-items-center rounded-md text-foreground-subtle hover:bg-foreground/5 hover:text-foreground"
              >
                <Icon name={project.system_prompt ? "edit" : "plus"} className="h-3.5 w-3.5" />
              </button>
            </div>
            {project.system_prompt ? (
              <p className="mt-2 line-clamp-6 whitespace-pre-wrap text-[12px] leading-relaxed text-foreground-muted">
                {project.system_prompt}
              </p>
            ) : (
              <p className="mt-2 text-[12px] leading-relaxed text-foreground-subtle">
                UND Cortex의 응답 톤·규칙을 이 프로젝트에 맞게 조정할 Instructions를 추가해 보세요.
              </p>
            )}
          </div>

          {/* Files — 1차 placeholder. §B "첨부 파일 업로드" 항목과 함께 통합 예정. */}
          <div className="rounded-xl border border-foreground/10 bg-surface-raised p-4">
            <div className="flex items-center justify-between">
              <h3 className="text-[13px] font-semibold tracking-tight text-foreground">
                Files
              </h3>
              <button
                type="button"
                onClick={() => alert("파일 업로드는 곧 지원 예정입니다.")}
                aria-label="파일 추가"
                title="파일 추가 (곧 지원 예정)"
                className="grid h-7 w-7 place-items-center rounded-md text-foreground-subtle hover:bg-foreground/5 hover:text-foreground"
              >
                <Icon name="plus" className="h-3.5 w-3.5" />
              </button>
            </div>
            <div className="mt-3 grid place-items-center rounded-lg border border-dashed border-foreground/15 bg-background/40 px-4 py-6 text-center">
              <Icon
                name="paperclip"
                className="h-6 w-6 text-foreground-subtle"
              />
              <p className="mt-2 text-[11px] leading-relaxed text-foreground-subtle">
                PDF, 문서, 텍스트 등을
                <br />이 프로젝트에 추가해 컨텍스트로 사용하세요.
              </p>
            </div>
          </div>
        </aside>
      </div>

      <InstructionsModal
        open={editingInstructions}
        initial={project.system_prompt ?? ""}
        projectName={project.name}
        onClose={() => setEditingInstructions(false)}
        onSave={async (next) => {
          await saveInstructions(next);
          setEditingInstructions(false);
        }}
      />

      <ConfirmDialog
        open={confirmingDelete}
        icon="trash"
        title="이 프로젝트를 삭제할까요?"
        highlight={project.name}
        description={
          <>
            <p>프로젝트와 함께 다음이 정리됩니다:</p>
            <ul className="mt-2 list-disc pl-5 text-foreground-muted">
              <li>프로젝트 정보(이름·설명·Instructions)</li>
              <li>이 프로젝트로의 대화 매핑</li>
            </ul>
            <p className="mt-2 text-foreground-subtle">
              소속됐던 대화 자체는 삭제되지 않고 RECENT 에 그대로 남습니다. 다만 이 작업은 되돌릴 수 없습니다.
            </p>
          </>
        }
        confirmLabel="프로젝트 삭제"
        cancelLabel="취소"
        variant="danger"
        onConfirm={performDeleteProject}
        onClose={() => setConfirmingDelete(false)}
      />
    </div>
  );
}
