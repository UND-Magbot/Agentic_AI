"""회사 제품 추천 + 정정 학습(사용자 요청 2026-09-30).

의뢰인 요구: 조건(나중에 Q&A 로 받음, 지금은 자유 문장)을 주면 AI 가 종합 판단해 회사 제품 DB(지식 카드 kind=product)
에서 골라 추천한다. 추천이 틀렸을 때 사람이 "X 가 아니라 Y 를 쓴다, 왜냐하면 ~" 라고 바로잡으면 그 판단을
정정 기억(product_corrections)으로 쌓고, 다음에 비슷한 조건에서 X 를 추천하려 할 때 그 이유로 다시 생각해
피하게 한다(reconsidered 로 화면에 드러냄).

- AI 학습 내용(2026-10-02): 종류 경험(experience)·정정(correction)·기준(rule)을 같은 표에 쌓는다.
  대화에서 학습을 요청하면 AI 가 '학습시킬까요?' → 예 → 학습 문구를 보여 주고 사용자가 정확한 워딩으로 고친 뒤 저장.
- 권한(2026-10-02): 영업 관리자가 저장하면 바로 반영, 그 외는 승인 대기 → 관리자가 승인해야 반영(거절·끄기 가능).
- 회상: 이번 요청 ↔ 정정의 '조건+규칙' 임베딩 유사도 상위(임베딩이 안 되면 최근 것). 사내 AI 만 쓴다(외부 전송 없음).
"""
from __future__ import annotations

import functools
import json
import logging
import re
from typing import Any

from sqlalchemy import text

from .. import proposal_llm
from ..config import settings
from ..diagram.llm_spec import _extract_json

_log = logging.getLogger("company_knowledge.product_recommend")


def _default_chat():
    """추천·대화·정정 정리에 쓰는 사내 모델 — RECOMMEND_MODEL 이 있으면 그것, 없으면 제안서 모델."""
    if settings.recommend_model:
        return functools.partial(proposal_llm.chat, model=settings.recommend_model)
    return proposal_llm.chat


def _default_stream():
    """GPT 식 대화(atc_agent)의 스트리밍 호출 — 모델 고르는 기준은 _default_chat 과 같다."""
    if settings.recommend_model:
        return functools.partial(proposal_llm.chat_stream, model=settings.recommend_model)
    return proposal_llm.chat_stream

REQUEST_MAX = 2000
ITEMS_MAX = 3
RECALL_MAX = 6
MIN_SIM = 0.45
_CORRECTION_RE = re.compile(r"아니라|아니고|대신|말고|틀렸|잘못|정답은|써야|쓸 ?거|쓰겠|로 바꿔|바꿔야|이게 맞")


class RecommendError(Exception):
    """화면에 그대로 보여도 되는 한국어 문구."""


def _clean(s: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()[:limit]


def _norm(s: str) -> str:
    return re.sub(r"[\s\-_/·()\[\]]+", "", (s or "").lower())


def looks_like_correction(request: str) -> bool:
    """글이 정정처럼 보이나(빠른 1차 판별 — 대화 모드의 최종 판단은 converse 의 is_correction)."""
    return bool(_CORRECTION_RE.search(request or ""))


# ── 회사 제품 목록 ─────────────────────────────────────────────────────────────

# 개별 제품이 아닌 묶음·기술 소개 카드 — 추천 후보에서 뺀다(QA 1회차 2026-09-30: 10문제 중 6문제에서 섞여
# "Magbot EOAT" 같은 제품군 전체가 1순위로 나옴). 회사 지식 회상에는 그대로 남는다. 카드 데이터에 '개별 제품'
# 표시가 없어 이름으로 거른다 — docs/design/sales_qa_log.md 미흡점 참고.
NOT_PRODUCT = {"Magbot EOAT", "Magbot ATC", "Magbot ATC (스위칭 마그네틱)", "스위칭 마그네틱 기술",
               # 개별 모델(mDPG-S·mDPG-C)이 따로 있는 제품군 카드 — 같은 제품이 두세 번 추천됐다(QA 5회차)
               "magbot DC Pnuematic Gripper (mDPG 시리즈)"}
_NOT_PRODUCT_RE = re.compile(r"기술$")

# 동작 원리상 제약 — 카드에 용도·한계가 거의 없어(50장 중 '안 맞는 조건' 0장) 원리로 알 수 있는 것만 붙인다.
# 물리적으로 분명한 것만, '원리상 추정' 으로 표시(AI 가 단정하지 않게). (정규식, 원리 설명)
PRINCIPLES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"마그네틱|자석|Magnet|MG series|MGP|MGM", re.I),
     "자석(스위칭 마그네틱) 흡착 — 철·강 같은 강자성체만 집는다. 알루미늄·구리·유리·플라스틱·목재는 못 집는다"),
    (re.compile(r"mDPG|진공|흡착|Vacuum|Suction|석션", re.I),
     "진공 흡착 — 평평하고 매끈한 면에 맞다. 구멍·요철이 많거나 통기성 있는 면은 흡착이 어렵다"),
    (re.compile(r"FINGER|JAW|핑거", re.I), "기계식 핑거 — 재질과 무관하게 잡지만 대상 형상·크기에 맞는 손가락이 필요하다"),
    (re.compile(r"CYLINDRICAL|원통", re.I), "원통형 대상을 감싸 잡는 구조"),
    (re.compile(r"Shape Memory|형상기억|시프트락|SMG", re.I), "형상기억 방식 — 비정형 형상을 감싸 잡는 데 맞다"),
]


_GRIPPER_RE = re.compile(r"그리퍼|GRIPPER|Gripper|EOAT|End Tool|엔드툴|mDPG", re.I)


def principle_of(name: str, card: dict[str, Any]) -> str:
    """이름·동작 방식·강점에서 원리를 알아본다(없으면 빈 문자열). 물건을 집는 그리퍼·엔드툴에만 —
    툴체인저도 자석 결합을 쓰지만 '무엇을 집나' 제약과는 무관하다(QA 1회차: MTC 가 핑거로 잘못 분류)."""
    if not _GRIPPER_RE.search(f"{name} {card.get('family') or ''}"):
        return ""
    blob = " ".join([name, str(card.get("how_it_works") or ""), " ".join(map(str, card.get("strengths") or []))])
    for rx, note in PRINCIPLES:
        if rx.search(name) or (rx.pattern.startswith("마그네틱") and rx.search(blob)):
            return note
    for rx, note in PRINCIPLES:
        if rx.search(blob):
            return note
    return ""


_PAYLOAD_ITEM_RE = re.compile(r"가반|하중|payload", re.I)
_KG_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(kgf|kg)", re.I)


def payload_range(name: str, card: dict[str, Any]) -> str:
    """사양 표의 가반하중 → '5~30kgf(모델별)'. 수치가 없으면 원문('협의' 등), 그리퍼인데 자료가 없으면 그렇다고 적는다
    (QA 3회차: '강판 20kg' 에 가반하중 자료가 없는 진공 그리퍼가 1순위 — 무게로 거를 근거가 없었다)."""
    specs = [s for s in card.get("specs") or [] if isinstance(s, dict) and _PAYLOAD_ITEM_RE.search(str(s.get("item")))]
    vals, units, raw = [], set(), []
    for s in specs:
        v = str(s.get("value") or "")
        found = _KG_RE.findall(v)
        for num, unit in found:
            vals.append(float(num.replace(",", "")))
            units.add(unit.lower())
        if not found and v.strip():
            raw.append(_clean(v, 30))
    if vals:
        lo, hi = min(vals), max(vals)
        unit = "kgf" if units == {"kgf"} else "kg"
        rng = f"{lo:g}{unit}" if lo == hi else f"{lo:g}~{hi:g}{unit}"
        return f"가반하중: {rng}" + ("(모델별)" if len(specs) > 1 else "")
    if raw:
        return f"가반하중: {raw[0]}"
    if _GRIPPER_RE.search(f"{name} {card.get('family') or ''}"):
        return "가반하중: 자료 없음 — 무거운 대상은 확인 필요"
    return ""


def product_line(name: str, card: dict[str, Any]) -> str:
    def lst(k: str) -> list[str]:
        v = card.get(k)
        return [str(x) for x in v if str(x).strip()] if isinstance(v, list) else ([str(v)] if v else [])
    parts = [_clean(card.get("summary"), 120)]
    if card.get("how_it_works"):
        parts.append("동작 방식: " + _clean(card["how_it_works"], 100))
    principle = principle_of(name, card)
    if principle:
        parts.append("원리상 제약(추정): " + principle)
    payload = payload_range(name, card)
    if payload:
        parts.append(payload)
    if lst("strengths"):
        parts.append("강점: " + "; ".join(_clean(x, 50) for x in lst("strengths")[:3]))
    if card.get("applies_when"):
        parts.append("맞는 조건: " + _clean(card["applies_when"], 100))
    lim = (lst("limits") + lst("not_when"))[:2]
    if lim:
        parts.append("한계: " + "; ".join(_clean(x, 60) for x in lim))
    return f"- {name} [{_clean(card.get('family'), 30)}] " + " / ".join(p for p in parts if p)


def is_product(name: str) -> bool:
    return name not in NOT_PRODUCT and not _NOT_PRODUCT_RE.search(name)


OFFICIAL = "공식 사양"
LOW_INFO_NOTE = "공식 사양 미확보(소개서 요약만 있음) — 크기·용도·사양 확인 필요"


async def product_catalog(db) -> list[dict[str, Any]]:
    """추천 후보 제품. 정보 수준(공식 사양 / 소개서 요약)을 붙인다 — 정보가 미흡한 제품을 1순위로 내지 않기 위해
    (2026-10-01: 협동로봇 툴 교체에 소개서 카드 'H시리즈'(대형)가 1순위로 나옴)."""
    rows = (await db.execute(text(
        "SELECT id, name, card, evidence FROM knowledge_cards WHERE kind = 'product' AND active ORDER BY id"
    ))).mappings().all()
    out = []
    for r in rows:
        if not is_product(r["name"]):
            continue
        official = r["evidence"] == OFFICIAL
        level = "[정보: 공식 사양]" if official else "[정보: 소개서 요약 — 용도·사양 미흡]"
        aliases = [a for a in (r["card"] or {}).get("aliases") or [] if a]
        out.append({"card_id": r["id"], "name": r["name"], "family": (r["card"] or {}).get("family") or "",
                    "official": official, "aliases": aliases, "card": r["card"] or {},
                    "line": product_line(r["name"], r["card"] or {})
                    + (f" (다른 이름: {', '.join(aliases)})" if aliases else "") + " " + level})
    return out


def match_product(name: str, catalog: list[dict[str, Any]]) -> dict[str, Any] | None:
    """모델이 적은 이름 → 목록의 제품(정확히 같거나, 공백·기호를 뺀 이름이 포함 관계)."""
    n = _norm(name)
    if not n:
        return None
    for p in catalog:
        if _norm(p["name"]) == n or any(_norm(a) == n for a in p.get("aliases") or []):
            return p
    hits = [p for p in catalog if _norm(p["name"]) and (_norm(p["name"]) in n or n in _norm(p["name"]))]
    return max(hits, key=lambda p: len(p["name"])) if hits else None


