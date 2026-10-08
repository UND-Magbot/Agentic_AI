"""3단계 공정 제안·컨셉 이미지, 5단계 견적 행 초안 (가이드 "컨셉 이미지 제작 지시와 검수").

- propose(): 로컬 gemma 가 확인된 문항만 보고 로봇 대안 1~3개와, 비어 있는 '설계' 문항의 제안값을 낸다.
  고객 사실(수량·치수·목적)은 AI 가 채우지 않는다(가이드: 사실은 영업이 확인, 설계 대안은 AI 가 제안).
  문항에 없는 수치는 쓰지 못하게 걸러 낸다(장치 개수 1~9 는 설계 제안이라 허용).
- image_prompt(): 그리기 전에 장치 수량·위치 관계를 문장으로 고정한 그림 지시. 글자 없는 그림을 요청하고
  주석은 PPT 편집 요소(labels)로 덧씌운다.
- vision_check(): 생성된 그림을 gemma 가 보고 가이드 '생성 후 확인할 다섯 가지' 초안과 라벨 위치를 제안한다.
  승인은 사용자가 한다(AI 검수는 초안).
- draft_quote_lines(): 5c 계약 형식의 견적 행 초안. 단가는 넣지 않는다(근거 없는 금액 금지 → '별도 협의').
"""
from __future__ import annotations

import base64
import json
import re
from typing import Any

from .. import proposal_llm
from .catalog import QUESTIONS

ALT_IDS = ("A", "B", "C")
ALT_CODES = {"A": "P14", "B": "P15", "C": "P16"}
STRUCTURE_MAX = 9       # 제안 8문장 + 계획 확정 때 붙이는 '선택 모델' 1문장
USABLE = ("confirmed", "adopted", "assumed")
# AI 가 제안해도 되는 설계 문항(비어 있을 때만). 고객 사실 문항은 넣지 않는다.
DESIGN_CODES = ("P10", "P17", "P18", "P19", "P21", "P22", "P23", "P25", "P26", "P28",
                "P29", "P30", "P31", "P32", "P41")
QUOTE_GROUPS = ("공통", "통합·실증", "대안", "옵션", "현장 적용")
PROMPT_MAX = 2000
LABEL_MAX = 8
CHECKS = (
    ("count", "형태와 장치 수량"),
    ("layout", "로봇·설비의 위치 관계"),
    ("flow", "투입·반출 흐름 방향"),
    ("text", "그림 속 글자·요청하지 않은 요소 없음"),
    ("hidden", "잘리거나 가려진 장치 없음"),
)

_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")


def _nums(s: str) -> set[str]:
    return {n.replace(",", "") for n in _NUM_RE.findall(s or "")}


# 외부 두뇌(GPT)로는 보내지 않는 문항 — 고객사·부서·프로젝트명은 설계 판단에 필요 없다(가명 관문과 별개의 이중 차단).
EXTERNAL_EXCLUDE = ("P01",)


def item_lines(project: dict[str, Any], *, statuses=USABLE, exclude: tuple[str, ...] = ()) -> list[str]:
    out = []
    for code, it in project["items"].items():
        if code in exclude:
            continue
        if it["status"] in statuses and it["value"] and code in QUESTIONS:
            tag = {"confirmed": "확인됨", "adopted": "채택", "assumed": "가정"}.get(it["status"], it["status"])
            out.append(f"{code} {QUESTIONS[code].question}: {it['value']} [{tag}]")
    return out


def allowed_numbers(project: dict[str, Any]) -> set[str]:
    """제안 문장에 쓸 수 있는 수치 = 문항 값에 있는 수치 + 장치 개수(1~9)."""
    nums = {str(i) for i in range(1, 10)}
    for it in project["items"].values():
        nums |= _nums(it["value"])
    return nums


