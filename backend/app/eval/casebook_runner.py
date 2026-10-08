"""컨설팅 사례집 검색·관련성 판정 회귀 평가.

실행:
    # 검색 계층만 (결정론, 빠름) — 사례집 재적재·casebook.py 변경 직후 필수
    docker exec und_cortex_backend python -m app.eval.casebook_runner

    # 관련성 판정(LLM)까지 — proposal/case_guard.py·제안서 모델 변경 직후
    docker exec und_cortex_backend python -m app.eval.casebook_runner --with-llm

통과 기준은 casebook_dataset.py 머리말 참조. 실패가 하나라도 있으면 종료 코드 1.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from app.casebook import search_cases
from app.database import SessionLocal
from app.eval.casebook_dataset import CASES
from app.proposal import case_guard
from app.proposal.service import _CANDIDATES, case_query


def _check_search(case: dict, refs: list[str]) -> list[str]:
    errs = []
    if case.get("expect_empty"):
        if refs:
            errs.append(f"사례집 밖 공정인데 검색됨 {refs}")
        return errs
    if case.get("top1_any") and (not refs or refs[0] not in case["top1_any"]):
        errs.append(f"1순위 {refs[:1]} ∉ {case['top1_any']}")
    bad = [r for r in refs if r in case.get("never", [])]
    if bad:
        errs.append(f"있으면 안 되는 사례 {bad}")
    return errs


def _check_judge(case: dict, kept: list[str], ok: bool) -> list[str]:
    errs = []
    if not ok:
        errs.append("판정 호출 실패")
    if case.get("judge_keep_any") and not set(kept) & set(case["judge_keep_any"]):
        errs.append(f"판정 후 {case['judge_keep_any']} 중 남은 것 없음 (남음 {kept})")
    if case.get("judge_empty") and kept:
        errs.append(f"사례집 밖 공정인데 판정 후 사례가 남음 {kept}")
    bad = [r for r in kept if r in case.get("judge_never", [])]
    if bad:
        errs.append(f"판정이 걸러야 할 사례를 남김 {bad}")
    return errs


async def main() -> int:
    ap = argparse.ArgumentParser(description="사례집 검색·관련성 판정 회귀 평가")
    ap.add_argument("--with-llm", action="store_true", help="관련성 판정(case_guard)까지 검사")
    ap.add_argument("--only", default=None, help="name 에 이 문자열이 든 케이스만")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    cases = [c for c in CASES if not args.only or args.only in c["name"]]
    n_fail = 0
    async with SessionLocal() as db:
        for c in cases:
            hits = await search_cases(db, case_query(c["query"]), top_cases=_CANDIDATES)
            refs = [h.ref for h in hits]
            errs = _check_search(c, refs)
            line = f"search {[f'{h.ref} {h.score:.3f}' for h in hits]}"
            if args.with_llm and hits:
                kept, dropped, ok = await case_guard.judge(c["query"], hits)
                kept_refs = [v.hit.ref for v in kept]
                errs += _check_judge(c, kept_refs, ok)
                line += f"\n        judge keep={[(v.hit.ref, v.verdict) for v in kept]} " \
                        f"drop={[v.hit.ref for v in dropped]}"
            n_fail += bool(errs)
            print(f"{'✓' if not errs else '✗'} {c['name']:<24} {line}")
            for e in errs:
                print(f"        ! {e}")
    print("─" * 96)
    print(f"{len(cases) - n_fail}/{len(cases)} 통과" + (" (관련성 판정 포함)" if args.with_llm else ""))
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
