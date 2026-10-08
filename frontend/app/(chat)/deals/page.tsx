import { redirect } from "next/navigation";

// 영업 건 관리는 회사 제품 추천 화면의 탭 하나(사용자 2026-10-06) — 예전 주소로 와도 그 탭으로 보낸다.
export default function DealsPage() {
  redirect("/proposals/recommend?tab=deals");
}
