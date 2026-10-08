"""회사 소개서 → 지식 카드(제품·적용 사례·회사 역량) + 회상 단서(cues).

카드는 "찾아서 붙이는 원문"이 아니라 영업 전문가가 머릿속에 가진 지식의 모양으로 정리한다:
  - product    : 무엇인가 / 어떤 원리인가 / 사양(원문 수치만, 모델별) / 강점 / 한계·쓰면 안 되는 조건
  - reference  : 무슨 공정 / 문제 / 해법 / 핵심 아이디어 / 효과 / 주의점 / 다시 쓸 조건 / 쓴 제품
                 (experience_cards 와 같은 필드 — 회상 엔진이 두 카드를 같은 눈으로 읽는다)
  - capability : 회사가 해 온 일(SI 실적 목록·생산 거점·인증) — "이런 라인을 해 봤나?"에 답한다
  - company    : 회사 개요(연혁·조직·수상)
  - cues       : 이 카드를 떠올려야 할 고객 상황 신호(제품명이 아니라 상황의 말로). 회상 엔진이 요청의 신호와
                 이 문장들을 연상 검색한다 — 사람이 "철 부품 여러 형상" 을 듣고 "그때 그 마그네틱 그리퍼" 를
                 떠올리는 연결 고리.
근거 수준(evidence): 제품 사양 = "제품 사양", 수행한 공정 = "실적(회사 수행)", 컨셉만 제시 = "컨셉(회사 제안)",
역량 목록 = "회사 실적 목록". "효과가 좋았다"는 실적만 쓸 수 있다(04 경험 카드와 같은 규칙).
검증: 카드의 수치는 그 쪽 원문(글상자·표 또는 이미지 판독)에 있어야 한다. 없으면 버리고 보고한다.
모두 사내 gemma 로만 만든다(외부 전송 없음).

실행:
    docker compose run --rm --no-deps -v ./docs:/docs backend python -m app.company_knowledge.cards build
    docker compose run --rm --no-deps backend python -m app.company_knowledge.cards cues   # 경험 카드 단서까지
    docker compose run --rm --no-deps -v ./docs:/docs backend python -m app.company_knowledge.cards export  # 문서만
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import text

from .. import proposal_llm
from ..database import SessionLocal
from ..diagram.llm_spec import _extract_json
from .intro_deck import DOCS, SOURCE_PATH, Slide, slides_with_ocr

_log = logging.getLogger("company_knowledge.cards")

SOURCE_DOC = "회사 소개서 3Q"
KINDS = ("product", "reference", "capability", "company")
EVIDENCE = {"product": "제품 사양", "reference": "실적(회사 수행)", "concept": "컨셉(회사 제안)",
            "capability": "회사 역량", "company": "회사 개요"}
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")

_SYSTEM = """너는 유엔디로보틱스의 수석 기술영업이다. 회사 소개서의 한 쪽을 읽고, 다음 상담에서 머릿속에서 바로 꺼내 쓸
지식 카드로 정리한다. JSON 으로만 답한다: {"cards": [...]}. 이 쪽에 정리할 내용이 없으면 {"cards": []}.

카드 종류(kind):
- product: 회사 제품·제품군(맥봇 툴체인저·그리퍼·AMR·4족 로봇·관제 솔루션 등). 한 쪽에 제품군이 여럿이면 여러 장.
- reference: 회사가 실제로 적용한 공정 사례("본 공정은 …" 처럼 수행한 공정). 한 공정 = 한 장.
  수행이 아니라 아이디어만 제시한 경우("본 컨셉은 …")도 reference 로 쓰되 is_concept=true.
- capability: 회사가 해 온 SI 라인·설비 목록, 생산 거점, 주요 고객사 목록 등 "이런 일을 해 봤다" 는 역량.
  서비스 시나리오·기능 소개(순찰 시나리오, SLAM 매핑, 관제 화면 등)는 수행 실적이 아니다 → product 또는 capability.
- company: 회사 개요(연혁·조직·인증·수상·투자).
표지·연락처 쪽, 영상 안내만 있는 쪽은 cards 를 비운다.

