"""회상 엔진 회귀 평가.

실행:
    # 연상 단계(상황 분석 LLM + 단서 유사도 점수) — 카드·단서 재생성, recall.py 점수 변경 직후
    docker exec und_cortex_backend python -m app.eval.recall_runner

    # 되짚기까지(hits/also, fit 판정) — brain 은 local(사내 gemma) 또는 gpt(Codex, 가명 관문 경유)
    docker exec und_cortex_backend python -m app.eval.recall_runner --reflect local

기준은 recall_dataset.py 머리말. 실패가 하나라도 있으면 종료 코드 1.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from app.company_knowledge import recall as rc
from app.database import SessionLocal
from app.eval.recall_dataset import CASES

TOP_N = 8          # recall() 이 되짚기에 상세로 넘기는 카드 수(top_k)와 같게


def _find(titles: list[str], needle: str) -> int:
    """제목 조각이 몇 번째(1부터)에 있나. 없으면 0."""
    return next((i for i, t in enumerate(titles, 1) if needle.lower() in t.lower()), 0)


async def associate(situation: str) -> list[rc.Card]:
    """recall() 의 1~3단계(분석·연상·번짐)만 — 되짚기 없이 연상 순위."""
    analysis = await rc.analyze(situation)
    signals = [situation[:500]] + analysis["cues"]
    aliases = [str(x).strip() for x in analysis.get("aliases") or [] if str(x).strip()]
    if aliases:
        signals.append(f"{analysis.get('objects') or ''} ({', '.join(aliases[:6])}) 을 다루는 공정")
    async with SessionLocal() as db:
        cards = await rc._load_cards(db)
        matched = rc.aggregate(await rc._matches(db, signals))
    for key, (score, _) in matched.items():
        if key in cards:
            cards[key].score = score
    terms = rc.key_terms(analysis)
    for c in cards.values():
        if c.score > 0 and terms:
            c.score += rc.term_bonus(terms, c.corpus())
    rc.spread(cards, rc._knowledge_keys(cards))
    return sorted((c for c in cards.values() if c.score > 0), key=lambda c: -c.score)


async def main(reflect: str | None) -> int:
    fails = 0
    for case in CASES:
        errs: list[str] = []
        ranked = await associate(case["situation"])
        titles = [c.title for c in ranked]
        for need in case.get("top", []):
            pos = _find(titles, need)
            if not pos or pos > TOP_N:
                errs.append(f"연상 상위 {TOP_N} 밖: '{need}' (순위 {pos or '없음'})")
        if reflect:
            r = await rc.recall(case["situation"], brain=reflect)
            seen = [h["title"] for h in r["hits"]] + r["also"]
            for need in case.get("top", []) + case.get("recall", []):
                if not _find(seen, need):
                    errs.append(f"되짚기 hits/also 에 없음: '{need}'")
            for bad in case.get("never_fit", []):
                for h in r["hits"]:
                    if bad.lower() in h["title"].lower() and h["fit"] == "맞음":
                        errs.append(f"맞지 않아야 할 카드가 '맞음': {h['title']}")
        status = "PASS" if not errs else "FAIL"
        fails += bool(errs)
        print(f"[{status}] {case['id']}: 상위 {', '.join(t[:18] for t in titles[:TOP_N])}")
        for e in errs:
            print(f"    ✗ {e}")
    print(f"\n{len(CASES) - fails}/{len(CASES)} 통과")
    return 1 if fails else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reflect", choices=("local", "gpt"), default=None)
    sys.exit(asyncio.run(main(ap.parse_args().reflect)))