def _clean(s: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()[:limit]


# ---------- 공정·대안 제안 ----------

_PROPOSE_SYSTEM = """너는 유엔디로보틱스의 수석 기술영업 엔지니어다. 고객 공정 정보를 보고 로봇 자동화 공정 컨셉을 제안한다.
JSON 으로만 답한다.

규칙:
1. 공정 컨셉은 기본 1개다. 고객이 비교해 달라고 한 로봇 형태·구성이 [문항](P13~P16)에 여러 개 있을 때만
   그 수만큼(최대 3개) 만든다. 고객이 지정했거나 검토 중인 로봇이 있으면 그것으로 컨셉을 만든다.
   지정이 없으면 공정 조건(대상물·처리량·공간·환경)에 가장 맞는 로봇 형태 하나를 골라 이유를 쓴다.
2. [문항]에 없는 브랜드·모델명을 새로 만들지 않는다([회사 그리퍼·툴 제품]의 제품 이름은 예외). 로봇은 형태로 쓴다.
   특정 브랜드가 더 싸거나 더 좋다고 쓰지 않는다.
3. 수치는 [문항]에 있는 수치만 쓴다. 장치 개수(1~9대)는 써도 된다. 치수·속도·가격을 지어내지 않는다.
4. structure 는 그림을 그릴 때 그대로 쓸 문장이다: 장치 종류와 개수, 서로의 위치 관계, 물품 흐름 방향.
   3~8문장. 공정에 필요한 주변 설비(컨베이어·비전·지그 등)도 넣되 유엔디 표준 제품처럼 쓰지 않는다.
5. exclude 는 그림에 넣지 말아야 할 것(요청과 무관한 장치, 확정되지 않은 설비 등) 0~4개.
6. fills 는 [제안 문항] 중 설계 대안으로 답할 수 있는 것만 채운다. 고객에게 확인할 사실은 채우지 않는다.
   value 는 한 문장, reason 은 왜 그렇게 제안하는지 한 문장.
7. [회사 그리퍼·툴 제품]이 있으면(고객이 그리퍼를 지정하지 않음) 그리퍼는 그 목록에서 대상물·조건에 맞는 제품을
   우선 골라 제품 이름만(대괄호 속 제품군은 빼고) structure 와 P21 에 쓰고, 왜 맞는지 reason 에 쓴다.
   맞는 제품이 없으면 그리퍼 형태로 쓴다. [공식 사양] 제품 중에 맞는 것이 있으면 [소개서 요약 — 사양 미흡] 제품보다
   먼저 고른다(사양이 확인되지 않은 제품을 우선 추천하지 않는다).
   목록의 사양 수치는 옮기지 않는다. 특정 대상물과 그리퍼 조합이 검증된 것처럼 쓰지 않는다(실증에서 확인).
   툴체인저·툴스탠드는 회사 제품이라는 이유만으로 넣지 않는다 — 그리퍼 교체가 꼭 필요할 때만 P23 에 옵션으로 제안한다.
8. 카메라는 위치별 역할을 나눈다: 앞단 사전 판단(P17)과 취출 직전 위치·자세 확인(P18). 카메라를 달면 인식(반사·물기·겹침 등)이
   해결된다고 쓰지 않는다 — 인식 난점은 실증 확인 항목으로 둔다. 문항에 없는 시간·정확도 값은 쓰지 않는다.
9. robot_need 는 로봇 DB 에서 로봇 팔 모델 후보를 고르기 위한 내부 추정이다. 고객 문서에 쓰지 않고, 화면에
   'AI 추정'으로 표시되며 사용자가 바꾼다 — 그래서 규칙 3(수치 금지)의 예외다. 자료에 정확한 무게·치수가 없어도
   대상물 종류(예: 식기·보틀·박스)와 배치로 일반적인 값을 추정해 반드시 숫자로 넣는다. null 은 대상물을 전혀 알 수
   없을 때만.
   arm: 협동로봇 팔을 따로 골라 붙이는 구성(단일 팔·양팔·AMR 위 팔)이면 true. 휴머노이드·델타·갠트리처럼 로봇
   자체가 완제품이면 false.
   payload_kg = 대상물 최대 무게 추정 + 그리퍼 무게 여유(보통 1~3kg), reach_mm = 배치상 필요한 도달거리 추정,
   ip_min = 물기·세척·분진 환경이면 필요한 IP 두 자리(예: 54), 일반 환경이면 null. why 에 "무엇을 근거로 어떻게
   추정했는지" 한 문장. mobile = 팔을 AMR·이동체 위에 싣으면 true(팔 무게가 적재 부담 — 가벼운 모델 우선).
   exclude_makers = 고객·사용자가 빼 달라고 한 로봇 제조사 이름 목록(없으면 []).
   makers = [문항]에서 고객·사용자가 로봇 팔 제조사를 지정했으면 그 이름(예: ["DOBOT"]), 아니면 [].
   priority = [문항]이 국내 로봇·국내 신뢰성을 중시하면 "domestic", 아니면 "price"(회사 기본: 가격 중심).
10. gripper 는 이 컨셉에 가장 맞는 그리퍼 한 가지(회사 제품 이름 또는 형태), gripper_why 는 그 이유 한 문장.

출력 형식:
{"alternatives": [{"name": "컨셉 이름(15자 이내)", "robot": "로봇 형태와 대수", "summary": "한 문장 요약",
  "reason": "이 컨셉을 제안하는 이유", "structure": ["..."], "exclude": ["..."],
  "robot_need": {"arm": true, "payload_kg": 5, "reach_mm": 900, "ip_min": 54, "mobile": false,
                 "exclude_makers": [], "makers": [], "priority": "price", "why": "..."},
  "gripper": "...", "gripper_why": "..."}],
 "fills": [{"code": "P21", "value": "...", "reason": "..."}]}"""


def _propose_user(project: dict[str, Any], *, exclude: tuple[str, ...] = (), tooling: list[str] = ()) -> str:
    open_design = [c for c in DESIGN_CODES if project["items"][c]["status"] in ("empty", "unknown")]
    lines = "\n".join(item_lines(project, exclude=exclude)) or "(확인된 문항 없음)"
    design = "\n".join(f"- {c}: {QUESTIONS[c].question}" for c in open_design) or "(없음)"
    out = f"[문항]\n{lines}\n\n[제안 문항]\n{design}"
    if tooling and gripper_open(project):
        out += "\n\n[회사 그리퍼·툴 제품]\n" + "\n".join(tooling)
    return out


def gripper_open(project: dict[str, Any]) -> bool:
    """고객이 그리퍼 제품군(P21)을 정하지 않았나 — 첫 화면 선택 질문을 비웠거나 자료에 없음."""
    return project["items"].get("P21", {}).get("status") not in USABLE


def _as_list(v: Any) -> list[str]:
    if isinstance(v, list):
        return [str(x) for x in v if str(x).strip()]
    return [str(v)] if v and str(v).strip() else []


def tooling_line(name: str, card: dict[str, Any]) -> str:
    """제품 카드 → 한 줄(이름 [제품군] 요약 / 강점 / 맞는 조건 / 한계)."""
    parts = [_clean(card.get("summary"), 90)]
    strengths = _as_list(card.get("strengths"))[:2]
    if strengths:
        parts.append("강점: " + "; ".join(_clean(s, 50) for s in strengths))
    if card.get("applies_when"):
        parts.append("맞는 조건: " + _clean(card["applies_when"], 80))
    limits = (_as_list(card.get("limits")) + _as_list(card.get("not_when")))[:2]
    if limits:
        parts.append("한계: " + "; ".join(_clean(s, 60) for s in limits))
    return f"- {name} [{card.get('family') or ''}] " + " / ".join(p for p in parts if p)


async def company_tooling() -> list[str]:
    """회사 그리퍼·툴체인저 제품 카드(활성) 한 줄 목록. DB 를 못 읽으면 빈 목록 — 제안 자체는 막지 않는다."""
    from sqlalchemy import text

    from ..company_knowledge.product_images import category_of
    from ..database import SessionLocal

    try:
        async with SessionLocal() as db:
            rows = (await db.execute(text(
                "SELECT name, card, evidence FROM knowledge_cards WHERE kind = 'product' AND active "
                "ORDER BY (evidence = '공식 사양') DESC, id"))).mappings().all()
    except Exception:  # noqa: BLE001
        return []
    # 정보 수준을 붙인다 — 소개서 요약만 있는 제품을 우선 추천하지 않게(2026-10-01).
    return [tooling_line(r["name"], r["card"]) + (" [공식 사양]" if r["evidence"] == "공식 사양"
                                                  else " [소개서 요약 — 사양 미흡]")
            for r in rows if category_of(r["card"].get("family") or "", r["name"]) in ("eoat", "atc")]


def validate_proposal(raw: dict[str, Any], project: dict[str, Any]) -> tuple[list[dict], list[dict], list[str]]:
    """모델 응답 → (대안, 설계 문항 제안, 버린 사유). 근거 없는 수치가 든 문장은 버린다."""
    nums = allowed_numbers(project)
    dropped: list[str] = []

    def ok(text: str, where: str) -> bool:
        bad = _nums(text) - nums
        if bad:
            dropped.append(f"{where}: 근거 없는 수치 {sorted(bad)}")
        return not bad

    alts = []
    for k, a in enumerate([a for a in (raw.get("alternatives") or []) if isinstance(a, dict)][:3]):
        aid = ALT_IDS[k]
        name = _clean(a.get("name"), 30) or f"대안 {aid}"
        robot, summary, reason = (_clean(a.get(f), 200) for f in ("robot", "summary", "reason"))
        structure = [s for s in (_clean(x, 200) for x in a.get("structure") or []) if s and ok(s, f"대안 {aid} 구성")][:8]
        exclude = [s for s in (_clean(x, 120) for x in a.get("exclude") or []) if s and ok(s, f"대안 {aid} 제외")][:4]
        robot = robot if ok(robot, f"대안 {aid} 로봇") else ""
        summary = summary if ok(summary, f"대안 {aid} 요약") else ""
        reason = reason if ok(reason, f"대안 {aid} 이유") else ""
        if not structure:
            dropped.append(f"대안 {aid}: 구성 문장이 없어 뺌")
            continue
        alts.append({"id": aid, "name": name, "robot": robot, "summary": summary, "reason": reason,
                     "structure": structure, "exclude": exclude, "item_codes": [ALT_CODES[aid]],
                     "plan": plan_hint(a)})
    # 버린 대안이 있으면 id 를 A부터 다시 매긴다(5c: 대안 id ↔ P14/P15/P16).
    for k, a in enumerate(alts):
        a["id"] = ALT_IDS[k]
        a["item_codes"] = [ALT_CODES[a["id"]]]

    fills = []
    for f in raw.get("fills") or []:
        if not isinstance(f, dict):
            continue
        code = _clean(f.get("code"), 8).upper()
        value, reason = _clean(f.get("value"), 400), _clean(f.get("reason"), 200)
        if code not in DESIGN_CODES or not value or project["items"][code]["status"] not in ("empty", "unknown"):
            continue
        if ok(value, code):
            fills.append({"code": code, "value": value, "reason": reason})
    return alts, fills, dropped


def _range(v: Any, lo: float, hi: float) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if lo <= x <= hi else None


# 로봇 DB 의 제조사 표기로 맞춘다(모델이 영문·약칭으로 적어도 지정 제조사 필터가 걸리게).
_MAKER_ALIAS = {"dobot": "DOBOT", "두봇": "DOBOT", "hanwha": "한화로보틱스", "한화": "한화로보틱스",
                "rainbow": "레인보우로보틱스", "레인보우": "레인보우로보틱스", "rainbow robotics": "레인보우로보틱스",
                "ur": "Universal Robots", "universal robots": "Universal Robots", "유니버설로봇": "Universal Robots",
                "kuka": "KUKA", "쿠카": "KUKA"}


def plan_hint(a: dict[str, Any]) -> dict[str, Any]:
    """모델이 낸 로봇 요구 사양·그리퍼 추천 → 계획 초안(로봇 DB 후보는 plan.attach 가 붙인다).
    요구 사양 수치는 로봇 고르기용 AI 추정이라 문항 수치 검증 대상이 아니다 — 범위만 막는다."""
    need = a.get("robot_need") if isinstance(a.get("robot_need"), dict) else {}
    ip = _range(need.get("ip_min"), 20, 69)
    ex = need.get("exclude_makers") if isinstance(need.get("exclude_makers"), list) else []
    out = {"arm": bool(need.get("arm", True)), "payload_kg": _range(need.get("payload_kg"), 0.1, 100),
           "reach_mm": _range(need.get("reach_mm"), 100, 3000), "ip_min": int(ip) if ip else None,
           "exclude_makers": [_clean(x, 30) for x in ex if _clean(x, 30)][:5], "why": _clean(need.get("why"), 200)}
    mk = need.get("makers") if isinstance(need.get("makers"), list) else []
    out["makers"] = [_MAKER_ALIAS.get(_clean(x, 30).lower(), _clean(x, 30)) for x in mk if _clean(x, 30)][:3]
    out["priority"] = "domestic" if need.get("priority") == "domestic" else "price"
    if "mobile" in need:
        out["mobile"] = bool(need["mobile"])
    return {"robot_need": out,
            "gripper_ai": _clean(a.get("gripper"), 60), "gripper_why": _clean(a.get("gripper_why"), 200)}


def _json(raw: str) -> dict[str, Any]:
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else {}
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw or "", re.S)
        try:
            return json.loads(m.group(0)) if m else {}
        except json.JSONDecodeError:
            return {}