규칙:
1. 이 쪽(원문 + 이미지 판독)에 적힌 내용만 쓴다. 적히지 않은 사실·수치·효과·고객명을 지어내지 않는다.
2. specs 는 원문 수치를 그대로, 모델별로: {"model": "TCV2", "item": "가반하중", "value": "16kgf"}.
3. limits 는 적용 조건·한계(예: 강자성체만 흡착, 기본 IP45, 사용 온도, "† 미확정 사양은 변경될 수 있음").
   원문에 드러난 것만. not_when 은 limits 로 보아 쓰면 안 되는 상황.
4. cues 는 이 카드를 떠올려야 할 "고객 상황"을 고객이 자기 현장을 설명하는 서술문으로 4~8개(질문형 금지).
   제품명이 아니라 상황·문제·조건으로. 예: "철 재질 부품을 형상이 다양하게 옮겨야 한다",
   "AMR 위에서 공압 그리퍼를 쓰고 싶은데 컴프레서가 없다", "툴을 자주 바꿔야 해서 라인이 멈춘다".
   limits 와 어긋나는 상황(예: 기본 IP45 제품에 "방수 등급이 높은 환경")은 cues 에 쓰지 않는다.
4-1. lessons 는 시험·시연·적용에서 드러난 "해 보니 이랬다"(성공·실패 조건, 사양 비교 수치)를 원문 그대로의 수치로.
   예: "S극 Ø22mm 사양에서 3.5~4kg 흡착 성공, Ø15mm 사양은 1.5~2kg 로 실패". 원문에 있으면 반드시 쓴다 —
   다음 상담에서 가장 값진 경험이다.
5. products_used 는 사례에 쓰인 회사 제품 이름(원문 표기). customers 는 원문에 적힌 고객사명만(없으면 빈 목록).
6. 이미 만든 카드 이름 목록을 줄 테니, 앞 쪽에서 이어지는 같은 제품(사양표가 다음 쪽에 이어짐 등)이면 같은 name 을 쓴다.
   사례(reference)는 이 쪽의 공정이 앞 쪽과 같은 공정일 때만 같은 name — 다른 공정이면 반드시 새 이름.
6-1. name 은 이 쪽의 제목·설명에 실제로 나오는 말로 짓는다(영문 코드명·파일명식 이름 금지, 예: "주요 고객사").
6-2. 한 항목의 제목과 설명이 서로 다른 공정을 가리키면(소개서 편집 오류) 설명("본 공정은/본 설비는 …")을 따라
   카드를 쓰고, source_issues 에 "제목 '…' 와 설명(…)이 다름" 처럼 적는다.
7. 쪽 이미지가 함께 주어지면 그림 배치를 보고 어느 설명·수치가 어느 사진·사양에 붙은 것인지 판단한다
   (글상자 순서만으로는 짝이 뒤바뀐다). 수치는 그림에 적힌 그대로.

