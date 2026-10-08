"use client";

import {
  useEffect,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
} from "react";
import type { ChatMessage, DomainKey, DomainMeta } from "@/lib/shared/types";
import { NEUTRAL_ACCENT } from "@/lib/shared/neutral";
import { cn } from "@/lib/shared/utils";
import { Icon } from "@/components/shared/ui/icon";

type Feedback = "up" | "down";

// 백엔드 LLM 클라이언트(NOTICE_SENTINEL)와 동일. 응답이 비정상 종결될 때 본문 끝에 붙어 오는
// 분리 마커. 이 글자 이후의 텍스트는 본문이 아닌 "끊김 안내"로 별도 박스에 표시한다.
const NOTICE_SENTINEL = "␟";

// 백엔드(main.PROGRESS_SENTINEL)와 동일. 진행 단계 상태를 한 줄 JSON 으로 감싸 본문에
// 흘려보내는 마커. `␜{"type":"progress","steps":[...]}␜\n` 형태. 본문에서 매치된 마커는
// 모두 제거하고 마지막 payload 만 ProgressCard 로 렌더한다.
const PROGRESS_SENTINEL = "␜";
const PROGRESS_MARKER_RE = /␜([\s\S]*?)␜/g;

type ProgressStepState = "pending" | "active" | "done" | "error";
type ProgressStep = { id: string; label: string; state: ProgressStepState };
type ProgressPayload = { type: "progress"; steps: ProgressStep[] };

/** body 에서 progress 마커들을 추출하고 본문에서 제거한 결과를 반환. */
function extractProgress(raw: string): {
  cleaned: string;
  latest: ProgressStep[] | null;
} {
  if (!raw || raw.indexOf(PROGRESS_SENTINEL) < 0) {
    return { cleaned: raw, latest: null };
  }
  let latest: ProgressStep[] | null = null;
  PROGRESS_MARKER_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = PROGRESS_MARKER_RE.exec(raw)) !== null) {
    try {
      const parsed = JSON.parse(m[1]) as ProgressPayload;
      if (parsed && parsed.type === "progress" && Array.isArray(parsed.steps)) {
        latest = parsed.steps;
      }
    } catch {
      /* 잘린 마커(스트리밍 중) — 무시. 완성된 다음 마커가 곧 도착한다. */
    }
  }
  // 진행 마커는 결과 본문보다 항상 먼저, 연속 블록으로 흘러온다(서버가 모든 마커를
  // emit 한 뒤에야 요약/다운로드 텍스트를 보냄). 따라서 sentinel 위치를 모아 통째로
  // 제거하면 부분 JSON 한 조각도 본문에 새지 않는다(개별 ␜…␜ 쌍만 지우던 기존 방식은
  // 스트리밍 중 잘린 마커·짝 어긋남에서 `…pending"},{"id":"upload"…` 같은 누출이 났다).
  //   · sentinel 개수가 짝수 → 모든 마커 완성: 첫 sentinel~마지막 sentinel(닫힘) 제거,
  //     그 뒤(요약/링크)는 보존.
  //   · 홀수 → 마지막 마커가 스트리밍 중 잘림: 첫 sentinel~끝까지 제거(요약은 아직 안 옴).
  const idxs: number[] = [];
  for (
    let p = raw.indexOf(PROGRESS_SENTINEL);
    p >= 0;
    p = raw.indexOf(PROGRESS_SENTINEL, p + 1)
  ) {
    idxs.push(p);
  }
  let cleaned = raw;
  if (idxs.length > 0) {
    const first = idxs[0];
    const closing = idxs.length % 2 === 0 ? idxs[idxs.length - 1] : -1;
    cleaned =
      closing >= 0 ? raw.slice(0, first) + raw.slice(closing + 1) : raw.slice(0, first);
  }
  // keepalive zero-width(U+200B)·마커가 남긴 빈 줄·선두 개행 정리.
  cleaned = cleaned
    .replace(/\u200B/g, "")
    .replace(/\n{3,}/g, "\n\n")
    .replace(/^\n+/, "");
  return { cleaned, latest };
}

// CJK(한자/가나) 흐름 감지로 잘린 응답에 붙는 안내는 사용자에게 노출하지 않는다(silent cut).
// 1차 방어는 backend(`_truncation_notice` 가 None 반환)이지만, hot-reload 미반영/캐시된 응답
// 등에 대비해 frontend 에서도 한 번 더 차단. 텍스트 패턴으로 식별해 silently drop.
const SILENT_NOTICE_PATTERNS: RegExp[] = [
  /다른 언어로 흐를/,
  /언어로 흐를 위험/,
];

// 본문이 자연 종결 어미/문장부호로 끝나는지 — 끝나지 않으면 trail-off "…" 를 붙여 잘림감 완화.
const NATURAL_END_RE = /[.!?:;)\]}"'…다요죠네까어야라음임함]\s*$/;

/**
 * LaTeX/KaTeX 마커를 평문으로 변환한다.
 * 본 챗 UI 는 수식 렌더러가 없어 raw `\\[ \\frac{a}{b} \\]` 가 그대로 보이는 게 사용자 경험을 망친다.
 * 1차 방어는 system prompt(LaTeX 출력 금지)이지만, 모델이 미준수하면 화면 단계에서라도 정리한다.
 *
 * 변환 예:
 *   "\\[ \\text{영업이익률} = \\frac{\\text{영업이익}}{\\text{매출액}} \\times 100 \\]"
 *   → "영업이익률 = (영업이익) / (매출액) × 100"
 */