# ── 정정 기억 ────────────────────────────────────────────────────────────────

# AI 학습 내용 종류(사용자 2026-10-02): 경험(실제 공정에서 써 본 결과) / 정정(A 말고 B) / 기준(다음부터 이렇게 판단).
KINDS = ("experience", "correction", "rule")
KIND_KO = {"experience": "경험", "correction": "정정", "rule": "기준"}


def norm_kind(k: Any) -> str:
    """예전 이름 success → experience. 모르는 값은 정정."""
    k = "experience" if k == "success" else k
    return k if k in KINDS else "correction"


def correction_text(c: dict[str, Any]) -> str:
    """회상·프롬프트용 한 줄. 사람이 확인한 '학습 문구'(rule)를 앞세우고 종류별 근거를 붙인다."""
    kind = norm_kind(c.get("kind"))
    rule = (c.get("rule") or "").strip()
    head = f"#{c['id']} [{KIND_KO[kind]}] [조건] {c.get('situation') or '-'} →"
    if kind == "experience":
        body = f" '{c.get('right_name')}' 사용. 결과: {c.get('reason')}"
    elif kind == "rule":
        body = f" {rule or c.get('reason')}" + (f" (이유: {c['reason']})" if rule and c.get("reason") else "")
        return head + body
    else:
        body = f" '{c.get('wrong_name') or '이전 추천'}' 가 아니라 '{c.get('right_name')}'. 이유: {c.get('reason')}"
    return head + body + (f" / 학습 문구: {rule}" if rule else "")


async def _embed(texts: list[str]) -> list[list[float]] | None:
    from ..rag import embed_batch
    try:
        return await embed_batch(texts)
    except Exception as e:  # noqa: BLE001 — 임베딩 서버가 없어도 정정 저장·추천은 된다(최근 것으로 회상)
        _log.warning("임베딩 실패: %r", e)
        return None


