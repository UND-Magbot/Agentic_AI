"""회상 엔진 — 상황을 보고 먼저 생각한 뒤, 회사가 겪은 경험·가진 제품 지식을 떠올려 "이렇게 해 보시겠습니까?"를 만든다.

사람 전문가의 순서를 따른다(검색해 붙이기가 아니다):
  1) 상황 파악(analyze): 요청에서 공정·대상물·재질·무게·다품종·환경·이동성·툴 교체·전원/공압 제약·문제를 읽고,
     "이 상황의 신호"를 문장으로 뽑는다. — 사내 gemma(요청 원문은 밖으로 나가지 않는다).
  2) 연상(associate): 신호 문장 ↔ 카드의 회상 단서(knowledge_cues) 임베딩 유사도. 한 신호에 강하게 걸린 카드보다
     여러 신호에 두루 걸린 카드를 앞에 둔다. 과거 [적용]/[빼기] 피드백(experience_feedback)으로 조금 가감한다.
  3) 번짐(spread): 떠오른 사례가 쓴 제품, 떠오른 제품을 쓴 사례로 활성이 번진다(카드의 related 링크).
  4) 되짚기(reflect): 1~3단계로 추린 후보만(상위 top_k 장 상세 + 나머지 후보 한 줄씩, 최대 POOL_SIZE 장) 요청 글에
     붙여 "이번 조건에 맞나, 무엇을 조심하나"를 판단하게 한다. 전체 기억 목록은 보내지 않는다(사용자 결정 2026-09-30:
     카드가 늘어도 한 번에 보내는 양이 일정하게 — 공정 상황과 과거 기록을 비교해 후보군에 관련된 카드만).
     brain="gpt" 면 Codex(외부, codex_client._post 의 가명 관문 경유), "local" 이면 사내 gemma.
  같은 상황·같은 기억 상태면 저장해 둔 결과를 다시 쓴다(recall_cache) — 카드·피드백이 바뀌면 자동으로 다시 계산.
  5) 근거 확인(ground): 모델이 인용한 사실(facts)은 해당 카드에 실제로 있는 문장만 남긴다. "효과가 좋았다" 류 표현은
     실적 카드에만 허용한다 — 제안·컨셉 카드는 "이렇게 제안한 적이 있다" 까지.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text

from .. import proposal_llm
from ..diagram.llm_spec import _extract_json

_log = logging.getLogger("company_knowledge.recall")

TABLE_PREFIX = {"knowledge_cards": "K", "experience_cards": "E"}
PROVEN = ("실적", "실적(회사 수행)")                 # "효과가 좋았다" 를 쓸 수 있는 근거
MIN_SIM = 0.50           # 신호↔단서 유사도가 이보다 낮으면 연상으로 치지 않는다
REL_MARGIN = 0.10        # 한 신호에서 가장 잘 맞은 카드보다 이만큼 이상 낮으면 그 신호로는 떠오르지 않은 것으로 본다
                         # (0.07 이면 한 카드가 압도할 때 두 번째로 맞는 실적이 잘렸다 — 식기 분류 K164, 실측)
# REL_MARGIN·BREADTH_WEIGHT·TERM_BONUS 는 eval/recall_dataset 5개 상황에서 격자 탐색해, 정답 7/8 이 상위 6 안에 드는
# 넓고 평평한 구간(0.10~0.12 / 0.15~0.35 / 0.05~0.08)의 가운데를 골랐다 — 한 사례에 맞춘 값이 아니다.
TERM_BONUS, TERM_CAP = 0.05, 0.15   # 상황의 핵심 명사(대상물·재질·공정)가 카드에 그대로 나오면 가산
_TERM_STOP = {"공정", "자동화", "로봇", "작업", "라인", "설비", "시스템", "제품", "부품", "필요", "고객", "현재", "경우"}
MIN_SIGNAL_CHARS = 8     # 한두 낱말 신호("식판", "하루 수천 개")는 아무 카드에나 약하게 걸려 순위를 흐린다(실측)
BREADTH_WEIGHT = 0.25    # 두 번째 이후로 걸린 신호의 가산 비율(여러 신호에 두루 걸린 카드 우선)
SPREAD_WEIGHT = 0.5      # 연결된 카드로 번지는 활성 비율
FEEDBACK_STEP, FEEDBACK_CAP = 0.04, 0.12
QUESTION_MAX, CONTEXT_MAX = 3800, 58000             # codex-bridge LIMITS(question 4000, context 60000) 여유
POOL_SIZE = 30           # 되짚기에 보내는 후보 수(상위 top_k 는 상세, 나머지는 한 줄)
CACHE_DAYS = 14
_VERIFIED_RE = re.compile(r"(?:이미\s*)?(?:검증된|입증된)\s*")
_BRAG_RE = re.compile(r"[^.。!?]*(?:효과가 좋|효과를 봤|효과를 보았|성공적|입증|검증된|검증되었)[^.。!?]*[.。!?]?")

_ANALYZE = """너는 로봇 자동화 수석 엔지니어다. 고객 상황을 읽고 먼저 판단한다. JSON 으로만 답한다.
{"process": "공정 명사구", "objects": "대상물", "material": "재질(모르면 빈 값)", "weight": "무게(원문 그대로, 없으면 빈 값)",
 "variety": "품종·형상 다양성", "environment": "환경(물기·온도·클린룸·옥외 등)", "mobility": "이동 필요 여부(AMR·4족 등)",
 "tool_change": "툴 교체 필요", "constraints": ["전원·공압·공간·안전 제약"], "problems": ["고객이 겪는 문제"],
 "aliases": ["대상물·재질·공정을 현장에서 달리 부르는 말 3~6개(동의어·상위어). 예: 주철 브라켓 → 주물, 주조품, 강자성체"],
 "cues": ["이 상황의 신호를 고객 상황을 설명하는 완결된 문장으로 6~10개"]}