async def propose(project: dict[str, Any], *, chat=proposal_llm.chat) -> tuple[list[dict], list[dict], list[str]]:
    raw = await chat([{"role": "system", "content": _PROPOSE_SYSTEM},
                      {"role": "user", "content": _propose_user(project)}],
                     fmt="json", num_predict=4000, temperature=0.3)
    return validate_proposal(_json(raw), project)


async def propose_with_brain(project: dict[str, Any], *, brain: str, user_id: int | None,
                             chat=None, tooling: list[str] | None = None) -> dict[str, Any]:
    """두뇌(gemma|gpt)를 골라 제안 → {alternatives, fills, dropped, used, fallback_reason}.
    검증(validate_proposal)은 두뇌와 무관하게 같다. 그리퍼 미지정이면 회사 그리퍼·툴 제품 목록을 같이 준다."""
    from .brain import ask_json

    if tooling is None:
        tooling = await company_tooling() if gripper_open(project) else []
    data = _propose_user(project, exclude=EXTERNAL_EXCLUDE if brain == "gpt" else (), tooling=tooling)
    res = await ask_json(_PROPOSE_SYSTEM, data, brain=brain, user_id=user_id, chat=chat)
    alts, fills, dropped = validate_proposal(_json(res.raw), project)
    return {"alternatives": alts, "fills": fills, "dropped": dropped, "used": res.used,
            "fallback_reason": res.fallback_reason, "raw": res.raw}


