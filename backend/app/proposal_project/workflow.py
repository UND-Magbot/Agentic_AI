"""3~6단계 진행 — 공정 제안 → 컨셉 이미지 승인 → 페이지 구성·견적 승인 → PPT 제작.

가이드 "승인 순서": 컨셉(이미지) 확정 → 10쪽 구성 확인 → 제작. 이미 받은 승인은 다시 묻지 않고,
승인 이후 구조가 바뀌면 해당 단계로 되돌린다(back_to). PPT 는 5c 의 project_deck.generate_pptx 로 만든다.

바로 만들기(run_quick)는 실행 시험용 장치다: 질문이 남아 있어도 넘어가고, 제안·이미지·구성을 자동으로
채택해 제작까지 간다. 자동 채택한 것은 진행 기록과 결과 경고에 남긴다.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import codex_client, external_gateway
from ..company_knowledge import product_images
from ..database import SessionLocal
from ..request_files import safe_name, save_result
from . import concept, dialog, jobs, plan
from .brain import BRAIN_LABEL
from .catalog import QUESTIONS
from .service import ProjectError, get_project, set_item

_log = logging.getLogger("proposal_project.workflow")

Step = Callable[[str], Awaitable[None]]
REF_MAX = 3
REF_BYTES_MAX = 4 * 1024 * 1024
# 참고 이미지(외형 참조·이전 버전·회사 제품 사진)를 Codex 로 보낼지. 브리지가 reference_images 를 받도록
# 확장됨(사용자 결정 2026-09-30, 2026-09-29 보류 철회). 브리지가 옛 버전이면 참고 없이 다시 보낸다(codex_client).
BRIDGE_TAKES_REFS = True
_IMAGE_MIMES = ("image/png", "image/jpeg", "image/jpg", "image/webp")
PAGE_TYPES = ("cover", "overview", "flow", "common_concept", "alternative", "equipment", "poc", "quote")
FILLED = ("confirmed", "adopted", "assumed")


async def _message(db: AsyncSession, pid: int, content: str, role: str = "assistant") -> None:
    await db.execute(text("INSERT INTO project_messages (project_id, role, content) VALUES (:p, :r, :c)"),
                     {"p": pid, "r": role, "c": content})


async def _set(db: AsyncSession, pid: int, **cols: Any) -> None:
    json_cols = ("alternatives", "pages", "quote_lines", "experience")
    sets = ", ".join(f"{k} = CAST(:{k} AS jsonb)" if k in json_cols else f"{k} = :{k}" for k in cols)
    vals = {k: json.dumps(v, ensure_ascii=False) if k in json_cols else v for k, v in cols.items()}
    await db.execute(text(f"UPDATE proposal_projects SET {sets}, updated_at = NOW() WHERE id = :pid"),
                     {**vals, "pid": pid})


async def _load(pid: int) -> dict[str, Any]:
    async with SessionLocal() as db:
        p = await get_project(db, pid)
    if p is None:
        raise ProjectError("프로젝트를 찾을 수 없습니다.")
    return p


def _require(project: dict[str, Any], *stages: str) -> None:
    if project["stage"] not in stages:
        raise ProjectError("지금 단계에서는 할 수 없는 작업입니다.")


# ---------- 3단계: 공정 컨셉 제안 ----------

# 사용자 결정(2026-09-29): '만드는' 단계(공정 컨셉 제안·비교안·수정 반영·견적 행 초안)는 선택 없이 GPT 가 한다.
# GPT 호출이 실패하면 사내 gemma 가 대신하고 진행 기록에 남긴다(brain.ask_json).
MAKER = "gpt"


async def run_propose(pid: int, step: Step) -> str:
    project = await _load(pid)
    brain = MAKER
    await step(f"{BRAIN_LABEL[brain]}가 공정 컨셉을 구상하는 중" + (" (1~3분)" if brain == "gpt" else ""))
    res = await concept.propose_with_brain(project, brain=brain, user_id=project["user_id"])
    retried = False
    if not res["alternatives"]:
        # 같은 입력이라도 응답이 매번 달라 한 번은 형식이 깨지거나 모든 문장에 근거 없는 수치가 들어 빠진다
        # (실측 2026-09-30: 프로젝트 180 실패 → 같은 입력 재실행은 대안 3개). 한 번만 다시 묻는다.
        _log.warning("공정 컨셉 0개 project=%s dropped=%s raw=%r", pid, res["dropped"][:6], res.get("raw", "")[:600])
        await step(f"{BRAIN_LABEL[brain]} 응답에서 쓸 컨셉이 없어 한 번 더 요청하는 중")
        res = await concept.propose_with_brain(project, brain=brain, user_id=project["user_id"])
        retried = True
    alts, fills, dropped = res["alternatives"], res["fills"], res["dropped"]
    if alts:
        await step("로봇 DB·회사 그리퍼에서 후보를 고르는 중")
        grippers = await plan.gripper_names()
        alts = [await plan.attach(a, grippers) for a in alts]
    if not alts:
        _log.warning("공정 컨셉 재시도도 0개 project=%s dropped=%s raw=%r", pid, dropped[:6], res.get("raw", "")[:600])
        why = ("모든 구성 문장에 문항에 없는 수치가 들어가 뺐습니다" if dropped
               else "AI 응답을 컨셉 형식으로 읽지 못했습니다")
        raise ProjectError(f"공정 컨셉을 만들지 못했습니다(두 번 시도, {why}). 잠시 뒤 다시 시도해 주세요.")
    async with SessionLocal() as db:
        await _apply_proposal(db, project, alts, fills)
        what = (f"공정 컨셉 '{alts[0]['name']}'" if len(alts) == 1
                else f"비교 컨셉 {len(alts)}개(" + ", ".join(f"{a['id']} {a['name']}" for a in alts) + ")")
        msg = f"[{BRAIN_LABEL[res['used']]}] {what}을 제안하고 설계 문항 {len(fills)}개를 '가정' 으로 채웠습니다."
        if res["fallback_reason"]:
            msg += f" (외부 AI 호출 실패로 사내 AI 가 대신했습니다: {res['fallback_reason'][:120]})"
        if dropped:
            msg += f" (근거 없는 수치가 든 문장 {len(dropped)}개는 뺐습니다)"
        if retried:
            msg += " (첫 응답에서 쓸 컨셉이 없어 한 번 더 요청했습니다)"
        await _message(db, pid, msg)
        await db.commit()
    return msg + await _recall_experience(pid, step)


def _situation(project: dict[str, Any]) -> str:
    """회상에 넘길 이번 프로젝트 상황 — 고객사·부서·프로젝트명(P01)은 빼고, 제안된 컨셉까지."""
    lines = concept.item_lines(project, exclude=concept.EXTERNAL_EXCLUDE)
    for a in project["alternatives"]:
        lines.append(f"[제안 컨셉 {a['id']}] {a['name']} / {a.get('robot', '')} / " + " ".join(a["structure"][:5]))
    return "\n".join(lines)


async def _recall_experience(pid: int, step: Step) -> str:
    """공정 컨셉을 만든 뒤 회사 경험을 떠올려 '경험 기반 제안' 으로 남긴다. 실패해도 컨셉 제안은 유지."""
    from . import experience

    project = await _load(pid)
    await step("회사 경험을 떠올리는 중")
    try:
        hits = await experience.recall(_situation(project), brain=MAKER, user_id=project["user_id"])
    except Exception as e:  # noqa: BLE001 — 회상 실패가 공정 컨셉 제안을 막지 않게
        _log.warning("회사 경험 회상 실패: %r", e)
        return " (회사 경험 회상 실패)"
    target = project["alternatives"][0]["id"] if project["alternatives"] else None
    for h in hits:
        h["alt_id"] = target
    async with SessionLocal() as db:
        await _set(db, pid, experience=hits)
        if hits:
            await _message(db, pid, f"회사 경험에서 {len(hits)}가지를 떠올렸습니다. 적용할지 골라 주세요: "
                           + " / ".join(h["title"][:40] for h in hits))
        await db.commit()
    return f" 회사 경험 {len(hits)}가지를 떠올렸습니다."


# 경험 기반 제안의 두 판단을 나눈다(사용자 결정 2026-09-30):
#   - 컨셉 단계 reflect/dismiss: 이번 컨셉에 쓸지만 정한다. 지식에는 아무것도 기록하지 않는다.
#   - 제안서 완성 후 apply/skip: 그 경험이 맞는 지식이었는지 판정 → 이때만 experience_feedback 에 기록.
#     컨셉을 막 만든 시점에는 그 경험이 실제로 맞았는지 판단할 근거가 없기 때문이다.
_CONCEPT_ACTIONS = {"reflect": "reflected", "dismiss": "dismissed"}
_KNOWLEDGE_ACTIONS = {"apply": "apply", "skip": "skip"}


async def judge_experience(db: AsyncSession, project: dict[str, Any], card_id: int, action: str,
                           user_id: int) -> dict[str, Any]:
    """경험 기반 제안 판단. reflect 면 호출부가 그 경험을 컨셉에 반영하는 수정 작업을 시작한다."""
    from . import experience

    hits = project["experience"]
    hit = next((h for h in hits if h.get("card_id") == card_id), None)
    if hit is None:
        raise ProjectError("떠올린 경험을 찾을 수 없습니다.")
    if action in _CONCEPT_ACTIONS:
        _require(project, "concept")
        if hit.get("status") != "suggested":
            raise ProjectError("이미 고른 경험입니다.")
        hit["status"] = _CONCEPT_ACTIONS[action]
        label = "컨셉에 반영" if action == "reflect" else "넘기기"
    elif action in _KNOWLEDGE_ACTIONS:
        if project["stage"] != "done":
            raise ProjectError("맞는 지식인지 판정은 제안서(PPT)를 완성한 뒤에 합니다.")
        if hit.get("judged"):
            raise ProjectError("이미 판정한 경험입니다.")
        await experience.record_feedback(card_id, action, project_id=project["id"], user_id=user_id,
                                         card_table=hit.get("card_table", "experience_cards"))
        hit["judged"] = action
        label = "맞는 지식으로 적용" if action == "apply" else "안 맞는 지식으로 빼기"
    else:
        raise ProjectError("알 수 없는 선택입니다.")
    await _set(db, project["id"], experience=hits)
    await _message(db, project["id"], f"회사 경험 '{hit['title'][:40]}' — {label}", role="user")
    await db.commit()
    return hit


def experience_request(hit: dict[str, Any]) -> str:
    """회사 경험 [컨셉에 반영] → 수정 요청 문장. 추상적인 한 줄('~할 만합니다')만 보내면 구성이 거의 안 바뀌었다
    (실측 2026-09-30, 사용자: '반영해도 컨셉도가 바뀐 게 체감이 안 됨'). 그 경험이 실제로 쓴 방식을 함께 주고,
    구성 문장에 장치·위치로 드러나게 고치라고 한다."""
    d = hit.get("detail") or {}
    parts = [f"회사 경험 '{hit.get('title', '')}' 반영."]
    if d.get("process") or hit.get("process"):
        parts.append(f"그 경험의 공정: {d.get('process') or hit.get('process')}.")
    how = d.get("solution") or d.get("how_it_works") or ""
    if how:
        parts.append(f"그때 쓴 방식: {how}.")
    facts = [f for f in hit.get("facts") or [] if f][:3]
    if facts:
        parts.append("핵심 사실: " + " / ".join(facts) + ".")
    parts.append(f"이번에 쓸 점: {hit.get('suggestion', '')}")
    parts.append("[반영 방법] 이 경험의 방식 중 이번 공정에 맞는 것을 구성 문장에 구체적인 장치·위치·동작으로 넣는다"
                 "(구성 문장을 최소 1줄 새로 쓰거나 바꾼다). 이번 공정과 맞지 않는 부분은 넣지 않고, 이미 같은 내용이"
                 " 있으면 더 구체적으로 고친다. note 에 구성에서 무엇이 바뀌었는지 적는다.")
    return " ".join(parts)[:concept.REVISION_MAX]


async def save_all_knowledge(db: AsyncSession, project: dict[str, Any], *, user_id: int | None = None,
                             approver: bool = False) -> dict[str, Any]:
    """'지식 저장' 한 번(사용자 요청 2026-09-30 — 낱개 판단이 헷갈림):
    - 완성된 컨셉마다 경험 카드로 저장(이미 저장·승인 대기인 것은 건너뜀, 반려된 것은 다시 저장).
    - 컨셉에 반영한 회사 경험은 '맞는 지식'으로 기록. 넘긴 경험은 기록하지 않는다 — 이번에 안 썼을 뿐
      틀린 지식이라는 근거가 아니어서, 다음 회상 순위를 낮추지 않는다.
    승인권자가 아니면 카드는 승인 대기(영업 관리자 승인 후 회상에 쓰임)."""
    from . import experience

    if project["stage"] != "done":
        raise ProjectError("회사 지식 저장은 제안서(PPT)를 완성한 뒤에 합니다.")
    judged = 0
    hits = project["experience"]
    for h in hits:
        if h.get("status") == "reflected" and not h.get("judged"):
            await experience.record_feedback(h["card_id"], "apply", project_id=project["id"], user_id=user_id,
                                             card_table=h.get("card_table", "experience_cards"))
            h["judged"] = "apply"
            judged += 1
    if judged:
        await _set(db, project["id"], experience=hits)
    reviews = await experience.card_reviews([a["knowledge_card_id"] for a in project["alternatives"]
                                             if a.get("knowledge_card_id")])
    applied = [h for h in hits if h.get("status") == "reflected" and h.get("judged") != "skip"]
    saved: list[str] = []
    alts = [dict(a) for a in project["alternatives"]]
    for a in alts:
        if a.get("knowledge_card_id") and reviews.get(a["knowledge_card_id"]) != "rejected":
            continue
        card_id, _review = await experience.save_project_card(project, a, applied, user_id=user_id, approver=approver)
        a["knowledge_card_id"] = card_id
        saved.append(f"E{card_id}")
    if saved:
        await _set(db, project["id"], alternatives=alts)
    if not saved and not judged:
        raise ProjectError("이미 모두 저장했습니다.")
    after = "다음 프로젝트의 공정 컨셉 때 떠오릅니다." if approver else "영업 관리자가 승인하면 다음 프로젝트 때 떠오릅니다."
    msg = "지식 저장: " + ", ".join(
        ([f"컨셉 {len(saved)}개를 경험 카드({', '.join(saved)})로"] if saved else [])
        + ([f"반영한 회사 경험 {judged}개를 맞는 지식으로"] if judged else [])) + f" 저장했습니다. {after}"
    await _message(db, project["id"], msg, role="user")
    await db.commit()
    return {"saved": saved, "judged": judged}


async def save_concept_knowledge(db: AsyncSession, project: dict[str, Any], alt_id: str, *,
                                 user_id: int | None = None, approver: bool = False) -> int:
    """잘 만든 공정 컨셉 → 회사 경험 카드로 저장. 제안서를 완성한 뒤에만.
    승인권자(영업 관리자)가 저장하면 바로 회상에 쓰이고, 그 외는 승인 대기 — 승인 후에 떠오른다."""
    from . import experience

    if project["stage"] != "done":
        raise ProjectError("회사 지식 저장은 제안서(PPT)를 완성한 뒤에 합니다.")
    alt = next((a for a in project["alternatives"] if a["id"] == alt_id), None)
    if alt is None:
        raise ProjectError("컨셉을 찾을 수 없습니다.")
    # 컨셉에 반영했고, 완성 후 '안 맞는 지식' 으로 빼지 않은 경험만 함께 담는다.
    applied = [h for h in project["experience"] if h.get("status") == "reflected" and h.get("judged") != "skip"]
    card_id, review = await experience.save_project_card(project, alt, applied, user_id=user_id, approver=approver)
    alts = [{**a, "knowledge_card_id": card_id} if a["id"] == alt_id else a for a in project["alternatives"]]
    await _set(db, project["id"], alternatives=alts)
    after = ("다음 프로젝트의 공정 컨셉 때 떠오릅니다." if review == "approved"
             else "영업 관리자가 승인하면 다음 프로젝트의 공정 컨셉 때 떠오릅니다(승인 대기).")
    await _message(db, project["id"], f"{concept_label(project, alt_id)} '{alt['name']}' 을 회사 지식(경험 카드 E{card_id})으로 "
                                      f"저장했습니다. {after}", role="user")
    await db.commit()
    return card_id


async def _apply_proposal(db: AsyncSession, project: dict[str, Any], alts: list[dict], fills: list[dict]) -> None:
    pid = project["id"]
    items = project["items"]
    await _set(db, pid, alternatives=alts)
    for a in alts:
        code = concept.ALT_CODES[a["id"]]
        if items[code]["status"] in ("empty", "unknown") or items[code]["source"] == "ai":
            await set_item(db, pid, code, value=concept.alternative_item_value(a), status="assumed", source="ai",
                           evidence=f"AI 제안: {a.get('reason') or a['name']}", reason="공정 제안")
    if items["P13"]["status"] in ("empty", "unknown") or items["P13"]["source"] == "ai":
        p13 = (f"컨셉 1종: {alts[0]['name']}" if len(alts) == 1
               else f"비교 컨셉 {len(alts)}종: " + ", ".join(f"{a['id']} {a['name']}" for a in alts))
        await set_item(db, pid, "P13", value=p13, status="assumed", source="ai", evidence="AI 제안", reason="공정 제안")
    for f in fills:
        await set_item(db, pid, f["code"], value=f["value"], status="assumed", source="ai",
                       evidence=f"AI 제안: {f['reason']}" if f["reason"] else "AI 제안", reason="공정 제안")


def clean_alternatives(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """사용자가 고친 컨셉 목록 검증 → 5c 계약 형식(alternatives). 컨셉은 기본 1개, 비교안 포함 최대 3개."""
    if not isinstance(raw, list) or not 1 <= len(raw) <= 3:
        raise ProjectError("공정 컨셉은 1개, 비교안을 더해도 3개까지입니다.")
    out = []
    for k, a in enumerate(raw):
        if not isinstance(a, dict):
            raise ProjectError("컨셉 형식이 올바르지 않습니다.")
        name = concept._clean(a.get("name"), 30)
        structure = [concept._clean(s, 200) for s in (a.get("structure") or [])
                     if concept._clean(s, 200)][:concept.STRUCTURE_MAX]
        if not name or not structure:
            raise ProjectError("컨셉마다 이름과 구성 문장(한 줄 이상)이 필요합니다.")
        aid = concept.ALT_IDS[k]
        out.append({"id": aid, "name": name, "robot": concept._clean(a.get("robot"), 200),
                    "summary": concept._clean(a.get("summary"), 200), "reason": concept._clean(a.get("reason"), 200),
                    "structure": structure,
                    "exclude": [concept._clean(s, 120) for s in (a.get("exclude") or []) if concept._clean(s, 120)][:4],
                    "item_codes": [concept.ALT_CODES[aid]],
                    **({"plan": a["plan"]} if isinstance(a.get("plan"), dict) else {})})
    return out


async def save_alternatives(db: AsyncSession, project: dict[str, Any], raw: list[dict[str, Any]]) -> None:
    _require(project, "concept")
    alts = clean_alternatives(raw)
    old = {a["id"]: a for a in project["alternatives"]}
    for a in alts:                      # 직접 고쳐도 '회사 지식으로 저장' 표시는 유지
        if old.get(a["id"], {}).get("knowledge_card_id"):
            a["knowledge_card_id"] = old[a["id"]]["knowledge_card_id"]
    await _set(db, project["id"], alternatives=alts)
    # 구성이 바뀐 컨셉의 초안 이미지는 새 구성과 맞지 않으므로 표시만 남기고 승인을 풀지 않는다(버전 고정).
    changed = [a["id"] for a in alts if old.get(a["id"], {}).get("structure") != a["structure"]]
    if changed:
        await _message(db, project["id"], f"컨셉 구성을 직접 고쳤습니다({', '.join(changed)}). 필요하면 이미지를 다시 만드세요.",
                       role="user")
    await db.commit()


# ---------- 3단계: 자동 진행 · 비교안 · 수정 요청 ----------

IMAGE_PARALLEL = 3      # 브리지 MAX_CONCURRENT 와 같게(2026-09-30 2→3: 컨셉 3개면 3번째가 앞 작업을 기다렸다)


async def _images_for(pid: int, alt_ids: list[str], step: Step, *, auto_approve: bool = False) -> list[str]:
    """여러 컨셉 이미지를 동시에(최대 IMAGE_PARALLEL 장) 만든다. 반환: 실패 사유 목록."""
    sem = asyncio.Semaphore(IMAGE_PARALLEL)
    fails: list[str] = []

    async def one(aid: str) -> None:
        async with sem:
            try:
                await run_image(pid, aid, step, auto_approve=auto_approve)
            except Exception as e:  # noqa: BLE001 — 한 장 실패가 다른 컨셉을 막지 않게
                fails.append(f"{aid}: {e}")
    await asyncio.gather(*(one(a) for a in alt_ids))
    return fails


async def run_concept_plan(pid: int, step: Step) -> str:
    """핵심 질문을 마치면 — 공정 컨셉 계획(로봇·그리퍼 후보, 회사 경험)까지만 만든다. 이미지는 사용자가 계획을
    확정한 뒤(run_plan_confirm) 한 번 그린다(사용자 요청 2026-09-30: 그림을 보고 고치느라 여러 번 다시 그림)."""
    project = await _load(pid)
    msgs = []
    if not project["alternatives"]:
        msgs.append(await run_propose(pid, step))
    async with SessionLocal() as db:
        await _message(db, pid, "공정 컨셉 계획을 만들었습니다. 로봇·그리퍼·회사 경험을 확인하고 "
                                "[이 계획으로 컨셉 이미지 그리기]를 눌러 주세요. 고칠 점은 계획 수정 요청에 적으면 글로 먼저 고칩니다.")
        await db.commit()
    return " ".join(msgs + ["계획을 확인해 주세요."])


async def plan_select(db: AsyncSession, project: dict[str, Any], alt_id: str, robot_pick: str | None,
                      gripper_pick: str | None) -> None:
    """계획 단계에서 로봇 팔·그리퍼 선택 저장."""
    _require(project, "concept")
    alt = next((a for a in project["alternatives"] if a["id"] == alt_id), None)
    if alt is None or not plan.alt_pending(alt):
        raise ProjectError("계획을 확인 중인 컨셉이 아닙니다.")
    alts = [plan.select(a, robot_pick, gripper_pick) if a["id"] == alt_id else a for a in project["alternatives"]]
    await _set(db, project["id"], alternatives=alts)
    await db.commit()


async def run_plan_confirm(pid: int, picks: list[dict[str, Any]], step: Step) -> str:
    """계획 확정 → 고른 모델을 구성·문항에 반영 → 이미지가 없는 컨셉만 그린다."""
    project = await _load(pid)
    _require(project, "concept")
    if not plan.pending(project):
        raise ProjectError("확정할 계획이 없습니다.")
    by = {p_.get("alt_id"): p_ for p_ in picks or []}
    alts = []
    for a in project["alternatives"]:
        if plan.alt_pending(a):
            sel = by.get(a["id"]) or {}
            a = plan.confirm(plan.select(a, sel.get("robot_pick"), sel.get("gripper_pick")))
        alts.append(a)
    async with SessionLocal() as db:
        await _set(db, pid, alternatives=alts)
        for a in alts:
            code = concept.ALT_CODES[a["id"]]
            if project["items"][code]["status"] != "confirmed":
                await set_item(db, pid, code, value=concept.alternative_item_value(a), status="adopted",
                               source="dialog", evidence="공정 컨셉 계획 확정", reason="계획 확정")
        g = next((a["plan"].get("gripper_pick") for a in alts if (a.get("plan") or {}).get("gripper_pick")), "")
        if g and concept.gripper_open(project):
            await set_item(db, pid, "P21", value=g, status="adopted", source="dialog",
                           evidence="공정 컨셉 계획에서 선택", reason="계획 확정")
        await _message(db, pid, "공정 컨셉 계획을 확정했습니다: " + " / ".join(
            f"{concept_label({**project, 'alternatives': alts}, a['id'])} {plan.summary(a)}" for a in alts), role="user")
        await db.commit()
    project = await _load(pid)
    todo = [a["id"] for a in project["alternatives"]
            if not any(i["alt_id"] == a["id"] and i["status"] in ("draft", "approved", "generating")
                       for i in project["images"])]
    if not codex_client.is_configured():
        return "계획을 확정했습니다. 외부 AI 연결이 설정되지 않아 이미지는 만들지 않았습니다."
    fails = await _images_for(pid, todo, step)
    return (f"계획을 확정하고 컨셉 이미지 {len(todo) - len(fails)}장을 만들었습니다."
            + (" 실패: " + "; ".join(f[:120] for f in fails) if fails else ""))


async def run_extra(pid: int, step: Step) -> str:
    """비교안 1개 추가 → 그 이미지까지."""
    project = await _load(pid)
    brain = MAKER
    await step(f"{BRAIN_LABEL[brain]}가 비교안을 구상하는 중")
    res = await concept.propose_extra(project, brain=brain, user_id=project["user_id"])
    if not res["alternatives"]:
        raise ProjectError("비교안을 만들지 못했습니다. " + "; ".join(res["dropped"])[:200])
    new = await plan.attach(dict(res["alternatives"][0]))
    alts = project["alternatives"] + [new]
    new["id"] = concept.ALT_IDS[len(alts) - 1]
    new["item_codes"] = [concept.ALT_CODES[new["id"]]]
    async with SessionLocal() as db:
        await _apply_proposal(db, {**project, "alternatives": alts}, alts, [])
        await _message(db, pid, f"[{BRAIN_LABEL[res['used']]}] 비교안 {new['id']} '{new['name']}' 을 더했습니다.")
        await db.commit()
    # 비교안도 계획(로봇·그리퍼)을 먼저 확인한 뒤 그린다.
    return f"비교안 {new['id']} '{new['name']}' 을 더했습니다. 계획을 확인하고 이미지 그리기를 눌러 주세요."


async def run_revise(pid: int, alt_id: str, request: str, step: Step) -> str:
    """사용자 수정 요청 1건 → 컨셉 구성 문장 수정 → 이전 버전을 참고로 새 버전 이미지. 여러 번 반복할 수 있다."""
    request = (request or "").strip()
    if not request:
        raise ProjectError("수정할 내용을 적어 주세요.")
    if len(request) > concept.REVISION_MAX:
        raise ProjectError(f"수정 요청은 {concept.REVISION_MAX}자 이내로 적어 주세요.")
    project = await _load(pid)
    alt = next((a for a in project["alternatives"] if a["id"] == alt_id), None)
    if alt is None:
        raise ProjectError("컨셉을 찾을 수 없습니다.")
    brain = MAKER
    await step(f"{BRAIN_LABEL[brain]}가 수정 요청을 구성에 반영하는 중")
    res = await concept.revise_alternative(project, alt, request, brain=brain, user_id=project["user_id"])
    new = res["alternative"]
    new["changes"] = concept_changes(alt, new, request, res["note"])
    planning = plan.alt_pending(alt)
    if planning:
        new = await plan.attach(plan.apply_maker_intent(new, request))
        new["plan"]["confirmed"] = False
    alts = [new if a["id"] == alt_id else a for a in project["alternatives"]]
    async with SessionLocal() as db:
        await _set(db, pid, alternatives=alts)
        code = concept.ALT_CODES[alt_id]
        if project["items"][code]["status"] != "confirmed":
            await set_item(db, pid, code, value=concept.alternative_item_value(new), status="assumed", source="dialog",
                           evidence=f"수정 요청: {request[:120]}", reason="컨셉 수정")
        label = concept_label({**project, "alternatives": alts}, alt_id)
        await _message(db, pid, f"{label} 수정 요청: {request}", role="user")
        note = res["note"] or "구성을 고쳤습니다."
        if res["dropped"]:
            note += f" (근거 없는 수치 {len(res['dropped'])}건은 넣지 않음)"
        await _message(db, pid, f"[{BRAIN_LABEL[res['used']]}] {note}")
        await db.commit()
    if planning:
        return note + " (계획 단계 — 이미지는 계획을 확정하면 그립니다)"
    if not codex_client.is_configured():
        return note + " (외부 AI 연결 미설정 — 이미지는 다시 그리지 않음)"
    drawn = ("structure", "exclude", "robot")
    if all(new.get(k) == alt.get(k) for k in drawn):
        # 그림에 들어가는 구성(장치·배치·제외·로봇)이 그대로면 다시 그려도 같은 그림 — 이미지 요청을 아낀다.
        async with SessionLocal() as db:
            await _message(db, pid, "그림에 들어가는 구성이 바뀌지 않아 이미지는 다시 그리지 않았습니다.")
            await db.commit()
        return note + " (구성 변화 없음 — 이미지 유지)"
    await run_image(pid, alt_id, step, revision=request)
    return note


def concept_changes(old: dict[str, Any], new: dict[str, Any], request: str, note: str) -> dict[str, Any]:
    """수정 한 번으로 구성에서 무엇이 바뀌었나 — 화면에 '바뀐 점'으로 보여 준다(사용자: 반영 체감이 안 됨)."""
    before, after = list(old.get("structure") or []), list(new.get("structure") or [])
    ch: dict[str, Any] = {"request": request[:300], "note": note or "",
                          "added": [x for x in after if x not in before],
                          "removed": [x for x in before if x not in after]}
    if (old.get("robot") or "") != (new.get("robot") or ""):
        ch["robot"] = [old.get("robot") or "", new.get("robot") or ""]
    if (old.get("name") or "") != (new.get("name") or ""):
        ch["name"] = [old.get("name") or "", new.get("name") or ""]
    ex_add = [x for x in new.get("exclude") or [] if x not in (old.get("exclude") or [])]
    if ex_add:
        ch["excluded"] = ex_add
    return ch


# ---------- 3단계: 컨셉 이미지 ----------

BLOCK_NOT_ALLOWED = "외부 전송 미허용"
BLOCK_PRICE = "가격 자료 전송 금지"


def ref_block_reason(asset: dict[str, Any]) -> str | None:
    """참고 이미지로 보낼 수 없으면 사유, 보낼 수 있으면 None.
    사용자 결정(2026-09-29): 역할과 무관하게 '외부 전송 허용' 을 체크한 이미지만 보낸다.
    가격 근거는 체크해도 보내지 않는다(가격 정보는 외부 AI 에 넘기지 않는다는 원칙)."""
    if asset["role"] == "price":
        return BLOCK_PRICE
    if not asset["external_ok"]:
        return BLOCK_NOT_ALLOWED
    return None


async def _reference_images(project: dict[str, Any], alt_id: str | None
                            ) -> tuple[list[tuple[bytes, str]], list[dict[str, str]]]:
    """→ (보낼 이미지 [(bytes, mime)], 같은 순서의 설명 [{role, note}])."""
    from .dialog import _read_object

    async with SessionLocal() as db:
        keys = dict((await db.execute(
            text("SELECT id, object_key FROM attachments WHERE id = ANY(:ids)"),
            {"ids": [a["attachment_id"] for a in project["assets"]] or [0]},
        )).all())
    refs: list[tuple[bytes, str]] = []
    meta: list[dict[str, str]] = []
    blocked: dict[str, list[dict[str, Any]]] = {}
    for a in project["assets"]:
        if a["mime"] not in _IMAGE_MIMES or a["alt_id"] not in (None, alt_id):
            continue
        reason = ref_block_reason(a)
        if reason:
            # 보내지 않는다 — 뺀 사실만 사유별로 감사 기록에 남긴다.
            blocked.setdefault(reason, []).append(
                {"attachment_id": a["attachment_id"], "role": a["role"], "external_ok": a["external_ok"]})
            continue
        if len(refs) >= REF_MAX:
            continue
        data = await asyncio.to_thread(_read_object, keys[a["attachment_id"]])
        if len(data) <= REF_BYTES_MAX:
            refs.append((data, a["mime"]))
            meta.append({"role": a["role"], "note": a["note"] or ""})
    for reason, items in blocked.items():
        await external_gateway.record("/v1/image", "image", {"excluded_reference_images": items, "reason": reason},
                                      {}, project["user_id"], status="blocked")
    return refs, meta


def concept_label(project: dict[str, Any], alt_id: str | None) -> str:
    """컨셉이 하나면 '공정 컨셉', 여럿이면 '컨셉 A' — 비교안이 없는 프로젝트에 '대안' 이라 부르지 않는다."""
    if not alt_id:
        return "공통"
    return "공정 컨셉" if len(project["alternatives"]) <= 1 else f"컨셉 {alt_id}"


async def _previous_image(project: dict[str, Any], alt_id: str | None) -> tuple[bytes, str] | None:
    """수정 요청 시 이전 버전 그림(가장 최근 완성본) — 구도를 유지하도록 참고로 함께 보낸다."""
    from .dialog import _read_object

    done = [i for i in project["images"] if i["alt_id"] == alt_id and i["status"] in ("draft", "approved")
            and i["attachment_id"]]
    if not done:
        return None
    last = max(done, key=lambda i: i["version"])
    async with SessionLocal() as db:
        row = (await db.execute(text("SELECT object_key, mime FROM attachments WHERE id = :a"),
                                {"a": last["attachment_id"]})).first()
    if row is None:
        return None
    return await asyncio.to_thread(_read_object, row.object_key), row.mime or "image/png"


async def run_image(pid: int, alt_id: str | None, step: Step, *, auto_approve: bool = False,
                    revision: str = "") -> int:
    """컨셉 이미지 1장 생성 → 초안(또는 자동 승인). revision 이 있으면 이전 버전을 참고로 보내 그 부분만 고친다.
    반환: project_images.id"""
    project = await _load(pid)
    alt = next((a for a in project["alternatives"] if a["id"] == alt_id), None)
    if alt_id and alt is None:
        raise ProjectError(f"컨셉 {alt_id} 가 없습니다.")
    refs: list[tuple[bytes, str]] = []
    ref_meta: list[dict[str, str]] = []
    if BRIDGE_TAKES_REFS:
        refs, ref_meta = await _reference_images(project, alt_id)
        # 사용자가 올린 참고가 먼저, 남은 자리에 대안에 나오는 회사 제품(AMR 등)의 승인된 사진.
        for data, mime, name in await product_images.refs_for_alt(alt, REF_MAX - len(refs)):
            refs.append((data, mime))
            ref_meta.append({"role": "appearance", "note": f"회사 제품 {name} — 비슷한 모양이면 된다"})
        if revision:
            prev = await _previous_image(project, alt_id)
            if prev:
                # 이전 버전은 우리가 만든 그림이라 외부 전송 허용 대상 — 참고 이미지 맨 앞에 둔다(최대 3장 유지).
                refs, ref_meta = [prev] + refs[:REF_MAX - 1], [{"role": "previous", "note": ""}] + ref_meta[:REF_MAX - 1]
    prompt = concept.image_prompt(project, alt, refs=ref_meta, revision=revision)
    async with SessionLocal() as db:
        version = int((await db.execute(
            text("SELECT COALESCE(MAX(version), 0) + 1 FROM project_images WHERE project_id = :p "
                 "AND alt_id IS NOT DISTINCT FROM :a"), {"p": pid, "a": alt_id})).scalar_one())
        iid = int((await db.execute(
            text("INSERT INTO project_images (project_id, alt_id, version, status, prompt, ref_sent, revision) "
                 "VALUES (:p, :a, :v, 'generating', :pr, :r, :rv) RETURNING id"),
            {"p": pid, "a": alt_id, "v": version, "pr": prompt, "r": len(refs),
             "rv": revision[:concept.REVISION_MAX]})).scalar_one())
        await db.commit()
    label = concept_label(project, alt_id)
    try:
        await step(f"{label} 이미지 생성 중(1~2분)")
        png, mime, _desc, used = await codex_client.generate_image_with_refs(prompt, refs, user_id=project["user_id"])
        ext = "png" if "png" in mime else "jpg"
        saved = await save_result(project["user_id"], f"컨셉_{safe_name(project['title'], '제안서')}_{alt_id or '공통'}_v{version}.{ext}",
                                  png, mime)
        if auto_approve:
            # 바로 만들기는 곧장 PPT 를 만들고 라벨이 거기 들어간다 — 검수 초안을 기다린다.
            await step(f"{label} 이미지 검수 초안 작성 중")
            checks, labels = await _vision_or_fail(png, alt)
        else:
            # 이미지는 바로 보여 주고 검수 초안(gemma, 약 40초)은 뒤에서 채운다(사용자 결정 2026-09-30).
            checks, labels = _checks_with(VISION_PENDING_NOTE), []
        async with SessionLocal() as db:
            await db.execute(
                text("UPDATE project_images SET status = :s, attachment_id = :a, checks = CAST(:c AS jsonb), "
                     "labels = CAST(:l AS jsonb), ref_used = :u WHERE id = :i"),
                {"s": "approved" if auto_approve else "draft", "a": saved.attachment_id,
                 "c": json.dumps(checks, ensure_ascii=False), "l": json.dumps(labels, ensure_ascii=False),
                 "u": used, "i": iid})
            ref_note = ""
            if refs:
                ref_note = (f" 참고 이미지 {len(refs)}장 중 {used}장 반영." if used else
                            f" 참고 이미지 {len(refs)}장을 보냈지만 반영되지 않았습니다.")
            await _message(db, pid, f"{label} 이미지 v{version} 을 만들었습니다.{ref_note}"
                           + (" (시험 진행: 자동 승인)" if auto_approve else " 검수 후 승인해 주세요."))
            await db.commit()
        if not auto_approve:
            _start_vision(pid, iid, png, alt)
    except Exception as e:
        async with SessionLocal() as db:
            await db.execute(text("UPDATE project_images SET status = 'failed', error = :e WHERE id = :i"),
                             {"e": str(e)[:500], "i": iid})
            await db.commit()
        raise
    return iid


VISION_PENDING_NOTE = "AI 검수 초안 작성 중 — 잠시 후 채워집니다"
_vision_tasks: dict[int, tuple[int, asyncio.Task]] = {}      # image id → (project id, 검수 작업)


def _checks_with(note: str) -> list[dict[str, Any]]:
    return [{"key": k, "label": lb, "ok": None, "note": note} for k, lb in concept.CHECKS]


async def _vision_or_fail(png: bytes, alt: dict[str, Any] | None) -> tuple[list, list]:
    try:
        return await concept.vision_check(png, alt)
    except Exception as e:  # noqa: BLE001 — 검수 초안 실패는 이미지 자체를 버릴 이유가 아니다
        _log.warning("비전 검수 실패: %r", e)
        return _checks_with("AI 검수 실패 — 직접 확인해 주세요"), []


def _start_vision(pid: int, iid: int, png: bytes, alt: dict[str, Any] | None) -> None:
    async def work() -> None:
        checks, labels = await _vision_or_fail(png, alt)
        async with SessionLocal() as db:
            # 아직 '작성 중' 일 때만 채운다. 그 사이 사용자가 고친 라벨은 덮어쓰지 않는다.
            await db.execute(text(
                "UPDATE project_images SET checks = CAST(:c AS jsonb), "
                "labels = CASE WHEN labels IS NULL OR labels = '[]'::jsonb THEN CAST(:l AS jsonb) ELSE labels END "
                "WHERE id = :i AND checks @> CAST(:m AS jsonb)"),
                {"c": json.dumps(checks, ensure_ascii=False), "l": json.dumps(labels, ensure_ascii=False), "i": iid,
                 "m": json.dumps([{"note": VISION_PENDING_NOTE}], ensure_ascii=False)})
            await db.commit()

    task = asyncio.create_task(work())
    _vision_tasks[iid] = (pid, task)
    task.add_done_callback(lambda _t: _vision_tasks.pop(iid, None))


def vision_pending(pid: int) -> bool:
    """이 프로젝트에 검수 초안을 쓰는 중인 이미지가 있나 — 작업 화면이 그동안 계속 새로 받아 오게."""
    return any(p == pid for p, _t in _vision_tasks.values())


async def wait_vision() -> None:
    """뒤에서 도는 검수 초안이 모두 끝날 때까지(시험용)."""
    await asyncio.gather(*(t for _p, t in list(_vision_tasks.values())), return_exceptions=True)


async def _image(db: AsyncSession, pid: int, image_id: int) -> dict[str, Any]:
    r = (await db.execute(text("SELECT * FROM project_images WHERE id = :i AND project_id = :p"),
                          {"i": image_id, "p": pid})).mappings().first()
    if r is None:
        raise ProjectError("이미지를 찾을 수 없습니다.")
    return dict(r)


async def approve_image(db: AsyncSession, project: dict[str, Any], image_id: int, user_id: int) -> None:
    _require(project, "concept")
    img = await _image(db, project["id"], image_id)
    if img["status"] not in ("draft", "approved"):
        raise ProjectError("완성된 이미지만 승인할 수 있습니다.")
    # 같은 컨셉에서 승인된 것은 하나만 — 이전 승인본은 초안으로 돌려 이력으로 남긴다(어느 버전이든 다시 승인 가능).
    await db.execute(text("UPDATE project_images SET status = 'draft' WHERE project_id = :p AND status = 'approved' "
                          "AND alt_id IS NOT DISTINCT FROM :a"), {"p": project["id"], "a": img["alt_id"]})
    await db.execute(text("UPDATE project_images SET status = 'approved' WHERE id = :i"), {"i": image_id})
    await db.execute(text("INSERT INTO project_approvals (project_id, target, version, approved_by) "
                          "VALUES (:p, :t, :v, :u)"),
                     {"p": project["id"], "t": f"concept:{img['alt_id'] or 'common'}", "v": img["version"], "u": user_id})
    await _message(db, project["id"], f"{concept_label(project, img['alt_id'])} 이미지 v{img['version']} 을 승인했습니다.",
                   role="user")
    await db.commit()


def clean_labels(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for lb in raw or []:
        try:
            x, y = float(lb.get("x")), float(lb.get("y"))
        except (TypeError, ValueError, AttributeError):
            raise ProjectError("라벨 위치가 올바르지 않습니다.") from None
        t = concept._clean(lb.get("text"), 20)
        if t and 0 <= x <= 1 and 0 <= y <= 1:
            out.append({"text": t, "x": round(x, 3), "y": round(y, 3)})
    if len(out) > 12:
        raise ProjectError("라벨은 12개까지입니다.")
    return out


async def save_labels(db: AsyncSession, project: dict[str, Any], image_id: int, raw: list[dict]) -> None:
    _require(project, "concept", "structure")
    await _image(db, project["id"], image_id)
    await db.execute(text("UPDATE project_images SET labels = CAST(:l AS jsonb) WHERE id = :i"),
                     {"l": json.dumps(clean_labels(raw), ensure_ascii=False), "i": image_id})
    await db.commit()


async def finish_concept(db: AsyncSession, project: dict[str, Any], user_id: int) -> list[str]:
    """컨셉 확정 → 컨셉 문항(P14~P16)을 '채택 컨셉' 으로. 반환: 경고(승인 이미지 없는 컨셉 등)."""
    _require(project, "concept")
    if plan.pending(project):
        raise ProjectError("먼저 공정 컨셉 계획을 확정하고 이미지를 그려 주세요.")
    alts = project["alternatives"]
    if not alts:
        raise ProjectError("공정 컨셉을 먼저 제안받거나 입력해 주세요.")
    warn = [f"{concept_label(project, a['id'])} 에 승인된 이미지가 없습니다." for a in alts
            if not any(i["approved"] and i["alt_id"] == a["id"] for i in project["images"])]
    for a in alts:
        code = concept.ALT_CODES[a["id"]]
        if project["items"][code]["status"] != "confirmed":
            await set_item(db, project["id"], code, value=concept.alternative_item_value(a), status="adopted",
                           source="dialog", evidence="컨셉 확정", reason="컨셉 확정")
    await db.execute(text("INSERT INTO project_approvals (project_id, target, version, approved_by) "
                          "VALUES (:p, 'concept', 1, :u)"), {"p": project["id"], "u": user_id})
    await _set(db, project["id"], stage="structure")
    await _message(db, project["id"], "컨셉을 확정했습니다." + (" " + " ".join(warn) if warn else ""))
    await db.commit()
    return warn


# ---------- 5단계: 페이지 구성·견적 ----------

async def run_structure(pid: int, step: Step) -> str:
    from ..proposal.project_deck import build_default_pages

    project = await _load(pid)
    pages = project["pages"] or build_default_pages(project)
    quote = project["quote_lines"]
    if not quote:
        await step("견적 구성표 초안 작성 중")
        quote = await concept.draft_quote_lines(project)
    async with SessionLocal() as db:
        await _set(db, pid, pages=pages, quote_lines=quote)
        msg = f"페이지 {len(pages)}쪽 구성과 견적 행 {len(quote)}개 초안을 만들었습니다. 금액은 근거가 있을 때만 넣습니다."
        await _message(db, pid, msg)
        await db.commit()
    return msg


PAGES_HARD_MAX = 30     # 입력 오류 방지용 안전 상한. 가이드의 10쪽은 권장 상한이라 넘어도 저장하고 경고만 한다.


def clean_pages(raw: list[dict[str, Any]], max_pages: int, alt_ids: set[str], image_ids: set[int]) -> list[dict]:
    """페이지 구성 검증. max_pages(가이드 K08 10쪽)는 넘어도 거부하지 않는다 — 호출부가 경고로 알린다."""
    if not isinstance(raw, list) or not raw:
        raise ProjectError("페이지 구성이 비어 있습니다.")
    if len(raw) > PAGES_HARD_MAX:
        raise ProjectError(f"페이지는 {PAGES_HARD_MAX}쪽까지만 만들 수 있습니다.")
    out = []
    for n, p in enumerate(raw, 1):
        if not isinstance(p, dict) or p.get("type") not in PAGE_TYPES:
            raise ProjectError("알 수 없는 페이지 유형입니다.")
        codes = [c for c in (p.get("item_codes") or []) if isinstance(c, str) and re.fullmatch(r"[PK]\d{2}", c)]
        page = {"page_no": n, "type": p["type"], "title": concept._clean(p.get("title"), 40) or p["type"],
                "item_codes": codes,
                "asset_ids": [int(a) for a in (p.get("asset_ids") or [])
                              if str(a).isdigit() and int(a) in image_ids]}
        if p["type"] == "alternative":
            if p.get("alt_id") not in alt_ids:
                raise ProjectError("컨셉 페이지의 컨셉이 올바르지 않습니다.")
            page["alt_id"] = p["alt_id"]
        out.append(page)
    # 가이드 K08: 고객 제안서에는 공정 컨셉·주요 항목·견적을 반드시 넣는다(컨셉은 승인된 수만큼 1쪽씩).
    missing = [f"컨셉 {a}" if len(alt_ids) > 1 else "공정 컨셉"
               for a in sorted(alt_ids) if not any(pg.get("alt_id") == a for pg in out)]
    missing += [label for t, label in (("equipment", "주요 항목"), ("quote", "견적")) if not any(pg["type"] == t for pg in out)]
    if missing:
        raise ProjectError(f"{', '.join(missing)} 쪽은 빼면 안 됩니다(가이드 K08 필수 페이지).")
    return out


def clean_quote(raw: list[dict[str, Any]], alt_ids: set[str]) -> list[dict]:
    out = []
    for ln in raw or []:
        if not isinstance(ln, dict):
            raise ProjectError("견적 행 형식이 올바르지 않습니다.")
        group, item = concept._clean(ln.get("group"), 10), concept._clean(ln.get("item"), 60)
        if group not in concept.QUOTE_GROUPS or not item:
            raise ProjectError("견적 행마다 구분과 항목 이름이 필요합니다.")
        alt = ln.get("alt_id") or None
        if group == "대안" and alt not in alt_ids:
            raise ProjectError(f"'{item}' 행의 컨셉을 골라 주세요.")
        price = ln.get("unit_price")
        if price in ("", None):
            price = None
        else:
            try:
                price = float(price)
            except (TypeError, ValueError):
                raise ProjectError(f"'{item}' 행의 단가는 숫자여야 합니다.") from None
            if price < 0:
                raise ProjectError(f"'{item}' 행의 단가가 음수입니다.")
        basis = concept._clean(ln.get("basis"), 120)
        if price is not None and not basis:
            raise ProjectError(f"'{item}' 행에 단가를 넣으려면 근거(견적서·단가표 등)도 적어 주세요.")
        out.append({"group": group, "alt_id": alt if group == "대안" else None, "item": item,
                    "qty": concept._clean(ln.get("qty"), 10) or "1", "unit": concept._clean(ln.get("unit"), 6) or "식",
                    "unit_price": price, "currency": concept._clean(ln.get("currency"), 4) or "원",
                    "basis": basis, "included": ln.get("included") is not False})
    if len(out) > 30:
        raise ProjectError("견적 행은 30개까지입니다.")
    return out


async def save_structure(db: AsyncSession, project: dict[str, Any], pages: list, quote_lines: list) -> None:
    _require(project, "structure")
    alt_ids = {a["id"] for a in project["alternatives"]}
    max_pages = int((project["output"] or {}).get("max_pages") or 10)
    image_ids = {i["id"] for i in project["images"] if i["approved"]}
    cleaned = clean_pages(pages, max_pages, alt_ids, image_ids)
    await _set(db, project["id"], pages=cleaned, quote_lines=clean_quote(quote_lines, alt_ids))
    if len(cleaned) > max_pages:
        await _message(db, project["id"], f"페이지 구성이 {len(cleaned)}쪽으로 권장 상한 {max_pages}쪽(가이드 K08)을 넘었습니다. "
                                          "그대로 제작할 수 있지만 필요하면 줄여 주세요.")
    await db.commit()


async def approve_structure(db: AsyncSession, project: dict[str, Any], user_id: int) -> None:
    _require(project, "structure")
    if not project["pages"]:
        raise ProjectError("페이지 구성 초안이 아직 없습니다.")
    await db.execute(text("INSERT INTO project_approvals (project_id, target, version, approved_by) "
                          "VALUES (:p, 'structure', 1, :u)"), {"p": project["id"], "u": user_id})
    await _set(db, project["id"], stage="producing")
    await _message(db, project["id"], f"페이지 {len(project['pages'])}쪽 구성을 승인했습니다. 제작을 시작합니다.", role="user")
    await db.commit()


# ---------- 6단계: 제작 ----------

async def _save_output(project: dict[str, Any], data: bytes, report: Any, deck: dict[str, Any],
                       extra: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    """완성 PPT 한 버전 저장(파일 + 검수 결과 + 프롬프트 수정용 문구). 반환: (버전, 검수 결과)."""
    pid = project["id"]
    rep = {"pages": report.pages, "overflow": report.overflow, "masked": report.masked,
           "missing_facts": report.missing_facts, "warnings": report.warnings,
           "softened": list(getattr(report, "softened", [])), **(extra or {})}
    open_items = [c for c, it in project["items"].items() if it["status"] in ("unknown", "conflict", "empty")
                  and QUESTIONS[c].required]
    if open_items:
        rep["warnings"] = rep["warnings"] + [f"필수 문항 미확정: {', '.join(open_items)}"]
    async with SessionLocal() as db:
        version = int((await db.execute(text("SELECT COALESCE(MAX(version), 0) + 1 FROM project_outputs "
                                             "WHERE project_id = :p"), {"p": pid})).scalar_one())
        name = f"{safe_name(project['title'], '제안서')}_제안서_v{version}.pptx"
        saved = await save_result(project["user_id"], name, data,
                                  "application/vnd.openxmlformats-officedocument.presentationml.presentation")
        await db.execute(text("INSERT INTO project_outputs (project_id, version, attachment_id, filename, report, deck) "
                              "VALUES (:p, :v, :a, :f, CAST(:r AS jsonb), CAST(:d AS jsonb))"),
                         {"p": pid, "v": version, "a": saved.attachment_id, "f": name,
                          "r": json.dumps(rep, ensure_ascii=False), "d": json.dumps(deck, ensure_ascii=False)})
        await _set(db, pid, stage="done")
        await db.commit()
    return version, rep


async def run_produce(pid: int, step: Step) -> str:
    from ..proposal.project_deck import generate_deck

    project = await _load(pid)
    await step("페이지 문구 작성·조판 중(로컬 모델)")
    data, report, deck = await generate_deck(project)
    version, rep = await _save_output(project, data, report, deck)
    msg = (f"제안서 v{version} ({report.pages}쪽)을 만들었습니다. 넘침 {len(report.overflow)}건, "
           f"가린 수치 {len(report.masked)}건, 빠진 확인값 {len(report.missing_facts)}건, 경고 {len(rep['warnings'])}건.")
    async with SessionLocal() as db:
        await _message(db, pid, msg)
        await db.commit()
    return msg


async def run_deck_edit(pid: int, request: str, step: Step) -> str:
    """완성본을 프롬프트로 수정(사용자 요청 2026-09-30: PPT 를 보고 요청을 입력해 계속 고치고 싶음 — 음성 아님).
    마지막 버전의 쪽별 문구를 사내 AI 가 요청대로 고치고 → 수치·표현 검증 → 같은 틀로 다시 조판해 새 버전으로 저장.
    문구가 저장되지 않은 예전 버전이면 문구부터 다시 쓴 뒤 고친다."""
    from ..proposal import project_deck as pd

    request = (request or "").strip()
    if not request:
        raise ProjectError("고칠 내용을 적어 주세요.")
    if len(request) > pd.EDIT_MAX:
        raise ProjectError(f"수정 요청은 {pd.EDIT_MAX}자 이내로 적어 주세요.")
    project = await _load(pid)
    _require(project, "done")
    async with SessionLocal() as db:
        row = (await db.execute(text("SELECT version, deck FROM project_outputs WHERE project_id = :p "
                                     "ORDER BY version DESC LIMIT 1"), {"p": pid})).first()
    if row is None:
        raise ProjectError("먼저 제안서를 만들어 주세요.")
    state = row[1]
    if not state:
        await step("예전 버전이라 쪽 문구부터 다시 쓰는 중(로컬 모델)")
        _, _, state = await pd.generate_deck(project)
    await step("사내 AI가 수정 요청을 문구에 반영하는 중")
    try:
        data, report, deck, res = await pd.edit_deck(project, state, request)
    except ValueError as e:
        raise ProjectError(str(e)) from e
    if not res.changed:
        async with SessionLocal() as db:
            await _message(db, pid, f"제안서 수정 요청: {request}", role="user")
            await _message(db, pid, "바꿀 곳을 찾지 못해 새 버전을 만들지 않았습니다. "
                           + (res.note or "쪽 번호나 바꿀 문장을 조금 더 구체적으로 적어 주세요."))
            await db.commit()
        return "바뀐 곳이 없어 새 버전을 만들지 않았습니다." + (f" {res.note}" if res.note else "")
    version, rep = await _save_output(project, data, report, deck,
                                      {"edit": {"from": int(row[0]), "request": request[:300],
                                                "changed": res.changed, "note": res.note}})
    msg = f"제안서 v{version} — v{row[0]}에서 프롬프트 수정으로 {', '.join(res.changed)}을(를) 고쳤습니다." + (
        f" {res.note}" if res.note else "")
    async with SessionLocal() as db:
        await _message(db, pid, f"제안서 수정 요청: {request}", role="user")
        await _message(db, pid, msg)
        await db.commit()
    return msg


async def back_to(db: AsyncSession, project: dict[str, Any], stage: str) -> None:
    """승인 이후 고치기 — done/producing 실패 → structure, structure → concept."""
    allowed = {"structure": ("done", "producing"), "concept": ("structure", "done")}
    if stage not in allowed or project["stage"] not in allowed[stage]:
        raise ProjectError("그 단계로 되돌릴 수 없습니다.")
    await _set(db, project["id"], stage=stage)
    await _message(db, project["id"], f"수정을 위해 '{'구성 확인' if stage == 'structure' else '공정·컨셉'}' 단계로 되돌렸습니다.",
                   role="user")
    await db.commit()


# ---------- 바로 만들기(실행 시험용) ----------

async def run_quick(pid: int, step: Step, *, with_images: bool) -> str:
    notes: list[str] = []
    project = await _load(pid)
    if project["stage"] in ("intake", "reading"):
        await step("자료 읽는 중")
        await dialog.run_reading(pid)
        project = await _load(pid)
    if project["stage"] == "questioning":
        missing = dialog.required_open(project)
        async with SessionLocal() as db:
            await _set(db, pid, stage="concept")
            await _message(db, pid, "시험 진행: 남은 질문을 건너뛰었습니다."
                           + (f" 필수 문항 미답: {', '.join(missing)}" if missing else ""))
            await db.commit()
        if missing:
            notes.append(f"필수 문항 미답 {len(missing)}개")
        project = await _load(pid)
    if project["stage"] == "concept":
        if not project["alternatives"]:
            await run_propose(pid, step)
            project = await _load(pid)
        if plan.pending(project):
            alts = [plan.confirm(a) if plan.alt_pending(a) else a for a in project["alternatives"]]
            async with SessionLocal() as db:
                await _set(db, pid, alternatives=alts)
                await _message(db, pid, "시험 진행: 공정 컨셉 계획을 AI 추천대로 확정했습니다.")
                await db.commit()
            project = await _load(pid)
        if with_images and codex_client.is_configured():
            for a in project["alternatives"]:
                if any(i["approved"] and i["alt_id"] == a["id"] for i in project["images"]):
                    continue
                try:
                    await run_image(pid, a["id"], step, auto_approve=True)
                except Exception as e:  # noqa: BLE001 — 이미지 없이도 제안서는 만든다
                    notes.append(f"컨셉 {a['id']} 이미지 실패: {e}")
            project = await _load(pid)
        elif with_images:
            notes.append("외부 AI 연결 미설정 — 이미지 없이 진행")
        async with SessionLocal() as db:
            await finish_concept(db, project, project["user_id"])
        project = await _load(pid)
    if project["stage"] == "structure":
        await run_structure(pid, step)
        project = await _load(pid)
        async with SessionLocal() as db:
            await approve_structure(db, project, project["user_id"])
    if project["stage"] in ("producing", "structure", "done"):
        msg = await run_produce(pid, step)
        return msg + (" 시험 진행 메모: " + "; ".join(notes) if notes else "")
    raise ProjectError("제작 단계까지 가지 못했습니다.")
