# -*- coding: utf-8 -*-
"""채팅에서 제품 사양 바로 답하기(사용자 2026-10-28).

'TCV1 페이로드 몇 kg이야?'·'MG10 무게'·'UR5e 도달거리'를 AI 가 회사 소개서 검색으로 답하지 않고(소개서엔 편집 오류가 있고
'확인 필요'·적용 조건이 빠진다) DB 를 바로 읽어 답한다 — /v1/generate 의 결정론 fast-path(대여 목록·영업 데이터와 같은 방식).
- 맥봇·회사 제품: 지식 카드(knowledge_cards kind=product, 사용 중인 것) — 공식 스펙 요약표로 다시 세팅한 카드(magbot_catalog)가 기준,
  그 밖의 제품(AMR 등)은 소개서에서 뽑은 카드 — 답에 근거 자료를 그대로 적는다.
- 타사 협동로봇: robot_specs(기술영업팀 6축 로봇 비교표). '확인 필요' 칸은 그대로 '확인 필요'.
모델 이름을 못 찾으면 None — AI 대화로(지어내지 않는다). 추천·견적·가격 질문은 가로채지 않는다.
"""
from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .robot_specs import DECK_NAME as ROBOT_DECK, UNVERIFIED, _fmt

MAX_MODELS = 6

# 사양을 묻는 말 — 이 말이 있어야 가로챈다
_SPEC = re.compile(r"페이로드|가반|하중|payload|무게|중량|weight|크기|사이즈|치수|size|\bIP\s*\d*|IP\s*등급|방수|방진|온도|습도|"
                   r"전기|전압|전류|전원|사양|스펙|spec|도달|리치|reach|반복\s*정밀|정밀도|센서|IoT|몇\s*(kg|킬로|키로|mm|g\b)", re.I)
# 추천·견적·가격·영업 건 이야기 — 여기서 답하지 않는다
_NOT = re.compile(r"추천|맞는|어울|골라|고르|선정|견적|명세서|출하|입금|수주|발주|단가|가격|비용|얼마에|할인|재고|납기|"
                  r"사진|이미지|그려|만들|제안서|어떻게\s*(써|사용|설치|연결)|방법", re.I)

# 물은 항목 → 카드 항목 이름에서 찾을 말
ASPECTS: list[tuple[str, re.Pattern[str], re.Pattern[str]]] = [
    ("가반하중", re.compile(r"페이로드|가반|하중|payload|몇\s*(kg|킬로|키로)\s*(까지|들|버티)", re.I), re.compile(r"가반|payload", re.I)),
    ("무게", re.compile(r"무게|중량|weight|몇\s*(g|그램)\b", re.I), re.compile(r"무게|중량|weight", re.I)),
    ("크기", re.compile(r"크기|사이즈|치수|size|몇\s*mm", re.I), re.compile(r"사이즈|크기|size|치수|dimension", re.I)),
    ("IP 등급", re.compile(r"\bIP\s*\d*|IP\s*등급|방수|방진", re.I), re.compile(r"\bIP|ingress", re.I)),
    ("온도·습도", re.compile(r"온도|습도", re.I), re.compile(r"온도|습도|temp|humid", re.I)),
    ("전기 사양", re.compile(r"전기|전압|전류|전원", re.I), re.compile(r"전기|전압|전류|voltage|current|power", re.I)),
    ("센서", re.compile(r"센서", re.I), re.compile(r"센서|sensor", re.I)),
    ("IoT", re.compile(r"IoT", re.I), re.compile(r"IoT", re.I)),
    ("반복 정밀도", re.compile(r"반복|정밀", re.I), re.compile(r"정밀|repeat", re.I)),
    ("도달거리", re.compile(r"도달|리치|reach", re.I), re.compile(r"도달|reach", re.I)),
]
ROBOT_FIELDS = {"가반하중": ("가반", "payload_kg", "kg", ""), "도달거리": ("도달", "reach_mm", "mm", ""),
                "반복 정밀도": ("반복", "repeatability_mm", "mm", "±"), "무게": ("중량", "weight_kg", "kg", "")}


def detect(messages: list[dict]) -> str | None:
    """사양을 묻는 말이면 그 말(모델은 answer 에서 DB 로 찾는다) · 아니면 None."""
    if not messages or messages[-1].get("role") != "user":
        return None
    t = str(messages[-1].get("content") or "").strip()
    if not t or len(t) > 200 or not _SPEC.search(t) or _NOT.search(t):
        return None
    return t


