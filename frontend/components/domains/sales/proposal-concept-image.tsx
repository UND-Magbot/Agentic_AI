// 컨셉 이미지 1장 — 그림 + 편집 가능한 라벨 덧씌우기 + 가이드 5항목 검수 초안 + 승인.
"use client";

import { useState } from "react";
import type { ConceptImage, ImageLabel } from "@/lib/shared/proposal-projects";
import { Icon } from "@/components/shared/ui/icon";
import { cn } from "@/lib/shared/utils";
import { ErrorBox, inputCls, primaryBtn, secondaryBtn, useProjectAction } from "./proposal-actions";

const MAX_LABELS = 12;

function CheckMark({ ok }: { ok: boolean | null }) {
  if (ok === true) return <span className="text-emerald-600 dark:text-emerald-400">맞음</span>;
  if (ok === false) return <span className="text-red-600 dark:text-red-400">확인 필요</span>;
  return <span className="text-foreground-subtle">미판정</span>;
}

export function ConceptImageCard({
  projectId,
  image,
  editable,
}: {
  projectId: number;
  image: ConceptImage;
  editable: boolean;
}) {
  const { busy, error, run } = useProjectAction(projectId);
  const [labels, setLabels] = useState<ImageLabel[]>(image.labels);
  const [selected, setSelected] = useState<number | null>(null);
  const [dirty, setDirty] = useState(false);
  const src = image.attachment_id ? `/api/attachments/${image.attachment_id}/download` : null;

  function update(next: ImageLabel[]) {
    setLabels(next);
    setDirty(true);
  }

  function place(e: React.MouseEvent<HTMLDivElement>) {
    if (!editable || selected === null) return;
    const r = e.currentTarget.getBoundingClientRect();
    const x = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
    const y = Math.min(1, Math.max(0, (e.clientY - r.top) / r.height));
    update(labels.map((l, i) => (i === selected ? { ...l, x: +x.toFixed(3), y: +y.toFixed(3) } : l)));
  }

  if (image.status === "generating") {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-border px-3 py-3 text-[12.5px] text-foreground-muted">
        <Icon name="refresh" className="h-4 w-4 animate-spin text-accent" /> v{image.version} 생성 중…
      </div>
    );
  }
  if (image.status === "failed") {
    return (
      <div className="rounded-lg border border-red-500/30 bg-red-500/5 px-3 py-2 text-[12.5px] text-red-600 dark:text-red-400">
        v{image.version} 생성 실패: {image.error || "알 수 없는 오류"}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <div
        className={cn("relative overflow-hidden rounded-lg border border-border bg-white", editable && selected !== null && "cursor-crosshair")}
        onClick={place}
      >
        {src && (
          // eslint-disable-next-line @next/next/no-img-element -- 인증 쿠키가 필요한 첨부 프록시라 next/image 최적화 불가
          <img src={src} alt={`컨셉 이미지 v${image.version}`} className="block h-auto w-full" />
        )}
        {labels.map((l, i) => (
          <span
            key={i}
            style={{ left: `${l.x * 100}%`, top: `${l.y * 100}%` }}
            className={cn(
              "pointer-events-none absolute -translate-x-1/2 -translate-y-1/2 rounded-md px-1.5 py-0.5 text-[11px] font-semibold shadow",
              i === selected ? "bg-accent text-white" : "bg-slate-900/80 text-white",
            )}
          >
            {l.text}
          </span>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-2 text-[12px] text-foreground-subtle">
        <span className={cn("rounded-full px-2 py-0.5 font-medium", image.approved ? "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300" : "bg-foreground/8 text-foreground-muted")}>
          v{image.version} {image.approved ? "승인됨" : "초안"}
        </span>
        {image.ref_sent > 0 && (
          <span>
            참고 이미지 {image.ref_sent}장 보냄 ·{" "}
            {image.ref_used ? `${image.ref_used}장 반영` : "반영 안 됨"}
          </span>
        )}
      </div>

      {/* 검수 초안은 이미지가 나온 뒤 뒤에서 채워진다(workflow.VISION_PENDING_NOTE) — 그동안은 한 줄로 */}
      {image.checks.length > 0 && image.checks.every((c) => c.ok === null && c.note.startsWith("AI 검수 초안 작성 중")) ? (
        <p className="flex items-center gap-2 text-[12px] text-foreground-subtle">
          <Icon name="refresh" className="h-3.5 w-3.5 animate-spin" /> AI 검수 초안 작성 중(약 40초) — 이미지는 먼저 보고 승인해도 됩니다
        </p>
      ) : (
        <table className="w-full text-[12px]">
          <caption className="pb-1 text-left text-[12px] font-medium text-foreground-muted">AI 검수 초안(사내 AI 판정) — 승인 전에 직접 확인하세요</caption>
          <tbody>
            {image.checks.map((c) => (
              <tr key={c.key} className="border-t border-border/60">
                <td className="py-1 pr-2 text-foreground">{c.label}</td>
                <td className="w-16 py-1 pr-2"><CheckMark ok={c.ok} /></td>
                <td className="py-1 text-foreground-subtle">{c.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {editable && (
        <div className="flex flex-col gap-1.5">
          <div className="text-[12px] font-medium text-foreground-muted">
            라벨 (PPT 에서 편집 가능한 글상자로 들어갑니다) — 라벨을 고른 뒤 그림을 누르면 그 자리로 옮깁니다.
          </div>
          <ul className="flex flex-col gap-1">
            {labels.map((l, i) => (
              <li key={i} className="flex items-center gap-1.5">
                <input
                  type="radio"
                  name={`label-${image.id}`}
                  aria-label={`${l.text || "라벨"} 위치 옮기기`}
                  checked={selected === i}
                  onChange={() => setSelected(i)}
                />
                <input
                  className={inputCls}
                  value={l.text}
                  maxLength={20}
                  onChange={(e) => update(labels.map((x, j) => (j === i ? { ...x, text: e.target.value } : x)))}
                />
                <button
                  type="button"
                  aria-label="라벨 삭제"
                  className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-foreground-subtle hover:bg-foreground/10"
                  onClick={() => {
                    update(labels.filter((_, j) => j !== i));
                    setSelected(null);
                  }}
                >
                  <Icon name="x" className="h-3.5 w-3.5" />
                </button>
              </li>
            ))}
          </ul>
          <ErrorBox error={error} />
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className={secondaryBtn}
              disabled={labels.length >= MAX_LABELS}
              onClick={() => {
                update([...labels, { text: "새 라벨", x: 0.5, y: 0.5 }]);
                setSelected(labels.length);
              }}
            >
              라벨 추가
            </button>
            <button
              type="button"
              className={secondaryBtn}
              disabled={!dirty || !!busy}
              onClick={() => void run("라벨 저장 중", "labels", { image_id: image.id, labels }, () => setDirty(false))}
            >
              라벨 저장
            </button>
            {!image.approved && (
              <button
                type="button"
                className={primaryBtn}
                disabled={dirty || !!busy}
                title={dirty ? "라벨을 먼저 저장하세요" : undefined}
                onClick={() => void run("승인 중", "image-approve", { image_id: image.id })}
              >
                <Icon name="check" className="h-4 w-4" /> 이 이미지 승인
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
