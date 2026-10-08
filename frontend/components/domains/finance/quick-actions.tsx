// finance 전용 퀵액션 + 원클릭 작업. 추후 ChatWindow 의 slot 또는 사이드 패널로 결합.
// 다른 도메인 컴포넌트를 import 하지 않는다(공통 shared 타입만 참조).
"use client";

import { useEffect, useRef, useState } from "react";
import type { ComposerAttachment } from "@/components/shared/chat/composer";
import { Icon } from "@/components/shared/ui/icon";
import { cn } from "@/lib/shared/utils";

export function FinanceQuickActions({ onPick }: { onPick: (text: string) => void }) {
  const actions = [
    "이번 달 마감 체크리스트",
    "부서별 예산 집행률",
    "거래처 미수금 TOP 10",
  ];
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

// 두 첨부 슬롯 정의 — 라벨/예시 파일명/설명을 한 곳에서 관리.
// key 는 분류가 아니라 UX 안내용(서버가 시트로 자동 판별하므로 슬롯 배정은 무관).
type ReconcileSlotKey = "balance" | "tx";

const RECONCILE_SLOTS: {
  key: ReconcileSlotKey;
  title: string;
  example: string;
  desc: string;
}[] = [
  {
    key: "balance",
    title: "일일자금수지 (자금실적)",
    example: "자금실적_FY26_260528.xlsx",
    desc: "당일잔액·증감액이 비어 있는 일일자금수지 파일. 자금계획이 안에 포함돼 있어 따로 첨부하지 않아도 됩니다.",
  },
  {
    key: "tx",
    title: "은행 거래내역",
    example: "주식회사 유엔디_은행 거래내역_20260401~20260531.xlsx",
    desc: "은행에서 내려받은 거래내역 엑셀('통합 거래내역' 시트 포함).",
  },
];

// 자금계획 자동작성 — 두 첨부 슬롯(자금계획 파일 / 월마감 자료).
type PlanSlotKey = "plan" | "closing";

const PLAN_SLOTS: {
  key: PlanSlotKey;
  title: string;
  example: string;
  desc: string;
}[] = [
  {
    key: "plan",
    title: "자금계획 엑셀",
    example: "자금계획_FY26_260624.xlsx",
    desc: "다음 달 계획표가 아직 작성되지 않은 자금계획 파일('자금계획_원화'·'자금계획_외화' 시트 포함). 위쪽 과거 자동이체 기록을 참조합니다.",
  },
  {
    key: "closing",
    title: "월마감 자료",
    example: "5월마감_20260611.xlsx",
    desc: "확정매출·입금예정·결제예정이 담긴 월마감 자료. 마감 기준일 이후 2개월 계획을 이 자료로 채웁니다.",
  },
];

const XLSX_ACCEPT =
  ".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

// 파일 크기 표기(composer.tsx·message.tsx 의 로컬 헬퍼와 동일 규약).
function formatBytes(n: number): string {
  if (n < 1024) return `${n}B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)}KB`;
  return `${(n / (1024 * 1024)).toFixed(1)}MB`;
}

/** 첨부 1건 업로드 → ComposerAttachment. 실패 시 throw. */
async function uploadAttachment(file: File): Promise<ComposerAttachment> {
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch("/api/attachments", { method: "POST", body: fd });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const detail =
      typeof data?.detail === "string" ? data.detail : `${file.name} 업로드 실패`;
    throw new Error(detail);
  }
  return {
    id: data.id,
    filename: data.filename,
    mime: data.mime,
    size_bytes: data.size_bytes,
  };
}

/**
 * 일일자금수지 비교·검증 원클릭 버튼.
 *
 * 클릭하면 모달이 열리고, 두 개의 라벨 슬롯(일일자금수지 / 은행 거래내역)에 각각
 * 파일을 채운 뒤 '작성 및 검증 시작'을 누르면 업로드 후 `onRun(attachments)` 으로 넘긴다.
 * 호출자(ChatWindow)는 표준 프롬프트와 함께 send 한다. 어느 파일이 거래내역/일일자금수지인지
 * 슬롯으로 구분하지 않아도 됨 — 서버가 시트로 자동 판별하며, 슬롯은 사용자 안내용이다.
 */