_EXTRA_RULE = """

[추가 요청] 이미 있는 컨셉([기존 컨셉])과 로봇 형태·배치가 분명히 다른 비교안 1개만 alternatives 에 넣는다.
기존 컨셉은 다시 쓰지 않는다. fills 는 빈 목록으로 둔다."""


async def propose_extra(project: dict[str, Any], *, brain: str, user_id: int | None, chat=None) -> dict[str, Any]:
    """비교안 1개 추가 제안(사용자가 '비교안 추가' 를 눌렀을 때). 반환 형식은 propose_with_brain 과 같다."""
    from .brain import ask_json

    existing = project.get("alternatives") or []
    if len(existing) >= len(ALT_IDS):
        return {"alternatives": [], "fills": [], "dropped": ["비교안은 3개까지입니다."], "used": brain,
                "fallback_reason": ""}
    known = "\n".join(f"- {a['name']}: {a.get('robot', '')} / " + "; ".join(a["structure"][:3]) for a in existing)
    tooling = await company_tooling() if gripper_open(project) else []
    data = (_propose_user(project, exclude=EXTERNAL_EXCLUDE if brain == "gpt" else (), tooling=tooling)
            + f"\n\n[기존 컨셉]\n{known}")
    res = await ask_json(_PROPOSE_SYSTEM + _EXTRA_RULE, data, brain=brain, user_id=user_id, chat=chat)
    alts, _fills, dropped = validate_proposal(_json(res.raw), project)
    return {"alternatives": alts[:1], "fills": [], "dropped": dropped, "used": res.used,
            "fallback_reason": res.fallback_reason}


