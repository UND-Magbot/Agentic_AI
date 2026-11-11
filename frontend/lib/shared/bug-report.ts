// 버그 신고(사용자 2026-10-08 — 프로필 메뉴 [버그 등록]과 같은 /api/bug-reports). 화면에서 자동으로 신고할 때 쓴다.
// 예: 고친 견적서·발주서 파일의 AI 판독이 실패하거나 이상할 때 [버그 신고 후 넘어가기](사용자 2026-11-11).

/** 버그를 등록하고 접수 번호(UND-00012)를 돌려준다. 실패하면 서버 문장으로 Error. */
export async function fileBugReport(title: string, content: string): Promise<string> {
  const fd = new FormData();
  fd.append("title", title.slice(0, 120));
  fd.append("content", content.slice(0, 5000));
  const r = await fetch("/api/bug-reports", { method: "POST", body: fd });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof d?.detail === "string" ? d.detail : "버그를 신고하지 못했습니다.");
  return String(d.report_no ?? "");
}

/** 고친 파일 판독 실패 신고 본문 — 무엇을 올렸고 AI 가 무엇을 읽었는지(개발자가 그대로 재현할 수 있게). */
export function readFailureContent(p: {
  where: string; filename: string; storageKey?: string; status?: string; summary?: string;
  lines: string[];
}): string {
  return [
    "[자동 신고] 고친 파일의 AI 판독이 실패했거나 이상해서, 담당자가 [버그 신고 후 넘어가기]로 이 파일 그대로 발행했습니다.",
    `- 화면: ${p.where}`,
    `- 파일: ${p.filename}${p.storageKey ? ` (저장 위치 ${p.storageKey})` : ""}`,
    `- 판독 결과: ${p.status ?? "-"} — ${p.summary ?? "-"}`,
    `- 읽은 품목 ${p.lines.length}줄${p.lines.length ? ":" : ""}`,
    ...p.lines.slice(0, 15).map((x, i) => `  ${i + 1}. ${x}`),
  ].join("\n");
}