function sanitizeLatex(text: string): string {
  return text
    // 블록/인라인 수식 wrapper 제거 (안의 내용은 살린다).
    .replace(/\\\[\s*([\s\S]*?)\s*\\\]/g, "$1")
    .replace(/\\\(\s*([\s\S]*?)\s*\\\)/g, "$1")
    .replace(/\$\$\s*([\s\S]*?)\s*\$\$/g, "$1")
    // \text{xxx} → xxx, \mathrm{xxx} → xxx, \mathbf{xxx} → xxx
    .replace(/\\(?:text|mathrm|mathbf|mathit|mathsf|mathtt|operatorname)\{([^{}]*)\}/g, "$1")
    // \frac{a}{b} → (a) / (b)
    .replace(/\\d?frac\{([^{}]*)\}\{([^{}]*)\}/g, "($1) / ($2)")
    // 흔한 수학 명령어 → 유니코드 기호.
    .replace(/\\times\b/g, "×")
    .replace(/\\div\b/g, "÷")
    .replace(/\\cdot\b/g, "·")
    .replace(/\\pm\b/g, "±")
    .replace(/\\leq\b/g, "≤")
    .replace(/\\geq\b/g, "≥")
    .replace(/\\neq\b/g, "≠")
    .replace(/\\approx\b/g, "≈")
    .replace(/\\infty\b/g, "∞")
    .replace(/\\sum\b/g, "Σ")
    .replace(/\\prod\b/g, "∏")
    // \left( \right) 같은 sizing 명령은 괄호만 남긴다.
    .replace(/\\left\s*([(\[{|])/g, "$1")
    .replace(/\\right\s*([)\]}|])/g, "$1")
    // 이스케이프된 일반 기호.
    .replace(/\\%/g, "%")
    .replace(/\\\$/g, "$")
    .replace(/\\#/g, "#")
    .replace(/\\&/g, "&")
    // 공백 명령.
    .replace(/\\,/g, " ")
    .replace(/\\;/g, " ")
    .replace(/\\:/g, " ")
    .replace(/\\quad\b/g, "  ")
    .replace(/\\qquad\b/g, "    ")
    // 잔여 한 단어 명령(`\foo` 형태) — 인자 없으면 제거.
    .replace(/\\[a-zA-Z]+(?![a-zA-Z{])/g, "");
}

function splitBodyAndNotice(raw: string): { body: string; notice: string | null } {
  const idx = raw.indexOf(NOTICE_SENTINEL);
  const rawBody = idx < 0 ? raw : raw.slice(0, idx).replace(/\s+$/g, "");
  const body = sanitizeLatex(rawBody);
  if (idx < 0) return { body, notice: null };
  const rawNotice = raw.slice(idx + NOTICE_SENTINEL.length).trim() || null;
  if (rawNotice && SILENT_NOTICE_PATTERNS.some((re) => re.test(rawNotice))) {
    const polishedBody = NATURAL_END_RE.test(body) ? body : `${body}…`;
    return { body: polishedBody, notice: null };
  }
  return { body, notice: rawNotice };
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n}B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)}KB`;
  return `${(n / (1024 * 1024)).toFixed(1)}MB`;
}

/**
 * 어시스턴트 본문에서 다운로드 가능한 첨부를 카드 UI 로 변환.
 *
 * 우선순위 매칭:
 *   1) Markdown link 형식: `[파일명.ext](/api/attachments/{id}/download)`
 *      → 파일명 + 확장자 + 다운로드 아이콘 카드로 렌더(권장 — backend 가 사용).
 *   2) Bare URL 형식: `/api/attachments/{id}/download` (또는 `/v1/...`)
 *      → 파일명 없는 폴백, 작은 inline anchor 로 렌더(구 버전 메시지 호환).
 *
 * anchor 는 `download` 속성으로 클릭 시 현재 탭에서 파일 저장 다이얼로그가 뜬다
 * (backend 의 Content-Disposition: attachment 헤더와 결합).
 */
const _MARKDOWN_DOWNLOAD_RE =
  /\[([^\]\n]+?)\]\(\/(?:api|v1)\/attachments\/(\d+)\/download\)/g;
const _BARE_DOWNLOAD_URL_RE = /\/(?:api|v1)\/attachments\/(\d+)\/download/g;

const _EXT_BADGE_MAP: Record<string, string> = {
  xlsx: "Excel", xls: "Excel", csv: "CSV",
  pdf: "PDF", docx: "Word", doc: "Word",
  pptx: "PPT", ppt: "PPT",
  zip: "ZIP", txt: "Text",
  png: "PNG", jpg: "JPG", jpeg: "JPG", gif: "GIF", webp: "Webp",
  drawio: "draw.io",
};

// 본문에서 미리보기를 함께 띄우는 이미지 확장자(개념도 PNG 등).
const _PREVIEW_EXTS = new Set(["png", "jpg", "jpeg", "webp"]);

function _fileExtension(name: string): string {
  const i = name.lastIndexOf(".");
  return i > 0 ? name.slice(i + 1).toLowerCase() : "";
}

/**
 * 첨부를 fetch 로 받아 응답을 검증한 뒤 blob 으로 저장한다.
 *
 * 평범한 `<a download>` 는 응답이 무엇이든(인증 만료 시 401 JSON, /login HTML
 * 리다이렉트 등) 그 본문을 그대로 파일로 저장해 '손상된 xlsx' 를 만든다.
 * 여기서는 성공 응답(스프레드시트 등 정상 바이너리)만 저장하고, 에러/리다이렉트
 * 응답이면 파일을 만들지 않고 사용자에게 보여줄 메시지를 반환한다.
 *
 * 반환: 성공 시 null, 실패 시 에러 메시지.
 */
/** Content-Disposition 헤더에서 실제 파일명을 추출(없으면 null). */
function _filenameFromContentDisposition(cd: string | null): string | null {
  if (!cd) return null;
  // RFC 5987 `filename*=UTF-8''<percent-encoded>` 우선.
  const star = /filename\*=UTF-8''([^;]+)/i.exec(cd);
  if (star) {
    try {
      return decodeURIComponent(star[1]);
    } catch {
      /* percent-decoding 실패 시 plain filename 으로 폴백 */
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(cd);
  return plain ? plain[1] : null;
}

async function downloadAttachment(
  href: string,
  filename: string,
): Promise<string | null> {
  let res: Response;
  try {
    res = await fetch(href, { cache: "no-store" });
  } catch {
    return "다운로드 중 네트워크 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.";
  }
  if (!res.ok) {
    return res.status === 401
      ? "세션이 만료되었습니다. 다시 로그인한 뒤 다운로드해 주세요."
      : `다운로드에 실패했습니다 (오류 ${res.status}).`;
  }
  // 인증 미들웨어가 /login HTML 로 리다이렉트했거나 에러 JSON 을 준 경우 —
  // 그 본문을 파일로 저장하지 않는다.
  const ct = (res.headers.get("content-type") ?? "").toLowerCase();
  if (ct.includes("text/html") || ct.includes("application/json")) {
    return "세션이 만료되었습니다. 다시 로그인한 뒤 다운로드해 주세요.";
  }
  const blob = await res.blob();
  // 서버가 보낸 실제 파일명 우선(bare URL 처럼 호출부가 파일명을 모를 때 대비).
  const serverName = _filenameFromContentDisposition(
    res.headers.get("content-disposition"),
  );
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = serverName || filename || "download";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  return null;
}

function DownloadCard({
  filename,
  attachmentId,
}: {
  filename: string;
  attachmentId: string;
}) {
  const ext = _fileExtension(filename);
  const badge = _EXT_BADGE_MAP[ext] ?? ext.toUpperCase() ?? "FILE";
  const href = `/api/attachments/${attachmentId}/download`;
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleClick(e: ReactMouseEvent<HTMLAnchorElement>) {
    // 기본 anchor 다운로드를 막고 검증된 fetch 다운로드로 대체.
    e.preventDefault();
    if (downloading) return;
    setDownloading(true);
    setError(null);
    const err = await downloadAttachment(href, filename);
    setError(err);
    setDownloading(false);
  }

  return (
    <span className="not-prose my-2 flex flex-col gap-1">
    <a
      href={href}
      download={filename}
      onClick={handleClick}
      aria-busy={downloading}
      title={`${filename} — 클릭하면 다운로드됩니다`}
      className={cn(
        "group inline-flex max-w-full items-center gap-3",
        "rounded-xl border border-border bg-surface-raised px-3.5 py-2.5",
        "shadow-sm transition-all",
        "hover:border-foreground/30 hover:bg-surface hover:shadow-md",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-foreground/30",
        "active:translate-y-px",
        downloading && "pointer-events-none opacity-70",
      )}
    >
      <span
        aria-hidden
        className={cn(
          "grid h-10 w-10 shrink-0 place-items-center rounded-lg",
          "bg-emerald-500/10 text-emerald-700 ring-1 ring-inset ring-emerald-500/25",
          "dark:bg-emerald-500/15 dark:text-emerald-300 dark:ring-emerald-500/30",
        )}
      >
        <Icon name="file-spreadsheet" className="h-5 w-5" />
      </span>
      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="truncate text-[14px] font-medium text-foreground">
          {filename}
        </span>
        <span className="flex items-center gap-1.5 text-[11px] text-foreground-subtle">
          <span className="rounded-sm bg-foreground/5 px-1.5 py-px font-medium uppercase tracking-wide">
            {badge}
          </span>
          <span>{downloading ? "· 다운로드 중…" : "· 클릭하여 다운로드"}</span>
        </span>
      </span>
      <span
        aria-hidden
        className={cn(
          "grid h-8 w-8 shrink-0 place-items-center rounded-md",
          "text-foreground-subtle transition-colors",
          "group-hover:bg-foreground/5 group-hover:text-foreground",
        )}
      >
        <Icon name="download" className="h-4 w-4" />
      </span>
    </a>
      {error && (
        <span className="text-[12px] text-red-600 dark:text-red-400">
          {error}
        </span>
      )}
    </span>
  );
}

/**
 * 단순 다운로드 앵커 — DownloadCard 의 카드 UI 가 아닌, 인라인 링크/칩 형태로
 * 쓰는 곳(구버전 bare 링크, 사용자 첨부 칩)에서 재사용.
 *
 * DownloadCard 와 동일하게 클릭 시 검증된 fetch 다운로드를 수행한다 — 인증 만료 시
 * 에러 응답(401 JSON · /login HTML)을 파일로 저장하지 않고 에러 메시지를 노출한다.
 */
function DownloadLink({
  href,
  filename,
  className,
  title,
  children,
}: {
  href: string;
  filename: string;
  className?: string;
  title?: string;
  children: ReactNode;
}) {
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleClick(e: ReactMouseEvent<HTMLAnchorElement>) {
    e.preventDefault();
    if (downloading) return;
    setDownloading(true);
    setError(null);
    const err = await downloadAttachment(href, filename);
    setError(err);
    setDownloading(false);
  }

  return (
    <>
      <a
        href={href}
        download={filename || true}
        onClick={handleClick}
        aria-busy={downloading}
        title={title}
        className={cn(className, downloading && "pointer-events-none opacity-70")}
      >
        {children}
      </a>
      {error && (
        <span className="ml-1 text-[11px] text-red-600 dark:text-red-400">
          {error}
        </span>
      )}
    </>
  );
}

/**
 * 진행 단계 카드 — STT/요약/양식 생성 등 다단계 처리의 현재 진행 상태를 시각화.
 * 각 단계는 pending(회색) → active(스피너 + 강조) → done(체크) / error(X).
 * 활성 단계가 하나라도 있으면 카드 상단에 헤더 스피너도 함께 표시한다.
 */
function ProgressCard({ steps }: { steps: ProgressStep[] }) {
  if (!steps || steps.length === 0) return null;
  const hasActive = steps.some((s) => s.state === "active");
  const hasError = steps.some((s) => s.state === "error");
  const allDone = steps.every((s) => s.state === "done");

  const headerLabel = hasError
    ? "처리 중 오류가 발생했습니다."
    : allDone
      ? "처리가 완료되었습니다."
      : hasActive
        ? `${steps.find((s) => s.state === "active")?.label ?? "처리"} 중…`
        : "처리 준비 중…";

  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        "not-prose my-2 flex flex-col gap-2.5",
        "rounded-xl border border-border bg-surface-raised px-3.5 py-3 shadow-sm",
      )}
    >
      <div className="flex items-center gap-2 text-[13px] font-medium text-foreground">
        {hasActive ? (
          <Spinner className="h-4 w-4 text-foreground/70" />
        ) : hasError ? (
          <Icon name="x" className="h-4 w-4 text-red-600 dark:text-red-400" />
        ) : (
          <Icon name="check" className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
        )}
        <span>{headerLabel}</span>
      </div>
      <ol className="flex flex-col gap-1.5">
        {steps.map((s, idx) => (
          <li
            key={s.id}
            className={cn(
              "flex items-center gap-2.5 text-[12.5px]",
              s.state === "done" && "text-foreground",
              s.state === "active" && "text-foreground",
              s.state === "pending" && "text-foreground-subtle",
              s.state === "error" && "text-red-600 dark:text-red-400",
            )}
          >
            <ProgressStepIcon state={s.state} index={idx + 1} />
            <span className={cn(s.state === "active" && "font-medium")}>
              {s.label}
            </span>
            {s.state === "active" && (
              <span className="text-[11px] text-foreground-subtle">진행 중…</span>
            )}
            {s.state === "done" && (
              <span className="text-[11px] text-emerald-700/70 dark:text-emerald-300/70">완료</span>
            )}
            {s.state === "error" && (
              <span className="text-[11px]">실패</span>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}

function ProgressStepIcon({
  state,
  index,
}: {
  state: ProgressStepState;
  index: number;
}) {
  const base = "grid h-5 w-5 shrink-0 place-items-center rounded-full text-[10px] font-semibold";
  if (state === "done") {
    return (
      <span className={cn(base, "bg-emerald-500/15 text-emerald-700 ring-1 ring-inset ring-emerald-500/30 dark:text-emerald-300")} aria-hidden>
        <Icon name="check" className="h-3 w-3" />
      </span>
    );
  }
  if (state === "active") {
    return (
      <span className={cn(base, "bg-foreground/5 text-foreground ring-1 ring-inset ring-foreground/30")} aria-hidden>
        <Spinner className="h-3 w-3 text-foreground/80" />
      </span>
    );
  }
  if (state === "error") {
    return (
      <span className={cn(base, "bg-red-500/10 text-red-700 ring-1 ring-inset ring-red-500/30 dark:text-red-300")} aria-hidden>
        <Icon name="x" className="h-3 w-3" />
      </span>
    );
  }
  // pending
  return (
    <span className={cn(base, "bg-foreground/[0.04] text-foreground-subtle ring-1 ring-inset ring-foreground/10")} aria-hidden>
      {index}
    </span>
  );
}

/** 원형 회전 스피너 — Icon 세트에 없는 SVG 기반. */
function Spinner({ className }: { className?: string }) {
  return (
    <svg
      className={cn("animate-spin", className)}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden
    >
      <circle
        cx="12"
        cy="12"
        r="9"
        stroke="currentColor"
        strokeOpacity="0.2"
        strokeWidth="3"
      />
      <path
        d="M21 12a9 9 0 0 0-9-9"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
      />
    </svg>
  );
}

/**
 * 일일자금수지 비교·검증 결과(reconcile_service `_build_summary`)인지 식별.
 * 백엔드가 만드는 고정 헤더(`✓|⚠ YYYY-MM-DD 자금계획 ↔ …`)로만 트리거 — 일반 답변엔 영향 없음.
 */
function isReconcileSummary(body: string): boolean {
  return /(?:^|\n)[ \t]*(?:✓|⚠)\s+\d{4}-\d{2}-\d{2}\s+자금계획\s*↔/.test(body);
}

/** 텍스트 내 금액/숫자 토큰을 굵게 강조한 ReactNode 배열로 변환. */
function emphasizeNumbers(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(\$?-?\d[\d,]*(?:\.\d+)?)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    out.push(
      <strong key={`n-${i++}`} className="font-semibold tabular-nums text-foreground">
        {m[0]}
      </strong>,
    );
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

type ReconcileTone = "ok" | "warn" | "muted";

function _verdictTone(s: string): ReconcileTone {
  if (/변동\s*없음/.test(s)) return "muted";
  if (/(점검\s*필요|차액|불일치|✗|≠)/.test(s)) return "warn";
  if (/(일치|○|=)/.test(s)) return "ok";
  return "muted";
}

/** 상태 칩 — 초록(일치)/적색(점검)/회색(변동없음). */
function StatusPill({ tone, label }: { tone: ReconcileTone; label: string }) {
  const cls =
    tone === "ok"
      ? "bg-emerald-500/12 text-emerald-700 ring-emerald-500/30 dark:text-emerald-300"
      : tone === "warn"
        ? "bg-rose-500/12 text-rose-700 ring-rose-500/30 dark:text-rose-300"
        : "bg-foreground/5 text-foreground-subtle ring-foreground/15";
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset",
        cls,
      )}
    >
      {tone === "ok" && <Icon name="check" className="h-3 w-3" />}
      {tone === "warn" && <Icon name="x" className="h-3 w-3" />}
      {label}
    </span>
  );
}

/** 항목 라벨 칩 색상 — 원화/외화 구분으로 한눈에. */
function _labelTone(label: string): string {
  if (/외화|USD/i.test(label))
    return "bg-violet-500/12 text-violet-700 ring-violet-500/25 dark:text-violet-300";
  return "bg-sky-500/12 text-sky-700 ring-sky-500/25 dark:text-sky-300";
}

/**
 * 자금 비교·검증 결과 카드 — 평문 요약을 색상/볼드/상태칩으로 재구성해 한눈에 보이게.
 * 본문 텍스트(backend)는 그대로 두고 표시만 강화. 파싱 실패 시 호출부가 평문으로 폴백.
 */
function ReconcileResultCard({ body }: { body: string }) {
  // 1) 다운로드 링크 추출 후 요약 텍스트에서 제거. (공유 정규식 상태를 건드리지 않도록
  //    로컬 RegExp + matchAll 사용 — react-hooks/immutability 준수.)
  const linkRe = new RegExp(_MARKDOWN_DOWNLOAD_RE.source, "g");
  const links = [...body.matchAll(linkRe)].map((m) => ({
    filename: m[1].trim(),
    id: m[2],
  }));
  const summary = body.replace(new RegExp(_MARKDOWN_DOWNLOAD_RE.source, "g"), "").trim();
  const lines = summary
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);

  const headerLine = lines.find((l) => /^(✓|⚠)/.test(l)) ?? "";
  const hm = headerLine.match(/^(✓|⚠)\s+(\S+)\s+(.+?)\s+—\s+(.+)$/);
  const ok = headerLine.startsWith("✓");
  const date = hm?.[2] ?? "";
  const title = hm?.[3] ?? "자금계획 ↔ 자금실적 비교·검증";
  const verdict = hm?.[4] ?? "";

  const bullets = lines.filter((l) => l.startsWith("•")).map((l) => l.replace(/^•\s*/, ""));
  const notes = lines.filter((l) => l.startsWith("※")).map((l) => l.replace(/^※\s*/, ""));
  // 교차검증 푸터 — 신/구 문구 모두 매칭(헤더·불릿 제외).
  const footer =
    lines.find(
      (l) =>
        !l.startsWith("•") &&
        !/^(✓|⚠)/.test(l) &&
        /(최종 확정은|교차검증)/.test(l),
    ) ?? "";

  return (
    <div className="not-prose my-1 flex flex-col gap-3">
      {/* 헤더 — 결과 배지 + 일자 + 판정 */}
      <div
        className={cn(
          "flex items-start gap-3 rounded-xl border px-3.5 py-3",
          ok
            ? "border-emerald-500/25 bg-emerald-500/[0.07]"
            : "border-amber-500/30 bg-amber-500/[0.08]",
        )}
      >
        <span
          aria-hidden
          className={cn(
            "mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-lg ring-1 ring-inset",
            ok
              ? "bg-emerald-500/15 text-emerald-600 ring-emerald-500/30 dark:text-emerald-300"
              : "bg-amber-500/15 text-amber-600 ring-amber-500/30 dark:text-amber-300",
          )}
        >
          <Icon name={ok ? "check" : "help"} className="h-4 w-4" />
        </span>
        <div className="flex min-w-0 flex-col gap-0.5">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13.5px]">
            {date && (
              <span className="rounded-md bg-foreground/[0.06] px-1.5 py-0.5 font-semibold tabular-nums text-foreground">
                {date}
              </span>
            )}
            <span className="font-medium text-foreground">{title}</span>
          </div>
          {verdict && (
            <p
              className={cn(
                "text-[13px] font-bold underline decoration-2 underline-offset-2",
                ok
                  ? "text-emerald-700 decoration-emerald-500/40 dark:text-emerald-300"
                  : "text-amber-700 decoration-amber-500/40 dark:text-amber-300",
              )}
            >
              {verdict}
            </p>
          )}
        </div>
      </div>

      {/* 항목별 비교 행 */}
      {bullets.length > 0 && (
        <ul className="flex flex-col gap-2">
          {bullets.map((b, idx) => {
            const colon = b.indexOf(":");
            const label = colon > 0 ? b.slice(0, colon).trim() : "";
            let rest = colon > 0 ? b.slice(colon + 1).trim() : b;
            // 후행 괄호(당일잔액/누락 등)를 보조 노트로 분리.
            const paren = rest.match(/\(([^)]+)\)\s*$/);
            const subnote = paren?.[1] ?? "";
            if (paren) rest = rest.slice(0, paren.index).trim();
            // '→' 기준으로 비교부 / 판정부 분리.
            const arrow = rest.split(/→/);
            const compare = arrow[0].trim();
            const vtext = (arrow[1] ?? "").trim();
            const tone = _verdictTone(vtext || compare);
            const pillLabel = /변동\s*없음/.test(vtext || compare)
              ? "변동 없음"
              : tone === "ok"
                ? "일치"
                : "점검 필요";
            return (
              <li
                key={idx}
                className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border border-border bg-surface-raised px-3 py-2"
              >
                {label && (
                  <span
                    className={cn(
                      "shrink-0 rounded-md px-2 py-0.5 text-[11.5px] font-semibold ring-1 ring-inset",
                      _labelTone(label),
                    )}
                  >
                    {label}
                  </span>
                )}
                <span className="min-w-0 flex-1 text-[13px] text-foreground-muted">
                  {emphasizeNumbers(compare)}
                  {subnote && (
                    <span className="ml-1.5 text-[12px] text-foreground-subtle">
                      ({emphasizeNumbers(subnote)})
                    </span>
                  )}
                </span>
                {(vtext || /변동\s*없음/.test(compare)) && (
                  <StatusPill tone={tone} label={pillLabel} />
                )}
              </li>
            );
          })}
        </ul>
      )}

      {/* 경고 노트(※) */}
      {notes.map((n, i) => (
        <div
          key={`note-${i}`}
          className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/[0.07] px-3 py-2 text-[12.5px] text-amber-800 dark:text-amber-200"
        >
          <Icon name="help" className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>{n}</span>
        </div>
      ))}

      {/* 푸터(교차검증 요약) */}
      {footer && (
        <p className="px-0.5 text-[12px] leading-relaxed text-foreground-subtle">
          {emphasizeNumbers(footer)}
        </p>
      )}

      {/* 다운로드 카드 */}
      {links.length > 0 && (
        <div className="flex flex-col gap-1.5">
          {links.map((l) => (
            <DownloadCard key={l.id} filename={l.filename} attachmentId={l.id} />
          ))}
        </div>
      )}
    </div>
  );
}

/** 이미지 첨부 미리보기 — 클릭하면 새 탭에서 원본 크기로 연다. */
function ImagePreview({ filename, attachmentId }: { filename: string; attachmentId: string }) {
  const href = `/api/attachments/${attachmentId}/download`;
  const [failed, setFailed] = useState(false);
  if (failed) return null;
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      title="클릭하면 원본 크기로 엽니다"
      className="my-2 block overflow-hidden rounded-xl border border-border bg-white"
    >
      {/* eslint-disable-next-line @next/next/no-img-element -- 인증 쿠키가 필요한 첨부 프록시라 next/image 최적화 불가 */}
      <img
        src={href}
        alt={filename}
        loading="lazy"
        onError={() => setFailed(true)}
        className="block h-auto w-full"
      />
    </a>
  );
}

// 개념도·제안서 결과의 확정 마커 — backend confirmed.confirm_link 와 같은 형식.
const _CONFIRM_RE = /\[이 결과를 확정\]\(\/api\/proposal-records\/(\d+)\/confirm\)/g;
const _REVISED_ACCEPT = ".drawio,.pptx,.docx";

type ConfirmState = "loading" | "draft" | "confirmed" | "busy" | "unavailable";

/**
 * 결과 확정 카드. 확정하면 이 결과가 다음 비슷한 요청의 형식 참고 예시가 된다.
 * 수정본(.drawio/.pptx/.docx)을 올려 확정하면 담당자가 고친 내용이 예시로 쓰인다.
 */
function ConfirmCard({ recordId }: { recordId: string }) {
  const [state, setState] = useState<ConfirmState>("loading");
  const [revised, setRevised] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let alive = true;
    fetch(`/api/proposal-records/${recordId}`, { cache: "no-store" })
      .then(async (r) => {
        if (!alive) return;
        if (!r.ok) return setState("unavailable");
        const d = await r.json();
        setRevised(!!d.revised);
        setState(d.status === "confirmed" ? "confirmed" : "draft");
      })
      .catch(() => alive && setState("unavailable"));
    return () => {
      alive = false;
    };
  }, [recordId]);

  async function confirm(file: File | null) {
    setState("busy");
    setError(null);
    try {
      let revisedId: number | null = null;
      if (file) {
        const fd = new FormData();
        fd.append("file", file);
        const up = await fetch("/api/attachments", { method: "POST", body: fd });
        const ud = await up.json().catch(() => ({}));
        if (!up.ok) throw new Error(typeof ud?.detail === "string" ? ud.detail : "수정본 업로드 실패");
        revisedId = ud.id;
      }
      const r = await fetch(`/api/proposal-records/${recordId}/confirm`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ revised_attachment_id: revisedId }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof d?.detail === "string" ? d.detail : `확정 실패 (${r.status})`);
      setRevised(!!d.revised);
      setState("confirmed");
    } catch (e) {
      setError(e instanceof Error ? e.message : "확정 중 오류가 발생했습니다.");
      setState("draft");
    }
  }

  if (state === "unavailable") return null;
  const busy = state === "busy" || state === "loading";

  return (
    <div className="my-2 rounded-xl border border-border bg-surface px-4 py-3">
      {state === "confirmed" ? (
        <div className="flex items-start gap-2 text-[13px] text-foreground">
          <Icon name="check" className="mt-0.5 h-4 w-4 shrink-0 text-accent" />
          <span>
            확정했습니다{revised ? " (수정본 반영)" : ""}. 다음에 비슷한 요청이 오면 이 결과의 형식을
            참고합니다.
          </span>
        </div>
      ) : (
        <>
          <p className="text-[13px] text-foreground">
            이 결과를 최종본으로 쓰시나요? 확정하면 다음에 비슷한 요청이 올 때 이 형식을 참고합니다.
          </p>
          <p className="mt-0.5 text-[11px] text-foreground-subtle">
            draw.io·PowerPoint 에서 고쳤다면 수정본을 올려 확정해 주세요. 고친 내용이 기준이 됩니다.
          </p>
          <div className="mt-2.5 flex flex-wrap gap-2">
            <button
              type="button"
              disabled={busy}
              onClick={() => void confirm(null)}
              className="und-grad inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[12px] font-semibold text-white disabled:opacity-50"
            >
              <Icon name="check" className="h-3.5 w-3.5" />
              {state === "busy" ? "확정 중…" : "그대로 확정"}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => inputRef.current?.click()}
              className="inline-flex items-center gap-1.5 rounded-lg border border-foreground/15 px-3 py-1.5 text-[12px] text-foreground hover:bg-foreground/5 disabled:opacity-50"
            >
              <Icon name="paperclip" className="h-3.5 w-3.5" />
              수정본 올리고 확정
            </button>
          </div>
          {error && (
            <div className="mt-2 rounded-md border border-red-500/30 bg-red-500/5 px-3 py-2 text-[12px] text-red-600 dark:text-red-400">
              {error}
            </div>
          )}
          <input
            ref={inputRef}
            type="file"
            accept={_REVISED_ACCEPT}
            hidden
            onChange={(e) => {
              const f = e.target.files?.[0];
              e.target.value = "";
              if (f) void confirm(f);
            }}
          />
        </>
      )}
    </div>
  );
}

function renderBodyWithDownloads(body: string): ReactNode {
  if (!body) return body;

  // 1) Markdown 형식 우선 추출 (파일명 알 수 있음 → 카드 UI).
  const cards: { start: number; end: number; filename: string; id: string }[] = [];
  _MARKDOWN_DOWNLOAD_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = _MARKDOWN_DOWNLOAD_RE.exec(body)) !== null) {
    cards.push({
      start: m.index,
      end: m.index + m[0].length,
      filename: m[1].trim(),
      id: m[2],
    });
  }

  // 2) Bare URL 들 추출 (markdown 매치 영역 제외).
  const bareLinks: { start: number; end: number; id: string }[] = [];
  _BARE_DOWNLOAD_URL_RE.lastIndex = 0;
  let mb: RegExpExecArray | null;
  while ((mb = _BARE_DOWNLOAD_URL_RE.exec(body)) !== null) {
    const s = mb.index;
    const e = s + mb[0].length;
    // markdown range 안이면 건너뜀.
    if (cards.some((c) => s >= c.start && e <= c.end)) continue;
    bareLinks.push({ start: s, end: e, id: mb[1] });
  }

  // 3) 확정 카드 마커 (backend confirmed.confirm_link).
  const confirms: { start: number; end: number; id: string }[] = [];
  _CONFIRM_RE.lastIndex = 0;
  let mc: RegExpExecArray | null;
  while ((mc = _CONFIRM_RE.exec(body)) !== null) {
    confirms.push({ start: mc.index, end: mc.index + mc[0].length, id: mc[1] });
  }

  if (cards.length === 0 && bareLinks.length === 0 && confirms.length === 0) return body;

  // 모든 점프 포인트 시간순 병합 후 텍스트/카드 노드 구성.
  const points: (
    | ({ kind: "card" } & (typeof cards)[number])
    | ({ kind: "bare" } & (typeof bareLinks)[number])
    | ({ kind: "confirm" } & (typeof confirms)[number])
  )[] = [
    ...cards.map((c) => ({ kind: "card" as const, ...c })),
    ...bareLinks.map((b) => ({ kind: "bare" as const, ...b })),
    ...confirms.map((c) => ({ kind: "confirm" as const, ...c })),
  ].sort((a, b) => a.start - b.start);

  const out: ReactNode[] = [];
  let cursor = 0;
  let i = 0;
  for (const p of points) {
    if (p.start > cursor) out.push(body.slice(cursor, p.start));
    if (p.kind === "confirm") {
      out.push(<ConfirmCard key={`cf-${i++}-${p.id}`} recordId={p.id} />);
    } else if (p.kind === "card") {
      if (_PREVIEW_EXTS.has(_fileExtension(p.filename))) {
        out.push(
          <ImagePreview key={`dlp-${i++}-${p.id}`} filename={p.filename} attachmentId={p.id} />,
        );
      }
      out.push(
        <DownloadCard
          key={`dlc-${i++}-${p.id}`}
          filename={p.filename}
          attachmentId={p.id}
        />,
      );
    } else {
      const href = `/api/attachments/${p.id}/download`;
      out.push(
        <DownloadLink
          key={`dlb-${i++}-${p.id}`}
          href={href}
          filename=""
          className="inline text-foreground underline decoration-foreground/40 underline-offset-2 hover:decoration-foreground"
          title="클릭하면 파일이 다운로드됩니다"
        >
          {body.slice(p.start, p.end)}
        </DownloadLink>,
      );
    }
    cursor = p.end;
  }
  if (cursor < body.length) out.push(body.slice(cursor));
  return out;
}

type Props = {
  message: ChatMessage;
  /** 도메인 키 → meta 매핑. message.domains 를 해석할 때 사용. */
  domainMap: Record<DomainKey, DomainMeta>;
  /** auto 모드일 때만 보임: 다른 도메인으로 같은 질문 다시 묻기. */
  onReroute?: (domain: DomainKey) => void;
  /** 재라우팅 옵션 (보통 현재 응답 도메인 제외한 나머지) */
  rerouteOptions?: DomainMeta[];
  /** 같은 user 메시지로 다시 응답 생성. 없으면 버튼 미표시. */
  onRegenerate?: () => void;
  /** 좋아요/싫어요 피드백 — 1차는 stub, §B DB 도입 후 영속화. */
  onFeedback?: (score: Feedback) => void;
  /** 이 메시지가 현재 스트리밍 중인지. 스트리밍 종료 후엔 자연 종결이 아니면 trail-off "…" 부착. */
  isStreaming?: boolean;
};

export function Message({
  message,
  domainMap,
  onReroute,
  rerouteOptions,
  onRegenerate,
  onFeedback,
  isStreaming,
}: Props) {
  const isUser = message.role === "user";
  const resolved = message.domains.map((k) => domainMap[k]).filter(Boolean);
  const primary = resolved[0];
  const avatarAccent = primary ? primary.accent : NEUTRAL_ACCENT;
  const avatarIcon = primary ? primary.icon : "logo";

  const [copied, setCopied] = useState(false);
  const [feedback, setFeedback] = useState<Feedback | null>(null);

  // 재라우팅 드롭다운 (auto 모드 + 옵션 있을 때만 노출).
  const [rerouteOpen, setRerouteOpen] = useState(false);
  const rerouteWrapRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!rerouteOpen) return;
    const onDocClick = (e: MouseEvent) => {
      if (!rerouteWrapRef.current?.contains(e.target as Node)) setRerouteOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setRerouteOpen(false);
    };
    window.addEventListener("mousedown", onDocClick);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDocClick);
      window.removeEventListener("keydown", onKey);
    };
  }, [rerouteOpen]);

  async function handleCopy() {
    // 클립보드엔 본문만 — 분리 마커/끊김 안내는 제외.
    const text = (isUser ? message.content : splitBodyAndNotice(message.content).body).trim();
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      /* 클립보드 권한 미허용 시 무시. */
    }
  }

  function handleFeedback(score: Feedback) {
    setFeedback(score);
    onFeedback?.(score);
  }

  // 본문/끊김 안내 분리. 사용자 메시지는 마커가 들어올 일이 없으므로 그대로.
  const split = isUser
    ? { body: message.content, notice: null as string | null }
    : splitBodyAndNotice(message.content);

  // 진행 마커 추출(있으면). 본문에서 마커 영역 제거 후 cleaned 텍스트로 trail-off/렌더 진행.
  const { cleaned: bodyNoProgress, latest: progressSteps } = isUser
    ? { cleaned: split.body, latest: null as ProgressStep[] | null }
    : extractProgress(split.body);

  // 모든 단계가 종결(done/error)이거나 progress 가 없을 때만 trail-off "…" 부착 대상.
  // 처리 중(active 포함) 상태에서 본문이 미완성이면 사용자가 끊긴 걸로 오인할 수 있으니
  // 마침표 추가는 보류한다.
  const stepsTerminal =
    !progressSteps || progressSteps.every((s) => s.state === "done" || s.state === "error");

  // 스트리밍 종료 후 본문이 자연 종결이 아니면(예: CJK silent cut, 모델 조기 EOS 등 어떤 이유로든)
  // trail-off "…" 한 글자만 부드럽게 부착해 사용자가 "응답이 끝났구나" 인지하게 한다.
  // 안내 박스가 이미 있으면 중복 시각 자극이라 부착하지 않는다.
  // 스트리밍 중에는 본문이 계속 자라므로 부착하지 않는다 — 깜빡임 방지.
  const body =
    !isUser && !isStreaming && !split.notice && bodyNoProgress && stepsTerminal && !NATURAL_END_RE.test(bodyNoProgress)
      ? `${bodyNoProgress}…`
      : bodyNoProgress;
  const { notice } = split;

  // 액션 표시 조건: assistant + 본문 있음. (스트리밍 중인 빈 메시지는 액션 미노출)
  // progress 카드만 있는 처리 중 상태도 액션 미노출.
  const showActions = !isUser && Boolean(body.trim()) && stepsTerminal;

  return (
    <div className={cn("flex gap-3", isUser ? "flex-row-reverse" : "flex-row")}>
      <div
        className={cn(
          // 머신드 칩 — 라운드 스퀘어 + 링 + 상단 시트/드롭으로 입체. 테마와 통일.
          "grid h-8 w-8 shrink-0 place-items-center rounded-[10px] text-white ring-1",
          "shadow-[inset_0_1px_0_rgba(255,255,255,0.3),0_4px_10px_-4px_rgba(15,30,70,0.5)]",
          isUser
            ? "und-grad ring-black/10 dark:ring-white/10"
            : cn(avatarAccent.bg, "ring-black/10 dark:ring-white/10"),
        )}
        aria-hidden
      >
        <Icon name={isUser ? "user" : avatarIcon} className="h-4 w-4" />
      </div>
      <div className="flex max-w-[680px] flex-col gap-1.5">
        {/* 본문 박스 — content 가 비어있고 첨부만 있는 케이스는 본문 박스 자체를 숨긴다.
            progress 카드만 있는 처리 중 상태도 박스는 표시(카드를 담아야 함). */}
        {(body || progressSteps || !isUser || (message.attachments?.length ?? 0) === 0) && (
          <div
            className={cn(
                "text-[15px] leading-[1.7] tracking-[-0.005em] transition-shadow duration-200",
              isUser
                ? // 사용자 말풍선 — 브랜드 그라디언트 + 입체(테마와 통일).
                  "rounded-2xl rounded-tr-md bubble-user px-4 py-3 text-white"
                : cn(
                    // 어시스턴트 — 떠 있는 머신드 패널(서피스+보더+인셋/드롭) + 도메인 accent 레일.
                    "relative rounded-2xl rounded-tl-md border border-border bg-surface-raised text-foreground",
                    "px-4 py-3.5 anim-fade-in-up",
                    isStreaming ? "border-accent/30 bubble-live" : "bubble-panel",
                  ),
            )}
            data-streaming={!isUser && isStreaming ? "true" : undefined}
          >
            {isUser ? (
              <p className="whitespace-pre-wrap">{body}</p>
            ) : (
              <div className="prose-chat whitespace-pre-wrap">
                {/* 도메인 accent 레일 — 라우팅된 답변에 좌측 하이라이트(어디로 연결됐는지 색으로). */}
                {primary && (
                  <span
                    aria-hidden
                    className={cn(
                      "absolute left-0 top-3 bottom-3 w-[3px] rounded-full",
                      avatarAccent.bg,
                    )}
                  />
                )}
                {progressSteps && <ProgressCard steps={progressSteps} />}
                {body ? (
                  <>
                    {/* 자금 비교·검증 결과는 전용 카드로(스트리밍 완료 후 — 부분 텍스트 파싱 방지). */}
                    {!isStreaming && isReconcileSummary(body) ? (
                      <ReconcileResultCard body={body} />
                    ) : (
                      <>
                        {renderBodyWithDownloads(body)}
                        {isStreaming && <StreamingCaret />}
                      </>
                    )}
                  </>
                ) : progressSteps ? null /* progress 카드가 자체 진행 표시 역할 */ : (
                  <ThinkingIndicator />
                )}
              </div>
            )}
          </div>
        )}

        {/* 첨부 칩 — user 메시지의 첨부는 본문(기능 수행 문구) "아래 줄"에 노출. */}
        {isUser && message.attachments && message.attachments.length > 0 && (
          <div className={cn("flex flex-wrap gap-1.5", "justify-end")}>
            {message.attachments.map((a) => (
              // 검증된 다운로드 — 인증 만료 시 에러 응답을 파일로 저장하지 않는다.
              <DownloadLink
                key={a.id}
                href={`/api/attachments/${a.id}/download`}
                filename={a.filename}
                title={`${a.filename} · ${a.mime} · ${formatBytes(a.size_bytes)}`}
                className="group inline-flex max-w-[260px] items-center gap-2 rounded-md bg-surface-raised px-2 py-1.5 text-foreground ring-1 ring-foreground/10 hover:ring-foreground/25"
              >
                <Icon name="paperclip" className="h-3.5 w-3.5 shrink-0 text-foreground-subtle" />
                <span className="truncate text-[12px]">{a.filename}</span>
                <span className="shrink-0 text-[10px] text-foreground-subtle">
                  {formatBytes(a.size_bytes)}
                </span>
              </DownloadLink>
            ))}
          </div>
        )}

        {/* 끊김 안내 — 본문과 명확히 분리된 보조 박스. */}
        {!isUser && notice && (
          <div
            role="note"
            className="flex items-start gap-2 rounded-md border border-dashed border-foreground/20 bg-foreground/5 px-3 py-2 text-[12px] leading-relaxed text-foreground-subtle"
          >
            <Icon name="help" className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>{notice}</span>
          </div>
        )}

        {/* 메시지 액션 — assistant 한정. 복사 / 재생성 / 좋아요 / 싫어요 / 다른 도메인. 모두 좌측에서 같은 간격으로 정렬. */}
        {showActions && (
          <div className="flex items-center gap-0.5 px-1">
            <ActionButton
              label={copied ? "복사됨" : "복사"}
              icon="copy"
              onClick={handleCopy}
              activeTone={copied}
            />
            {onRegenerate && (
              <ActionButton label="재생성" icon="refresh" onClick={onRegenerate} />
            )}
            <ActionButton
              label="좋아요"
              icon="thumb-up"
              onClick={() => handleFeedback("up")}
              pressed={feedback === "up"}
            />
            <ActionButton
              label="싫어요"
              icon="thumb-down"
              onClick={() => handleFeedback("down")}
              pressed={feedback === "down"}
            />
            {onReroute && rerouteOptions && rerouteOptions.length > 0 && (
              <div ref={rerouteWrapRef} className="relative">
                <button
                  type="button"
                  onClick={() => setRerouteOpen((v) => !v)}
                  aria-label="다른 도메인으로 다시 묻기"
                  aria-expanded={rerouteOpen}
                  aria-haspopup="menu"
                  title="다른 도메인으로 다시 묻기"
                  className={cn(
                    "inline-flex h-7 items-center gap-1 rounded-md px-1.5 text-[11px] transition",
                    rerouteOpen
                      ? "bg-foreground/10 text-foreground"
                      : "text-foreground-subtle hover:bg-foreground/5 hover:text-foreground",
                  )}
                >
                  <Icon name="chat" className="h-3.5 w-3.5" />
                  <span>다른 도메인</span>
                  <Icon name="chevron-down" className="h-3 w-3" />
                </button>
                {rerouteOpen && (
                  <div
                    role="menu"
                    className="absolute left-0 top-full z-20 mt-1 flex min-w-[160px] flex-col gap-0.5 rounded-md bg-surface-raised p-1 shadow-md ring-1 ring-foreground/15"
                  >
                    {rerouteOptions.map((d) => (
                      <button
                        key={d.key}
                        type="button"
                        role="menuitem"
                        onClick={() => {
                          onReroute(d.key);
                          setRerouteOpen(false);
                        }}
                        className={cn(
                          "inline-flex items-center gap-2 rounded-sm px-2 py-1.5 text-left text-[12px] text-foreground hover:bg-foreground/5",
                        )}
                      >
                        <span className={cn("h-1.5 w-1.5 rounded-full", d.accent.dot)} aria-hidden />
                        <Icon name={d.icon} className={cn("h-3.5 w-3.5", d.accent.softText)} />
                        <span className="truncate">{d.label}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* 응답 도메인 배지 — 아이콘+텍스트 (시크/로보틱 톤, sharp corner) */}
        {!isUser && resolved.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5 px-1">
            <span className="text-[10px] font-medium tracking-tight text-foreground-subtle">응답</span>
            {resolved.map((d) => (
              <span
                key={d.key}
                className={cn(
                  "inline-flex items-center gap-1 rounded-sm px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset",
                  d.accent.soft,
                  d.accent.softText,
                  d.accent.ring,
                )}
              >
                <Icon name={d.icon} className="h-3 w-3" />
                {d.label}
              </span>
            ))}
          </div>
        )}

        {/* RAG 출처 칩 — 사내 자료 검색 결과가 답변 근거로 사용된 경우. */}
        {!isUser && message.sources && message.sources.length > 0 && (
          <div className="mt-1 flex flex-wrap items-center gap-1.5 px-1">
            <span className="text-[10px] font-medium tracking-tight text-foreground-subtle">출처</span>
            {message.sources.map((s) => (
              <span
                key={s.id}
                title={s.snippet}
                className="inline-flex items-center gap-1 rounded-sm bg-amber-500/10 px-1.5 py-0.5 text-[11px] font-medium text-amber-700 ring-1 ring-inset ring-amber-500/30 dark:bg-amber-500/15 dark:text-amber-300"
              >
                <Icon name="folder" className="h-3 w-3" />
                <span className="max-w-[280px] truncate">{s.source_label}</span>
                <span className="ml-0.5 text-[9px] tabular-nums text-amber-700/70 dark:text-amber-300/60">
                  {Math.round(s.score * 100)}%
                </span>
              </span>
            ))}
          </div>
        )}

      </div>
    </div>
  );
}

type ActionButtonProps = {
  label: string;
  icon: "copy" | "refresh" | "thumb-up" | "thumb-down";
  onClick: () => void;
  pressed?: boolean;
  activeTone?: boolean;
};

function ActionButton({ label, icon, onClick, pressed, activeTone }: ActionButtonProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      aria-pressed={pressed}
      className={cn(
        "inline-flex h-7 items-center gap-1 rounded-md px-1.5 text-[11px] transition",
        pressed
          ? "bg-foreground/10 text-foreground"
          : activeTone
            ? "text-foreground"
            : "text-foreground-subtle hover:bg-foreground/5 hover:text-foreground",
      )}
    >
      <Icon name={icon} className="h-3.5 w-3.5" />
      {(pressed || activeTone) && <span>{label}</span>}
    </button>
  );
}

/**
 * 어시스턴트가 첫 토큰을 받기 전(=생성 중) 표시. 부드러운 3-dot wave + 안내 텍스트.
 * 기존 Pulse 보다 시각적으로 강화 — 사용자가 "지금 무슨 일이 일어나고 있는지" 명확.
 */
function ThinkingIndicator() {
  return (
    <span className="inline-flex items-center gap-2 text-[13px] text-foreground-subtle">
      <span className="inline-flex items-center gap-1">
        <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-current [animation-delay:0ms] [animation-duration:1.2s]" />
        <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-current [animation-delay:150ms] [animation-duration:1.2s]" />
        <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-current [animation-delay:300ms] [animation-duration:1.2s]" />
      </span>
      <span className="italic">생각 중…</span>
    </span>
  );
}

/**
 * 어시스턴트가 토큰을 받는 중(=쓰는 중) 본문 끝에 깜빡이는 커서. 글이 쓰여지고 있다는
 * 시각 신호 — 빈 답변/끊김과 구분된다.
 */
function StreamingCaret() {
  return (
    <span
      aria-hidden
      className={cn(
        "ml-0.5 inline-block h-[1.05em] w-[2px] -translate-y-[0.05em] align-middle",
        "bg-accent anim-caret",
        "rounded-[1px]",
      )}
    />
  );
}