cues 는 낱말이 아니라 무엇을·어떤 조건에서·무엇이 문제인지 담은 문장으로(예: "주철 브라켓 6종을 형상이 달라도 한 그리퍼로
옮겨야 한다", "세척을 마친 여러 종류의 식기를 종류별로 나눠 쌓아야 한다"). 원문에 없는 조건을 지어내지 않는다."""

_REFLECT = """너는 유엔디로보틱스의 수석 기술영업이다. 아래 고객 상황을 보고, 회사가 겪은 경험·가진 제품 지식 중
이번에 꺼내 쓸 것을 판단한다. [떠오른 카드]는 상세, [다른 후보]는 이 상황과 관련해 함께 떠오른 카드의 한 줄 요약이다.
다른 후보 중 상세 카드보다 더 맞는 것이 있으면 also 에 적는다.

규칙:
1. 카드에 적힌 내용만 근거로 쓴다. facts 에는 카드 문장을 그대로 옮긴다(바꿔 쓰지 않는다).
2. 근거 수준을 지킨다: "실적" 카드만 "효과가 좋았다/개선했다"고 말할 수 있다. "제안"·"컨셉" 카드는 "이렇게 제안한 적이
   있다", "제품 사양" 카드는 "이런 제품이 있다"까지.
3. fit: 이번 조건에 그대로 맞으면 "맞음", 조건 확인이 필요하면 "주의", 안 맞으면 "안 맞음"(목록에서 빼지 말고 이유를 쓴다).
   한계·주의점(예: 강자성체만, 가반하중, 환경 등급)을 이번 상황과 대조해 caution 에 쓴다.
4. why 는 "예전에 ~한 상황을 ~로 풀었습니다 → 이번에도 ~이라 적용할 만합니다" 처럼 경험을 꺼내는 말투로 한두 문장.
5. 순서: 회사가 실제로 해 본 경험(실적·실적(회사 수행)) 중 맞는 것을 먼저, 그 경험을 이루는 제품은 그 다음.
   경험이 맞으면 "예전에 이렇게 했습니다"가 제안의 첫 문장이 되게 한다.
6. 없는 ref 를 만들지 않는다. 주의할 점이 없으면 caution 은 빈 문자열.
JSON 으로만: {"thinking": "상황 판단 2~3문장", "hits": [{"ref": "K12", "fit": "맞음|주의|안 맞음", "why": "...",
 "caution": "...", "facts": ["카드 문장 그대로"]}], "also": ["E7"], "suggestion": "종합 제안 두세 문장"}"""


@dataclass
class Card:
    table: str
    id: int
    evidence: str
    card: dict[str, Any]
    score: float = 0.0
    matched: list[str] = field(default_factory=list)       # 걸린 신호

    @property
    def ref(self) -> str:
        return f"{TABLE_PREFIX[self.table]}{self.id}"

    @property
    def title(self) -> str:
        return self.card.get("name") or self.card.get("title") or ""

    def line(self) -> str:
        """전체 기억 목록의 한 줄."""
        c = self.card
        gist = (c.get("key_ideas") or [c.get("summary") or c.get("solution") or ""])[0]
        proc = c.get("process") or c.get("family") or c.get("kind") or ""
        return f"{self.ref} [{self.evidence}] {self.title} — {proc} · {gist}"[:180]

    def detail(self) -> dict[str, Any]:
        keep = ("name", "title", "family", "kind", "summary", "how_it_works", "process", "problem", "solution",
                "key_ideas", "effect", "effect_numbers", "strengths", "limits", "not_when", "cautions",
                "lessons", "applies_when", "products_used", "customers", "specs", "items")
        d = {k: self.card[k] for k in keep if self.card.get(k)}
        if isinstance(d.get("specs"), list):
            d["specs"] = [f"{s.get('model', '')} {s.get('item', '')} {s.get('value', '')}".strip() for s in d["specs"][:20]]
        return {"ref": self.ref, "evidence": self.evidence, **d}

    def corpus(self) -> str:
        return _norm(json.dumps(self.card, ensure_ascii=False))


def _norm(s: str) -> str:
    return re.sub(r"[\s\W_]+", "", s or "").lower()


# ── 2) 연상 점수 ────────────────────────────────────────────────────────────

def aggregate(matches: list[tuple[str, str, int, float]]) -> dict[tuple[str, int], tuple[float, list[str]]]:
    """(신호, 카드표, 카드id, 유사도) 목록 → 카드별 (점수, 걸린 신호). 신호마다 카드의 최고 유사도만 쓰고,
    가장 강한 신호 1개 + 나머지 신호 × BREADTH_WEIGHT. 두루 걸린 카드가 한 번 세게 걸린 카드를 이길 수 있다."""
    top_of: dict[str, float] = {}
    for sig, _t, _c, sim in matches:
        top_of[sig] = max(top_of.get(sig, 0.0), sim)
    best: dict[tuple[str, int], dict[str, float]] = {}
    for sig, table, cid, sim in matches:
        # 절대 하한 + 상대 하한: 그 신호에 가장 잘 맞은 카드 근처만. 실측(식기 분류): "사람이 하고 있음" 같은 신호가
        # 슬리브 CNC·피스톤링 연마 카드에 0.5대로 두루 걸려 식기 분류 실적 카드를 9위로 밀어냈다.
        if sim < max(MIN_SIM, top_of[sig] - REL_MARGIN):
            continue
        per = best.setdefault((table, cid), {})
        per[sig] = max(per.get(sig, 0.0), sim)
    out = {}
    for key, per in best.items():
        sims = sorted(per.values(), reverse=True)
        out[key] = (sims[0] + BREADTH_WEIGHT * sum(sims[1:]), [s for s, _ in sorted(per.items(), key=lambda x: -x[1])])
    return out


def key_terms(analysis: dict[str, Any]) -> set[str]:
    """상황 분석의 대상물·재질·공정·환경 칸에서 핵심 명사(2자 이상 한글, 3자 이상 영문)."""
    text_ = " ".join(str(analysis.get(k) or "") for k in ("process", "objects", "material", "environment"))
    text_ += " " + " ".join(str(a) for a in analysis.get("aliases") or [])
    words = set(re.findall(r"[가-힣]{2,}|[A-Za-z]{3,}", text_))
    # 조사 붙은 꼴("식기를")은 앞 2~3자로도 찾게 한다
    stems = {w[:-1] for w in words if len(w) >= 3 and w[-1] in "을를이가은는의에와과도"}
    return {w.lower() for w in words | stems} - _TERM_STOP


def term_bonus(terms: set[str], corpus: str) -> float:
    return min(TERM_CAP, TERM_BONUS * sum(1 for t in terms if t in corpus))


def spread(cards: dict[tuple[str, int], Card], by_key: dict[str, tuple[str, int]]) -> None:
    """연결된 카드로 활성을 번지게 한다(한 단계). by_key: knowledge card_key → (표, id)."""
    boosts: dict[tuple[str, int], float] = {}
    for key, c in cards.items():
        if c.score <= 0:
            continue
        for rk in c.card.get("related") or []:
            if rk in by_key and by_key[rk] != key:
                boosts[by_key[rk]] = max(boosts.get(by_key[rk], 0.0), c.score * SPREAD_WEIGHT)
    for k, b in boosts.items():
        if k in cards:
            cards[k].score = max(cards[k].score, b) if cards[k].score == 0 else cards[k].score + b * 0.3


def feedback_bonus(apply_n: int, skip_n: int) -> float:
    return max(-FEEDBACK_CAP, min(FEEDBACK_CAP, FEEDBACK_STEP * (apply_n - skip_n)))


# ── 5) 근거 확인 ────────────────────────────────────────────────────────────

def ground(result: dict[str, Any], pool: dict[str, Card]) -> tuple[list[dict[str, Any]], list[str]]:
    """모델 출력의 hits 를 카드와 대조: 모르는 ref 제외, 카드에 없는 facts 제외, 근거 수준에 맞지 않는 자랑 문장 제거."""
    hits, notes = [], []
    for h in result.get("hits") or []:
        if not isinstance(h, dict):
            continue
        ref = str(h.get("ref") or "").strip()
        c = pool.get(ref)
        if c is None and re.fullmatch(r"[KE]\d+", ref):
            # 접두어만 틀린 경우(실측: K164 양팔 식기 분류를 "E164" 로 적음) — 다른 접두어의 같은 번호가 하나뿐이면 그것.
            alt = ("E" if ref[0] == "K" else "K") + ref[1:]
            if alt in pool and ref not in pool:
                c = pool[alt]
                notes.append(f"{ref} → {alt} 로 바로잡음(접두어 오류)")
        if c is None:
            notes.append(f"알 수 없는 카드 {ref} 제외")
            continue
        corpus = c.corpus()
        facts, bad = [], 0
        for f in h.get("facts") or []:
            f = str(f).strip()
            if len(_norm(f)) >= 6 and _norm(f) in corpus:
                facts.append(f)
            else:
                bad += 1
        if bad:
            notes.append(f"{c.ref} 카드에 없는 인용 {bad}건 제외")
        why = str(h.get("why") or "").strip()
        if c.evidence not in PROVEN and _BRAG_RE.search(why):
            why = _BRAG_RE.sub("", why).strip() or "이렇게 제안한 적이 있습니다."
            notes.append(f"{c.ref}({c.evidence}) 효과 단정 표현 제거")
        fit = h.get("fit") if h.get("fit") in ("맞음", "주의", "안 맞음") else "주의"
        caution = str(h.get("caution") or "").strip()
        caution = "" if caution.lower() in ("none", "없음", "n/a", "-", "null") else caution
        hits.append({"card_table": c.table, "card_id": c.id, "ref": c.ref, "title": c.title, "evidence": c.evidence,
                     "fit": fit, "why": why, "caution": caution, "facts": facts,
                     "score": round(c.score, 3), "matched": c.matched[:4]})
    return hits, notes


# ── 조립 ────────────────────────────────────────────────────────────────────

def _situation_text(situation: str, items: dict[str, str] | None) -> str:
    extra = "\n".join(f"{k}: {v}" for k, v in (items or {}).items() if v)
    return (situation.strip() + ("\n" + extra if extra else "")).strip()


async def analyze(situation: str, *, chat=None) -> dict[str, Any]:
    chat = chat or proposal_llm.chat
    data = _extract_json(await chat([{"role": "system", "content": _ANALYZE}, {"role": "user", "content": situation}],
                                    fmt="json", num_predict=1500, temperature=0.0))   # 실행마다 신호가 달라 순위가 흔들렸다
    if not isinstance(data, dict):
        data = {}
    cues = [str(c).strip() for c in data.get("cues") or [] if len(str(c).strip()) >= MIN_SIGNAL_CHARS][:10]
    data["cues"] = cues
    return data


async def _load_cards(db) -> dict[tuple[str, int], Card]:
    cards: dict[tuple[str, int], Card] = {}
    for r in (await db.execute(text("SELECT id, evidence, card FROM knowledge_cards WHERE active"))).mappings():
        cards[("knowledge_cards", r["id"])] = Card("knowledge_cards", r["id"], r["evidence"], r["card"])
    if (await db.execute(text("SELECT to_regclass('experience_cards')"))).scalar():
        for r in (await db.execute(text("SELECT id, evidence, card FROM experience_cards WHERE active"))).mappings():
            cards[("experience_cards", r["id"])] = Card("experience_cards", r["id"], r["evidence"], r["card"])
    return cards


async def _matches(db, signals: list[str]) -> list[tuple[str, str, int, float]]:
    from ..rag import embed_batch

    out = []
    for sig, vec in zip(signals, await embed_batch(signals)):
        lit = "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
        rows = (await db.execute(text(
            "SELECT card_table, card_id, 1 - (embedding <=> CAST(:v AS vector)) AS sim FROM knowledge_cues "
            "ORDER BY embedding <=> CAST(:v AS vector) LIMIT 25"), {"v": lit})).all()
        out += [(sig, t, int(cid), float(sim)) for t, cid, sim in rows]
    return out


async def _feedback(db) -> dict[tuple[str, int], float]:
    if not (await db.execute(text("SELECT to_regclass('experience_feedback')"))).scalar():
        return {}
    rows = (await db.execute(text(
        "SELECT card_table, card_id, SUM((action = 'apply')::int) AS a, SUM((action = 'skip')::int) AS s "
        "FROM experience_feedback GROUP BY card_table, card_id"))).all()
    return {(t, int(c)): feedback_bonus(int(a), int(s)) for t, c, a, s in rows}


def _reflect_inputs(situation: str, analysis: dict[str, Any], top: list[Card],
                    others: list[Card]) -> tuple[str, str]:
    """(question, context) — question 에 지시·상황, context 에 카드. 브리지 글자 수 한도 안으로."""
    question = (_REFLECT + "\n\n[고객 상황]\n" + situation[:1800]
                + "\n\n[상황 판단(사내 분석)]\n" + json.dumps({k: v for k, v in analysis.items() if v and k != "cues"},
                                                              ensure_ascii=False)[:700])[:QUESTION_MAX]
    detail = "[떠오른 카드]\n" + "\n".join(json.dumps(c.detail(), ensure_ascii=False) for c in top)
    index = "[다른 후보]\n" + "\n".join(c.line() for c in others)
    context = detail[:CONTEXT_MAX - min(len(index), CONTEXT_MAX // 2) - 10] + "\n\n" + index[:CONTEXT_MAX // 2]
    return question, context


async def _think(question: str, context: str, brain: str, user_id: int | None, *, chat=None, ask=None) -> tuple[dict, str]:
    """되짚기. gpt 가 실패하면 사내 모델로 대신한다(결과에 표시)."""
    if brain == "gpt":
        from .. import codex_client

        ask = ask or codex_client.ask
        try:
            raw = await ask(question + "\n\nJSON 객체 하나만 출력하라.", context, user_id=user_id)
            data = _extract_json(raw)
            if isinstance(data, dict):
                return data, "gpt"
        except Exception as e:  # noqa: BLE001 — 외부 실패 시 사내 모델로
            _log.warning("[recall] gpt 되짚기 실패 → 사내 모델: %s", e)
    chat = chat or proposal_llm.chat
    raw = await chat([{"role": "system", "content": question}, {"role": "user", "content": context}],
                     fmt="json", num_predict=4000, temperature=0.2)
    data = _extract_json(raw)
    return (data if isinstance(data, dict) else {}), "local"


def cache_key(situation: str, brain: str, top_k: int, memory_version: str) -> str:
    import hashlib

    raw = json.dumps([situation.strip(), brain, top_k, POOL_SIZE, memory_version], ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def _memory_version(db) -> str:
    """기억 상태 지문 — 카드가 추가·수정·비활성되거나 피드백이 쌓이면 바뀐다(캐시 무효화)."""
    parts = []
    for table in ("knowledge_cards", "experience_cards", "experience_feedback"):
        if not (await db.execute(text("SELECT to_regclass(:t)"), {"t": table})).scalar():
            continue
        col = "created_at" if table == "experience_feedback" else "updated_at"
        where = "" if table == "experience_feedback" else " WHERE active"
        n, last = (await db.execute(text(f"SELECT count(*), max({col}) FROM {table}{where}"))).one()
        parts.append(f"{table}:{n}:{last}")
    return "|".join(parts)


async def _cache_get(db, key: str) -> dict[str, Any] | None:
    if not (await db.execute(text("SELECT to_regclass('recall_cache')"))).scalar():
        return None
    row = (await db.execute(text(
        "SELECT result FROM recall_cache WHERE cache_key = :k AND created_at > NOW() - make_interval(days => :d)"),
        {"k": key, "d": CACHE_DAYS})).scalar()
    return dict(row) if row else None


async def _cache_put(key: str, result: dict[str, Any]) -> None:
    from ..database import SessionLocal

    try:
        async with SessionLocal() as db:
            await db.execute(text(
                "INSERT INTO recall_cache (cache_key, result) VALUES (:k, CAST(:r AS jsonb)) "
                "ON CONFLICT (cache_key) DO UPDATE SET result = EXCLUDED.result, created_at = NOW()"),
                {"k": key, "r": json.dumps(result, ensure_ascii=False)})
            await db.commit()
    except Exception as e:  # noqa: BLE001 — 저장 실패가 회상 결과를 막지 않게
        _log.warning("[recall] 결과 저장 실패: %s", e)


async def recall(situation: str, items: dict[str, str] | None = None, *, brain: str = "gpt",
                 user_id: int | None = None, top_k: int = 8, use_cache: bool = True,
                 chat=None, ask=None) -> dict[str, Any]:
    """상황 → 회상. 반환: {analysis, thinking, hits, also, suggestion, memory_index, brain, notes, cached}.
    memory_index 는 되짚기에 보낸 후보 목록(전체 기억 목록이 아니다)."""
    from ..database import SessionLocal

    sit = _situation_text(situation, items)
    async with SessionLocal() as db:
        key = cache_key(sit, brain, top_k, await _memory_version(db))
        hit = await _cache_get(db, key) if use_cache else None
    if hit:
        hit["cached"] = True
        return hit
    analysis = await analyze(sit, chat=chat)
    signals = [sit[:500]] + analysis["cues"]          # 원문 한 덩어리 + 신호 문장들
    aliases = [str(a).strip() for a in analysis.get("aliases") or [] if str(a).strip()]
    if aliases:
        # 같은 것을 다른 말로 부르는 카드(상황 "주철 브라켓" ↔ 카드 "주물 … 강자성체")도 떠오르게(실측: 탱크 링크 누락)
        signals.append(f"{analysis.get('objects') or ''} ({', '.join(aliases[:6])}) 을 다루는 공정")
    async with SessionLocal() as db:
        cards = await _load_cards(db)
        matched = aggregate(await _matches(db, signals))
        fb = await _feedback(db)
    for key_, (score, sigs) in matched.items():
        if key_ in cards:
            cards[key_].score, cards[key_].matched = score, [s for s in sigs if s != signals[0]]
    for key_, bonus in fb.items():
        if key_ in cards and cards[key_].score > 0:
            cards[key_].score += bonus
    terms = key_terms(analysis)
    for c in cards.values():
        if c.score > 0 and terms:
            c.score += term_bonus(terms, c.corpus())
    spread(cards, _knowledge_keys(cards))
    ranked = sorted((c for c in cards.values() if c.score > 0), key=lambda c: -c.score)
    top, others = ranked[:top_k], ranked[top_k:POOL_SIZE]
    question, context = _reflect_inputs(sit, analysis, top, others)
    data, used = await _think(question, context, brain, user_id, chat=chat, ask=ask)
    pool = {c.ref: c for c in top + others}           # 보낸 후보만 — 보내지 않은 카드를 모델이 지어낼 수 없게
    hits, notes = ground(data, pool)
    if not hits and top:        # 모델이 아무것도 못 고르면 연상 상위를 그대로(판단 없이) 보여 준다
        notes.append("되짚기 결과가 없어 연상 상위 카드를 판단 없이 표시")
        hits = [{"card_table": c.table, "card_id": c.id, "ref": c.ref, "title": c.title, "evidence": c.evidence,
                 "fit": "주의", "why": "", "caution": "", "facts": [], "score": round(c.score, 3),
                 "matched": c.matched[:4]} for c in top]
    also = [pool[r].line() for r in (data.get("also") or []) if isinstance(r, str) and r in pool
            and r not in {h["ref"] for h in hits}]
    # 연상 상위인데 모델이 언급하지 않은 카드도 버리지 않고 "떠올랐지만 판단 보류"로 붙인다
    # (실측: 옥외 순찰에서 연상 1위였던 주유소 4족 순찰 제안을 GPT 가 건너뜀 — 경험이 사라지면 안 된다).
    mentioned = {h["ref"] for h in hits} | {r for r in (data.get("also") or []) if isinstance(r, str)}
    also += [c.line() + " (연상 상위 — 판단 보류)" for c in top if c.ref not in mentioned]
    # 종합 제안은 여러 카드를 섞어 말하므로 단정·과장 표현을 낮춘다(실측: 제품 사양 카드뿐인데 "내구성이 검증된").
    # "확보·가능" 까지 일괄로 낮추면 실적 카드를 말하는 문장이 "안전을 확보 검토하고" 처럼 깨졌다(실측) — 과장만 뺀다.
    raw_sug = str(data.get("suggestion") or "")
    suggestion = _VERIFIED_RE.sub("", raw_sug).replace("극대화", "향상")
    if suggestion != raw_sug:
        notes.append("종합 제안의 과장 표현(검증된·극대화) 뺌")
    result = {"analysis": analysis, "thinking": str(data.get("thinking") or ""), "hits": hits, "also": also,
              "suggestion": suggestion, "memory_index": "\n".join(c.line() for c in top + others),
              "brain": used, "notes": notes, "cached": False}
    if hits and used == brain:       # GPT 가 실패해 사내 모델로 대신한 결과는 저장하지 않는다(다음에 다시 GPT 로)
        await _cache_put(key, result)
    return result


def _knowledge_keys(cards: dict[tuple[str, int], Card]) -> dict[str, tuple[str, int]]:
    """지식 카드의 related 는 card_key 로 적혀 있다 → (표, id) 로. card_key 는 카드 JSON 에 없어 이름으로 다시 만든다."""
    from .cards import card_key

    return {card_key(c.card.get("kind", ""), c.card.get("name", "")): k
            for k, c in cards.items() if c.table == "knowledge_cards"}
