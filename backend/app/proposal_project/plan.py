"""공정 컨셉 계획 확인 — 이미지를 그리기 전에 글로 먼저 결정한다(사용자 요청 2026-09-30).

흐름: 핵심 질문 완료 → AI 가 컨셉을 제안(로봇 요구 사양·그리퍼 추천 포함) → 이 단계에서
  - 로봇 팔 모델: 로봇 DB(robot_specs)에서 요구 사양(가반하중·도달거리·IP)을 만족하는 후보, 여유가 가장 적은 것을 추천
  - 그리퍼: 회사 그리퍼 제품(지식 카드) 후보, AI 추천을 기본 선택
  - 회사 경험 반영 여부, 프롬프트로 계획 수정(글만 고침, 이미지 안 그림)
을 정하고 [이 계획으로 컨셉 이미지 그리기] 를 누르면 그때 한 번 그린다 — 그림을 보고 "로봇을 바꿔 달라"며
다시 그리는 일을 줄이려는 단계.

alternatives[].plan = {robot_need{arm,payload_kg,reach_mm,ip_min,why}, robot_candidates[], robot_pick,
                       gripper_ai, gripper_why, gripper_candidates[], gripper_pick, confirmed}
plan 이 없는 컨셉(이 단계 이전에 만든 프로젝트)은 확정된 것으로 본다.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import text

_log = logging.getLogger("proposal_project.plan")

ROBOT_CANDIDATES = 3
_MOBILE_RE = re.compile(r"AMR|AGV|이동형|모바일|자율주행", re.I)
PICK_PREFIX = "선택 모델:"          # 확정 때 구성 문장 끝에 붙는 한 줄(그림·견적·제안서에 모델이 들어가게)


_MAKER_WORDS = {"DOBOT": ("dobot", "두봇"), "한화로보틱스": ("한화", "hanwha"),
                "레인보우로보틱스": ("레인보우", "rainbow"), "Universal Robots": ("유니버설", "universal robots", " ur "),
                "KUKA": ("kuka", "쿠카")}
_EXCLUDE_WORDS = ("빼", "제외", "말고", "말구", "빼고", "없이")


def maker_intent(request: str) -> tuple[list[str], list[str]]:
    """계획 수정 요청 속 제조사 → (지정, 제외). AI 가 robot_need 에 안 적어도 요청대로 되게(QA 3회차:
    '한화 제품 중에서 골라 줘' 가 반영되지 않음). 제조사 이름 바로 뒤 몇 글자에 빼/제외/말고가 있으면 제외."""
    low = f" {request.lower()} "
    makers, excludes = [], []
    for maker, words in _MAKER_WORDS.items():
        for w in words:
            i = low.find(w)
            if i < 0:
                continue
            tail = low[i + len(w): i + len(w) + 8]
            (excludes if any(x in tail for x in _EXCLUDE_WORDS) else makers).append(maker)
            break
    return makers, excludes


def apply_maker_intent(alt: dict[str, Any], request: str) -> dict[str, Any]:
    makers, excludes = maker_intent(request)
    if not makers and not excludes:
        return alt
    plan = dict(alt.get("plan") or {})
    need = dict(plan.get("robot_need") or {"arm": True})
    if makers:
        need["makers"] = makers
    if excludes:
        need["exclude_makers"] = sorted(set((need.get("exclude_makers") or []) + excludes))
        need["makers"] = [m for m in need.get("makers") or [] if m not in excludes]
    plan["robot_need"] = need
    return {**alt, "plan": plan}


def pending(project: dict[str, Any]) -> bool:
    """계획 확인 중인가 — 계획이 있고 확정 전인 컨셉이 하나라도 있으면."""
    return project.get("stage") == "concept" and any(
        isinstance(a.get("plan"), dict) and not a["plan"].get("confirmed") for a in project.get("alternatives") or [])


def alt_pending(alt: dict[str, Any]) -> bool:
    return isinstance(alt.get("plan"), dict) and not alt["plan"].get("confirmed")


def robot_row(r: dict[str, Any]) -> dict[str, Any]:
    """화면·저장용 로봇 후보 한 줄."""
    from ..company_knowledge.robot_specs import spec_line

    return {"maker": r["maker"], "model": r["model"], "label": f"{r['maker']} {r['model']}",
            "payload_kg": r.get("payload_kg"), "reach_mm": r.get("reach_mm"),
            "repeatability_mm": r.get("repeatability_mm"), "weight_kg": r.get("weight_kg"),
            "ip_rating": r.get("ip_rating"), "unverified": r.get("unverified") or [], "line": spec_line(r),
            "reach_short": bool(r.get("reach_short")), "policy_note": r.get("policy_note") or ""}


async def robot_candidates(need: dict[str, Any]) -> list[dict[str, Any]]:
    """요구 사양 → 로봇 DB 후보(과하지 않은 순, AMR 탑재면 가벼운 것 우선, 제외 제조사 빼고).
    6축 팔이 아니거나 가반하중 추정이 없으면 빈 목록."""
    from ..company_knowledge.robot_specs import candidates

    if not need.get("arm") or not need.get("payload_kg"):
        return []
    kw = {"reach_mm": need.get("reach_mm"), "exclude_makers": need.get("exclude_makers") or [],
          "mobile": bool(need.get("mobile")), "limit": ROBOT_CANDIDATES,
          # 회사 우선순위(가이드 K03): 기본 가격 중심(DOBOT), 국내 신뢰성 중시면 레인보우. 지정 제조사가 있으면 그것만.
          "priority": need.get("priority") if need.get("priority") in ("price", "domestic") else "price",
          "makers": need.get("makers") or None}
    try:
        rows = await candidates(float(need["payload_kg"]), ip_min=need.get("ip_min"), **kw)
        if not rows and need.get("ip_min"):        # IP 까지 맞는 게 없으면 IP 조건만 풀어 보여 준다(표시로 구분)
            rows = await candidates(float(need["payload_kg"]), **kw)
            return [{**robot_row(r), "ip_short": True} for r in rows]
    except Exception as e:  # noqa: BLE001 — 로봇 DB 가 없어도 계획 단계는 진행
        _log.warning("로봇 후보 조회 실패: %r", e)
        return []
    return [robot_row(r) for r in rows]


async def gripper_names() -> list[dict[str, str]]:
    """회사 그리퍼 제품 [{name, family}](활성 지식 카드, 부류 eoat). 공식 사양 카드를 앞에, 묶음 카드는 뺀다."""
    from ..company_knowledge.product_images import category_of
    from ..company_knowledge.product_recommend import is_product
    from ..database import SessionLocal

    try:
        async with SessionLocal() as db:
            rows = (await db.execute(text(
                "SELECT name, card->>'family' AS family, card->'aliases' AS aliases, evidence FROM knowledge_cards "
                "WHERE kind = 'product' AND active "
                "ORDER BY (evidence = '공식 사양') DESC, id"))).mappings().all()
    except Exception:  # noqa: BLE001
        return []
    return [{"name": r["name"], "family": r["family"] or "", "aliases": list(r["aliases"] or [])} for r in rows
            if category_of(r["family"] or "", r["name"]) == "eoat" and is_product(r["name"])]


GRIPPER_CANDIDATES = 12
SIBLINGS = 4


def gripper_candidates(ai: str, grippers: list[dict[str, str]] | list[str]) -> list[str]:
    """AI 추천 → 같은 제품군 모델 → 다른 제품군마다 대표 1개. 모델 단위 카드가 많아(MG 10종 등) 앞에서 자르면
    다른 방식의 그리퍼가 통째로 빠진다(2026-10-01 맥봇 공식 사양 적재 후)."""
    items = [g if isinstance(g, dict) else {"name": g, "family": ""} for g in grippers]
    # AI 가 다른 이름(예: '시프트락 그리퍼')으로 적었으면 그 제품 카드 이름으로 바꾼다.
    low = re.sub(r"[\s\-_/·()]+", "", (ai or "").lower())
    ai = next((g["name"] for g in items if low and low in
               [re.sub(r"[\s\-_/·()]+", "", x.lower()) for x in [g["name"], *g.get("aliases", [])]]), ai)
    fam_of = {g["name"]: g["family"] for g in items}
    ai_fam = fam_of.get(ai, "")
    out = [ai] if ai else []
    # 같은 제품군은 몇 개만 — MG 10종이 다 들어가면 다른 방식(핑거·진공 등)이 목록에서 밀려난다.
    out += [g["name"] for g in items if ai_fam and g["family"] == ai_fam and g["name"] not in out][:SIBLINGS]
    seen = {ai_fam} if ai_fam else set()
    for g in items:
        if g["name"] in out:
            continue
        key = g["family"] or g["name"]
        if key not in seen:
            seen.add(key)
            out.append(g["name"])
    out += [g["name"] for g in items if g["name"] not in out]
    return out[:GRIPPER_CANDIDATES]


async def attach(alt: dict[str, Any], grippers: list | None = None, *, keep_picks: bool = True) -> dict[str, Any]:
    """컨셉에 계획(후보·기본 선택)을 붙인다. keep_picks 면 사용자가 이미 고른 것은 후보에 있는 한 유지."""
    plan = dict(alt.get("plan") or {})
    need = dict(plan.get("robot_need") or {"arm": True})
    if "mobile" not in need:          # AI 가 안 적었으면 로봇 형태로 판단(AMR·이동형 위에 싣는 팔)
        need["mobile"] = bool(_MOBILE_RE.search(f"{alt.get('robot') or ''} {alt.get('name') or ''}"))
    cands = await robot_candidates(need)
    labels = [c["label"] for c in cands]
    pick = plan.get("robot_pick") if keep_picks and plan.get("robot_pick") else ""
    if not pick or (pick not in labels and not plan.get("robot_custom")):
        pick = labels[0] if labels else ""
    grippers = grippers if grippers is not None else await gripper_names()
    ai = plan.get("gripper_ai") or ""
    g_cands = gripper_candidates(ai, grippers)
    g_pick = plan.get("gripper_pick") if keep_picks and plan.get("gripper_pick") else (g_cands[0] if ai and g_cands else ai)
    plan.update({"robot_need": need, "robot_candidates": cands, "robot_pick": pick,
                 "gripper_candidates": g_cands[:12], "gripper_pick": g_pick, "confirmed": bool(plan.get("confirmed"))})
    return {**alt, "plan": plan}


def select(alt: dict[str, Any], robot_pick: str | None, gripper_pick: str | None) -> dict[str, Any]:
    """사용자 선택 저장(후보 밖 직접 입력도 허용 — robot_custom 표시)."""
    plan = dict(alt.get("plan") or {})
    if robot_pick is not None:
        rp = " ".join(robot_pick.split())[:60]
        plan["robot_pick"] = rp
        plan["robot_custom"] = bool(rp) and rp not in [c["label"] for c in plan.get("robot_candidates") or []]
    if gripper_pick is not None:
        plan["gripper_pick"] = " ".join(gripper_pick.split())[:60]
    return {**alt, "plan": plan}


def confirm(alt: dict[str, Any]) -> dict[str, Any]:
    """확정: 고른 로봇·그리퍼를 구성 끝 한 줄로 넣는다(그림·견적·제안서가 같은 모델을 쓰게). 수치는 넣지 않는다 —
    제안서 문구 검증(근거 없는 수치 가림)에 걸리지 않게, 사양은 로봇 DB 에서 따로 본다."""
    plan = dict(alt.get("plan") or {})
    parts = []
    if plan.get("robot_pick"):
        parts.append(f"로봇 팔 {plan['robot_pick']}")
    if plan.get("gripper_pick"):
        # '그리퍼' 라는 부류 말을 쓰지 않는다 — 회사 제품이 아닌 그리퍼를 골라도 부류 말 때문에 회사 그리퍼 사진이
        # 외형 참고로 붙었다(실측). 회사 제품을 고르면 제품 이름으로 그 사진이 붙는다.
        parts.append(f"엔드툴 {plan['gripper_pick']}")
    structure = [s for s in alt.get("structure") or [] if not s.startswith(PICK_PREFIX)]
    if parts:
        from .concept import STRUCTURE_MAX
        structure = structure[:STRUCTURE_MAX - 1] + [f"{PICK_PREFIX} {', '.join(parts)}를 적용한다."]
    plan["confirmed"] = True
    return {**alt, "structure": structure, "plan": plan}


def summary(alt: dict[str, Any]) -> str:
    """진행 기록용 한 줄."""
    plan = alt.get("plan") or {}
    bits = [f"로봇 팔 {plan['robot_pick']}" if plan.get("robot_pick") else "",
            f"그리퍼 {plan['gripper_pick']}" if plan.get("gripper_pick") else ""]
    return ", ".join(b for b in bits if b) or "모델 선택 없음"