def _norm(s: str) -> str:
    return re.sub(r"[\s\-_.·()/\[\]]", "", str(s or "").lower())


def _spaced(s: str) -> str:
    """띄어쓰기는 남긴 정규화 — 'TCV1 IP 등급'이 'tcv1ip…'로 붙어 모델 끝을 못 찾는 일을 막는다."""
    return re.sub(r"\s+", " ", re.sub(r"[\-_.·()/\[\]]", "", str(s or "").lower())).strip()


def _hit(key: str, nt: str) -> bool:
    """정규화한 말 nt 안에 모델 이름 key 가 따로 있는지 — MG10 이 MG100 에, TCV1 이 TCV12 에 걸리지 않게."""
    return len(key) >= 3 and re.search(rf"(?<![0-9a-z]){re.escape(key)}(?![0-9a-z])", nt) is not None


def asked(t: str) -> list[str]:
    return [name for name, ask, _ in ASPECTS if ask.search(t)]


async def _cards(db: AsyncSession) -> list[dict[str, Any]]:
    rs = (await db.execute(text(
        "SELECT id, name, source_doc, evidence, card FROM knowledge_cards WHERE kind = 'product' AND active"))).mappings().all()
    out = []
    for r in rs:
        card = r["card"] if isinstance(r["card"], dict) else json.loads(r["card"] or "{}")
        names = [r["name"], re.sub(r"\s*\(.*\)$", "", r["name"]), *(card.get("aliases") or [])]
        m = re.search(r"\(([A-Za-z]{2,6})\)$", r["name"])           # 'Pogo Pin Female (PPF)' → PPF
        if m:
            names.append(m[1])
        out.append({"id": r["id"], "name": r["name"], "source": r["source_doc"] or r["evidence"] or "", "card": card,
                    "keys": {_norm(n) for n in names if n}, "spaced": {_spaced(n) for n in names if n}})
    return out


async def _robots(db: AsyncSession) -> list[dict[str, Any]]:
    rs = (await db.execute(text("SELECT * FROM robot_specs WHERE active ORDER BY maker, model"))).mappings().all()
    return [dict(r) for r in rs]


def _find_cards(cards: list[dict], t: str) -> list[dict]:
    nt, st = _norm(t), _spaced(t)
    hits = [c for c in cards if any(_hit(k, nt) for k in c["keys"]) or any(_hit(k, st) for k in c["spaced"])]
    # 'MGM3D' 와 그 다른 이름처럼 같은 카드가 여러 번 걸리는 일은 없지만, 짧은 이름이 긴 이름의 조각이면 긴 쪽만
    names = [_norm(c["name"]) for c in hits]
    return [c for c, n in zip(hits, names) if not any(n != o and n in o for o in names)]


def _find_robots(robots: list[dict], t: str) -> list[dict]:
    """'UR5e'·'CR10A' 처럼 모델을 그대로, 또는 'UR5' 처럼 앞부분만 말해도(그 이름으로 시작하는 모델들)."""
    tokens = {_norm(x) for x in re.findall(r"[A-Za-z]+[\s\-]?\d+[A-Za-z0-9\-]*", t)}
    exact = [r for r in robots if _norm(r["model"]) in tokens or _norm(r["maker"] + r["model"]) in tokens]
    if exact:
        return exact
    return [r for r in robots for k in tokens if len(k) >= 3 and _norm(r["model"]).startswith(k)]


def _is(aspect: str, item: str) -> bool:
    """사양 항목 이름이 그 물음 항목인지 — 'Payload (Weight Capability)'는 무게가 아니라 가반하중."""
    pat = next(p for n, _, p in ASPECTS if n == aspect)
    if aspect != "가반하중" and _is("가반하중", item):
        return False
    return bool(pat.search(item))


async def photos(db: AsyncSession, card_ids: list[int]) -> dict[int, int]:
    """제품 카드 → 승인된 사진 하나(먼저 올린 것). 채팅 답에 '사진: /api/product-images/file/N' 로 붙여 화면이 그린다."""
    if not card_ids:
        return {}
    rs = (await db.execute(text(
        "SELECT DISTINCT ON (card_id) card_id, id FROM product_images WHERE card_id = ANY(:c) AND active "
        "AND review_status = 'approved' ORDER BY card_id, id"), {"c": card_ids})).all()
    return {r.card_id: r.id for r in rs}


