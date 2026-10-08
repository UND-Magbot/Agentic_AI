// 제안서 작성 1단계 — 왼쪽: 필수 질문 답변 폼(14문항) + 맨 아래 선택 질문(그리퍼와 툴 구성), 오른쪽: 이미지·파일 첨부(사용자 결정 2026-09-29).
// 제출 후 AI 가 답·첨부에서 나머지 문항을 채우고, 그래도 필요한 정보가 있으면 작업 화면에서 추가로 묻는다.
// 제출하면 프로젝트를 만들고 작업 화면(/proposals/[id])으로 이동한다.
"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import type { AssetRole, EssentialGroup, ProposalCatalog } from "@/lib/shared/proposal-projects";
import { Icon } from "@/components/shared/ui/icon";
import { cn } from "@/lib/shared/utils";

const DRAFT_KEY = "und-proposal-intake-draft";
const IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp"];
const MAX_FILES = 20;

type Draft = { title: string; request: string; answers?: Record<string, string> };
/** 외부(Codex) 전송 가능 여부 — 이미지이고 가격 근거가 아닐 때(사용자 결정 2026-09-29). */
function canSendExternal(role: AssetRole, mime: string): boolean {
  return role !== "price" && IMAGE_TYPES.includes(mime);
}

type PendingFile = { key: string; file: File; role: AssetRole; note: string; externalOk: boolean };

function readDraft(): Draft | null {
  try {
    const raw = window.localStorage.getItem(DRAFT_KEY);
    return raw ? (JSON.parse(raw) as Draft) : null;
  } catch {
    return null;
  }
}

function writeDraft(d: Draft | null) {
  try {
    if (d) window.localStorage.setItem(DRAFT_KEY, JSON.stringify(d));
    else window.localStorage.removeItem(DRAFT_KEY);
  } catch {
    /* 저장 불가(사생활 모드 등) — 임시 저장 없이 진행 */
  }
}

function guessRole(file: File): AssetRole {
  return IMAGE_TYPES.includes(file.type) ? "appearance" : "content";
}

