/**
 * 가벼운 className 합성기. clsx/tailwind-merge 없이 동작하도록 직접 구현.
 * 충돌 머지가 필요해지면 clsx + tailwind-merge 도입을 검토.
 */
export function cn(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

/**
 * 대화창 맨 아래로 — 그 대화창(가장 가까운 스크롤 칸) 하나만 움직인다.
 * scrollIntoView 는 바깥 페이지 스크롤까지 같이 옮겨, [변경 저장] 뒤 화면이 맨 위로 올라가는 문제가 있었다(사용자 2026-10-23).
 */
export function scrollChatToEnd(marker: HTMLElement | null): void {
  let box = marker?.parentElement ?? null;
  while (box && !(/(auto|scroll)/.test(getComputedStyle(box).overflowY) && box.scrollHeight > box.clientHeight)) {
    box = box.parentElement;
  }
  // 대화창이 넘치지 않으면(스크롤 칸 없음) 아무것도 움직이지 않는다 — 페이지 스크롤 칸까지 올라가면 그만둔다
  if (!box || box === document.scrollingElement || box.classList.contains("und-page-bg")) return;
  box.scrollTo({ top: box.scrollHeight, behavior: "smooth" });
}

/**
 * 버튼을 누른 자리 그대로 — 작업(저장 등) 뒤 위쪽 내용 높이가 바뀌어도 그 버튼이 화면의 같은 높이에 있게
 * 바깥 스크롤 칸을 그만큼 되돌린다(사용자 2026-10-23: [변경 저장] 뒤 창이 그대로 있게).
 */
export async function keepInPlace<T>(el: HTMLElement, work: () => Promise<T>): Promise<T> {
  const top = el.getBoundingClientRect().top;
  const result = await work();
  requestAnimationFrame(() => requestAnimationFrame(() => {
    if (!el.isConnected) return;
    let box: HTMLElement | null = el.parentElement;
    while (box && !(/(auto|scroll)/.test(getComputedStyle(box).overflowY) && box.scrollHeight > box.clientHeight)) box = box.parentElement;
    const diff = el.getBoundingClientRect().top - top;
    if (box && Math.abs(diff) >= 1) box.scrollTop += diff;
  }));
  return result;
}

export function formatRelativeTime(iso: string, now: Date = new Date()): string {
  const then = new Date(iso);
  const diffMs = now.getTime() - then.getTime();
  const min = Math.round(diffMs / 60_000);
  if (min < 1) return "방금";
  if (min < 60) return `${min}분 전`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}시간 전`;
  const day = Math.round(hr / 24);
  if (day < 7) return `${day}일 전`;
  return then.toLocaleDateString("ko-KR");
}

/** RECENT/채팅 목록을 시간 기준으로 4 그룹(오늘/어제/이번 주/이전)으로 묶는다. */
export type RecentTimeGroupKey = "today" | "yesterday" | "thisWeek" | "older";

const RECENT_GROUP_ORDER: RecentTimeGroupKey[] = ["today", "yesterday", "thisWeek", "older"];
const RECENT_GROUP_LABEL: Record<RecentTimeGroupKey, string> = {
  today: "오늘",
  yesterday: "어제",
  thisWeek: "이번 주",
  older: "이전",
};

function getRecentGroup(iso: string, now: Date = new Date()): RecentTimeGroupKey {
  const then = new Date(iso).getTime();
  const today0 = new Date(now);
  today0.setHours(0, 0, 0, 0);
  const t0 = today0.getTime();
  const day = 24 * 60 * 60 * 1000;
  if (then >= t0) return "today";
  if (then >= t0 - day) return "yesterday";
  if (then >= t0 - 6 * day) return "thisWeek";
  return "older";
}

export function groupRecentByTime<T extends { updatedAt: string }>(
  items: T[],
  now: Date = new Date(),
): Array<{ key: RecentTimeGroupKey; label: string; items: T[] }> {
  const buckets = new Map<RecentTimeGroupKey, T[]>();
  for (const it of items) {
    const g = getRecentGroup(it.updatedAt, now);
    if (!buckets.has(g)) buckets.set(g, []);
    buckets.get(g)!.push(it);
  }
  for (const arr of buckets.values()) {
    arr.sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime());
  }
  return RECENT_GROUP_ORDER.filter((g) => buckets.has(g)).map((g) => ({
    key: g,
    label: RECENT_GROUP_LABEL[g],
    items: buckets.get(g)!,
  }));
}
