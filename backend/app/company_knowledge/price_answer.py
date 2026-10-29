# -*- coding: utf-8 -*-
"""채팅에서 제품 단가 바로 답하기 — 영업부만(사용자 2026-10-28).

'TCV3 가격 얼마야?'·'MG10 단가'·'TCV1 마스터 대리점가'를 단가 DB(product_prices, 사용 중인 최신 단가표)에서 그대로 답한다.
가격은 내부 정보라 영업부(또는 전체 권한)만 — 다른 부서가 물으면 AI 로 넘기지 않고 '영업부만' 이라고 끊는다(짐작해 답하지 않게).
0원·빈 칸은 '가격 미정'(무상이 아님). 금액은 공급가액(부가세 별도). 모델을 못 찾으면 None — 다음 흐름(AI)으로.
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .plain_text import hide_codes
from .product_prices import won_text
from .spec_answer import _hit, _norm, _spaced, photo_line, photos

DENIED = "제품 단가는 영업부만 볼 수 있습니다. 필요하시면 영업부에 문의해 주세요."

_PRICE = re.compile(r"가격|단가|판매가|대리점가|고객가|소비자가|얼마|금액|price", re.I)
# 영업 건·서류 이야기는 영업 데이터 답·건 이동으로, 사양(무게 얼마야)은 사양 답으로
_NOT = re.compile(r"견적서|명세서|입금|출하|수주|발주|할인\s*(해|율)|무게|중량|가반|하중|페이로드|크기|사이즈|도달|몇\s*(kg|mm)", re.I)
# 물은 품목 → 단가표 품목 이름
ITEMS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"마스터|master|로봇\s*측", re.I), "MASTER T.C"),
    (re.compile(r"툴\s*플레이트|tool\s*plate|\bT\.?P\b|툴\s*측", re.I), "T.P"),
    (re.compile(r"PPM|PPF|포고", re.I), "PPM/PPF"),
    (re.compile(r"PMM|PMF|공압\s*(모듈|커플러)", re.I), "PMM/PMF"),
    (re.compile(r"케이블\s*0\.?3|cable\s*0\.?3|0\.3\s*m", re.I), "Cable 0.3m"),
    (re.compile(r"케이블\s*4|cable\s*4|4\s*m", re.I), "Cable 4m"),
    (re.compile(r"\bIB\b|브라켓|bracket", re.I), "IB"),
    (re.compile(r"컨트롤\s*박스|control\s*box|controlbox", re.I), "CONTROLBOX"),
]


def detect(messages: list[dict]) -> str | None:
    if not messages or messages[-1].get("role") != "user":
        return None
    t = str(messages[-1].get("content") or "").strip()
    if not t or len(t) > 200 or not _PRICE.search(t) or _NOT.search(t):
        return None
    return t


async def _rows(db: AsyncSession) -> list[dict[str, Any]]:
    rs = (await db.execute(text("SELECT * FROM product_prices WHERE active ORDER BY id"))).mappings().all()
    return [dict(r) for r in rs]


def _models(rows: list[dict], t: str) -> list[str]:
    nt, st = _norm(t), _spaced(t)
    names = list(dict.fromkeys(r["model"] for r in rows))
    hit = [m for m in names if _hit(_norm(m), nt) or _hit(_spaced(m.replace("_", " ")), st)]
    if hit:
        return hit
    # 'MTC 가격' → MTC_L·MTC_S 처럼 앞부분만 말한 경우
    tokens = {_norm(x) for x in re.findall(r"[A-Za-z]{2,}\d*", t)}
    return [m for m in names for k in tokens if len(k) >= 3 and _norm(m).startswith(k)]


def _note(v: str) -> str:
    """단가 비고 → 화면 문장. 규칙 번호(v0.8 C16 등)는 화면에 내지 않는다."""
    return re.sub(r"\s*\(v\d+(?:\.\d+)*\)", "", hide_codes(v)).strip()


def _won(v: Any) -> int | None:
    return int(round(float(v))) if v is not None else None


async def answer(db: AsyncSession, t: str, *, allowed: bool) -> str | None:
    """allowed = 영업부(또는 전체 권한). 모델을 못 찾으면 None. 영업부가 아니면 모델을 찾았을 때만 DENIED."""
    rows = await _rows(db)
    models = _models(rows, t)
    if not models:
        return None
    if not allowed:
        return DENIED
    items = [name for pat, name in ITEMS if pat.search(t)]
    dealer = bool(re.search(r"대리점", t))
    out = ["단가 DB에서 찾은 값입니다 (공급가액 · 부가세 별도)."]
    # 모델 사진 — 같은 이름의 제품 카드(맥봇 공식 사양 카드)에 승인된 사진이 있으면
    cards = dict((await db.execute(text(
        "SELECT lower(name), id FROM knowledge_cards WHERE kind = 'product' AND active AND lower(name) = ANY(:n)"),
        {"n": [m.lower() for m in models[:6]]})).all())
    pics = await photos(db, list(cards.values()))
    any_unset = False
    for m in models[:6]:
        mine = [r for r in rows if r["model"] == m]
        pick = [r for r in mine if r["item"] in items] if items else mine
        out.append(f"\n■ {m}" + (f" ({mine[0]['category']})" if mine and mine[0].get("category") else ""))
        out += photo_line(pics.get(cards.get(m.lower(), -1)))
        if not pick:
            out.append(f"- {', '.join(items)}: 이 모델의 단가표에 없는 품목입니다 (있는 품목: {', '.join(r['item'] for r in mine)})")
            continue
        for r in pick:
            cp, dp = _won(r["customer_price"]), _won(r["dealer_price"])
            unset = r.get("price_status") == "unset"
            any_unset = any_unset or unset or cp is None
            line = f"- {r['item']}: 고객가 {'가격 미정' if unset or cp is None else won_text(cp)}"
            if dp is not None or dealer:
                line += f" · 대리점가 {'가격 미정' if unset or dp is None else won_text(dp)}"
            out.append(line)
            if r.get("note"):
                out.append(f"  ({_note(r['note'])})")
    if len(models) > 6:
        out.append(f"\n… 외 {len(models) - 6}개 모델 — 모델 이름을 더 정확히 말씀해 주세요.")
    pl = rows[0]
    out.append(f"\n근거: {pl['price_list']}" + (f" (기준일 {pl['effective_date']})" if pl.get("effective_date") else ""))
    if any_unset:
        out.append("'가격 미정'은 단가표의 0원·빈 칸입니다 — 무상이 아니며 견적에 그대로 넣지 않습니다.")
    return "\n".join(out)