function formatBytes(n: number): string {
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)}KB`;
  return `${(n / (1024 * 1024)).toFixed(1)}MB`;
}

async function upload(file: File): Promise<number> {
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch("/api/attachments", { method: "POST", body: fd });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof d?.detail === "string" ? d.detail : `${file.name} 업로드 실패`);
  return d.id as number;
}

const inputCls =
  "w-full resize-y rounded-lg border border-foreground/15 bg-background px-3 py-2 text-[13px] leading-relaxed text-foreground placeholder:text-foreground-subtle/60 focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/40";

export function ProposalIntakeForm({
  assetRoles,
  essential,
  optional,
}: {
  assetRoles: ProposalCatalog["asset_roles"];
  essential: EssentialGroup[];
  optional: EssentialGroup[];
}) {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [request, setRequest] = useState("");
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [files, setFiles] = useState<PendingFile[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [restored, setRestored] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  // 이전에 쓰던 내용 복원 (첨부 파일은 브라우저 보안상 복원하지 않는다).
  // 브라우저 저장소는 서버 렌더에서 읽을 수 없어 마운트 후 1회 복원한다(초기값으로 읽으면 하이드레이션 불일치).
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    const d = readDraft();
    if (d) {
      setTitle(d.title ?? "");
      setRequest(d.request ?? "");
      setAnswers(d.answers ?? {});
      setRestored(true);
    }
  }, []);
  /* eslint-enable react-hooks/set-state-in-effect */

  useEffect(() => {
    const empty = !title && !request && Object.values(answers).every((v) => !v.trim());
    writeDraft(empty ? null : { title, request, answers });
  }, [title, request, answers]);

  const total = essential.reduce((n, g) => n + g.questions.length, 0);
  const essentialCodes = essential.flatMap((g) => g.questions.map((q) => q.code));
  const answered = essentialCodes.filter((c) => answers[c]?.trim()).length;
  const anyAnswer = Object.values(answers).some((v) => v.trim());

  function addFiles(list: FileList | null) {
    if (!list) return;
    setError(null);
    const next = [...files];
    for (const f of Array.from(list)) {
      if (next.length >= MAX_FILES) {
        setError(`첨부는 ${MAX_FILES}개까지 가능합니다.`);
        break;
      }
      next.push({ key: `${f.name}-${f.size}-${f.lastModified}`, file: f, role: guessRole(f), note: "", externalOk: false });
    }
    setFiles(next);
  }

  function patchFile(key: string, patch: Partial<PendingFile>) {
    setFiles((fs) =>
      fs.map((f) => {
        if (f.key !== key) return f;
        const n = { ...f, ...patch };
        // 외부 전송은 이미지만, 가격 근거는 제외(서버도 같은 규칙으로 검사).
        if (!canSendExternal(n.role, n.file.type)) n.externalOk = false;
        return n;
      }),
    );
  }

  async function submit() {
    if (busy) return;
    if (!anyAnswer && !request.trim() && files.length === 0) {
      setError("필수 질문에 답하거나 자료를 첨부해 주세요.");
      return;
    }
    setError(null);
    try {
      const assets = [];
      for (let i = 0; i < files.length; i++) {
        setBusy(`첨부 올리는 중 (${i + 1}/${files.length})`);
        const f = files[i];
        assets.push({ attachment_id: await upload(f.file), role: f.role, note: f.note, external_ok: f.externalOk });
      }
      setBusy("프로젝트 만드는 중");
      const r = await fetch("/api/proposal-projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title, request_text: request, assets,
          intake: Object.fromEntries(Object.entries(answers).filter(([, v]) => v.trim())),
        }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof d?.detail === "string" ? d.detail : "프로젝트를 만들지 못했습니다.");
      writeDraft(null);
      router.push(`/proposals/${d.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "프로젝트를 만들지 못했습니다.");
      setBusy(null);
    }
  }

  return (
    <div className="scroll-thin h-full overflow-y-auto">
      <div className="mx-auto flex max-w-6xl flex-col gap-6 px-4 py-8 md:px-6">
        <header>
          <div className="text-[12px] font-medium tracking-wide text-accent">제안서 만들기 · 1단계 필수 질문·자료</div>
          <h1 className="mt-1 text-[22px] font-semibold tracking-tight text-foreground">필수 질문에 답하고 자료를 첨부해 주세요</h1>
          <p className="mt-1.5 text-[13px] leading-relaxed text-foreground-subtle">
            로봇 자동화 제안서를 쓰는 데 꼭 필요한 {total}가지입니다({answered}/{total} 답변). 아는 만큼만 적고 모르면 비워 두세요.
            제출하면 AI 가 답변과 첨부에서 나머지를 채우고, 더 필요한 정보가 있으면 다음 화면에서 추가로 묻습니다.
            그리퍼 조합·예외 처리 같은 설계는 AI 가 제안하므로 미리 정하지 않아도 됩니다.
          </p>
          {restored && (
            <p className="mt-2 text-[12px] text-foreground-subtle">이전에 쓰던 내용을 불러왔습니다. 첨부 파일은 다시 추가해 주세요.</p>
          )}
        </header>

        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_380px] lg:items-start">
          <div className="flex flex-col gap-4">
            <section className="rounded-xl border border-border bg-surface p-4">
              <label className="flex flex-col gap-1">
                <span className="text-[13px] font-medium text-foreground">
                  프로젝트 이름 <span className="text-[12px] font-normal text-foreground-subtle">(선택 — 비워 두면 답변에서 정합니다)</span>
                </span>
                <input
                  id="proposal-title"
                  value={title}
                  maxLength={300}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder="예: 약액 보틀 주입 공정 자동화 실증"
                  className="rounded-lg border border-foreground/15 bg-background px-3 py-2 text-[14px] text-foreground focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/40"
                />
              </label>
            </section>

            {essential.map((g, gi) => (
              <section key={g.title} className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-4">
                <h2 className="text-[14px] font-semibold text-foreground">
                  <span className="mr-1.5 text-[12px] tabular-nums text-foreground-subtle">{gi + 1}</span>{g.title}
                </h2>
                {g.questions.map((q) => (
                  <label key={q.code} htmlFor={`q-${q.code}`} className="flex flex-col gap-1">
                    <span className="text-[13px] text-foreground">
                      {q.question}
                      {q.required && (
                        <span className="ml-1.5 rounded-full bg-accent-soft px-1.5 py-0.5 text-[10.5px] font-medium text-accent">컨셉 필수</span>
                      )}
                    </span>
                    <textarea
                      id={`q-${q.code}`}
                      rows={2}
                      maxLength={4000}
                      value={answers[q.code] ?? ""}
                      placeholder={`${q.example} — 모르면 비워 두세요`}
                      onChange={(e) => setAnswers((a) => ({ ...a, [q.code]: e.target.value }))}
                      className={inputCls}
                    />
                  </label>
                ))}
              </section>
            ))}

            {optional.map((g) => (
              <section key={g.title} className="flex flex-col gap-3 rounded-xl border border-dashed border-border bg-surface p-4">
                <div>
                  <h2 className="text-[14px] font-semibold text-foreground">
                    {g.title}
                    <span className="ml-1.5 rounded-full bg-foreground/8 px-1.5 py-0.5 text-[10.5px] font-medium text-foreground-muted">선택</span>
                  </h2>
                  <p className="mt-0.5 text-[12px] leading-relaxed text-foreground-subtle">
                    {g.note}
                  </p>
                </div>
                {g.questions.map((q) => (
                  <label key={q.code} htmlFor={`q-${q.code}`} className="flex flex-col gap-1">
                    <span className="text-[13px] text-foreground">{q.question}</span>
                    <textarea
                      id={`q-${q.code}`}
                      rows={2}
                      maxLength={4000}
                      value={answers[q.code] ?? ""}
                      placeholder={`${q.example} — 비워 두어도 됩니다`}
                      onChange={(e) => setAnswers((a) => ({ ...a, [q.code]: e.target.value }))}
                      className={inputCls}
                    />
                  </label>
                ))}
              </section>
            ))}

            <details className="rounded-xl border border-border bg-surface p-4">
              <summary className="cursor-pointer select-none text-[13px] font-medium text-foreground-muted">
                기타 메모 (선택) — 고객 메일·회의 메모를 붙여 넣으면 나머지 문항을 찾는 데 씁니다
              </summary>
              <textarea
                id="proposal-request"
                value={request}
                maxLength={8000}
                rows={8}
                onChange={(e) => setRequest(e.target.value)}
                className={cn(inputCls, "mt-2")}
              />
            </details>
          </div>

          <aside>
        <section className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-4 lg:sticky lg:top-4">
          <div>
            <h2 className="text-[15px] font-semibold text-foreground">자료·참고 이미지 첨부</h2>
            <p className="mt-0.5 text-[12px] leading-relaxed text-foreground-subtle">
              파일마다 용도를 고르세요. 로봇·카메라처럼 모양을 따라 그려야 하는 이미지는 &lsquo;외형 참조&rsquo;입니다.
              외부 AI 이미지 생성에 참고로 쓰려면 &lsquo;외부 전송 허용&rsquo;을 체크하세요(외형·치수·내용 이미지).
              체크하지 않은 이미지와 가격 근거 자료는 외부로 보내지 않습니다.
            </p>
            <p className="mt-1 text-[12px] leading-relaxed text-amber-700 dark:text-amber-300">
              주의: 이미지 속 로고·명판·가격표·글자는 가려지지 않고 그대로 외부로 전송됩니다(글자 가림은 문장에만 적용).
              보내기 전에 잘라 내거나 가린 이미지를 올려 주세요.
            </p>
          </div>
          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              addFiles(e.dataTransfer.files);
            }}
            className="flex items-center justify-center gap-2 rounded-lg border border-dashed border-foreground/25 px-3 py-4 text-[13px] text-foreground-muted hover:border-foreground/40 hover:bg-foreground/5"
          >
            <Icon name="paperclip" className="h-4 w-4" />
            클릭하거나 끌어다 놓기 (이미지·PDF·문서, 최대 {MAX_FILES}개)
          </button>
          <input
            ref={fileRef}
            id="proposal-files"
            type="file"
            multiple
            hidden
            onChange={(e) => {
              addFiles(e.target.files);
              e.target.value = "";
            }}
          />
          {files.length > 0 && (
            <ul className="flex flex-col gap-2">
              {files.map((f) => {
                const canExternal = canSendExternal(f.role, f.file.type);
                return (
                  <li key={f.key} className="flex flex-col gap-2 rounded-lg border border-foreground/10 bg-background p-3">
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[13px] text-foreground">{f.file.name}</div>
                      <div className="text-[11px] text-foreground-subtle">{formatBytes(f.file.size)}</div>
                    </div>
                    <select
                      aria-label={`${f.file.name} 용도`}
                      value={f.role}
                      onChange={(e) => patchFile(f.key, { role: e.target.value as AssetRole })}
                      className="rounded-md border border-foreground/15 bg-surface px-2 py-1 text-[12px] text-foreground"
                    >
                      {(Object.keys(assetRoles) as AssetRole[]).map((r) => (
                        <option key={r} value={r}>{assetRoles[r]}</option>
                      ))}
                    </select>
                    <input
                      aria-label={`${f.file.name} 메모`}
                      value={f.note}
                      maxLength={300}
                      onChange={(e) => patchFile(f.key, { note: e.target.value })}
                      placeholder="반영할 부분 (예: 양팔 몸체 형태만)"
                      className="min-w-0 rounded-md border border-foreground/15 bg-surface px-2 py-1 text-[12px] text-foreground"
                    />
                    <label
                      title={canExternal ? "이미지 속 로고·명판·가격표는 가려지지 않고 그대로 전송됩니다"
                        : f.role === "price" ? "가격 근거 자료는 외부로 보내지 않습니다" : "이미지만 외부로 보낼 수 있습니다"}
                      className={cn("flex items-center gap-1.5 text-[12px]", canExternal ? "text-foreground" : "text-foreground-subtle/60")}
                    >
                      <input
                        type="checkbox"
                        checked={f.externalOk}
                        disabled={!canExternal}
                        onChange={(e) => patchFile(f.key, { externalOk: e.target.checked })}
                      />
                      외부 전송 허용
                    </label>
                    <button
                      type="button"
                      aria-label={`${f.file.name} 제거`}
                      onClick={() => setFiles((fs) => fs.filter((x) => x.key !== f.key))}
                      className="grid h-7 w-7 place-items-center rounded-md text-foreground-subtle hover:bg-foreground/10 hover:text-foreground"
                    >
                      <Icon name="x" className="h-3.5 w-3.5" />
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
          </aside>
        </div>

        {error && (
          <div role="alert" className="rounded-md border border-red-500/30 bg-red-500/5 px-3 py-2 text-[13px] text-red-600 dark:text-red-400">
            {error}
          </div>
        )}

        <div className="flex items-center justify-end gap-3 pb-6">
          {busy && <span className="text-[12px] text-foreground-subtle">{busy}…</span>}
          <button
            type="button"
            onClick={() => void submit()}
            disabled={!!busy}
            className="und-grad inline-flex items-center gap-2 rounded-xl px-5 py-2.5 text-[14px] font-semibold text-white shadow-[0_8px_18px_-8px_rgba(37,99,235,0.6)] disabled:cursor-not-allowed disabled:opacity-60"
          >
            {busy ? <Icon name="refresh" className="h-4 w-4 animate-spin" /> : <Icon name="check" className="h-4 w-4" />}
            답변·자료 제출하고 AI 분석 시작
          </button>
        </div>
      </div>
    </div>
  );
}