카드 형식(쓸모없는 필드는 빈 값):
{"kind": "product|reference|capability|company", "name": "짧은 고유 이름", "family": "제품군(예: 맥봇 ATC)",
 "summary": "한두 문장 요약", "how_it_works": "원리·구성", "specs": [...], "strengths": ["..."], "limits": ["..."],
 "not_when": ["..."], "is_concept": false, "process": "공정 명사구", "problem": "...", "solution": "...",
 "key_ideas": ["다른 공정에도 옮겨 쓸 설계 아이디어"], "effect": "...", "effect_numbers": [{"metric": "...", "value": "..."}],
 "cautions": ["..."], "lessons": ["..."], "applies_when": "...", "products_used": ["..."], "customers": ["..."], "items": ["capability 목록 항목"], "source_issues": ["소개서 편집 오류로 보이는 곳"],
 "cues": ["..."]}"""


def _clean(v: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:limit]


def _list(v: Any, limit: int, n: int) -> list[str]:
    return [x for x in (_clean(i, limit) for i in (v or []) if not isinstance(i, (dict, list))) if x][:n]


def _nums(s: str) -> set[str]:
    return {m.group(0).replace(",", "") for m in _NUM_RE.finditer(s or "")}


def card_key(kind: str, name: str) -> str:
    return f"intro:{kind}:" + re.sub(r"[\s\W_]+", "", name.lower())[:150]


@dataclass
class Checked:
    card: dict[str, Any]
    dropped: list[str] = field(default_factory=list)


def validate(raw: dict[str, Any], slide: Slide) -> Checked | None:
    """형식 정리 + 원문에 없는 수치 버림. 원문 글상자에 없고 이미지 판독에만 있는 수치는 source 로 표시."""
    kind = raw.get("kind")
    name = _clean(raw.get("name"), 80)
    if kind not in KINDS or not name:
        return None
    text_nums, ocr_nums = _nums(slide.text), _nums(slide.ocr)
    dropped: list[str] = []

    def num_source(value: str) -> str | None:
        ns = _nums(value)
        if ns <= text_nums:
            return "원문"
        if ns <= text_nums | ocr_nums:
            return "이미지 판독"
        return None

    specs = []
    for sp in raw.get("specs") or []:
        if not isinstance(sp, dict):
            continue
        item, value = _clean(sp.get("item"), 60), _clean(sp.get("value"), 80)
        if not item or not value:
            continue
        src = num_source(value)
        if src is None:
            dropped.append(f"{slide.no}쪽 원문에 없는 사양 수치: {item} {value}")
            continue
        specs.append({"model": _clean(sp.get("model"), 40), "item": item, "value": value, "source": src,
                      "slide": slide.no})
    effects = []
    for e in raw.get("effect_numbers") or []:
        if not isinstance(e, dict):
            continue
        metric, value = _clean(e.get("metric"), 60), _clean(e.get("value"), 60)
        if metric and value and num_source(value):
            effects.append({"metric": metric, "value": value})
        elif metric and value:
            dropped.append(f"{slide.no}쪽 원문에 없는 효과 수치: {metric} {value}")
    lessons = []
    for x in _list(raw.get("lessons"), 250, 5):
        if num_source(x) is None:
            dropped.append(f"{slide.no}쪽 원문에 없는 수치의 교훈: {x[:50]}")
        else:
            lessons.append(x)
    effect = _clean(raw.get("effect"), 300)
    if effect and num_source(effect) is None:
        dropped.append(f"{slide.no}쪽 효과 문장의 수치가 원문에 없음 → 수치 없이 보관: {effect[:50]}")
        effect = _NUM_RE.sub("", effect).strip()
    if kind == "reference" and not _name_grounded(name, slide):
        better = _clean(raw.get("process") or raw.get("summary"), 40)
        dropped.append(f"{slide.no}쪽 카드 이름 '{name}' 이 쪽에 없는 말 → '{better or slide.title}' 로 바꿈")
        name = better or _clean(slide.title, 40)
    concept = bool(raw.get("is_concept")) or (kind == "reference" and "본 컨셉" in slide.text
                                               and "본 공정" not in slide.text)
    card = {
        "kind": kind, "name": name, "family": _clean(raw.get("family"), 60),
        "summary": _clean(raw.get("summary"), 300), "how_it_works": _clean(raw.get("how_it_works"), 400),
        "specs": specs, "strengths": _list(raw.get("strengths"), 150, 6), "limits": _list(raw.get("limits"), 150, 6),
        "not_when": _list(raw.get("not_when"), 150, 4), "is_concept": concept,
        "process": _clean(raw.get("process"), 60), "problem": _clean(raw.get("problem"), 400),
        "solution": _clean(raw.get("solution"), 500), "key_ideas": _list(raw.get("key_ideas"), 200, 4),
        "effect": effect, "effect_numbers": effects[:6], "cautions": _list(raw.get("cautions"), 200, 4),
        "lessons": lessons,
        "applies_when": _clean(raw.get("applies_when"), 200),
        "products_used": _list(raw.get("products_used"), 60, 6),
        "customers": [c for c in _list(raw.get("customers"), 60, 6) if c in slide.full()],   # 원문에 있는 이름만
        "items": _list(raw.get("items"), 120, 40),
        "source_issues": [f"{slide.no}쪽: {x}" for x in _list(raw.get("source_issues"), 200, 3)],
        "cues": _list(raw.get("cues"), 120, 8),
        "slides": [slide.no],
    }
    return Checked(card, dropped)


def _name_grounded(name: str, slide: Slide) -> bool:
    """카드 이름의 낱말이 절반 이상 이 쪽(원문·판독)에 있나. 실측: 100쪽 'H자동차 매뉴얼 가이드북 물류' 카드에
    앞 쪽 사례 이름('자동차 도장 붓 교체 공정')을 그대로 붙임."""
    words = re.findall(r"[가-힣]{2,}|[A-Za-z]{2,}|\d+", name)
    if not words:
        return False
    body = re.sub(r"\s+", "", slide.full()).lower()
    return sum(w.lower() in body for w in words) / len(words) >= 0.5


_GENERIC = {"공정", "시스템", "자동화", "라인", "설비", "로봇", "플랫폼", "적용", "이송"}


def title_mismatch(card: dict[str, Any]) -> bool:
    """사례 이름과 설명이 다른 공정을 가리키나(소개서 편집 오류). 실측: 65쪽 제목 '수삽 공정 이송…' 아래 설명은
    수직 원주 용접, 64쪽 제목 '용접 로봇 플랫폼' 아래 설명은 Leak Tester — 모델은 제목을 이름으로 그대로 썼다."""
    if card["kind"] != "reference" or not card.get("summary"):     # 설명이 없으면 판정 근거가 없다(49쪽 오탐)
        return False
    name = _tokens(card["name"]) - _GENERIC
    desc = _tokens(" ".join([card.get("summary") or "", card.get("solution") or "", card.get("how_it_works") or ""]))
    return bool(name and desc) and not (name & desc)


# 낱말이 안 겹쳐도 같은 공정일 수 있다(영문 이름·한글 설명: "Taper Bearing 조립기" / "베어링 구성품…") — 실측 52장 중
# 14장이 걸렸고 실제 편집 오류는 4장. 그래서 낱말 검사는 후보만 고르고, 같은 공정인지는 모델이 판정한다.
_SAME = """아래 사례의 제목과 설명이 같은 공정·설비를 가리키는지 판정하라. 영어/한국어 표기 차이, 약어, 같은 뜻의 다른 말
(예: LM 가이드 = 리니어 레일), 제목이 설명 속 장비·부품·기능 하나를 가리키는 경우는 모두 같은 것으로 본다.
제목과 설명이 서로 다른 제품·공정을 말할 때만 다르다(예: 제목 "배터리 조립라인", 설명 "테이프 부착기").
JSON 으로만: {"same": true|false, "reason": "한 문장"}"""


async def same_equipment(card: dict[str, Any], chat) -> tuple[bool, str]:
    """제목·설명이 같은 설비인가(글로만 판정). 실측: 후보 14장 중 실제 편집 오류 4장을 모두, 오탐 없이 골랐다.

    어느 쪽이 맞는지는 자동으로 정하지 않는다. 사용자와 원본을 대조해 보니(2026-09-30) 64·65쪽 위칸은 제목이,
    62쪽 아래칸은 설명이 잘못 복사됐고 65쪽 아래칸은 둘 다 복사돼 설비 불명이었다 — '설명을 따른다' 는 62쪽을 틀리게
    고쳤고, 쪽 이미지로 판정시키면 12b 가 4건 중 1건만 맞혔다(한 쪽의 다른 칸 그림을 섞어 봄). 그래서 다르면
    '확인 필요' 로 빼고 영업팀 확인을 받는다 — 틀린 지식이 회상에 섞이는 것보다 잠시 빠지는 편이 낫다."""
    body = json.dumps({"title": card["name"], **{k: card.get(k) for k in ("summary", "solution", "how_it_works")}},
                      ensure_ascii=False)
    data = _extract_json(await chat([{"role": "system", "content": _SAME}, {"role": "user", "content": body}],
                                    fmt="json", num_predict=200, temperature=0.0))
    if not isinstance(data, dict):
        return False, "판정 응답 없음"
    return data.get("same") is not False, _clean(data.get("reason"), 200)


def evidence_of(card: dict[str, Any]) -> str:
    if card["kind"] == "reference":
        return EVIDENCE["concept" if card.get("is_concept") else "reference"]
    return EVIDENCE[card["kind"]]


def merge(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """같은 이름의 카드(여러 쪽에 걸친 제품 등)를 합친다: 목록은 합집합, 글은 더 긴 쪽."""
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, list):
            seen = [json.dumps(x, ensure_ascii=False, sort_keys=True) for x in out.get(k) or []]
            out[k] = list(out.get(k) or []) + [x for x in v if json.dumps(x, ensure_ascii=False, sort_keys=True)
                                               not in seen]
        elif isinstance(v, str) and len(v) > len(out.get(k) or ""):
            out[k] = v
        elif isinstance(v, bool):
            out[k] = bool(out.get(k)) and v          # 한 쪽이라도 실제 수행이면 컨셉이 아니다
    out["cues"] = out["cues"][:10]
    return out


def _slide_image(slide: Slide) -> str | None:
    """쪽 이미지(있으면) — 1600px 로 줄여 base64. 글상자만으로는 사진·수치의 짝을 모른다(실측: 49쪽 도어힌지
    그리퍼의 성공·실패 사양이 12b·26b 모두 뒤바뀜 — Ø22 가 실패, Ø25 가 성공인데 반대로 적음)."""
    import base64
    import io as _io

    from PIL import Image

    from .intro_deck import SLIDE_DIR

    path = SLIDE_DIR / f"s{slide.no:03d}.png"
    if not path.exists():
        return None
    im = Image.open(path).convert("RGB")
    if im.width > 1600:
        im = im.resize((1600, round(im.height * 1600 / im.width)))
    buf = _io.BytesIO()
    im.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


async def cards_from_slide(slide: Slide, known: list[str], *, chat=None, image: str | None = None
                           ) -> tuple[list[dict], list[str]]:
    chat = chat or proposal_llm.chat
    user = (f"[이미 만든 카드 이름] {', '.join(known[-80:]) or '(없음)'}\n"
            f"[소개서 {slide.no}쪽 원문]\n{slide.text[:6000]}\n"
            + (f"[{slide.no}쪽 이미지 판독(글상자에 없는 그림 속 글자)]\n{slide.ocr[:4000]}" if slide.ocr else "")
            + ("\n[쪽 이미지 첨부 — 배치를 보고 설명·수치의 짝을 판단]" if image else ""))
    msg: dict = {"role": "user", "content": user}
    if image:
        msg["images"] = [image]
    for attempt in range(2):          # 실측: 71쪽(X30) 이 한 번 JSONDecodeError 로 통째로 빠짐
        try:
            data = _extract_json(await chat([{"role": "system", "content": _SYSTEM}, msg], fmt="json",
                                            num_predict=6000, temperature=0.1))
            break
        except ValueError:
            if attempt:
                raise
    cards, dropped = [], []
    for r in (data.get("cards") if isinstance(data, dict) else None) or []:
        if isinstance(r, dict) and (c := validate(r, slide)):
            cards.append(c.card)
            dropped += c.dropped
    return cards, dropped


async def build_cards(slides: list[Slide] | None = None, *, chat=None) -> tuple[dict[str, dict], list[str]]:
    """전 쪽을 순서대로 읽어 카드 사전(card_key → card)을 만든다."""
    slides = slides if slides is not None else slides_with_ocr()
    cards: dict[str, dict] = {}
    report: list[str] = []
    for s in slides:
        try:
            known = [c["name"] for c in cards.values() if c["kind"] in ("product", "company")]
            got, dropped = await cards_from_slide(s, known, chat=chat,
                                                  image=_slide_image(s) if chat is None else None)
        except Exception as e:  # noqa: BLE001 — 한 쪽 실패가 전체를 막지 않게
            report.append(f"{s.no}쪽 카드 작성 실패: {e.__class__.__name__}")
            print(f"  {s.no:3d} 실패: {e}", flush=True)
            continue
        report += dropped
        for c in got:
            if title_mismatch(c):
                same, why = await same_equipment(c, chat or proposal_llm.chat)
                if not same:
                    c["needs_review"] = True
                    c["source_issues"].append(f"{s.no}쪽 '{c['name']}': 제목과 설명이 다른 설비({why}) — "
                                              "어느 쪽이 맞는지 확인 필요")
                    report.append(f"{s.no}쪽 '{c['name']}' 제목·설명 불일치 → 확인 필요로 제외")
            k = card_key(c["kind"], c["name"])
            if k in cards and c["kind"] == "reference" and not same_case(cards[k], c, s, slides):
                k = f"{k}:{s.no}"         # 이름만 같고 다른 공정 — 합치지 않는다(실측: 38쪽 다품종 이송 + 49쪽 도어힌지)
            cards[k] = merge(cards[k], c) if k in cards else c
        print(f"  {s.no:3d} → {len(got)}장 {[c['name'] for c in got]}", flush=True)
    _link(cards)
    return cards, report


def _tokens(t: str) -> set[str]:
    return set(re.findall(r"[가-힣]{2,}|[A-Za-z]{3,}", t.lower())) - {"reference", "본", "공정", "공정입니다", "클릭"}


def same_case(prev: dict[str, Any], new: dict[str, Any], slide: Slide, slides: list[Slide]) -> bool:
    """같은 이름의 사례를 합쳐도 되나 — 소개서에 거의 그대로 반복된 쪽(47·69쪽 스팟 도킹, 35·77쪽 4족 솔루션)만.

    드문 낱말(3쪽 이하에만 나오는 말)의 Jaccard ≥ 0.6. 실측으로 낱말 겹침 지표는 '한 사례가 두 쪽'(39·40, 31·32)과
    '다른 사례'(62·65 제목 복사 오류, 101·102 전주/미국 공장)를 가르지 못했다. 다른 사례를 합치면 지식이 섞여 잃고,
    한 사례가 두 장으로 남는 것은 중복일 뿐이라 보수적으로 합친다.
    """
    df: dict[str, int] = {}
    for x in slides:
        for t in _tokens(x.text):
            df[t] = df.get(t, 0) + 1
    rare = lambda text: {t for t in _tokens(text) if df.get(t, 0) <= 3}  # noqa: E731
    a = rare(" ".join(x.text for x in slides if x.no in prev.get("slides", [])))
    b = rare(slide.text)
    return bool(a and b) and len(a & b) / len(a | b) >= 0.6


def _link(cards: dict[str, dict]) -> None:
    """사례 ↔ 제품 연결: 사례의 products_used 표기가 제품 카드 이름·제품군과 겹치면 서로의 related 에 넣는다."""
    products = {k: c for k, c in cards.items() if c["kind"] == "product"}
    norm = lambda s: re.sub(r"[\s\W_]+", "", s.lower())  # noqa: E731
    for rk, r in cards.items():
        if r["kind"] != "reference":
            continue
        for used in r.get("products_used") or []:
            u = norm(used)
            for pk, p in products.items():
                names = [norm(p["name"]), norm(p.get("family") or "")]
                if u and any(n and (n in u or u in n) for n in names):
                    r.setdefault("related", [])
                    p.setdefault("related", [])
                    if pk not in r["related"]:
                        r["related"].append(pk)
                    if rk not in p["related"]:
                        p["related"].append(rk)


async def save(cards: dict[str, dict]) -> None:
    from .magbot_catalog import SUPERSEDED_KEYS, apply_family_overrides

    async with SessionLocal() as db:
        await db.execute(text("UPDATE knowledge_cards SET active = FALSE WHERE source_doc = :d"), {"d": SOURCE_DOC})
        for k, c in cards.items():
            await db.execute(text(
                "INSERT INTO knowledge_cards (card_key, kind, name, evidence, source_doc, slides, card, active) "
                "VALUES (:k, :kind, :n, :e, :d, :s, CAST(:c AS jsonb), :a) "
                "ON CONFLICT (card_key) DO UPDATE SET kind = EXCLUDED.kind, name = EXCLUDED.name, "
                "evidence = EXCLUDED.evidence, slides = EXCLUDED.slides, card = EXCLUDED.card, active = EXCLUDED.active, "
                "updated_at = NOW()"),
                {"k": k, "kind": c["kind"], "n": c["name"], "e": evidence_of(c), "d": SOURCE_DOC,
                 "s": sorted(set(c["slides"])), "c": json.dumps(c, ensure_ascii=False),
                 # 확인 필요 카드는 보관만 하고 회상에는 쓰지 않는다. 맥봇 공식 사양으로 대체된 카드는 다시 켜지 않는다.
                 "a": not c.get("needs_review") and k not in SUPERSEDED_KEYS})
        await apply_family_overrides(db)
        await db.commit()


# ── 회상 단서(cues) ─────────────────────────────────────────────────────────

_EXP_CUES = """아래는 우리 회사가 겪은 자동화 경험 카드다. 이 경험을 떠올려야 할 "고객 상황"을 고객이 말할 법한 문장으로
4~8개 써라. 제품명·회사명이 아니라 공정·문제·조건의 말로. JSON 으로만: {"cues": ["..."]}"""


async def _experience_cues(card: dict[str, Any], *, chat=None) -> list[str]:
    chat = chat or proposal_llm.chat
    body = json.dumps({k: card.get(k) for k in ("title", "process", "problem", "solution", "key_ideas",
                                                "applies_when")}, ensure_ascii=False)
    data = _extract_json(await chat([{"role": "system", "content": _EXP_CUES}, {"role": "user", "content": body}],
                                    fmt="json", num_predict=800, temperature=0.1))
    return _list(data.get("cues") if isinstance(data, dict) else [], 120, 8)


async def refresh_experience_cues(card_id: int, card: dict[str, Any], *, chat=None) -> int:
    """경험 카드 한 장의 회상 단서만 다시 만든다(확정 제안서가 새 경험 카드가 될 때 — 전체 재생성 없이).
    04 의 proposal_project/experience.py 가 부른다. 반환: 넣은 단서 수."""
    from ..rag import embed_batch

    cues = await _experience_cues(card, chat=chat)
    cues.append(_clean(f"{card.get('title')}: {card.get('process')}", 200))
    vecs = await embed_batch(cues)
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM knowledge_cues WHERE card_table = 'experience_cards' AND card_id = :c"),
                         {"c": card_id})
        for q, v in zip(cues, vecs):
            await db.execute(text(
                "INSERT INTO knowledge_cues (card_table, card_id, cue, embedding) "
                "VALUES ('experience_cards', :c, :q, CAST(:v AS vector)) ON CONFLICT DO NOTHING"),
                {"c": card_id, "q": q, "v": "[" + ",".join(f"{x:.6f}" for x in v) + "]"})
        await db.commit()
    return len(cues)


async def rebuild_cues(*, include_experience: bool = True) -> int:
    """knowledge_cards(활성) + experience_cards(활성) 의 단서를 다시 임베딩한다."""
    from ..rag import embed_batch

    async with SessionLocal() as db:
        kc = (await db.execute(text("SELECT id, card FROM knowledge_cards WHERE active"))).mappings().all()
        ec = (await db.execute(text("SELECT id, card FROM experience_cards WHERE active"))).mappings().all() \
            if include_experience else []
    rows: list[tuple[str, int, str]] = []
    for r in kc:
        c = r["card"]
        # 단서 + 이름·요약 한 줄(단서가 빈약해도 이름으로는 떠오르게)
        rows += [("knowledge_cards", r["id"], q) for q in (c.get("cues") or [])]
        rows.append(("knowledge_cards", r["id"], _clean(f"{c['name']}: {c.get('summary') or c.get('process')}", 200)))
    for r in ec:
        for q in await _experience_cues(r["card"]):
            rows.append(("experience_cards", r["id"], q))
        rows.append(("experience_cards", r["id"], _clean(f"{r['card'].get('title')}: {r['card'].get('process')}", 200)))
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM knowledge_cues WHERE card_table = ANY(:t)"),
                         {"t": ["knowledge_cards"] + (["experience_cards"] if include_experience else [])})
        for i in range(0, len(rows), 16):
            batch = rows[i:i + 16]
            vecs = await embed_batch([q for _, _, q in batch])
            for (t, cid, q), v in zip(batch, vecs):
                await db.execute(text(
                    "INSERT INTO knowledge_cues (card_table, card_id, cue, embedding) "
                    "VALUES (:t, :c, :q, CAST(:v AS vector)) ON CONFLICT DO NOTHING"),
                    {"t": t, "c": cid, "q": q, "v": "[" + ",".join(f"{x:.6f}" for x in v) + "]"})
        await db.commit()
    return len(rows)


# ── 사람이 읽는 정리 ────────────────────────────────────────────────────────

def card_markdown(c: dict[str, Any], evidence: str) -> str:
    lines = [f"### [{evidence}] {c['name']}" + (f" · {c['family']}" if c.get("family") else ""),
             f"- 출처: {SOURCE_DOC} {', '.join(str(s) for s in sorted(set(c['slides'])))}쪽"]
    if c.get("summary"):
        lines.append(f"- 요약: {c['summary']}")
    if c.get("how_it_works"):
        lines.append(f"- 원리·구성: {c['how_it_works']}")
    for label, key in (("강점", "strengths"), ("한계·조건", "limits"), ("쓰면 안 되는 상황", "not_when")):
        lines += [f"- {label}: {x}" for x in c.get(key) or []]
    if c.get("specs"):
        lines.append("- 사양: " + "; ".join(f"{s['model'] + ' ' if s['model'] else ''}{s['item']} {s['value']}"
                                            + (" (이미지 판독)" if s["source"] == "이미지 판독" else "")
                                            for s in c["specs"][:30]))
    for label, key in (("공정", "process"), ("문제", "problem"), ("해법", "solution"), ("효과", "effect"),
                       ("다시 쓸 조건", "applies_when")):
        if c.get(key):
            lines.append(f"- {label}: {c[key]}")
    lines += [f"- 핵심 아이디어: {x}" for x in c.get("key_ideas") or []]
    lines += [f"- 주의점: {x}" for x in c.get("cautions") or []]
    lines += [f"- 해 보니: {x}" for x in c.get("lessons") or []]
    if c.get("products_used"):
        lines.append("- 쓴 제품: " + ", ".join(c["products_used"]))
    if c.get("customers"):
        lines.append("- 고객: " + ", ".join(c["customers"]))
    if c.get("items"):
        lines.append("- 목록: " + " / ".join(c["items"]))
    lines += [f"- 떠올릴 상황: {x}" for x in c.get("cues") or []]
    return "\n".join(lines)


def write_markdown(cards: dict[str, dict], report: list[str]) -> Path:
    out = DOCS / "company_knowledge"
    out.mkdir(parents=True, exist_ok=True)
    md = [f"# 회사 지식 카드 — {SOURCE_DOC}", "",
          f"총 {len(cards)}장. 원문은 RAG(documents, source_path={SOURCE_PATH})에도 쪽 단위로 적재됨.", ""]
    for kind, title in (("company", "회사 개요"), ("product", "제품"), ("reference", "적용 사례"),
                        ("capability", "역량·실적 목록")):
        group = [c for c in cards.values() if c["kind"] == kind and not c.get("needs_review")]
        if group:
            md += [f"## {title} ({len(group)}장)", ""] + [card_markdown(c, evidence_of(c)) + "\n" for c in group]
    review = [c for c in cards.values() if c.get("needs_review")]
    if review:
        md += [f"## 확인 필요로 뺀 카드 ({len(review)}장 — 회상·제안서에 쓰지 않음)", ""] + \
              [f"- {', '.join(str(x) for x in c['slides'])}쪽 '{c['name']}'" for c in review] + [""]
    issues = [i for c in cards.values() for i in c.get("source_issues") or []]
    if issues:
        md += ["## 소개서 점검 필요(편집 오류로 보이는 곳)", ""] + [f"- {i}" for i in issues] + [""]
    if report:
        md += ["## 검증에서 고치거나 버린 내용", ""] + [f"- {r}" for r in report]
    path = out / "cards.md"
    path.write_text("\n".join(md), encoding="utf-8")
    return path


async def build() -> None:
    cards, report = await build_cards()
    await save(cards)
    path = write_markdown(cards, report)
    print(f"카드 {len(cards)}장 저장, 버린 항목 {len(report)}건 → {path}", flush=True)


async def export() -> None:
    """DB 의 카드(비활성 포함)로 정리 문서만 다시 쓴다 — 카드를 손으로 끄거나 고친 뒤 문서를 맞출 때."""
    async with SessionLocal() as db:
        rows = (await db.execute(text("SELECT card_key, active, card FROM knowledge_cards WHERE source_doc = :d "
                                      "AND (active OR card ? 'needs_review' OR jsonb_array_length("
                                      "COALESCE(card->'source_issues', '[]'::jsonb)) > 0) ORDER BY id"),
                                 {"d": SOURCE_DOC})).mappings().all()
    cards = {}
    for r in rows:
        c = dict(r["card"])
        if not r["active"]:
            c["needs_review"] = True
        cards[r["card_key"]] = c
    print(f"정리 문서 → {write_markdown(cards, [])}", flush=True)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "build":
        asyncio.run(build())
    elif cmd == "export":
        asyncio.run(export())
    elif cmd == "cues":
        print(f"단서 {asyncio.run(rebuild_cues())}건", flush=True)
    else:
        print(__doc__)