# ---------- 수정 요청 반영(여러 번) ----------

REVISION_MAX = 1000
_REVISE_SYSTEM = """너는 로봇 자동화 공정 컨셉 담당자다. 사용자의 [수정 요청]을 [현재 컨셉]에 반영해 고친 컨셉을 JSON 으로만 답한다.
규칙:
1. 수정 요청과 관련된 문장만 고치거나 더한다. 요청과 무관한 구성 문장은 한 글자도 바꾸지 않는다.
2. structure 는 그림에 그대로 쓸 문장(장치 종류·개수·위치 관계·흐름 방향), 3~8문장.
   exclude 는 그림에 넣지 말 것 0~4개. 사용자가 빼 달라고 한 것은 structure 에서 지우고 exclude 에 넣는다.
3. 수치는 [문항]·[현재 컨셉]·[수정 요청]에 있는 수치만 쓴다. 장치 개수(1~9)는 써도 된다.
4. 요청이 로봇 형태를 바꾸는 것이면 robot 도 고친다. 이름(name)은 형태가 바뀔 때만 고친다.
5. note 에 무엇을 바꿨는지 한 문장으로 적는다.
6. 요청이 로봇 크기·모델·가반하중·그리퍼를 바꾸는 것이면 robot_need·gripper 도 고친다(형식은 [현재 컨셉]과 같음).
   특정 로봇 제조사로 골라 달라면 robot_need.makers 에, 빼 달라면 robot_need.exclude_makers 에 그 이름을 적는다.
   관계없으면 [현재 컨셉]의 값을 그대로 둔다.
{"name": "...", "robot": "...", "summary": "...", "structure": ["..."], "exclude": ["..."],
 "robot_need": {"arm": true, "payload_kg": null, "reach_mm": null, "ip_min": null, "why": "..."},
 "gripper": "...", "gripper_why": "...", "note": "..."}"""


