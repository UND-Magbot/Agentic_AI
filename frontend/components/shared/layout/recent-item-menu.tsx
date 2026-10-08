"use client";

import { useEffect, useRef, useState } from "react";

import { ConfirmDialog } from "@/components/shared/ui/confirm-dialog";
import { Icon } from "@/components/shared/ui/icon";
import type { Conversation } from "@/lib/shared/types";
import { cn } from "@/lib/shared/utils";

type Props = {
  /** 메뉴를 띄울 대화 항목. */
  conversation: Conversation;
  /** 메뉴 트리거(`…`) 우측 정렬용 — 사이드바 좁은 공간 / `/chats` 페이지 모두 사용. */
  align?: "left" | "right";
  /**
   * 액션 후 부모가 데이터를 갱신할 수 있도록 콜백.
   * 어떤 액션이 일어났는지 type 으로 알려준다(낙관적 업데이트 + 서버 재조회 양쪽 다 가능하게).
   */
  onAction: (
    action:
      | { type: "starred"; starred: boolean }
      | { type: "renamed"; title: string }
      | { type: "addedToProject"; projectId: string; projectName: string }
      | { type: "deleted" },
  ) => void;
  /** "프로젝트에 추가" 모달 띄울 때 사용할 외부 핸들러. 없으면 메뉴에서 항목 자체를 숨긴다. */
  onRequestAddToProject?: () => void;
  /** 이름 변경 모달 띄우기. 없으면 항목 숨김. */
  onRequestRename?: () => void;
};

/**
 * 사이드바 RECENT / `/chats` 페이지의 각 대화 항목 우측 끝에 붙는 `…` 메뉴.
 *
 *  - Star: 즉시 PATCH /api/conversations/{id} { starred }
 *  - Rename / Add to project: 외부에서 모달 핸들러를 받아 호출(메뉴는 닫음).
 *  - Delete: confirm 후 DELETE.
 */
export function RecentItemMenu({
  conversation,
  align = "right",
  onAction,
  onRequestAddToProject,
  onRequestRename,
}: Props) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  // 삭제 확인 모달 노출 여부. confirm() 네이티브 다이얼로그 대체.
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  // outside click / ESC 닫기.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  async function toggleStar(e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    if (busy) return;
    setBusy(true);
    const next = !conversation.starred;
    try {
      const r = await fetch(`/api/conversations/${encodeURIComponent(conversation.id)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ starred: next }),
      });
      if (!r.ok) throw new Error(`star toggle failed (${r.status})`);
      onAction({ type: "starred", starred: next });
    } catch {
      /* 실패는 silent — 다음 새로고침 시 서버 상태로 복구. */
    } finally {
      setBusy(false);
      setOpen(false);
    }
  }

  // "삭제" 메뉴 항목 — 메뉴를 닫고 커스텀 확인 모달을 띄운다.
  function requestDelete(e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    setOpen(false);
    setConfirmingDelete(true);
  }

  // 모달의 "삭제" 버튼 — 실제 DELETE 호출. 실패 시 throw → 모달이 에러를 인라인 표시.
  async function performDelete() {
    const r = await fetch(`/api/conversations/${encodeURIComponent(conversation.id)}`, {
      method: "DELETE",
    });
    if (!r.ok && r.status !== 204) {
      const detail = await r.text().catch(() => "");
      throw new Error(detail || `삭제 실패 (${r.status})`);
    }
    setConfirmingDelete(false);
    onAction({ type: "deleted" });
  }

  return (
    <div ref={wrapRef} className="relative">
      <button
        type="button"
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        aria-label="대화 옵션"
        aria-haspopup="menu"
        aria-expanded={open}
        title="대화 옵션"
        className={cn(
          "grid h-6 w-6 place-items-center rounded text-foreground-subtle transition",
          "hover:bg-foreground/10 hover:text-foreground",
          open && "bg-foreground/10 text-foreground",
        )}
      >
        <Icon name="more" className="h-3.5 w-3.5" />
      </button>
      {open && (
        <div
          role="menu"
          className={cn(
            "anim-fade-in-up absolute z-30 mt-1 min-w-[180px] rounded-md border border-border bg-surface-raised p-1 shadow-lg",
            align === "right" ? "right-0" : "left-0",
            "top-full",
          )}
          onClick={(e) => e.stopPropagation()}
        >
          <MenuItem
            icon="star"
            label={conversation.starred ? "즐겨찾기 해제" : "즐겨찾기"}
            onClick={toggleStar}
            iconClassName={conversation.starred ? "text-amber-500" : undefined}
          />
          {onRequestRename && (
            <MenuItem
              icon="edit"
              label="이름 변경"
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                setOpen(false);
                onRequestRename();
              }}
            />
          )}
          {onRequestAddToProject && (
            <MenuItem
              icon="folder"
              label="프로젝트에 추가"
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                setOpen(false);
                onRequestAddToProject();
              }}
            />
          )}
          <MenuItem
            icon="trash"
            label="삭제"
            onClick={requestDelete}
            variant="danger"
          />
        </div>
      )}
      <ConfirmDialog
        open={confirmingDelete}
        icon="trash"
        title="이 대화를 삭제할까요?"
        highlight={conversation.title}
        description={
          <>
            <p>대화에 포함된 모든 메시지가 함께 삭제됩니다.</p>
            <p className="mt-1 text-foreground-subtle">이 작업은 되돌릴 수 없습니다.</p>
          </>
        }
        confirmLabel="삭제"
        cancelLabel="취소"
        variant="danger"
        onConfirm={performDelete}
        onClose={() => setConfirmingDelete(false)}
      />
    </div>
  );
}

function MenuItem({
  icon,
  label,
  onClick,
  variant = "default",
  iconClassName,
}: {
  icon: "star" | "edit" | "folder" | "trash";
  label: string;
  onClick: (e: React.MouseEvent) => void;
  variant?: "default" | "danger";
  iconClassName?: string;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onClick}
      className={cn(
        "flex w-full items-center gap-2.5 rounded-sm px-2.5 py-2 text-left text-[13px] transition",
        "hover:bg-foreground/5",
        variant === "danger" ? "text-red-600 dark:text-red-400" : "text-foreground",
      )}
    >
      <Icon name={icon} className={cn("h-4 w-4", iconClassName)} />
      <span className="flex-1 truncate">{label}</span>
    </button>
  );
}