export function FinanceFundReconcile({
  onRun,
  disabled,
}: {
  onRun: (attachments: ComposerAttachment[]) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div className="mt-7 w-full max-w-2xl text-left">
      <p className="font-sans text-[12px] font-medium tracking-tight text-foreground-subtle">
        원클릭 작업
      </p>
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen(true)}
        className={cn(
          "group relative mt-2 flex w-full items-center gap-3 overflow-hidden rounded-xl und-glass und-card-border px-4 py-3.5 transition",
          "shadow-[0_10px_24px_-16px_rgba(15,30,70,0.4)]",
          disabled
            ? "cursor-not-allowed opacity-60"
            : "hover:-translate-y-0.5 hover:shadow-[0_18px_36px_-18px_rgba(15,30,70,0.5)]",
        )}
      >
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-accent-soft text-accent">
          <Icon name="file-spreadsheet" className="h-5 w-5" />
        </span>
        <span className="flex flex-col">
          <span className="text-[14px] font-semibold text-foreground">
            일일자금수지 비교·검증
          </span>
          <span className="text-[12px] text-foreground-muted">
            거래내역·일일자금수지 엑셀을 첨부하면 당일잔액을 채우고 계획↔실적을 검증합니다.
          </span>
        </span>
      </button>

      {/* 열릴 때만 마운트 → 매번 초기 상태로 시작(리셋 effect 불필요). */}
      {open && <FundReconcileModal onClose={() => setOpen(false)} onRun={onRun} />}
    </div>
  );
}

/**
 * 자금계획 자동작성 원클릭 버튼.
 *
 * 클릭하면 모달이 열리고, 두 슬롯(자금계획 엑셀 / 월마감 자료)에 파일을 채운 뒤
 * '자동작성 시작'을 누르면 업로드 후 `onRun(attachments)` 으로 넘긴다. 서버가 마감 기준일
 * 이후 미래 표를 절단하고, 과거 6개월 자동이체 + 마감 확정 입금/결제로 다음 2개월 계획표를
 * 새로 작성한다. 어느 파일이 자금계획/마감인지는 서버가 시트로 자동 판별한다.
 */
export function FinanceFundPlanGenerate({
  onRun,
  disabled,
}: {
  onRun: (attachments: ComposerAttachment[]) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div className="mt-4 w-full max-w-2xl text-left">
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen(true)}
        className={cn(
          "group relative flex w-full items-center gap-3 overflow-hidden rounded-xl und-glass und-card-border px-4 py-3.5 transition",
          "shadow-[0_10px_24px_-16px_rgba(15,30,70,0.4)]",
          disabled
            ? "cursor-not-allowed opacity-60"
            : "hover:-translate-y-0.5 hover:shadow-[0_18px_36px_-18px_rgba(15,30,70,0.5)]",
        )}
      >
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-accent-soft text-accent">
          <Icon name="file-spreadsheet" className="h-5 w-5" />
        </span>
        <span className="flex flex-col">
          <span className="text-[14px] font-semibold text-foreground">
            자금계획 자동작성 (월마감 반영)
          </span>
          <span className="text-[12px] text-foreground-muted">
            자금계획·월마감 엑셀을 첨부하면 확정 입금/결제와 자동이체로 다음 2개월 계획표를 작성합니다.
          </span>
        </span>
      </button>

      {open && <FundPlanModal onClose={() => setOpen(false)} onRun={onRun} />}
    </div>
  );
}

/**
 * 개인카드 영수증 대조·검증 원클릭 버튼.
 *
 * 클릭하면 모달이 열리고, expense 엑셀 파일(개인카드 내역 시트 + 영수증 첨부 시트가
 * 한 파일에 담긴 통합 워크북) 하나를 채운 뒤 '대조·검증 시작'을 누르면 업로드 후
 * `onRun(attachments)` 으로 넘긴다. 서버가 개인카드 지출 날짜별 합계 ↔ 영수증
 * 날짜별 합계(사진 위 금액 + 이미지 OCR 종합)를 대조해 검증 워크북을 돌려준다.
 */