def validate_revision(raw: dict[str, Any], alt: dict[str, Any], project: dict[str, Any],
                      request: str) -> tuple[dict[str, Any], str, list[str]]:
    """수정 결과 검증 → (고친 컨셉, 바꾼 내용, 버린 사유). 구성이 비면 기존 구성을 유지한다."""
    nums = allowed_numbers(project) | _nums(request) | _nums(" ".join(alt["structure"] + [alt.get("robot") or ""]))
    dropped: list[str] = []

    def ok(t: str) -> bool:
        bad = _nums(t) - nums
        if bad:
            dropped.append(f"근거 없는 수치 {sorted(bad)}: {t[:60]}")
        return not bad

    structure = [s for s in (_clean(x, 200) for x in raw.get("structure") or []) if s and ok(s)][:STRUCTURE_MAX]
    exclude = [s for s in (_clean(x, 120) for x in raw.get("exclude") or []) if s and ok(s)][:4]
    if not structure:
        dropped.append("고친 구성이 비어 기존 구성을 유지")
        structure = alt["structure"]
        exclude = exclude or alt.get("exclude") or []
    robot = _clean(raw.get("robot"), 200)
    summary = _clean(raw.get("summary"), 200)
    new = {**alt, "name": _clean(raw.get("name"), 30) or alt["name"],
           "robot": robot if robot and ok(robot) else alt.get("robot", ""),
           "summary": summary if summary and ok(summary) else alt.get("summary", ""),
           "structure": structure, "exclude": exclude}
    if "robot_need" in raw or "gripper" in raw:
        old = alt.get("plan") or {}
        # 요구 사양은 칸별로 합친다 — 모델이 일부 칸만 돌려줘도 앞서 정한 지정·제외 제조사가 지워지지 않게(QA 3회차).
        need = dict(old.get("robot_need") or {})
        if isinstance(raw.get("robot_need"), dict):
            need.update({k: v for k, v in raw["robot_need"].items() if v not in (None, "", [])})
        hint = plan_hint({"robot_need": need, "gripper": raw.get("gripper") or old.get("gripper_ai") or "",
                          "gripper_why": raw.get("gripper_why") or old.get("gripper_why") or ""})
        new["plan"] = {**(alt.get("plan") or {}), **hint}
    return new, _clean(raw.get("note"), 200), dropped


async def revise_alternative(project: dict[str, Any], alt: dict[str, Any], request: str, *, brain: str,
                             user_id: int | None, chat=None) -> dict[str, Any]:
    """말로 한 수정 요청 → 컨셉 구성 문장 수정. 반환 {alternative, note, dropped, used, fallback_reason}."""
    from .brain import ask_json

    plan = alt.get("plan") or {}
    current = json.dumps({**{k: alt.get(k) for k in ("name", "robot", "summary", "structure", "exclude")},
                          "robot_need": plan.get("robot_need") or {}, "gripper": plan.get("gripper_pick")
                          or plan.get("gripper_ai") or "", "robot_model": plan.get("robot_pick") or ""},
                         ensure_ascii=False, indent=1)
    lines = "\n".join(item_lines(project, exclude=EXTERNAL_EXCLUDE if brain == "gpt" else ()))
    data = f"[문항]\n{lines}\n\n[현재 컨셉]\n{current}\n\n[수정 요청]\n{request}"
    res = await ask_json(_REVISE_SYSTEM, data, brain=brain, user_id=user_id, chat=chat, num_predict=2500)
    new, note, dropped = validate_revision(_json(res.raw), alt, project, request)
    return {"alternative": new, "note": note, "dropped": dropped, "used": res.used,
            "fallback_reason": res.fallback_reason}


def alternative_item_value(alt: dict[str, Any]) -> str:
    parts = [alt["name"]]
    if alt.get("robot"):
        parts.append(alt["robot"])
    plan = alt.get("plan") or {}
    if plan.get("robot_pick"):
        parts.append(f"로봇 팔 {plan['robot_pick']}")
    if plan.get("gripper_pick"):
        parts.append(f"그리퍼 {plan['gripper_pick']}")
    if alt.get("summary"):
        parts.append(alt["summary"])
    return " — ".join(parts)


# ---------- 컨셉 이미지 ----------

_IMAGE_GUIDE = """제조 현장 로봇 자동화 제안서에 넣을 컨셉 이미지 1장을 그려라.
- 16:9 가로, 흰 배경, 사선(아이소메트릭) 시점의 사실적인 3D 기계 렌더. 제안서 표지급 완성도.
- 그림 안에 글자·숫자·로고·라벨·화살표 문구·워터마크를 넣지 않는다(주석은 슬라이드에서 따로 덧씌운다).
- 치수와 성능값은 표현하지 않는다. 아래에 없는 장치를 임의로 추가하지 않는다.
- 작업자 접근 면과 물품 흐름을 가리지 않는다. 과장된 SF 표현 금지."""


_PROCESS_LABELS = (("P06", "대상물"), ("P07", "대상물 특성"), ("P09", "투입"), ("P03", "자동화 범위"),
                   ("P12", "최종 인계"))


_REF_USE = {
    "previous": "이전 버전 그림 — 시점·구도·배치를 그대로 두고 [이번 수정]만 바꾼다",
    "appearance": "로봇·장치의 모양을 이 이미지와 같게",
    "dimension": "배치·비율을 이 도면에 맞게(치수 글자는 그리지 않는다)",
    "content": "공정·설비 구성 참고",
}