def _vec(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


async def relevant_corrections(db, request: str, limit: int = RECALL_MAX) -> list[dict[str, Any]]:
    """이번 요청과 비슷한 조건의 켜진 정정(유사도 순). 임베딩이 안 되면 최근 정정."""
    cols = "id, kind, situation, wrong_name, wrong_card_id, right_name, right_card_id, reason, rule"
    vec = await _embed([request])
    if vec:
        rows = (await db.execute(text(
            f"SELECT {cols}, 1 - (embedding <=> CAST(:v AS vector)) AS sim FROM product_corrections "
            "WHERE active AND embedding IS NOT NULL ORDER BY embedding <=> CAST(:v AS vector) LIMIT :n"),
            {"v": _vec(vec[0]), "n": limit})).mappings().all()
        return [dict(r) for r in rows if float(r["sim"]) >= MIN_SIM]
    rows = (await db.execute(text(f"SELECT {cols} FROM product_corrections WHERE active ORDER BY id DESC LIMIT :n"),
                             {"n": limit})).mappings().all()
    return [dict(r) for r in rows]


# ── 추천 ──────────────────────────────────────────────────────────────────────

_RECOMMEND_SYSTEM = """너는 유엔디로보틱스의 제품 추천 담당 기술영업이다. 고객 [조건]을 종합해 [회사 제품] 목록에서만 골라 추천한다.
JSON 으로만 답한다.
규칙:
0. 먼저 대상물 재질·형상·무게와 제품의 동작 원리·가반하중이 맞는지 따진다. 대상물 무게가 제품 가반하중을
   넘거나 가반하중 자료가 없는데 무거운 대상이면 1순위로 두지 않는다(두면 caution 에 확인 필요를 쓴다). '원리상 제약'에 걸리는 제품은 고르지 않는다
   (예: 자석 방식은 알루미늄·유리·플라스틱 같은 비자성체를 못 집는다). 무엇을 하려는지(파지·툴 교체·이송·관제·순찰)
   와 제품 종류가 맞아야 한다.
   각 제품 끝의 [정보: …]를 본다. '소개서 요약'(용도·사양 미흡) 제품은 1순위로 두지 않는다 — 조건에 맞는 '공식 사양'
   제품이 있으면 그것을 1순위로. 소개서 요약 제품을 넣을 때는 caution 에 확인이 필요하다고 쓴다.
1. [회사 제품]에 있는 이름을 그대로 쓴다. 목록에 없는 제품·브랜드·모델을 만들지 않는다. 최대 3개, 가장 맞는 순.
   맞는 제품이 1~2개뿐이면 억지로 3개를 채우지 않는다.
2. [AI 학습 내용]은 사람 전문가가 확인한 판단이다 — [정정](예전 추천을 바로잡음)·[경험](실제 공정에서 써 본 결과)·
   [기준](다음부터 이렇게 판단). [정정]은 이번 조건이 그 정정의 조건과 같거나 비슷하면
   반드시 따른다: 정정에서 틀렸다고 한 제품은 그 이유가 이번에도 해당하면 추천하지 않고, 정답 제품을 우선 검토한다.
   그렇게 판단을 바꿨으면 reconsidered 에 정정 번호와 "무엇을 왜 뺐고 무엇을 골랐는지" 를 쓴다.
   조건이 달라 정정이 해당하지 않으면 따르지 않아도 된다(그 경우도 note 에 이유를 짧게).
   [경험]은 이번 조건이 비슷하면 그 제품을 우선 검토하고, 골랐으면 reason 에 비슷한 경험이 있다고 쓰고 reconsidered 에
   번호와 note 를 쓴다. [기준]은 조건이 맞으면 그대로 따르고 reconsidered 에 적는다. 기록에 없는 결과를 지어내지 않는다.
3. 화면에는 1순위 하나만 크게 보인다 — 1순위를 가장 신중하게 고르고 가장 자세히 쓴다(나머지는 검토 후보).
   reason: 이 조건에서 왜 이 제품이 가장 맞는지 2~3문장. 고객 조건(재질·무게·로봇·공압·동작·환경)과 제품의 원리·사양을
   직접 대조해 쓴다(예: "대상이 철판이라 스위칭 마그네틱 흡착이 가능하고, 25kg 은 가반하중 30kgf 안에 듭니다").
   checks: 조건 항목별 근거 2~5개 — condition 에 고객 조건(짧게), basis 에 이 제품이 그 조건에 맞는(또는 확인이 필요한)
   근거, ok 는 맞으면 true, 확인이 필요하면 false. 고객이 답하지 않은 조건은 만들지 않는다.
   가반하중은 여유를 본다: 대상 무게가 가반하중과 같거나 여유가 20% 미만이면 그 check 는 ok=false 로 두고 caution 에
   여유 부족을 쓰며, 같은 원리에서 여유가 있는 모델이 있으면 그것을 1순위로 둔다.
   가반하중이 수치로 적혀 대상 무게를 충족하는 제품을, 가반하중이 '패드 구성 의존'·'협의'·자료 없음인 제품보다 앞에 둔다.
   다른 모델의 수치를 이 제품의 수치처럼 쓰지 않는다(예: '8S7 기준 13kgf'는 8S7 의 값).
   고객의 '그 밖의 요구'(예: 여러 점에서 잡기, 넓은 판 처짐 방지)도 조건이다 — 1순위 제품 하나로 다 못 맞추면 그 check 를
   ok=false 로 두고 caution 에 어떻게 보완할지(예: 같은 제품 여러 개 배치, 그 요구를 직접 맞추는 다른 제품군 검토)를 쓴다.
   보완으로 다른 제품을 들 때는 대상물에 맞는 같은 원리의 제품만 든다 — 원리가 다른 제품을 섞는 조합을 지어내지 않는다
   (예: 철판을 자석으로 잡는데 진공·형상기억 그리퍼와 '조합'하라고 하지 않는다).
   '그 밖의 요구'에 무엇이 적혀 있으면 반드시 check 하나로 다룬다(맞추면 ok=true 와 근거, 못 맞추면 ok=false 와 caution).
   check 의 basis 는 그 조건에 대한 직접 근거만 쓴다.
   fit: 잘 맞는 조건 한 줄, caution: 확인이 필요한 점 한 줄(없으면 빈 문자열).
   사양 수치는 [회사 제품]에 있는 것만 쓰고 지어내지 않는다. 검증되지 않은 성능을 단정하지 않는다("검토"·"확인 필요").
4. 맞는 제품이 없으면 items 를 비우고 summary 에 이유를 쓴다.
{"summary": "한 문장", "items": [{"product": "...", "reason": "...", "checks": [{"condition": "...", "basis": "...", "ok": true}],
 "fit": "...", "caution": "..."}], "reconsidered": [{"correction_id": 1, "note": "..."}]}"""


async def recommend(request: str, *, user_id: int | None, chat=None) -> dict[str, Any]:
    """조건 → 회사 제품 추천(정정 기억으로 재고). 결과를 기록하고 {id, summary, items, reconsidered, corrections}."""
    from ..database import SessionLocal

    request = _clean(request, REQUEST_MAX)
    if not request:
        raise RecommendError("추천받을 조건을 적어 주세요.")
    chat = chat or _default_chat()
    async with SessionLocal() as db:
        catalog = await product_catalog(db)
        if not catalog:
            raise RecommendError("회사 제품 DB 가 비어 있습니다.")
        corrections = await relevant_corrections(db, request)
    user = (f"[조건]\n{request}\n\n[회사 제품]\n" + "\n".join(p["line"] for p in catalog)
            + "\n\n[AI 학습 내용]\n" + ("\n".join(correction_text(c) for c in corrections) or "(없음)"))
    raw = await chat([{"role": "system", "content": _RECOMMEND_SYSTEM}, {"role": "user", "content": user}],
                     num_predict=2500)
    data = _extract_json(raw)
    if not isinstance(data, dict):
        raise RecommendError("추천 결과를 읽지 못했습니다. 다시 시도해 주세요.")
    return await _save_recommendation(request, data, catalog, corrections, user_id=user_id)


CHECKS_MAX = 5
SPECS_MAX = 6
_KEY_SPEC_RE = re.compile(r"가반|하중|Payload|크기|사이즈|치수|Size|무게|중량|Weight|전기|IP|온도|스트로크|Stroke|속도|정밀", re.I)


# 사내 AI 가 제품 용어를 가끔 틀리게 쓴다(2026-10-01 실측: '스위칭 마어네틱') — 화면에 내기 전에 바로잡는다.
# 맞는 말(마그네틱·그리퍼·툴체인저)은 건드리지 않고, 한 글자 틀리거나 빠진 꼴만 고친다.
_TERM_FIXES = [
    (re.compile(r"마(?!그네틱)[가-힣]?네틱"), "마그네틱"),
    (re.compile(r"그(?!리퍼)[가-힣]퍼"), "그리퍼"),
    (re.compile(r"툴(?!체인저|체인져)체[가-힣]저"), "툴체인저"),
    (re.compile(r"스위(?!칭)[가-힣]\s마그네틱"), "스위칭 마그네틱"),
]


def fix_terms(s: str) -> str:
    for rx, right in _TERM_FIXES:
        s = rx.sub(right, s)
    return s


def _ai_text(v: Any, limit: int) -> str:
    """AI 가 쓴 문장 → 화면용(공백 정리 + 용어 오타 바로잡기)."""
    return fix_terms(_clean(v, limit))


def ai_lines(v: Any, limit: int) -> str:
    """AI 가 쓴 답 → 화면용 — 줄바꿈·목록은 살리고(사용자 2026-10-07: 더 보기 좋게) 줄 안 공백만 정리, 빈 줄은 하나까지."""
    out: list[str] = []
    for ln in str(v or "").replace("\r", "").split("\n"):
        t = re.sub(r"[ \t]+", " ", ln).strip()
        if t or (out and out[-1]):
            out.append(t)
    return fix_terms("\n".join(out).strip()[:limit])


# 대화 답 형식(제품 추천·견적서·수주 진행 공용) — 화면은 줄바꿈·목록·**강조**(볼드+밑줄)를 그대로 보여 준다
ANSWER_FORMAT = """답 형식(화면에 그대로 보인다):
- 한 문단은 1~2문장으로 짧게, 내용이 바뀌면 줄을 바꾼다.
- 항목이 2개 이상이면 한 줄에 하나씩 '- ' 목록이나 '1) ' 번호 목록으로 쓴다.
- 핵심 단어(금액, 모델명, 고객사, 수량, 날짜, 버튼 이름 등)는 **이렇게** 두 별표로 감싼다 — 한 답에 2~5곳만."""


def _checks(raw: Any) -> list[dict[str, Any]]:
    out = []
    for c in raw if isinstance(raw, list) else []:
        if isinstance(c, dict) and _clean(c.get("condition"), 60) and _clean(c.get("basis"), 200):
            out.append({"condition": _ai_text(c.get("condition"), 60), "basis": _ai_text(c.get("basis"), 200),
                        "ok": c.get("ok") is not False})
        if len(out) >= CHECKS_MAX:
            break
    return out


def card_facts(card: dict[str, Any]) -> dict[str, Any]:
    """제품 카드에 실제로 적힌 사양·적용 조건·동작 방식 → 추천 결과 화면용(모델이 아니라 DB 가 근거).
    모델이 여럿인 카드(예: Type 1~4)는 항목마다 모델 이름을 붙인다."""
    specs = [s for s in card.get("specs") or [] if isinstance(s, dict) and s.get("item") and s.get("value")]
    multi = len({s.get("model") for s in specs if s.get("model") and s.get("model") != card.get("name")}) > 1
    key = [s for s in specs if _KEY_SPEC_RE.search(str(s["item"]))] or specs
    facts = [{"item": _clean(s["item"], 40) + (f" · {_clean(s.get('model'), 30)}" if multi and s.get("model") else ""),
              "value": _clean(s["value"], 120)} for s in key[:SPECS_MAX]]
    return {"specs": facts, "applies_when": _clean(card.get("applies_when"), 200),
            "how_it_works": _clean(card.get("how_it_works"), 200)}


PAYLOAD_MARGIN = 1.2      # 가반하중 ≥ 대상 무게 × 1.2 (여유 20%) — 같거나 빠듯하면 1순위로 두지 않는다
# 'Q. 무게는? … A. 약 25kg' — 요청은 저장 전에 줄바꿈이 공백으로 합쳐지므로 다음 'Q.' 앞까지를 답으로 본다
_WEIGHT_RE = re.compile(r"Q\.\s*무게[^?]*\?[^A]*?A\.\s*(.*?)(?=\s+Q\.|$)", re.S)


def request_weight(request: str) -> float | None:
    """Q&A 의 무게 답 → kg(여럿이면 큰 값). 자유 문장 요청이면 None(판단하지 않음)."""
    m = _WEIGHT_RE.search(request or "")
    found = _KG_RE.findall(m.group(1)) if m else []
    return max(float(n.replace(",", "")) for n, _ in found) if found else None


def payload_max(card: dict[str, Any]) -> float | None:
    """제품 카드 사양의 가반하중 최댓값(kg·kgf 같은 값으로 본다). 수치가 없으면 None."""
    vals = [float(n.replace(",", "")) for s in card.get("specs") or []
            if isinstance(s, dict) and _PAYLOAD_ITEM_RE.search(str(s.get("item")))
            for n, _ in _KG_RE.findall(str(s.get("value") or ""))]
    return max(vals) if vals else None


def validate_recommendation(data: dict[str, Any], catalog: list[dict[str, Any]],
                            corrections: list[dict[str, Any]], request: str = "") -> dict[str, Any]:
    """모델 응답 검증: 목록에 없는 제품은 버리고, 정정에서 틀렸다고 한 제품이 그대로 나오면 경고를 단다."""
    known = {c["id"]: c for c in corrections}
    payloads: dict[int, float | None] = {p["card_id"]: payload_max(p.get("card") or {}) for p in catalog}
    items, dropped = [], []
    for it in (data.get("items") or [])[:ITEMS_MAX + 2]:
        if not isinstance(it, dict):
            continue
        p = match_product(_clean(it.get("product"), 80), catalog)
        if p is None:
            dropped.append(_clean(it.get("product"), 60))
            continue
        if any(x["card_id"] == p["card_id"] for x in items):
            continue
        warn = [f"#{c['id']}" for c in corrections if c.get("wrong_card_id") == p["card_id"]]
        items.append({"card_id": p["card_id"], "product": p["name"], "family": p["family"],
                      "official": p.get("official", True),
                      **({} if p.get("official", True) else {"info_note": LOW_INFO_NOTE}),
                      "reason": _ai_text(it.get("reason"), 600), "fit": _ai_text(it.get("fit"), 200),
                      "caution": _ai_text(it.get("caution"), 200), "checks": _checks(it.get("checks")),
                      **card_facts(p.get("card") or {}),
                      **({"warning": f"과거 정정({', '.join(warn)})에서 틀렸다고 한 제품입니다 — 조건이 다른지 확인하세요"}
                         if warn else {})})
        if len(items) >= ITEMS_MAX:
            break
    reconsidered = []
    for r in data.get("reconsidered") or []:
        if not isinstance(r, dict):
            continue
        try:
            cid = int(r.get("correction_id"))
        except (TypeError, ValueError):
            continue
        if cid in known and _clean(r.get("note"), 300):
            reconsidered.append({"correction_id": cid, "note": _ai_text(r.get("note"), 300),
                                 "correction": correction_text(known[cid])})
    # 정보가 미흡한(소개서 요약만 있는) 제품은 1순위로 두지 않는다 — 추천 목록에 공식 사양 제품이 있으면 그걸 앞으로.
    if items and not items[0]["official"]:
        first = next((i for i in items if i["official"]), None)
        if first is not None:
            items.remove(first)
            items.insert(0, first)
    # 가반하중 여유: 1순위가 대상 무게에 빠듯하면(여유 20% 미만) 여유 있는 후보를 앞으로(2026-10-01 실측: 25kg 에 MG25 가
    # 1순위로 나오고 이유에 'MG30 검토'라고 적음 — 프롬프트 규칙만으로는 매번 지켜지지 않음). 정보 미흡 제품은 올리지 않는다.
    weight = request_weight(request)
    if weight and items:
        top = payloads.get(items[0]["card_id"])
        if top is not None and top < weight * PAYLOAD_MARGIN:
            better = next((i for i in items[1:] if (payloads.get(i["card_id"]) or 0) >= weight * PAYLOAD_MARGIN
                           and (i["official"] or not items[0]["official"])), None)
            if better is not None:
                items.remove(better)
                items.insert(0, better)
    # 정정대로 판단이 바뀌었는데 모델이 reconsidered 를 안 적으면 화면에 반영 사실이 안 보인다(QA 5회차) —
    # 떠올린 정정의 '틀린 제품'이 빠지고 '정답'이 들어갔으면 코드가 적는다.
    ids = {i["card_id"] for i in items}
    noted = {r["correction_id"] for r in reconsidered}
    for c in corrections:
        if c["id"] in noted or not c.get("right_card_id") or c["right_card_id"] not in ids:
            continue
        if c.get("wrong_card_id") and c["wrong_card_id"] in ids:
            continue
        note = (f"비슷한 경험이 있는 '{c['right_name']}'을(를) 검토했습니다." if norm_kind(c.get("kind")) == "experience"
                else f"과거 정정대로 '{c['wrong_name'] or '이전 추천'}' 대신 '{c['right_name']}'을(를) 골랐습니다.")
        reconsidered.append({"correction_id": c["id"], "auto": True, "correction": correction_text(c), "note": note})
    return {"summary": _ai_text(data.get("summary"), 300), "items": items, "reconsidered": reconsidered,
            "dropped": dropped}


async def product_photos(db, card_ids: list[int]) -> dict[int, int]:
    """제품 카드 → 승인된 첫 사진 id(추천 결과에 그림으로). 사진 표가 없거나 사진이 없으면 빠진다."""
    if not card_ids or not (await db.execute(text("SELECT to_regclass('product_images')"))).scalar():
        return {}
    rows = (await db.execute(text(
        "SELECT DISTINCT ON (card_id) card_id, id FROM product_images WHERE active AND card_id = ANY(:c) "
        "ORDER BY card_id, reviewed_at, id"), {"c": list(card_ids)})).all()
    return {int(c): int(i) for c, i in rows}


async def _save_recommendation(request: str, data: dict[str, Any], catalog: list[dict[str, Any]],
                               corrections: list[dict[str, Any]], *, user_id: int | None) -> dict[str, Any]:
    from ..database import SessionLocal

    res = validate_recommendation(data, catalog, corrections, request)
    res["corrections"] = [{"id": c["id"], "text": correction_text(c)} for c in corrections]
    async with SessionLocal() as db:
        photos = await product_photos(db, [i["card_id"] for i in res["items"]])
        for it in res["items"]:
            it["image_id"] = photos.get(it["card_id"])
        rid = (await db.execute(text(
            "INSERT INTO product_recommendations (user_id, request, result) VALUES (:u, :r, CAST(:d AS jsonb)) "
            "RETURNING id"), {"u": user_id, "r": request, "d": json.dumps(res, ensure_ascii=False)})).scalar_one()
        used = [r["correction_id"] for r in res["reconsidered"]]
        if used:
            await db.execute(text("UPDATE product_corrections SET hits = hits + 1 WHERE id = ANY(:i)"), {"i": used})
        await db.commit()
    return {"id": rid, "request": request, **res}


# ── 추천 뒤 대화 ─────────────────────────────────────────────────────────────

HISTORY_MAX = 10
_CONVERSE_SYSTEM = """너는 유엔디로보틱스의 제품 추천 담당 기술영업이다. [현재 추천]에 대해 사용자와 이야기한다. JSON 으로만 답한다.
- 질문(왜 이 제품인지, 비교, 한계, 확인할 점 등)에는 [회사 제품]·[현재 추천]·[조건]에 있는 내용만으로 답한다.
  모르는 사양은 지어내지 말고 "자료에 없어 확인이 필요합니다"라고 한다. 수치를 새로 만들지 않는다.
- 사용자가 조건을 바꾸거나 더해 다시 골라 달라고 하면(예: 더 가벼운 것, 공압 없이, 다른 제품군) recommend_again=true,
  condition 에 바뀐·더해진 조건을 한 문장으로 적는다. 그 외에는 false.
- 사용자가 앞으로의 추천·판단에 반영하도록 '기억·학습시켜 달라'고 분명히 요구할 때만 learn=true(예: "다음부터는 이렇게 판단하도록
  학습해 줘", "이 기준 기억해 둬"). '학습'이라는 말이 있어도 묻는 말("학습 내용은 어디서 봐?")·학습하지 말라는 말·단순 의견·
  바로잡는 설명만 있는 말은 learn 이 아니다. learn 이면 answer 는 무엇을 배우면 되는지 한 문장으로 되짚는다(저장은 따로 묻는다).
  조건을 바꿔 다시 골라 달라는 요청은 학습이 아니다(recommend_again).
- answer 는 2~5문장, 존댓말.
{"answer": "...", "recommend_again": false, "condition": "", "learn": false}"""



async def converse(rec: dict[str, Any], history: list[dict[str, str]], message: str, *,
                   user_id: int | None, chat=None) -> dict[str, Any]:
    """추천 결과에 대한 대화. 조건을 바꿔 다시 골라 달라면 새 추천까지 → {answer, recommendation?}."""
    from ..database import SessionLocal

    message = _clean(message, REQUEST_MAX)
    if not message:
        raise RecommendError("내용을 적어 주세요.")
    chat = chat or _default_chat()
    async with SessionLocal() as db:
        catalog = await product_catalog(db)
    # 화면에는 1순위 하나만 보인다 — 나머지는 '검토했던 후보'로 알려 준다
    current = "\n".join((f"추천(화면에 보인 제품): {i['product']} — {i.get('reason', '')}" if n == 0
                         else f"검토했던 다른 후보: {i['product']} — {i.get('reason', '')}")
                        for n, i in enumerate(rec.get("items") or []))
    context = (f"[조건]\n{rec.get('request', '')}\n\n[현재 추천]\n{current or '(없음)'}\n\n"
               + (atc_context(rec) if rec.get("kind") == "atc" else "")
               + "[회사 제품]\n" + "\n".join(p["line"] for p in catalog))
    msgs = [{"role": "system", "content": _CONVERSE_SYSTEM}, {"role": "user", "content": context}]
    for h in history[-HISTORY_MAX:]:
        if h.get("role") in ("user", "assistant") and _clean(h.get("text"), 1000):
            msgs.append({"role": h["role"], "content": _clean(h["text"], 1000)})
    msgs.append({"role": "user", "content": message})
    data = _extract_json(await chat(msgs, num_predict=1500))
    if not isinstance(data, dict):
        raise RecommendError("답을 만들지 못했습니다. 다시 시도해 주세요.")
    from .ai_guard import strip_prompt_echo  # 지시문을 베낀 문장 거르기

    out: dict[str, Any] = {"answer": strip_prompt_echo(_ai_text(data.get("answer"), 1200), _CONVERSE_SYSTEM)}
    from .atc_chat import wants_learn       # 학습 제안은 분명한 요구일 때만(사용자 2026-10-02: '학습' 단어만으로 묻지 않게)

    if wants_learn(message, bool(data.get("learn") or data.get("is_correction"))):
        out["learn"] = True                  # 호출부가 '학습시킬까요?' 를 먼저 묻는다(바로 저장하지 않음)
        return out
    if data.get("recommend_again"):
        if rec.get("kind") == "atc":
            # 툴체인저 선정은 규칙 계산이라 대화로 다시 고르지 않는다 — 질문지 답을 고쳐 다시 계산
            out["answer"] += " 조건을 바꾸려면 왼쪽 [Q&A 고치기]에서 답을 고친 뒤 다시 진행해 주세요."
            return out
        cond = _clean(data.get("condition"), 300) or message
        out["recommendation"] = await recommend(f"{rec.get('request', '')}\n[추가 조건] {cond}", user_id=user_id,
                                                chat=chat)
    return out


# ── 툴체인저(ATC) 단품 선정 — 주니어 질문지 + 선정 규칙(atc_selection) ─────────────────────

def atc_context(rec: dict[str, Any]) -> str:
    """대화용: 규칙 판정 결과(상태·내부 검토 사유·빠진 정보)."""
    o = rec.get("atc") or {}
    lines = ["[툴체인저 선정 판정 — 선정 규칙 계산 결과, 모델표 승인 전 참고 후보]",
             "상태: " + ", ".join(s["status_ko"] for s in o.get("statuses") or []),
             "툴측 총무게(잠정): " + (f"{o['required_payload_kg']:g}kg" if o.get("required_payload_kg") is not None else "미정")]
    lines += [f"근거: {b['item']} — {b['input']} → {b['judgement']} ({b['rule']})" for b in o.get("basis") or []]
    lines += [f"내부 검토: {r['text_ko']}" for r in o.get("engineering_review_reasons") or []]
    lines += [f"빠진 정보: {m}" for m in o.get("missing_fields") or []]
    lines += [f"참고할 AI 학습 내용: {c['text']}" for c in rec.get("corrections") or []]
    for c in o.get("screening_candidates") or []:
        names = " / ".join(m["name"] for m in c.get("models") or []) or "후보 없음"
        lines.append(f"계열 {c['series_label']}: {names}" + (f" ({'; '.join(c['notes'])})" if c.get("notes") else ""))
    lines += [f"{a['rank']}순위 후보: {a['name']} — {a['reason']}" for a in o.get("alternatives") or []]
    from .plain_text import hide_codes  # AI 가 규칙 번호(R02·C11…)를 답에 옮겨 쓰지 않게 근거에서도 뺀다

    return hide_codes("\n".join(lines)) + "\n\n"


async def atc_catalog(db) -> list[dict[str, Any]]:
    from . import atc_selection as atc

    return atc.build_catalog(await product_catalog(db), payload_max)


async def atc_evaluate(intake: dict[str, Any]) -> dict[str, Any]:
    """저장 없이 판정만(후속 질문 계산용)."""
    from ..database import SessionLocal
    from . import atc_selection as atc

    from . import tool_spec_search as ts

    async with SessionLocal() as db:
        catalog = await atc_catalog(db)
    out = atc.evaluate(intake, catalog)
    # 실제 제품처럼 보이는 툴 — 화면이 외부 공개 사양 검색을 제안한다(사용자 2026-10-02)
    out["real_products"] = [{"index": i, "name": t.get("name") or f"툴 {i + 1}", "product": p}
                            for i, t in enumerate(intake.get("tools") or []) if (p := ts.looks_real(t))]
    return out


async def atc_recommend(intake: dict[str, Any], *, user_id: int | None,
                        meeting: dict[str, Any] | None = None) -> dict[str, Any]:
    """질문지 답 → 규칙 판정 + 기록(사례: 입력·AI 추천을 저장, 엔지니어 확정·설치·운전 결과는 따로 채운다).
    화면의 추천 결과·대화·정정이 같은 기록을 쓰도록 product_recommendations 에 kind='atc' 로 남긴다."""
    from ..database import SessionLocal
    from . import atc_selection as atc

    async with SessionLocal() as db:
        pcat = await product_catalog(db)
    cards = {p["card_id"]: p["card"] for p in pcat}
    out = atc.evaluate(intake, atc.build_catalog(pcat, payload_max))
    first = next((c for c in out["screening_candidates"] if c["models"] and not c.get("out_of_range")), None)
    # ATC 액세서리(참고 구성) — 제품 DB 액세서리 카드의 '코드' 사양으로 찾는다
    out["accessories"] = atc.accessory_bom(out, intake, _acc_cards(pcat))
    # 견적 단가(회사 단가표 고객사가, 국내) — 표가 아직 없거나 비어 있으면 단가 없이 수량만 보인다
    from . import product_prices as pp

    try:
        prices = await pp.list_prices()
    except Exception:  # noqa: BLE001 — 단가 표 문제로 선정 결과까지 막지 않는다
        _log.warning("단가표를 읽지 못함", exc_info=True)
        prices = []
    out["accessories"]["pricing"] = pp.attach_prices(out["accessories"]["items"], prices) if prices else None
    # 컨트롤러·전원(P09 제품 DB) — 1순위 모델 카드의 '전기 사양'·'실린더 센서'·전원 조건
    out["controller_info"] = None
    if first:
        c0 = cards.get(first["models"][0]["card_id"]) or {}
        sp = {str(x.get("item")): str(x.get("value")) for x in c0.get("specs") or []}
        supply = next((x.strip() for x in re.split(r"(?<=\))\s+|\s{2,}", str(c0.get("applies_when") or "")) if "전원" in x), None)
        if sp.get("전기 사양") or sp.get("실린더 센서"):
            out["controller_info"] = {"power": sp.get("전기 사양"), "control": sp.get("실린더 센서"), "supply": supply}
    # AI 학습 내용 — 선정 규칙 계산은 바꾸지 않고(엔지니어 검토 대상), 비슷한 조건의 학습 내용을 결과·대화에 함께 보인다
    async with SessionLocal() as db:
        learned = await relevant_corrections(db, atc.intake_summary(intake))
    items = []
    for c in out["screening_candidates"]:
        for m in c.get("models") or []:
            items.append({"card_id": m["card_id"], "product": m["name"], "family": c["series_label"],
                          "official": m.get("official", False),
                          "reason": out["junior_summary"]["reason_in_plain_korean"] if c is first else c["series_note"],
                          "fit": c["series_note"], "caution": "; ".join(c.get("notes") or []), "checks": [],
                          **card_facts(cards.get(m["card_id"]) or {})})
    request = atc.intake_summary(intake)
    if meeting and meeting.get("summary"):
        request = f"[미팅 요약] {meeting['summary']}\n{request}"
    res = {"kind": "atc", "summary": out["junior_summary"]["reason_in_plain_korean"], "items": items,
           "reconsidered": [], "corrections": [{"id": c["id"], "text": correction_text(c)} for c in learned],
           "dropped": [], "atc": out, "intake": intake,
           **({"meeting": meeting} if meeting else {}),
           "case": {"process_tags": [((intake.get("project") or {}).get("process") or {}).get("category")],
                    "ai_recommendation": {"candidate_model_ids": [i["card_id"] for i in items],
                                          "reason": out["junior_summary"]["reason_in_plain_korean"],
                                          "rules_revision": atc.RULES_REVISION, "catalog_revision": None},
                    "engineering_decision": {"status": "pending"}, "installation": {"status": "not_confirmed"},
                    "outcome_observations": []}}
    async with SessionLocal() as db:
        acc_ids = [x["card_id"] for x in out["accessories"]["items"] if x.get("card_id")]
        photos = await product_photos(db, [i["card_id"] for i in items] + acc_ids)
        for it in items:
            it["image_id"] = photos.get(it["card_id"])
        for x in out["accessories"]["items"]:
            x["image_id"] = photos.get(x["card_id"]) if x.get("card_id") else None
        rid = (await db.execute(text(
            "INSERT INTO product_recommendations (user_id, request, result) VALUES (:u, :r, CAST(:d AS jsonb)) RETURNING id"),
            {"u": user_id, "r": request, "d": json.dumps(res, ensure_ascii=False)})).scalar_one()
        await db.commit()
    return {"id": rid, "request": request, **res}



# ── 정정 ──────────────────────────────────────────────────────────────────────

_CORRECT_SYSTEM = """너는 제품 추천 기록 담당이다. 사용자가 AI 에 학습시키려는 내용을 [대화]와 [학습 요청]에서 찾아,
다음 추천 때 쓸 수 있는 판단 기억 하나로 정리한다. JSON 으로만 답한다. 종류(kind)는 셋 중 하나다.
- "correction"(정정): AI 추천이나 데이터를 바로잡음(예: "A 말고 B, 왜냐하면 ~").
- "experience"(경험): 실제 공정에서 어떤 제품을 써 본 결과(예: "OO 공정에 B 를 써서 ~ 했다").
- "rule"(기준): 특정 제품을 정하지 않고 다음부터 이렇게 판단하라는 지침(예: "협동로봇이면 속도부터 확인해").
항목:
- wrong: 정정이면 틀렸다고 한 제품 이름([직전 추천]·[회사 제품]의 이름 그대로, 없으면 글에 쓴 그대로). 그 외 "".
- right: 정정이면 맞다는 제품, 경험이면 사용한 제품. 기준이면 관련 제품이 있을 때만.
- reason: 정정이면 왜 그런지, 경험이면 어떤 결과가 있었는지, 기준이면 왜 그렇게 판단하는지 — 사용자가 말한 내용만,
  한두 문장. 말하지 않은 이유·수치·효과를 지어내지 않는다.
- situation: 이 판단이 해당하는 조건(공정·대상물·로봇·환경 등)을 [직전 요청]과 대화에서 한 문장으로. 고객사 이름은 뺀다.
- rule: 다음 추천 때 읽을 '학습 문구' 한 문장. 사용자가 쓴 표현·개념을 살려 짧고 정확하게
  (정정: "~한 조건이면 A 대신 B — 이유", 경험: "~한 공정이면 B 우선 검토 — 실제 결과", 기준: "~이면 ~한다").
빠진 것이 있으면 missing 에 적는다 — 정정은 "정답 제품"·"이유", 경험은 "사용 제품"·"결과", 기준은 "학습 문구".
{"kind": "correction", "wrong": "...", "right": "...", "reason": "...", "situation": "...", "rule": "...", "missing": []}"""


async def draft_correction(note: str, *, recommendation: dict[str, Any] | None, chat=None,
                           history: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """학습 요청(+ 직전 대화) → 저장할 학습 초안(저장하지 않음). 빠진 게 있으면 missing.
    '이거 학습해 줘'처럼 내용이 앞 대화에 있는 경우가 많아 최근 대화를 함께 읽는다."""
    from ..database import SessionLocal

    note = _clean(note, REQUEST_MAX)
    if not note:
        raise RecommendError("학습시킬 내용을 적어 주세요.")
    chat = chat or _default_chat()
    async with SessionLocal() as db:
        catalog = await product_catalog(db)
    prev = ""
    if recommendation:
        prev = (f"[직전 요청]\n{recommendation.get('request', '')}\n\n[직전 추천]\n"
                + "\n".join(f"- {i['product']}" for i in (recommendation.get("items") or [])[:3]) + "\n\n")
    talk = "\n".join(f"{'사용자' if h.get('role') == 'user' else 'AI'}: {_clean(h.get('text'), 600)}"
                     for h in (history or [])[-HISTORY_MAX:] if _clean(h.get("text"), 600))
    user = (prev + "[회사 제품]\n" + "\n".join(f"- {p['name']}" for p in catalog)
            + (f"\n\n[대화]\n{talk}" if talk else "") + f"\n\n[학습 요청]\n{note}")
    data = _extract_json(await chat([{"role": "system", "content": _CORRECT_SYSTEM},
                                     {"role": "user", "content": user}], num_predict=1200))
    if not isinstance(data, dict):
        raise RecommendError("학습시킬 내용을 읽지 못했습니다. 무엇을 어떻게 바꾸면 되는지 한 문장으로 적어 주세요.")
    return validate_correction(data, catalog, note)


# 종류별로 꼭 있어야 하는 것 — 빠지면 저장하지 않고 더 적어 달라고 한다(이름: 화면 문구)
_REQUIRED = {"correction": (("right_name", "정답 제품"), ("reason", "이유")),
             "experience": (("right_name", "사용 제품"), ("reason", "결과")),
             "rule": (("rule", "학습 문구"),)}


def _missing(kind: str, d: dict[str, Any]) -> list[str]:
    return [label for key, label in _REQUIRED[kind] if not _clean(d.get(key), 400)]


def validate_correction(data: dict[str, Any], catalog: list[dict[str, Any]], note: str) -> dict[str, Any]:
    kind = norm_kind(data.get("kind"))
    wrong = _clean(data.get("wrong"), 80) if kind == "correction" else ""
    right = _clean(data.get("right"), 80)
    wp, rp = match_product(wrong, catalog), match_product(right, catalog)
    d = {"kind": kind, "wrong_name": wp["name"] if wp else wrong, "wrong_card_id": wp["card_id"] if wp else None,
         "right_name": rp["name"] if rp else right, "right_card_id": rp["card_id"] if rp else None,
         "reason": _clean(data.get("reason"), 400), "situation": _clean(data.get("situation"), 300),
         "rule": _clean(data.get("rule"), 300), "note": note, "right_in_catalog": rp is not None}
    labels = {label for _, label in _REQUIRED[kind]}
    missing = [m for m in (data.get("missing") or []) if isinstance(m, str) and m in labels]
    d["missing"] = list(dict.fromkeys(missing + _missing(kind, d)))[:3]
    return d


async def save_correction(draft: dict[str, Any], *, user_id: int | None, recommendation_id: int | None,
                          approver: bool = False) -> dict[str, Any]:
    """학습 내용 저장(사용자가 '예' + 학습 문구 확인 뒤). 권한(사용자 2026-10-02):
    영업 관리자(승인권자)가 저장하면 바로 반영(kept·active), 그 외 사용자는 승인 대기(pending·꺼짐) —
    관리자가 [AI 학습 내용 관리]에서 승인해야 다음 추천에 쓰인다."""
    from ..database import SessionLocal

    kind = norm_kind(draft.get("kind"))
    miss = _missing(kind, draft)
    if miss:
        raise RecommendError(f"{', '.join(miss)}이(가) 없습니다. 조금 더 적어 주세요.")
    situation = _clean(draft.get("situation"), 300) or _clean(draft.get("note"), 300)
    rule = _clean(draft.get("rule"), 300)
    vec = await _embed([f"{situation} {rule}"])
    status_, active = ("kept", True) if approver else ("pending", False)
    async with SessionLocal() as db:
        rid = recommendation_id if recommendation_id and (await db.execute(
            text("SELECT 1 FROM product_recommendations WHERE id = :i"), {"i": recommendation_id})).scalar() else None
        cid = (await db.execute(text(
            "INSERT INTO product_corrections (user_id, recommendation_id, kind, situation, wrong_name, wrong_card_id, "
            "right_name, right_card_id, reason, rule, note, embedding, active, review_status, reviewed_by, reviewed_at) "
            "VALUES (:u, :rid, :k, :s, :wn, :wc, :rn, :rc, :r, :rule, :note, "
            + ("CAST(:v AS vector)" if vec else "NULL")
            + ", :a, :st, :rb, " + ("NOW()" if approver else "NULL") + ") RETURNING id"),
            {"u": user_id, "rid": rid, "k": kind, "s": situation,
             "wn": _clean(draft.get("wrong_name"), 80) if kind == "correction" else "",
             "wc": draft.get("wrong_card_id") if kind == "correction" else None,
             "rn": _clean(draft.get("right_name"), 80), "rc": draft.get("right_card_id"),
             "r": _clean(draft.get("reason"), 400), "rule": rule, "note": _clean(draft.get("note"), REQUEST_MAX),
             "a": active, "st": status_, "rb": user_id if approver else None,
             **({"v": _vec(vec[0])} if vec else {})})).scalar_one()
        await db.commit()
    return await get_correction(cid)


_LIST_SQL = ("SELECT c.id, c.kind, c.situation, c.wrong_name, c.wrong_card_id, c.right_name, c.right_card_id, c.reason, "
             "c.rule, c.note, c.active, c.review_status, c.hits, c.recommendation_id, c.created_at, c.reviewed_at, "
             "COALESCE(u.alias, u.username) AS submitted_by FROM product_corrections c "
             "LEFT JOIN users u ON u.id = c.user_id")


def _row(r: Any) -> dict[str, Any]:
    d = dict(r)
    for k in ("created_at", "reviewed_at"):
        d[k] = d[k].isoformat() if d.get(k) else None
    return d


async def get_correction(cid: int) -> dict[str, Any]:
    from ..database import SessionLocal

    async with SessionLocal() as db:
        r = (await db.execute(text(_LIST_SQL + " WHERE c.id = :i"), {"i": cid})).mappings().first()
    if r is None:
        raise RecommendError("정정을 찾을 수 없습니다.")
    return _row(r)


async def list_corrections(limit: int = 200) -> list[dict[str, Any]]:
    from ..database import SessionLocal

    async with SessionLocal() as db:
        rows = (await db.execute(text(_LIST_SQL + " ORDER BY c.id DESC LIMIT :n"), {"n": limit})).mappings().all()
    return [_row(r) for r in rows]


# 영업 관리자 검토: approve(승인 — 반영) / reject(거절) / off(끄기 — 다음 추천에서 안 씀) / on(다시 켜기). keep 은 예전 이름.
REVIEW_ACTIONS = {"approve": ("kept", True), "keep": ("kept", True), "reject": ("rejected", False),
                  "off": ("off", False), "on": ("kept", True)}


async def review_correction(cid: int, action: str, *, reviewer_id: int) -> dict[str, Any]:
    """영업 관리자 검토 — 승인해야 다음 추천에 쓰인다(사용자 2026-10-02)."""
    from ..database import SessionLocal

    if action not in REVIEW_ACTIONS:
        raise RecommendError("알 수 없는 처리입니다.")
    status_, active = REVIEW_ACTIONS[action]
    async with SessionLocal() as db:
        ok = (await db.execute(text(
            "UPDATE product_corrections SET review_status = :s, active = :a, reviewed_by = :r, reviewed_at = NOW() "
            "WHERE id = :i RETURNING id"), {"s": status_, "a": active, "r": reviewer_id, "i": cid})).first()
        await db.commit()
    if not ok:
        raise RecommendError("정정을 찾을 수 없습니다.")
    return await get_correction(cid)


def _acc_cards(pcat: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """제품 DB 'Magbot ATC · 액세서리' 카드 → {card_id, name, code}(카드 사양의 '코드')."""
    return [{"card_id": p["card_id"], "name": p["name"],
             "code": next((str(x.get("value")) for x in (p["card"] or {}).get("specs") or [] if x.get("item") == "코드"), None)}
            for p in pcat if ((p.get("card") or {}).get("family") or "") == "Magbot ATC · 액세서리"]


async def _fresh_accessories(rec: dict[str, Any], included: list[str] | None = None) -> dict[str, Any]:
    """저장된 입력·판정으로 구성품을 지금 규칙·단가표로 다시 만든다(예전 기록·규칙이 바뀐 뒤 기록도 같은 기준으로).
    옵션 체크는 included(없으면 저장된 체크)를 이어받는다. 사진 id 도 다시 붙인다."""
    from ..database import SessionLocal
    from . import atc_selection as atc
    from . import product_prices as pp

    o = rec.get("atc") or {}
    if included is None:
        included = [x["name"] for x in (o.get("accessories") or {}).get("items") or [] if x.get("included")]
    async with SessionLocal() as db:
        pcat = await product_catalog(db)
    o = atc.refresh_accessory(o, rec.get("intake") or {})          # 액세서리는 지금 규칙(v1.2)으로 — 고른 모델은 그대로
    acc = atc.accessory_bom(o, rec.get("intake") or {}, _acc_cards(pcat))
    acc = pp.apply_options(acc, included, await pp.list_prices())
    async with SessionLocal() as db:
        photos = await product_photos(db, [x["card_id"] for x in acc["items"] if x.get("card_id")])
    for x in acc["items"]:
        x["image_id"] = photos.get(x["card_id"]) if x.get("card_id") else None
    return acc


_FILE_BAD = re.compile(r'[\\/:*?"<>|\r\n\t]+')
_QUOTE_COLS = ("id, recommendation_id, deal_id, user_id, status, quote_no, revision, file_name, customer, total, state, history, "
               "created_at, updated_at")
QUOTE_CHAT_MAX = 60


def _quote_view(r: dict[str, Any]) -> dict[str, Any]:
    """견적 기록 → 화면용(상태·다음 질문·발행 가능 여부·합계·이력)."""
    from . import product_prices as pp
    from . import quote_session as qs

    st = r["state"] or {}
    return {"id": r["id"], "recommendation_id": r["recommendation_id"], "deal_id": r.get("deal_id"), "manual": bool(st.get("manual")),
            "status": r["status"], "quote_no": r["quote_no"],
            "revision": r["revision"], "file_name": r["file_name"], "customer": r["customer"],
            "state": {k: st.get(k) for k in ("model", "payload_kg", "applicable", "form", "lines", "options", "work", "delivery")},
            "subtotal": qs.subtotal(st) if st else 0, "question": qs.next_question(st) if st else None,
            "problems": qs.problems(st) if st else [], "chat": (st.get("chat") or [])[-QUOTE_CHAT_MAX:],
            "history": [{**{k: h.get(k) for k in ("revision", "issued_at", "total", "currency")},
                         "file_name": pp.clean_quote_file_name(str(h.get("file_name") or ""))}
                        for h in r["history"] or []],
            "created_at": r["created_at"].isoformat(), "updated_at": r["updated_at"].isoformat()}


async def _quote_row(db, qid: int, user_id: int | None) -> dict[str, Any]:
    r = (await db.execute(text(f"SELECT {_QUOTE_COLS} FROM product_quotes WHERE id = :i"), {"i": qid})).mappings().first()
    if r is None or (user_id is not None and r["user_id"] not in (None, user_id)):
        raise RecommendError("견적서를 찾을 수 없습니다.")
    return dict(r)


async def _save_state(db, qid: int, st: dict[str, Any]) -> None:
    await db.execute(text("UPDATE product_quotes SET state = CAST(:s AS jsonb), customer = :c, updated_at = NOW() WHERE id = :i"),
                     {"s": json.dumps(st, ensure_ascii=False), "c": (st.get("form") or {}).get("customer") or None, "i": qid})


async def quote_draft(rid: int, *, user_id: int | None, form: dict[str, Any] | None = None) -> dict[str, Any]:
    """[견적서 작성] — 이 추천의 견적이 있으면 그대로 열고, 없으면 추천 결과(지금 규칙·단가표)로 초안을 만든다.
    form: 브라우저에 기억한 담당자 정보·미팅 고객사 등 미리 채울 값."""
    from ..database import SessionLocal
    from . import quote_session as qs

    async with SessionLocal() as db:
        r = (await db.execute(text(f"SELECT {_QUOTE_COLS} FROM product_quotes WHERE recommendation_id = :r AND user_id "
                                   "IS NOT DISTINCT FROM :u ORDER BY id DESC LIMIT 1"), {"r": rid, "u": user_id})).mappings().first()
    rec = await get_recommendation(rid, user_id)
    if r is not None:
        r = dict(r)
        # 고객사를 비워 둔 채 만든 초안 — Q&A 의 고객사명으로 채운다(사용자 2026-10-08)
        if r["status"] == "draft" and rec and not str((r["state"].get("form") or {}).get("customer") or "").strip()                 and _rec_customer(rec):
            r["state"]["form"]["customer"] = _rec_customer(rec)
            async with SessionLocal() as db:
                await _save_state(db, r["id"], r["state"])
                await db.commit()
                return _quote_view(await _quote_row(db, r["id"], user_id))
        return _quote_view(r)
    o = (rec or {}).get("atc") or {}
    if not rec or rec.get("kind") != "atc" or not o.get("screening_candidates"):
        raise RecommendError("툴체인저 추천 결과가 있어야 견적서를 만들 수 있습니다.")
    first = next((c for c in o["screening_candidates"] if c.get("models") and not c.get("out_of_range")), None)
    if not first:
        raise RecommendError("후보 모델이 없어 견적서를 만들 수 없습니다.")
    # 고객사는 미팅 파일 또는 Q&A 에서 적은 고객사명으로 미리 채운다(사용자 2026-10-08)
    pre = {"customer": _rec_customer(rec)} | {k: v for k, v in (form or {}).items() if v}
    m = first["models"][0]
    st = qs.draft_state(await _fresh_accessories(rec), model=m["name"], payload_kg=m.get("payload_kg"), form=pre)
    q = qs.next_question(st)
    intro = (f"추천 결과({m['name']})로 견적 초안을 만들었습니다 — 품목 {len(st['lines'])}줄, 합계 {qs.subtotal(st):,}원(VAT 별도). "
             "왼쪽에서 바로 고치거나, 저에게 말로 고쳐 달라고 하셔도 됩니다.")
    st["chat"] = [{"role": "assistant", "text": intro + (f"\n\n{q['text']}" if q else "")}]
    async with SessionLocal() as db:
        qid = (await db.execute(text(
            "INSERT INTO product_quotes (recommendation_id, user_id, status, customer, state) "
            "VALUES (:r, :u, 'draft', :c, CAST(:s AS jsonb)) RETURNING id"),
            {"r": rid, "u": user_id, "c": st["form"].get("customer") or None, "s": json.dumps(st, ensure_ascii=False)})).scalar_one()
        await db.commit()
        return _quote_view(await _quote_row(db, qid, user_id))


# 손대지 않은 빈 수기 초안 — 고객사·품목 없음, 대화는 첫 인사뿐(사용자 2026-10-08). [수기 작성]을 다시 누르면 이걸 다시 열고,
# 견적서 목록에는 보이지 않는다(발행했는데 '(초안) 수기 작성'이 남아 보이던 것).
_EMPTY_MANUAL = ("status = 'draft' AND recommendation_id IS NULL AND deal_id IS NULL AND state->>'manual' = 'true' "
                 "AND COALESCE(state->'form'->>'customer', '') = '' AND jsonb_array_length(COALESCE(state->'lines', '[]'::jsonb)) = 0 "
                 "AND jsonb_array_length(COALESCE(state->'chat', '[]'::jsonb)) <= 1")


async def quote_manual(*, user_id: int | None, form: dict[str, Any] | None = None) -> dict[str, Any]:
    """[견적서 수기 작성](사용자 2026-10-08) — AI 제품 추천 없이 빈 견적서로 시작(그리퍼·AMR 등 추천 전 제품).
    발행하면 그때 영업 건(견적 단계)과 건 번호가 생기고, 이후 수주·거래명세서·출하는 추천으로 시작한 건과 같다.
    미팅 정보는 영업 건 관리에서 [미팅 정보 → 직접 입력]."""
    from ..database import SessionLocal
    from . import quote_session as qs

    st = qs.draft_state({}, model="", payload_kg=None, form={k: v for k, v in (form or {}).items() if v}, manual=True)
    q = qs.next_question(st)
    st["chat"] = [{"role": "assistant", "text": "수기 견적서를 시작했습니다. 품목은 왼쪽 [+ 항목]·[단가표에서 추가]로 넣거나 저에게 말로 알려 주세요."
                   + (f"\n\n{q['text']}" if q else "")}]
    async with SessionLocal() as db:
        # 손대지 않은 빈 수기 초안(고객사·품목 없음, 대화는 첫 인사뿐)이 있으면 그걸 다시 연다 — 누를 때마다 빈 초안이
        # 견적서 목록에 쌓이던 것(사용자 2026-10-08). 담당자 정보는 지금 값으로 다시 채운다.
        qid = (await db.execute(text(
            f"SELECT id FROM product_quotes WHERE user_id IS NOT DISTINCT FROM :u AND {_EMPTY_MANUAL} ORDER BY id DESC LIMIT 1"),
            {"u": user_id})).scalar()
        if qid:
            await db.execute(text("UPDATE product_quotes SET state = CAST(:s AS jsonb), updated_at = NOW() WHERE id = :i"),
                             {"s": json.dumps(st, ensure_ascii=False), "i": qid})
        else:
            qid = (await db.execute(text(
                "INSERT INTO product_quotes (recommendation_id, user_id, status, customer, state) "
                "VALUES (NULL, :u, 'draft', :c, CAST(:s AS jsonb)) RETURNING id"),
                {"u": user_id, "c": st["form"].get("customer") or None, "s": json.dumps(st, ensure_ascii=False)})).scalar_one()
        await db.commit()
        return _quote_view(await _quote_row(db, qid, user_id))


async def quote_price_list() -> list[dict[str, Any]]:
    """수기 견적의 [단가표에서 추가] — 회사 단가표(고객사가, 국내)에서 가격이 정해진 품목만."""
    from . import product_prices as pp

    return [{"model": r["model"], "item": r["item"], "unit_price": r["customer_price"]}
            for r in await pp.list_prices() if r.get("customer_price")]


async def quote_get(qid: int, *, user_id: int | None) -> dict[str, Any]:
    from ..database import SessionLocal

    async with SessionLocal() as db:
        return _quote_view(await _quote_row(db, qid, user_id))


async def quote_list(*, user_id: int | None, limit: int = 100) -> list[dict[str, Any]]:
    from ..database import SessionLocal

    async with SessionLocal() as db:
        rows = (await db.execute(text(
            "SELECT id, recommendation_id, status, quote_no, revision, customer, total, state->>'model' AS model, "
            "COALESCE((state->>'manual')::boolean, false) AS manual, updated_at "
            f"FROM product_quotes WHERE user_id IS NOT DISTINCT FROM :u AND NOT ({_EMPTY_MANUAL}) ORDER BY updated_at DESC LIMIT :n"),
            {"u": user_id, "n": limit})).mappings().all()
    return [{**dict(r), "total": float(r["total"]) if r["total"] is not None else None, "updated_at": r["updated_at"].isoformat()}
            for r in rows]


async def quote_chat(qid: int, *, user_id: int | None, message: str, chat=None) -> dict[str, Any]:
    """견적 대화 한 번 — 확실한 꼴의 답은 코드로, 나머지는 사내 AI 가 변경(ops)으로 옮기고 코드가 검증·적용한다.
    답 = 반영 목록 + 다음 질문(코드가 정한 순서)."""
    from ..database import SessionLocal
    from . import quote_session as qs

    message = _clean(message, 1000)
    if not message:
        raise RecommendError("내용을 적어 주세요.")
    async with SessionLocal() as db:
        r = await _quote_row(db, qid, user_id)
    st = r["state"]
    ops, answer = qs.quick_ops(st, message), ""
    if ops is None:
        q = qs.next_question(st)
        talk = "\n".join(f"{'사용자' if h.get('role') == 'user' else 'AI'}: {_clean(h.get('text'), 300)}"
                         for h in (st.get("chat") or [])[-6:])
        data = _extract_json(await (chat or _default_chat())(
            [{"role": "system", "content": qs.QUOTE_SYSTEM},
             {"role": "user", "content": f"[견적 상태]\n{qs.describe(st)}\n\n[지금 묻는 질문]\n{q['text'] if q else '(없음)'}"
                                         f"\n\n[대화]\n{talk or '(없음)'}\n\n[말]\n{message}"}], num_predict=900))
        data = data if isinstance(data, dict) else {}
        ops, answer = data.get("ops"), ai_lines(data.get("answer"), 900)
    new, done = qs.apply_ops(st, ops, message)
    q = qs.next_question(new)
    parts = []
    if done:
        parts.append("반영했습니다.\n" + "\n".join(f"- {x}" for x in done) + f"\n\n합계 **{qs.subtotal(new):,}원**(VAT 별도)")
    elif answer:
        parts.append(answer)
    else:
        parts.append("말씀을 견적 변경으로 옮기지 못했습니다. 품목·수량·금액을 조금 더 구체적으로 적어 주세요.")
    if q:
        parts.append(q["text"])
    elif done or not answer:
        parts.append("필요한 내용은 다 채웠습니다. 왼쪽에서 확인하고 [견적서 발행]을 눌러 주세요."
                     if not qs.problems(new) else "발행 전에 확인할 것: " + "; ".join(qs.problems(new)))
    reply = "\n\n".join(parts)
    new["chat"] = [*(st.get("chat") or []), {"role": "user", "text": message}, {"role": "assistant", "text": reply}][-QUOTE_CHAT_MAX:]
    async with SessionLocal() as db:
        await _save_state(db, qid, new)
        await db.commit()
        return {"quote": _quote_view(await _quote_row(db, qid, user_id)), "reply": reply, "applied": done}


async def quote_edit(qid: int, *, user_id: int | None, payload: dict[str, Any]) -> dict[str, Any]:
    """왼쪽 화면에서 직접 고친 머리·품목·작업 → 저장(업데이트). 반영 내역은 대화 기록에도 남긴다."""
    from ..database import SessionLocal
    from . import quote_session as qs

    async with SessionLocal() as db:
        r = await _quote_row(db, qid, user_id)
    st = r["state"]
    new, done = qs.apply_ops(st, qs.edit_ops(st, payload), None)
    if done:
        new["chat"] = [*(st.get("chat") or []), {"role": "assistant", "text": "직접 수정을 저장했습니다 — " + " · ".join(done)}][-QUOTE_CHAT_MAX:]
    async with SessionLocal() as db:
        await _save_state(db, qid, new)
        await db.commit()
        return _quote_view(await _quote_row(db, qid, user_id))


def _issue_parts(r: dict[str, Any], day) -> dict[str, Any]:
    """발행·미리보기 공통 — 지금 견적 상태 → 품목 줄·머리 입력·파일명 재료. 채울 것이 남았으면 RecommendError."""
    from . import quote_session as qs
    from . import quote_xlsx as qx

    st = r["state"]
    bad = qs.problems(st)
    if bad:
        raise RecommendError("발행 전에 채워야 할 것이 있습니다: " + "; ".join(bad))
    f = st["form"]
    lang = f.get("lang") or "ko"
    try:
        lines = qs.render_lines(st, lang, f.get("fx_rate"))
    except (qx.QuoteError, ValueError, TypeError) as e:
        raise RecommendError(str(e)) from e
    first = (r["history"] or [{}])[0]
    head0 = first.get("header") or {}
    return {"st": st, "form": f, "lang": lang, "lines": lines, "pogo": any(ln["key"] in ("PPM", "PPF") for ln in lines),
            "customer": _FILE_BAD.sub(" ", str(f["customer"]).strip()).strip(),
            "subject": _FILE_BAD.sub(" ", str(f.get("subject") or "").strip()).strip() or ("견적" if st.get("manual") else "magbot툴체인저"),
            # 파일명의 날짜·이니셜은 처음 발행 기준(수정본도 같은 이름)
            "d0": date_from_iso(head0.get("date")) or day, "ini": head0.get("initials") or f["initials"]}


def date_from_iso(v: Any):
    from datetime import date

    try:
        return date.fromisoformat(str(v)) if v else None
    except ValueError:
        return None


def _header(parts: dict[str, Any], no: str, rev: int, day) -> dict[str, Any]:
    from . import quote_xlsx as qx

    try:
        header = qx.quote_header(parts["form"], lang=parts["lang"], quote_no=no, day=day, pogo=parts["pogo"])
    except qx.QuoteError as e:
        raise RecommendError(str(e)) from e
    header["initials"] = parts["ini"]
    # 판 번호(v1·v2)는 사내 관리용 — 견적서·파일명에는 번호만(사용자 2026-10-08: 수정본이어도 (Rev.n) 안 씀)
    return header


async def quote_issue(qid: int, *, user_id: int | None, overwrite: bool = False) -> dict[str, Any]:
    """[견적서 발행] — 미정 값이 없어야(C13). 견적번호 = 제품 영업 건 관리의 건 번호(S26-0182 …, 사용자 2026-10-06).
    - 처음 발행: 이 추천의 영업 건('제품 추천 확정')이 견적 단계가 되고 그 건 번호가 견적번호(건이 없으면 새 건).
    - 다시 발행: 같은 번호에 판 +1(사내 v2·v3 …, 견적서에는 번호만). overwrite=True 면 판 번호를 올리지 않고 지금 판을 덮어쓴다.
    영업 건의 견적(품목·금액·판)도 함께 바꾼다 — 한 트랜잭션(번호가 겹치면 UNIQUE 로 걸려 다시 시도).
    발행한 머리·품목은 history 에 남아 같은 파일을 다시 받을 수 있다."""
    from datetime import date, datetime, timezone

    from sqlalchemy.exc import IntegrityError

    from ..database import SessionLocal
    from ..sales_deals import service as deals
    from . import product_prices as pp
    from . import quote_xlsx as qx

    async with SessionLocal() as db:
        r = await _quote_row(db, qid, user_id)
    day = date.today()
    parts = _issue_parts(r, day)
    f, lang, lines = parts["form"], parts["lang"], parts["lines"]
    over = bool(overwrite and r["quote_no"])
    rev = r["revision"] if over else (r["revision"] + 1 if r["quote_no"] else 0)
    deal = {"quote_date": day, "customer": str(f["customer"]).strip(), "contact": str(f.get("to") or "").strip() or None,
            "owner": str(f.get("contact_name") or "").strip() or None, "vat_included": False, "initials": parts["ini"],
            "currency": qx.CURRENCY[lang], "title": "", "note": "",
            "items": [{"name": ln["desc"], "unit_price": ln["unit_price"], "qty": ln["qty"]} for ln in lines]}
    async with SessionLocal() as db:
        for _ in range(3):
            pid = (await db.execute(text("SELECT id FROM product_proposals WHERE recommendation_id = :r"),
                                    {"r": r["recommendation_id"]})).scalar() if r["recommendation_id"] else None
            try:   # 수기 견적은 첫 발행에서 만든 건(deal_id)을 계속 쓴다
                deal_id, deal_no = await deals.quote_deal(db, user_id, pid, deal, rev + 1, deal_id=r.get("deal_id"))
            except IntegrityError:
                await db.rollback()
                continue
            except deals.DealError as e:              # 건 번호 이니셜 등 — 이유를 그대로
                await db.rollback()
                raise RecommendError(str(e)) from e
            no = r["quote_no"] or deal_no               # 예전 번호(UND-…)로 이미 발행한 견적은 그 번호 유지
            if pid and deal["customer"]:
                await db.execute(text(
                    "UPDATE product_proposals SET customer = :c, title = :c || ' · ' || model WHERE id = :p AND customer = ''"),
                    {"c": deal["customer"], "p": pid})
            header = _header(parts, no, rev, day)
            fname = pp.quote_file_name(parts["customer"], parts["subject"], parts["ini"], parts["d0"], no) + ".xlsx"
            snap = {"revision": rev, "issued_at": datetime.now(timezone.utc).isoformat(), "total": qx.total(lines),
                    "currency": qx.CURRENCY[lang], "file_name": fname, "header": header, "lines": lines}
            # 덮어쓰기면 마지막 판을 바꾸고, 아니면 판을 쌓는다
            hist = "(history - (jsonb_array_length(history) - 1))" if over and r["history"] else "history"
            try:
                await db.execute(text(
                    "UPDATE product_quotes SET status = 'issued', quote_no = :no, revision = :rev, lang = :lang, customer = :c, deal_id = :did, "
                    "file_name = :f, header = CAST(:h AS jsonb), lines = CAST(:l AS jsonb), total = :t, "
                    f"history = {hist} || CAST(:snap AS jsonb), updated_at = NOW() WHERE id = :i"),
                    {"no": no, "rev": rev, "lang": lang, "c": header["customer"], "did": deal_id, "f": fname,
                     "h": json.dumps(header, ensure_ascii=False), "l": json.dumps(lines, ensure_ascii=False),
                     "t": qx.total(lines), "snap": json.dumps([snap], ensure_ascii=False), "i": qid})
                await db.commit()
                break
            except IntegrityError:
                await db.rollback()
        else:
            raise RecommendError("견적번호를 정하지 못했습니다. 잠시 뒤 다시 시도해 주세요.")
        st = parts["st"]
        money = f"${qx.total(lines):,.2f}" if lang == "en" else f"{qx.total(lines):,.0f}원"
        st["chat"] = [*(st.get("chat") or []), {"role": "assistant", "text": (
            f"견적서를 덮어썼습니다 — {header['quote_no']} (v{rev + 1}), 합계 {money}." if over
            else f"견적서를 발행했습니다 — {header['quote_no']} (v{rev + 1}), 합계 {money}. 제품 영업 건 관리에도 반영했습니다.")}][-QUOTE_CHAT_MAX:]
        await _save_state(db, qid, st)
        await db.commit()
        return _quote_view(await _quote_row(db, qid, user_id))


async def quote_file(qid: int, *, user_id: int | None, revision: int | None = None, fmt: str = "xlsx") -> tuple[str, bytes]:
    """발행한 견적서(기본 = 최신 판, revision 을 주면 그 판) → (파일명, 바이트). fmt 'pdf' 면 같은 엑셀을 PDF 로. 만든 사람만."""
    from ..database import SessionLocal
    from . import product_prices as pp
    from . import quote_xlsx as qx

    async with SessionLocal() as db:
        r = await _quote_row(db, qid, user_id)
    hist = r["history"] or []
    snap = next((h for h in reversed(hist) if revision is None or h.get("revision") == revision), None)
    if snap is None:
        raise RecommendError("아직 발행한 견적서가 없습니다.")
    # 예전에 '번호 (Rev.n)'·'_Revn'·이니셜 날짜 중복 이름으로 저장한 발행본도 다시 받을 때는 지금 규칙으로(사용자 2026-10-08)
    header = {**snap["header"], "quote_no": re.sub(r"\s*\(Rev\.\d+\)$", "", str(snap["header"].get("quote_no") or ""))}
    name = pp.clean_quote_file_name(snap["file_name"])
    data = qx.build_xlsx(header, snap["lines"])
    if fmt == "pdf":
        return name.removesuffix(".xlsx") + ".pdf", await _to_pdf(data)
    return name, data


async def quote_preview(qid: int, *, user_id: int | None) -> bytes:
    """발행 전·후 지금 상태 그대로의 견적서 PDF(미리보기, 저장 안 함). 번호가 아직 없으면 '발행 시 부여'로 보인다."""
    from datetime import date

    from ..database import SessionLocal
    from . import quote_xlsx as qx

    async with SessionLocal() as db:
        r = await _quote_row(db, qid, user_id)
    day = date.today()
    parts = _issue_parts(r, day)
    header = _header(parts, r["quote_no"] or "(발행 시 부여)", r["revision"] if r["quote_no"] else 0, day)
    return await _to_pdf(qx.build_xlsx(header, parts["lines"]))


async def _to_pdf(xlsx: bytes) -> bytes:
    from . import quote_xlsx as qx

    try:
        return await qx.to_pdf(xlsx)
    except qx.QuoteError as e:
        raise QuotePdfError(str(e)) from e


class QuotePdfError(RecommendError):
    """PDF 변환 실패(변환기 없음·시간 초과) — 견적 내용 문제가 아니라 서버 문제."""


async def get_recommendation(rid: int, user_id: int | None = None) -> dict[str, Any] | None:
    from ..database import SessionLocal

    async with SessionLocal() as db:
        r = (await db.execute(text("SELECT id, user_id, request, result FROM product_recommendations WHERE id = :i"),
                              {"i": rid})).mappings().first()
    if r is None or (user_id is not None and r["user_id"] not in (None, user_id)):
        return None
    return {"id": r["id"], "request": r["request"], **(r["result"] or {})}


# ── 확정 제안(사용자 2026-10-02) — [최종 제안 확정] → 미팅·입력·선정 결과·근거·구성품·대화 기록을 한 건으로 ─────────

PROPOSAL_HISTORY_MAX = 60


def _rec_customer(rec: dict[str, Any]) -> str:
    """추천 기록의 고객사 — 미팅 파일 → Q&A 에서 적은 고객사명(intake.project.customer_name) 순."""
    meeting = ((rec.get("meeting") or {}).get("meeting") or {}) if isinstance(rec.get("meeting"), dict) else {}
    return _clean(meeting.get("customer"), 80) or _clean(((rec.get("intake") or {}).get("project") or {}).get("customer_name"), 80)


def _proposal_head(rec: dict[str, Any]) -> dict[str, str]:
    """목록에 보일 고객·모델·한 줄 요약."""
    customer = _rec_customer(rec)
    if rec.get("kind") == "atc":
        o = rec.get("atc") or {}
        first = next((c for c in o.get("screening_candidates") or [] if c.get("models") and not c.get("out_of_range")), None)
        model = " / ".join(m["name"] for m in first["models"]) if first else ""
        summary = (o.get("junior_summary") or {}).get("reason_in_plain_korean") or ""
    else:
        model = ((rec.get("items") or [{}])[0] or {}).get("product") or ""
        summary = rec.get("summary") or ""
    return {"customer": customer, "model": model, "summary": _clean(summary, 400),
            "title": " · ".join(x for x in (customer or "고객 미상", model or "후보 없음") if x)}


async def finalize_recommendation(rid: int, *, user_id: int | None, history: list[dict[str, str]],
                                  memo: str = "") -> dict[str, Any]:
    """추천 한 건을 최종 제안으로 확정해 저장(같은 추천을 다시 확정하면 덮어씀). 후보가 없으면 확정하지 않는다."""
    from ..database import SessionLocal

    rec = await get_recommendation(rid, user_id)
    if rec is None:
        raise RecommendError("추천을 찾을 수 없습니다.")
    head = _proposal_head(rec)
    if not head["model"]:
        raise RecommendError("후보가 없는 결과는 최종 제안으로 확정할 수 없습니다.")
    talk = [{"role": h.get("role"), "text": _clean(h.get("text"), 2000)} for h in history[-PROPOSAL_HISTORY_MAX:]
            if h.get("role") in ("user", "assistant") and _clean(h.get("text"), 2000)]
    snapshot = {"recommendation": rec, "history": talk}
    from sqlalchemy.exc import IntegrityError

    async with SessionLocal() as db:
        for _ in range(3):
            try:
                pid, deal_id, deal_no = await _save_proposal(db, rid, user_id, rec, head, memo, snapshot)
                await db.commit()
                break
            except IntegrityError:                     # 같은 순간 다른 건이 같은 건 번호를 받음 — 다시
                await db.rollback()
        else:
            raise RecommendError("영업 건 번호를 정하지 못했습니다. 잠시 뒤 다시 시도해 주세요.")
    return {**await get_proposal(pid), "deal_id": deal_id, "deal_no": deal_no}


async def _save_proposal(db, rid: int, user_id: int | None, rec: dict[str, Any], head: dict[str, Any], memo: str,
                         snapshot: dict[str, Any]) -> tuple[int, int, str]:
    """확정 기록(미팅·결과·근거·대화) 저장 + 제품 영업 건('제품 추천 확정', 사용자 2026-10-06). 커밋은 호출부."""
    from datetime import date

    from ..sales_deals import service as deals

    pid = (await db.execute(text(
        "INSERT INTO product_proposals (recommendation_id, user_id, kind, customer, title, model, summary, memo, snapshot) "
        "VALUES (:r, :u, :k, :c, :t, :m, :s, :memo, CAST(:snap AS jsonb)) "
        "ON CONFLICT (recommendation_id) WHERE recommendation_id IS NOT NULL DO UPDATE SET "
        "user_id = EXCLUDED.user_id, customer = EXCLUDED.customer, title = EXCLUDED.title, model = EXCLUDED.model, "
        "summary = EXCLUDED.summary, memo = EXCLUDED.memo, snapshot = EXCLUDED.snapshot, updated_at = NOW() RETURNING id"),
        {"r": rid, "u": user_id, "k": "atc" if rec.get("kind") == "atc" else "other", "c": head["customer"],
         "t": head["title"], "m": head["model"], "s": head["summary"], "memo": _clean(memo, 1000),
         "snap": json.dumps(snapshot, ensure_ascii=False)})).scalar_one()
    owner = (await db.execute(text("SELECT COALESCE(alias, username) FROM users WHERE id = :u"), {"u": user_id})).scalar() \
        if user_id else None
    deal_id, deal_no = await deals.recommend_deal(db, user_id, pid, {
        "day": date.today(), "customer": head["customer"], "owner": owner, "title": head["model"]})
    return pid, deal_id, deal_no


_PROPOSAL_SQL = ("SELECT p.id, p.recommendation_id, p.kind, p.customer, p.title, p.model, p.summary, p.memo, p.created_at, "
                 "p.updated_at, COALESCE(u.alias, u.username) AS submitted_by{snap} FROM product_proposals p "
                 "LEFT JOIN users u ON u.id = p.user_id")


async def list_proposals(limit: int = 200) -> list[dict[str, Any]]:
    from ..database import SessionLocal

    async with SessionLocal() as db:
        rows = (await db.execute(text(_PROPOSAL_SQL.format(snap="") + " ORDER BY p.updated_at DESC LIMIT :n"),
                                 {"n": limit})).mappings().all()
    return [{**dict(r), "created_at": r["created_at"].isoformat(), "updated_at": r["updated_at"].isoformat()} for r in rows]


async def get_proposal(pid: int) -> dict[str, Any]:
    from ..database import SessionLocal

    async with SessionLocal() as db:
        r = (await db.execute(text(_PROPOSAL_SQL.format(snap=", p.snapshot") + " WHERE p.id = :i"), {"i": pid})).mappings().first()
        if r is None:
            raise RecommendError("확정 제안을 찾을 수 없습니다.")
        d = dict(r)
        d["created_at"], d["updated_at"] = d["created_at"].isoformat(), d["updated_at"].isoformat()
        # 미팅 정보 화면(사용자 2026-10-07: 고객사·로봇 제조사가 확실히 보이게) — 미팅 기록에 없으면
        #  고객사는 이 확정으로 만든 영업 건(견적서에 적은 고객사)에서, 로봇 제조사는 로봇 사양 DB 에서 찾아 따로 알려 준다.
        deal = (await db.execute(text("SELECT deal_no, customer FROM sales_deals WHERE proposal_id = :p"), {"p": pid})).first()
        d["deal_no"] = deal[0] if deal else None
        d["deal_customer"] = deal[1] if deal else None
        robots = (((d.get("snapshot") or {}).get("recommendation") or {}).get("intake") or {}).get("robots") or []
        d["robot_makers"] = {}
        for rb in robots:
            model = str(rb.get("model") or "").strip()
            if model and not rb.get("manufacturer"):
                key = re.sub(r"[\s\-_]", "", model).lower()
                maker = (await db.execute(text(
                    "SELECT maker FROM robot_specs WHERE regexp_replace(lower(model), '[[:space:]_-]', '', 'g') = :k LIMIT 1"),
                    {"k": key})).scalar()
                if maker:
                    d["robot_makers"][model] = maker
    return d
