// 필수 칸 표시 — 영업 화면 공용(사용자 2026-11-16: 무심코 못 지나가게 눈에 띄게, 모든 필수 표시를 똑같이).
// 칸: REQ 를 펼쳐 넣으면(required + data-req) 비어 있을 때 빨간 두꺼운 테두리·옅은 빨간 바탕·빛(globals.css '필수 칸').
//     비었는지는 브라우저의 required 검사(:invalid)로 판단해서 화면마다 따로 계산하지 않는다.
// 이름: ReqLabel — 굵게 + 빨간 별 + 비어 있으면 빨간 '필수' 알약, 채우면 초록 체크(같은 req-scope 안의 칸 기준).
import { cloneElement, isValidElement } from "react";

import { Icon } from "@/components/shared/ui/icon";
import { cn } from "@/lib/shared/utils";

/** 필수 칸 속성 — <input {...REQ} …>. 조건부 필수는 {...req(조건)} */
export const REQ = { required: true, "data-req": "" } as const;
export const req = (on: boolean) => (on ? REQ : {});
/** 조건부 필수 칸(예: 한 줄 이상) — 채웠으면 초록, 아무것도 안 채웠으면 필수(빨강), 그 밖엔 보통 */
export const reqOr = (filled: boolean, need: boolean) => (filled ? FILLED : need ? REQ : {});
const FILLED = { "data-req-filled": "" } as const;

/** 필수 칸 이름 — 감싸는 요소(label 등)에 REQ_SCOPE 클래스가 있어야 '필수'↔체크가 바뀐다 */
export function ReqLabel({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <span className={cn("inline-flex flex-wrap items-center gap-1.5 font-bold text-foreground", className)}>
      {children}
      <span aria-hidden className="-ml-0.5 text-red-500">*</span>
      <span className="req-need rounded-full bg-red-500 px-1.5 text-[10px] font-bold leading-[16px] text-white">필수</span>
      <span className="req-ok items-center text-emerald-600 dark:text-emerald-400" aria-label="입력됨"><Icon name="check" className="h-3 w-3" /></span>
    </span>
  );
}

/** 표 머리 칸처럼 칸과 떨어진 곳 — 굵게 + 빨간 별만 */
export function ReqMark({ children }: { children: React.ReactNode }) {
  return <span className="font-bold text-foreground">{children}<span aria-hidden className="ml-0.5 text-red-500">*</span></span>;
}

export const REQ_SCOPE = "req-scope";

const FIELD_TAGS = new Set(["input", "select", "textarea"]);

/** 칸 요소(input·select·textarea)에 REQ 를 붙인다 — 칸을 만드는 함수가 따로 있을 때 */
export function withReq(child: React.ReactNode): React.ReactNode {
  return isValidElement(child) && typeof child.type === "string" && FIELD_TAGS.has(child.type)
    ? cloneElement(child as React.ReactElement<Record<string, unknown>>, REQ) : child;
}

/** 'X *' 처럼 끝에 별이 붙은 칸 이름 → [이름 요소, 필수 여부]. 칸(input·select·textarea)이 바로 아래면 REQ 를 붙인다. */
export function reqField(label: string, child: React.ReactNode): [React.ReactNode, React.ReactNode, boolean] {
  const on = /\s\*$/.test(label);
  if (!on) return [label, child, false];
  const el = isValidElement(child) && typeof child.type === "string" && FIELD_TAGS.has(child.type)
    ? cloneElement(child as React.ReactElement<Record<string, unknown>>, REQ) : child;
  return [<ReqLabel key="l">{label.replace(/\s\*$/, "")}</ReqLabel>, el, true];
}