def image_prompt(project: dict[str, Any], alt: dict[str, Any] | None, *,
                 refs: list[dict[str, str]] | None = None, revision: str = "") -> str:
    items = project["items"]

    def val(code: str) -> str:
        it = items.get(code) or {}
        return it["value"] if it.get("status") in USABLE and it.get("value") else ""

    process = "; ".join(f"{label}: {val(c)}" for c, label in _PROCESS_LABELS if val(c))
    parts = [_IMAGE_GUIDE, f"[공정] {process}" if process else ""]
    if alt:
        parts.append(f"[이 대안] {alt['name']} — {alt.get('robot') or ''}")
        parts.append("[구성 — 이 문장 그대로 배치]\n" + "\n".join(f"- {s}" for s in alt["structure"]))
        if alt.get("exclude"):
            parts.append("[넣지 않을 것]\n" + "\n".join(f"- {s}" for s in alt["exclude"]))
    if revision:
        parts.append("[이번 수정 — 이 부분만 바꾸고 나머지는 이전 버전과 같게]\n" + _clean(revision, 400))
    if refs:
        parts.append("[참고 이미지 — 첨부 순서대로]\n" + "\n".join(
            f"- {i}번: {_REF_USE.get(r['role'], '참고')}" + (f" ({_clean(r.get('note'), 60)})" if r.get("note") else "")
            for i, r in enumerate(refs, 1)))
    prompt = "\n\n".join(p for p in parts if p)
    return prompt[:PROMPT_MAX]


_VISION = """이 그림은 로봇 자동화 컨셉 이미지다. 아래 [요구 구성]과 비교해 검수하고 JSON 으로만 답하라.
checks 는 다섯 항목 모두 넣는다. ok 는 요구대로면 true, 아니면 false. note 는 무엇이 어떤지 한 문장.
 - count: 형태와 장치 수량이 요구와 같은가
 - layout: 로봇·컨베이어·주변 설비의 위치 관계가 요구와 같은가
 - flow: 물품 투입·반출 흐름 방향이 자연스러운가
 - text: 그림 속에 글자·숫자·로고가 없고, 요구하지 않은 장치가 없는가
 - hidden: 잘리거나 가려진 장치가 없는가
labels 는 그림 속 주요 장치 이름표 후보(최대 8개). text 는 12자 이내 한국어 장치 이름,
x·y 는 그 장치 중심의 그림 기준 위치(0~1, 왼쪽 위가 0,0).
{"checks": [{"key": "count", "ok": true, "note": "..."}], "labels": [{"text": "협동로봇", "x": 0.4, "y": 0.5}]}"""


def parse_vision(raw: str) -> tuple[list[dict], list[dict]]:
    d = _json(raw)
    by_key = {str(c.get("key")): c for c in d.get("checks") or [] if isinstance(c, dict)}
    checks = [{"key": k, "label": label, "ok": bool(by_key.get(k, {}).get("ok")) if k in by_key else None,
               "note": _clean(by_key.get(k, {}).get("note"), 200)}
              for k, label in CHECKS]
    labels = []
    for lb in d.get("labels") or []:
        if not isinstance(lb, dict):
            continue
        try:
            x, y = float(lb.get("x")), float(lb.get("y"))
        except (TypeError, ValueError):
            continue
        t = _clean(lb.get("text"), 12)
        if t and 0 <= x <= 1 and 0 <= y <= 1:
            labels.append({"text": t, "x": round(x, 3), "y": round(y, 3)})
    return checks, labels[:LABEL_MAX]


async def vision_check(image: bytes, alt: dict[str, Any] | None, *, chat=proposal_llm.chat) -> tuple[list, list]:
    want = "\n".join(f"- {s}" for s in (alt or {}).get("structure", [])) or "- 공통 공정 설비"
    raw = await chat([{"role": "user", "content": f"{_VISION}\n\n[요구 구성]\n{want}",
                       "images": [base64.b64encode(image).decode()]}],
                     fmt="json", num_predict=1500, temperature=0.1)
    return parse_vision(raw)


# ---------- 견적 행 초안 ----------