export function FinanceExpenseReconcile({
  onRun,
  disabled,
}: {
  onRun: (attachments: ComposerAttachment[]) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div className="mt-4 w-full max-w-2xl text-left">
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen(true)}
        className={cn(
          "group relative flex w-full items-center gap-3 overflow-hidden rounded-xl und-glass und-card-border px-4 py-3.5 transition",
          "shadow-[0_10px_24px_-16px_rgba(15,30,70,0.4)]",
          disabled
            ? "cursor-not-allowed opacity-60"
            : "hover:-translate-y-0.5 hover:shadow-[0_18px_36px_-18px_rgba(15,30,70,0.5)]",
        )}
      >
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-accent-soft text-accent">
          <Icon name="file-spreadsheet" className="h-5 w-5" />
        </span>
        <span className="flex flex-col">
          <span className="text-[14px] font-semibold text-foreground">
            개인카드 영수증 대조·검증
          </span>
          <span className="text-[12px] text-foreground-muted">
            expense 엑셀을 첨부하면 개인카드 지출 내역과 영수증(사진 금액+OCR)을 날짜별로 대조·검증합니다.
          </span>
        </span>
      </button>

      {open && <ExpenseReconcileModal onClose={() => setOpen(false)} onRun={onRun} />}
    </div>
  );
}

/** expense 엑셀 1개를 채우고 대조·검증을 시작하는 모달. */
function ExpenseReconcileModal({
  onClose,
  onRun,
}: {
  onClose: () => void;
  onRun: (attachments: ComposerAttachment[]) => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !uploading) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [uploading, onClose]);

  function pick(f: File | null) {
    setError(null);
    if (f && !f.name.toLowerCase().endsWith(".xlsx")) {
      setError("엑셀(.xlsx) 파일만 첨부할 수 있습니다.");
      return;
    }
    setFile(f);
  }

  async function start() {
    if (!file || uploading) return;
    setUploading(true);
    setError(null);
    try {
      const uploaded = [await uploadAttachment(file)];
      onRun(uploaded);
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
      aria-labelledby="expense-recon-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/30 p-4 backdrop-blur-sm"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !uploading) onClose();
      }}
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => e.preventDefault()}
    >
      <div className="anim-fade-in-up w-full max-w-lg rounded-2xl border border-border bg-surface-raised p-6 shadow-[0_30px_70px_-30px_rgba(15,30,70,0.55)]">
        <div className="flex items-center justify-between">
          <h2
            id="expense-recon-title"
            className="text-[18px] font-semibold tracking-tight text-foreground"
          >
            개인카드 영수증 대조·검증
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

        <p className="mt-1.5 text-[13px] text-foreground-subtle">
          개인카드 지출 내역 시트와 영수증 첨부 시트가 함께 담긴 expense 엑셀을 첨부하면,
          개인카드 지출을 날짜별로 합산해 영수증(사진 위 금액 + 이미지 OCR 종합)과 대조·검증합니다.
          원본은 수정하지 않고 검증 결과를 돌려드립니다.
        </p>

        <div className="mt-5 flex flex-col gap-3">
          <ReconcileSlot
            index={1}
            title="expense 엑셀 (개인카드 내역 + 영수증 첨부)"
            example="expense_26.02월_백동주.xlsx"
            desc="개인카드 지출 표(expense_내역)와 영수증 사진이 첨부된 시트가 함께 있는 파일. 개인카드 지출만 대조합니다."
            file={file}
            disabled={uploading}
            onSelect={(f) => pick(f)}
            onClear={() => pick(null)}
          />

          {error && (
            <div className="rounded-md border border-red-500/30 bg-red-500/5 px-3 py-2 text-[12px] text-red-600 dark:text-red-400">
              {error}
            </div>
          )}
        </div>

        <div className="mt-6 flex items-center justify-end gap-2">
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
            disabled={!file || uploading}
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
                <span>대조·검증 시작</span>
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}