def photo_line(image_id: int | None) -> list[str]:
    return [f"사진: /api/product-images/file/{image_id}"] if image_id else []


def _card_lines(c: dict, want: list[str], photo: int | None = None) -> list[str]:
    card = c["card"]
    specs = card.get("specs") or []
    fam = card.get("family") or ""
    head = f"■ {c['name']}" + (f" ({fam})" if fam else "")
    out = [head, *photo_line(photo)]
    if want:
        missing = []
        for name in want:
            got = [s for s in specs if _is(name, str(s.get("item") or ""))]
            if got:
                out += [f"- {s['item']}: {s['value']}" for s in got]
            else:
                missing.append(name)
        if missing:
            out.append(f"- {', '.join(missing)}: 자료에 없습니다")
            if len(missing) == len(want) and specs:
                out.append("  (자료에 있는 사양: " + ", ".join(dict.fromkeys(str(s.get("item")) for s in specs)) + ")")
    elif specs:
        out += [f"- {s['item']}: {s['value']}" for s in specs]
    else:
        out.append("- 등록된 수치 사양이 없습니다" + (f" — {card['summary']}" if card.get("summary") else ""))
    cond = str(card.get("applies_when") or "")
    # 적용 조건이 사양 값에 이미 있으면(액세서리 '적용 제품') 다시 적지 않는다
    if cond and not any(cond.split(":")[-1].strip() in str(s.get("value") or "") for s in specs):
        out.append(f"- 적용 조건: {cond}")
    for lim in card.get("limits") or []:
        if lim.startswith("적용 조건"):              # 위 적용 조건과 같은 말
            continue
        # 물은 항목이 있으면 그 항목의 '확인 필요·자료 없음'만
        if want and not any(_is(n, lim) for n in want):
            continue
        out.append(f"- ⚠ {lim}")
    out.append(f"근거: {c['source']}")
    return out


def _robot_lines(r: dict, want: list[str]) -> list[str]:
    out = [f"■ {r['maker']} {r['model']} (타사 협동로봇" + (f" · {r['payload_class']}" if r.get("payload_class") else "") + ")"]
    fields = [k for k in want if k in ROBOT_FIELDS or k == "IP 등급"] or [*ROBOT_FIELDS, "IP 등급"]
    for k in fields:
        if k == "IP 등급":
            out.append(f"- IP 등급: {r.get('ip_rating') or UNVERIFIED}")
            continue
        label, col, unit, pm = ROBOT_FIELDS[k]
        v = r.get(col)
        out.append(f"- {k}: {_fmt(float(v), unit, pm) if v is not None else UNVERIFIED}")
    other = [k for k in want if k not in ROBOT_FIELDS and k != "IP 등급"]
    if other:
        out.append(f"- {', '.join(other)}: 비교표에 없는 항목입니다")
    out.append(f"근거: 기술영업팀 6축 로봇 비교표 ({ROBOT_DECK})")
    return out


async def answer(db: AsyncSession, t: str) -> str | None:
    """모델을 DB 에서 찾아 물은 사양(없으면 전체)을 그대로. 모델을 못 찾으면 None."""
    cards = _find_cards(await _cards(db), t)
    robots = _find_robots(await _robots(db), t)
    if not cards and not robots:
        return None
    want = asked(t)
    pics = await photos(db, [c["id"] for c in cards[:MAX_MODELS]])
    blocks: list[list[str]] = [_card_lines(c, want, pics.get(c["id"])) for c in cards[:MAX_MODELS]]
    blocks += [_robot_lines(r, want) for r in robots[:max(0, MAX_MODELS - len(blocks))]]
    head = "제품 사양 DB에서 찾은 값입니다" + (f" — {', '.join(want)}" if want else "") + "."
    more = len(cards) + len(robots) - len(blocks)
    tail = [f"\n… 외 {more}개 모델 — 모델 이름을 더 정확히 말씀해 주시면 좁힙니다."] if more > 0 else []
    return "\n".join([head, *("\n" + "\n".join(b) for b in blocks), *tail])