_QUOTE_SYSTEM = """너는 로봇 자동화 제안서의 견적 구성표 초안을 만든다. JSON 으로만 답한다.
규칙:
1. group 은 "공통", "통합·실증", "대안", "옵션", "현장 적용" 중 하나.
   - 공통: 모든 대안에 들어가는 설비(비전, 컨베이어 연계, 제어반 등)
   - 대안: 대안마다 달라지는 로봇·툴. alt_id 에 대안 id(A/B/C)를 넣는다. 대안끼리 합산하지 않는다.
   - 통합·실증: 시스템 통합, 설치, 시운전, 실증
   - 옵션: 고객이 고를 수 있는 추가 항목 / 현장 적용: 양산 확장 시 추가 항목
2. 고객이 기존 설비를 재사용한다고 한 항목은 included 를 false 로 둔다.
3. 금액은 쓰지 않는다. qty 는 수량 숫자(문자열), unit 은 "대"·"식"·"set" 등.
4. [문항]에 없는 브랜드·모델명을 새로 만들지 않는다. 항목은 10~16행.
5. 공통·대안·옵션에는 공급하는 장치·부품·소프트웨어만 쓴다. 검토·검증·확인·설치·시운전·교육 같은 활동은
   통합·실증에 넣는다(예: "설치 공간 배치 검토"는 공통이 아니라 통합·실증).
{"lines": [{"group": "대안", "alt_id": "A", "item": "협동로봇 본체", "qty": "1", "unit": "대", "included": true}]}"""


# 장비가 아닌 활동 — 공통·대안·옵션 표(주요 항목)에 들어가면 장비 목록이 흐려진다(QA M8). 통합·실증으로 옮긴다.
_ACTIVITY_RE = re.compile(r"검토|검증|확인|실증|테스트|시운전|설치|교육|운영\s*평가|배치\s*설계|컨설팅")


def validate_quote(raw: dict[str, Any], alt_ids: set[str]) -> list[dict[str, Any]]:
    out = []
    for ln in raw.get("lines") or []:
        if not isinstance(ln, dict):
            continue
        group = _clean(ln.get("group"), 10)
        item = _clean(ln.get("item"), 60)
        if group not in QUOTE_GROUPS or not item:
            continue
        alt = _clean(ln.get("alt_id"), 2).upper() or None
        if group == "대안" and alt not in alt_ids:
            continue
        if group in ("공통", "대안", "옵션") and _ACTIVITY_RE.search(item):
            group, alt = "통합·실증", None
        qty = _clean(ln.get("qty"), 10)
        out.append({"group": group, "alt_id": alt if group == "대안" else None, "item": item,
                    "qty": qty if re.fullmatch(r"\d+(\.\d+)?", qty) else "1",
                    "unit": _clean(ln.get("unit"), 6) or "식", "unit_price": None, "currency": "원",
                    "basis": "", "included": ln.get("included") is not False})
    return out[:24]


_REUSE_RE = re.compile(r"재사용|기존\s*설비\s*활용|고객\s*보유")
_WORD_RE = re.compile(r"[가-힣A-Za-z]{2,}")


def apply_reuse(lines: list[dict[str, Any]], project: dict[str, Any]) -> list[dict[str, Any]]:
    """고객이 재사용·제외한다고 한 설비는 견적에서 '미포함' 으로(실측: 모델이 규칙을 놓치고 기존 컨베이어를 넣었다).
    재사용 문장에 항목 이름의 낱말(2자 이상)이 나오면 미포함으로 본다."""
    reuse = [s for it in project["items"].values() if it["status"] in USABLE
             for s in re.split(r"[.\n]", it["value"]) if _REUSE_RE.search(s)]
    for ln in lines:
        words = [w for w in _WORD_RE.findall(ln["item"]) if w not in ("시스템", "장치", "설비", "구성")]
        if ln["included"] and any(w in s for w in words for s in reuse):
            ln["included"] = False
            ln["basis"] = ln["basis"] or "고객 기존 설비 재사용"
    return lines


async def draft_quote_lines(project: dict[str, Any], *, brain: str = "gpt", chat=None) -> list[dict[str, Any]]:
    """견적 행 초안 — GPT 가 쓰고(실패 시 gemma), 검증·재사용 설비 미포함은 코드가 한다."""
    from .brain import ask_json

    alts = project.get("alternatives") or []
    alt_text = "\n".join(f"- {a['id']}: {a['name']} / {a.get('robot', '')} / " + "; ".join(a["structure"][:4])
                         for a in alts) or "(컨셉 없음)"
    lines = item_lines(project, exclude=EXTERNAL_EXCLUDE if brain == "gpt" else ())
    data = "[문항]\n" + "\n".join(lines) + f"\n\n[대안]\n{alt_text}"
    res = await ask_json(_QUOTE_SYSTEM, data, brain=brain, user_id=project.get("user_id"), chat=chat,
                         num_predict=3000, temperature=0.2)
    return apply_reuse(validate_quote(_json(res.raw), {a["id"] for a in alts}), project)