/** 두 슬롯(자금계획 / 월마감)에 파일을 채우고 자동작성을 시작하는 모달. */
function FundPlanModal({
  onClose,
  onRun,
}: {
  onClose: () => void;
  onRun: (attachments: ComposerAttachment[]) => void;
}) {
  const [files, setFiles] = useState<Record<PlanSlotKey, File | null>>({
    plan: null,
    closing: null,
  });
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !uploading) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [uploading, onClose]);

  const bothReady = !!files.plan && !!files.closing;

  function pick(key: PlanSlotKey, file: File | null) {
    setError(null);
    if (file && !file.name.toLowerCase().endsWith(".xlsx")) {
      setError("엑셀(.xlsx) 파일만 첨부할 수 있습니다.");
      return;
    }
    setFiles((prev) => ({ ...prev, [key]: file }));
  }

  async function start() {
    if (!files.plan || !files.closing || uploading) return;
    setUploading(true);
    setError(null);
    try {
      // 슬롯 순서대로 업로드(분류는 서버가 시트로 하지만 순서 유지가 안전).
      const uploaded = [
        await uploadAttachment(files.plan),
        await uploadAttachment(files.closing),
      ];
      onRun(uploaded);
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
      aria-labelledby="fund-plan-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/30 p-4 backdrop-blur-sm"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !uploading) onClose();
      }}
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => e.preventDefault()}
    >
      <div className="anim-fade-in-up w-full max-w-lg rounded-2xl border border-border bg-surface-raised p-6 shadow-[0_30px_70px_-30px_rgba(15,30,70,0.55)]">
        <div className="flex items-center justify-between">
          <h2
            id="fund-plan-title"
            className="text-[18px] font-semibold tracking-tight text-foreground"
          >
            자금계획 자동작성 (월마감 반영)
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

        <p className="mt-1.5 text-[13px] text-foreground-subtle">
          아래 두 파일을 첨부한 뒤 시작하면, 마감 기준일 이후 미래 표를 비우고 확정 입금/결제와
          과거 자동이체로 다음 2개월(예: 6·7월) 계획표를 자동으로 작성합니다. 원본은 수정하지
          않고 작성된 사본을 돌려드립니다.
        </p>

        <div className="mt-5 flex flex-col gap-3">
          {PLAN_SLOTS.map((slot, i) => (
            <ReconcileSlot
              key={slot.key}
              index={i + 1}
              title={slot.title}
              example={slot.example}
              desc={slot.desc}
              file={files[slot.key]}
              disabled={uploading}
              onSelect={(file) => pick(slot.key, file)}
              onClear={() => pick(slot.key, null)}
            />
          ))}

          {error && (
            <div className="rounded-md border border-red-500/30 bg-red-500/5 px-3 py-2 text-[12px] text-red-600 dark:text-red-400">
              {error}
            </div>
          )}
        </div>

        <div className="mt-6 flex items-center justify-end gap-2">
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
            disabled={!bothReady || uploading}
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
                <span>자동작성 시작</span>
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}

/** 두 슬롯에 파일을 채우고 시작하는 모달. */
function FundReconcileModal({
  onClose,
  onRun,
}: {
  onClose: () => void;
  onRun: (attachments: ComposerAttachment[]) => void;
}) {
  const [files, setFiles] = useState<Record<ReconcileSlotKey, File | null>>({
    balance: null,
    tx: null,
  });
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // ESC 닫기(업로드 중에는 무시).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !uploading) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [uploading, onClose]);

  const bothReady = !!files.balance && !!files.tx;

  function pick(key: ReconcileSlotKey, file: File | null) {
    setError(null);
    if (file && !file.name.toLowerCase().endsWith(".xlsx")) {
      setError("엑셀(.xlsx) 파일만 첨부할 수 있습니다.");
      return;
    }
    setFiles((prev) => ({ ...prev, [key]: file }));
  }

  async function start() {
    if (!files.balance || !files.tx || uploading) return;
    setUploading(true);
    setError(null);
    try {
      // 슬롯 순서대로 업로드(분류는 서버가 시트로 하지만 순서 유지가 안전).
      const uploaded = [
        await uploadAttachment(files.balance),
        await uploadAttachment(files.tx),
      ];
      onRun(uploaded);
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
      aria-labelledby="fund-reconcile-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/30 p-4 backdrop-blur-sm"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !uploading) onClose();
      }}
      // 슬롯 바깥에 떨어뜨린 파일을 브라우저가 새 탭으로 여는 기본 동작 차단.
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => e.preventDefault()}
    >
      <div className="anim-fade-in-up w-full max-w-lg rounded-2xl border border-border bg-surface-raised p-6 shadow-[0_30px_70px_-30px_rgba(15,30,70,0.55)]">
        <div className="flex items-center justify-between">
          <h2
            id="fund-reconcile-title"
            className="text-[18px] font-semibold tracking-tight text-foreground"
          >
            일일자금수지 비교·검증
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

        <p className="mt-1.5 text-[13px] text-foreground-subtle">
          아래 두 파일을 첨부한 뒤 시작하면, 당일잔액을 자동으로 채우고 계획↔실적을 검증합니다.
          원본은 수정하지 않고 채워진 사본과 검증 결과를 돌려드립니다.
        </p>

        <div className="mt-5 flex flex-col gap-3">
          {RECONCILE_SLOTS.map((slot, i) => (
            <ReconcileSlot
              key={slot.key}
              index={i + 1}
              title={slot.title}
              example={slot.example}
              desc={slot.desc}
              file={files[slot.key]}
              disabled={uploading}
              onSelect={(f) => pick(slot.key, f)}
              onClear={() => pick(slot.key, null)}
            />
          ))}

          {error && (
            <div className="rounded-md border border-red-500/30 bg-red-500/5 px-3 py-2 text-[12px] text-red-600 dark:text-red-400">
              {error}
            </div>
          )}
        </div>

        <div className="mt-6 flex items-center justify-end gap-2">
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
            disabled={!bothReady || uploading}
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
                <span>작성 및 검증 시작</span>
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}

