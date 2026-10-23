// 견적서 작성 칸 공용 스타일(사용자 2026-10-23) — 견적서 머리·기본 품목 줄·묶음 안 품목 줄이 같은 모양을 쓴다.

/** 흰 입력칸 + 테두리 — 회색으로 채운 칸은 비활성처럼 보여서 */
export const HEAD_FIELD =
  "h-10 w-full min-w-0 rounded-[10px] border border-border-strong bg-surface-raised px-3 text-[13.5px] font-medium text-foreground placeholder:font-normal placeholder:text-foreground-subtle/70 hover:border-foreground/30 focus:border-accent focus:outline-none focus:ring-[3px] focus:ring-accent/15 disabled:opacity-60";
/** 여러 줄 칸 */
export const HEAD_AREA = HEAD_FIELD.replace("h-10 ", "min-h-10 py-2 ");
/** 폭을 직접 정하는 칸(수량·단위·단가) — cn 은 클래스를 합치지 않아 w-full 이 w-[64px] 를 이기므로 뺀 것 */
export const ROW_FIELD = HEAD_FIELD.replace("w-full ", "");
/** 품목 줄 칸 이름 + 칸 */
export const ROW_LABEL = "flex min-w-0 flex-col gap-1.5 text-[12px] font-semibold text-foreground-muted";
/** 품목 줄 카드 — 자동 줄(물류·인건비)은 회색 바탕 */
export const ROW_CARD = "rounded-xl border border-border bg-surface-raised p-3.5 transition-colors hover:border-accent/35 hover:bg-accent/[0.02]";
export const ROW_CARD_AUTO = "rounded-xl border border-border bg-foreground/[0.025] p-3.5";

/** 품목 줄 버튼 — 누를 수 있는 게 보이게 테두리 + 입력칸과 같은 높이(40px)로 줄 맞춤(사용자 2026-10-23) */
export const ROW_BTN =
  "inline-flex h-10 shrink-0 items-center justify-center gap-1.5 rounded-[10px] border border-border-strong bg-surface-raised px-3 text-[12.5px] font-semibold text-foreground-muted shadow-[0_1px_0_rgba(15,23,42,0.04)] hover:border-foreground/30 hover:bg-foreground/[0.04] hover:text-foreground disabled:pointer-events-none disabled:opacity-40";
export const ROW_ICON_BTN = ROW_BTN.replace("px-3", "w-10 px-0");
/** 금액 — 입력칸과 같은 높이의 읽기 전용 상자(오른쪽 맞춤, 굵게) */
export const AMOUNT_BOX =
  "flex h-10 min-w-[150px] items-center justify-end whitespace-nowrap rounded-[10px] bg-foreground/[0.04] px-3 text-[16px] font-extrabold tracking-tight text-foreground";