/** 단일 첨부 슬롯 — 라벨 + 예시 파일명 placeholder + 설명 + 채움/비움 상태. */
function ReconcileSlot({
  index,
  title,
  example,
  desc,
  file,
  disabled,
  onSelect,
  onClear,
}: {
  index: number;
  title: string;
  example: string;
  desc: string;
  file: File | null;
  disabled?: boolean;
  onSelect: (file: File) => void;
  onClear: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const filled = !!file;

  function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragOver(false);
    if (disabled) return;
    const f = e.dataTransfer.files?.[0];
    if (f) onSelect(f);
  }

  return (
    <div className="rounded-xl border border-foreground/15 bg-background p-3">
      <div className="flex items-center gap-2">
        <span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-accent-soft text-[11px] font-semibold text-accent">
          {index}
        </span>
        <span className="text-[13px] font-medium text-foreground">{title}</span>
      </div>

      <button
        type="button"
        disabled={disabled}
        onClick={() => inputRef.current?.click()}
        onDragEnter={(e) => {
          e.preventDefault();
          if (!disabled) setDragOver(true);
        }}
        onDragOver={(e) => {
          e.preventDefault();
          if (!disabled) setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        className={cn(
          "mt-2 flex w-full items-center gap-2.5 rounded-lg border px-3 py-2.5 text-left transition",
          dragOver
            ? "border-accent bg-accent-soft/60 ring-1 ring-accent/40"
            : filled
              ? "border-accent/40 bg-accent-soft/40"
              : "border-dashed border-foreground/20 hover:border-foreground/35 hover:bg-foreground/5",
          disabled && "cursor-not-allowed opacity-60",
        )}
      >
        <Icon
          name={filled ? "file-spreadsheet" : "paperclip"}
          className={cn(
            "h-4 w-4 shrink-0",
            filled || dragOver ? "text-accent" : "text-foreground-subtle",
          )}
        />
        <span className="min-w-0 flex-1">
          {filled ? (
            <>
              <span className="block truncate text-[13px] text-foreground">{file!.name}</span>
              <span className="mt-0.5 block text-[11px] text-foreground-subtle">
                {formatBytes(file!.size)}
              </span>
            </>
          ) : (
            <>
              <span className="block truncate text-[13px] text-foreground-subtle">
                예: {example}
              </span>
              <span className="mt-0.5 block text-[11px] text-foreground-subtle/80">
                {dragOver ? "여기에 놓으세요" : "클릭하거나 파일을 끌어다 놓으세요"}
              </span>
            </>
          )}
        </span>
        {filled ? (
          <span
            role="button"
            tabIndex={0}
            aria-label="첨부 제거"
            onClick={(e) => {
              e.stopPropagation();
              if (!disabled) {
                onClear();
                if (inputRef.current) inputRef.current.value = "";
              }
            }}
            className="grid h-6 w-6 shrink-0 place-items-center rounded-md text-foreground-subtle hover:bg-foreground/10 hover:text-foreground"
          >
            <Icon name="x" className="h-3.5 w-3.5" />
          </span>
        ) : (
          <span className="shrink-0 rounded-md border border-foreground/15 px-2 py-0.5 text-[11px] text-foreground-muted">
            파일 선택
          </span>
        )}
      </button>

      <p className="mt-1.5 text-[11px] leading-relaxed text-foreground-subtle">{desc}</p>

      <input
        ref={inputRef}
        type="file"
        accept={XLSX_ACCEPT}
        hidden
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onSelect(f);
        }}
      />
    </div>
  );
}
