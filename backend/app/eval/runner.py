"""RAG 회귀 평가 runner.

실행:
    # 검색 정확도만 (빠름, vLLM 호출 없음)
    docker exec und_cortex_backend python -m app.eval.runner

    # 답변까지 vLLM 으로 생성하여 채점 (느림, 케이스당 5~15초)
    docker exec und_cortex_backend python -m app.eval.runner --with-llm

    # 실패 케이스 답변 본문도 출력
    docker exec und_cortex_backend python -m app.eval.runner --with-llm --verbose

언제 돌리나:
    1) 새 사칙 chunk 를 ingest 한 직후 — 어떤 질의가 깨졌는지 즉시 확인.
    2) rag.py / vllm_client.py / build_context_block / prompt 변경 직후 — 회귀 검출.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict

from app.database import SessionLocal
from app.eval.dataset import EVAL_CASES
from app.rag import RetrievedChunk, build_context_block, search
from app.llm import stream_chat


# Eval system prompt — 실제 채팅(frontend lib/agents/router.ts 의 GLOBAL_PREAMBLE)과 동일
# 기조로 맞춰, eval 이 실제 사용 품질을 근사 측정하도록 한다.
# ⚠ router.ts 의 GLOBAL_PREAMBLE 을 수정하면 이 문구도 함께 갱신할 것(검증 정합성).
# build_context_block 가 STRICT RULES + chunk 를 뒤에 직렬화하므로 여기선 핵심 기조만.
_EVAL_SYS_PREFIX = (
    "당신은 한국어 전용 사내 AI 어시스턴트 'UND Cortex' 입니다. 항상 한국어로 답합니다.\n"
    "사내 자료 발췌가 있으면 그 내용을 근거로 답합니다. 질문에 답하는 정보가 발췌에 있으면\n"
    "반드시 그 내용으로 답하고, '자료에 없다'며 회피하지 않습니다. 발췌에 없는 절차·수치·\n"
    "인물·날짜는 지어내지 않습니다. 수식은 LaTeX 없이 한국어 평문 산식으로 표기합니다.\n\n"
)


async def _generate(query: str, ctx: str, history: list[dict] | None = None) -> str:
    """단일 질의에 대해 vLLM 응답을 끝까지 모아 한 문자열로 반환.
    temperature 를 낮춰 sampling variance 를 줄여 회귀 평가 안정성을 높인다.
    seed 도 고정해 동일 query 는 항상 같은 답이 나오도록.
    history 가 있으면 이전 대화 컨텍스트를 함께 전달 (history-poisoning 시뮬레이션).
    """
    sys = _EVAL_SYS_PREFIX + ctx
    msgs: list[dict] = []
    if history:
        msgs.extend(history)
    msgs.append({"role": "user", "content": query})
    parts: list[str] = []
    async for delta in stream_chat(
        system_prompt=sys,
        messages=msgs,
        temperature=0.1,
        top_p=0.9,
        seed=42,
    ):
        parts.append(delta)
    return "".join(parts).strip()


def _retrieval_ok(case: dict, top1: RetrievedChunk) -> bool:
    """검색 정답 검증.
    expected_content_substring + expected_label_contains 둘 다 지정되면 **AND**(둘 다 만족).
    하나만 지정되면 그것만 검사. 둘 다 None 이면 채점 면제 (negative 케이스).
    """
    cs = case.get("expected_content_substring")
    el = case.get("expected_label_contains")
    if cs is None and el is None:
        return True
    if cs is not None and cs not in top1.content:
        return False
    if el is not None and el not in top1.source_label:
        return False
    return True


_re = __import__("re")
_WS_RE = _re.compile(r"\s+")
# 한국어 금액 표기("1만", "1만 2천", "3만") → 아라비아 숫자로 정규화. 모델이 사칙의
# "10,000" 을 "1만원" 처럼 한국어로 풀어 답해도 정답으로 인정한다(채점 false negative 방지).
_MAN_RE = _re.compile(r"(\d+)\s*만(?:\s*(\d+)\s*천)?")


def _normalize(s: str) -> str:
    """공백 압축 + 소문자화 — '비용 청구'='비용청구', 'P D F'='pdf' 모두 매칭."""
    return _WS_RE.sub("", s).lower()


def _normalize_amounts(s: str) -> str:
    """'1만'→'10000', '1만 2천'→'12000', '3만'→'30000', 콤마 제거('10,000'→'10000')."""
    def _repl(m) -> str:
        man = int(m.group(1))
        cheon = int(m.group(2)) if m.group(2) else 0
        return str(man * 10000 + cheon * 1000)
    return _MAN_RE.sub(_repl, s).replace(",", "")


def _kw_matches(kw: str, answer: str) -> bool:
    """키워드가 답변에 포함되는가 — 원문/공백정규화/금액정규화 순으로 관대하게 매칭."""
    if kw in answer:
        return True
    if _normalize(kw) in _normalize(answer):
        return True
    # 금액(만/천/콤마) 표기 차이 흡수: "10,000" 과 "1만" 을 동치로 본다.
    if _normalize(_normalize_amounts(kw)) in _normalize(_normalize_amounts(answer)):
        return True
    return False


def _answer_ok(case: dict, answer: str) -> tuple[bool, str]:
    """답변 검증. must_include 모두 포함 + must_not_include 모두 미포함이면 PASS.
    공백·금액 표기 차이에 강건(_kw_matches)."""
    for kw in case.get("must_include", []):
        if not _kw_matches(kw, answer):
            return False, f"missing:{kw!r}"
    norm_answer = _normalize(answer)
    for kw in case.get("must_not_include", []):
        if kw in answer or _normalize(kw) in norm_answer:
            return False, f"forbidden:{kw!r}"
    return True, "ok"


async def evaluate_case(db, case: dict, *, with_llm: bool) -> dict:
    chunks = await search(db, case["query"], top_k=4, min_score=0.5)
    result: dict = {
        "name": case["name"],
        "query": case["query"],
        "category": case.get("category", "?"),
        "top1_label": None,
        "top1_score": None,
        "retr_ok": False,
        "ans_ok": None,
        "ans_reason": None,
        "answer": None,
    }
    if not chunks:
        result["ans_reason"] = "no_chunks_retrieved"
        # negative 케이스는 검색 결과가 없어도 검색 채점은 면제될 수 있음.
        result["retr_ok"] = case.get("expected_content_substring") is None and case.get(
            "expected_label_contains"
        ) is None
    else:
        top1 = chunks[0]
        result["top1_label"] = top1.source_label
        result["top1_score"] = top1.score
        result["retr_ok"] = _retrieval_ok(case, top1)

    if with_llm and chunks:
        ctx = build_context_block(chunks, query=case["query"])
        try:
            answer = await _generate(case["query"], ctx, history=case.get("history"))
        except Exception as e:
            result["ans_ok"] = False
            result["ans_reason"] = f"llm_error:{type(e).__name__}"
            result["answer"] = ""
            return result
        ok, reason = _answer_ok(case, answer)
        result["ans_ok"] = ok
        result["ans_reason"] = reason
        result["answer"] = answer
    return result


def _fmt_pct(num: int, denom: int) -> str:
    if denom == 0:
        return "n/a"
    return f"{num}/{denom} ({num / denom * 100:.1f}%)"


async def main() -> None:
    parser = argparse.ArgumentParser(description="RAG 회귀 평가")
    parser.add_argument("--with-llm", action="store_true", help="vLLM 으로 답변까지 생성하여 채점")
    parser.add_argument("--verbose", action="store_true", help="실패 케이스 답변 본문 출력")
    parser.add_argument("--only", default=None, help="이 문자열을 name 에 포함하는 케이스만 실행")
    args = parser.parse_args()

    cases = EVAL_CASES
    if args.only:
        cases = [c for c in cases if args.only in c["name"]]
        if not cases:
            print(f"매칭되는 케이스 없음: --only={args.only!r}")
            return

    print(f"RUN eval — {len(cases)} cases  (with_llm={args.with_llm})")
    print("─" * 96)

    results: list[dict] = []
    for i, case in enumerate(cases, 1):
        # 케이스마다 새 세션 — 원격 DB(150)에서 embed 대기 중 커넥션이 idle 로 끊기는 것을 방지.
        async with SessionLocal() as db:
            r = await evaluate_case(db, case, with_llm=args.with_llm)
        results.append(r)
        mark_r = "✓" if r["retr_ok"] else "✗"
        line = (
            f"{mark_r} [{i:>2}/{len(cases)}] retr={('PASS' if r['retr_ok'] else 'FAIL'):4} "
            f" {r['name']:<32}"
        )
        if r["top1_label"]:
            line += f"  top1={r['top1_label']} ({r['top1_score']:.3f})"
        if args.with_llm:
            if r["ans_ok"] is None:
                line += "  ans=skip"
            else:
                mark_a = "✓" if r["ans_ok"] else "✗"
                line += f"  ans={mark_a} {r['ans_reason']}"
        print(line)
        if args.verbose and args.with_llm and r["answer"] is not None and r["ans_ok"] is False:
            print(f"      └─ answer: {r['answer'][:300]}")

    # ── Summary ────────────────────────────────────────────────────────────
    print("─" * 96)
    retr_pass = sum(1 for r in results if r["retr_ok"])
    print(f"Retrieval: {_fmt_pct(retr_pass, len(results))}")
    if args.with_llm:
        ans_total = sum(1 for r in results if r["ans_ok"] is not None)
        ans_pass = sum(1 for r in results if r["ans_ok"])
        print(f"Answer:    {_fmt_pct(ans_pass, ans_total)}")

    # 카테고리별 분포
    by_cat: dict[str, list[bool]] = defaultdict(list)
    for r in results:
        by_cat[r["category"]].append(bool(r["retr_ok"]))
    if len(by_cat) > 1:
        print("\nRetrieval by category:")
        for cat, vals in sorted(by_cat.items()):
            ok = sum(vals)
            print(f"  {cat:<18}  {_fmt_pct(ok, len(vals))}")

    # 실패 케이스 자세히
    failed = [
        r for r in results
        if not r["retr_ok"] or (args.with_llm and r["ans_ok"] is False)
    ]
    if failed:
        print("\n=== Failed cases ===")
        for r in failed:
            print(f"  ✗ {r['name']}")
            print(f"      query: {r['query']!r}")
            if r["top1_label"]:
                print(f"      top1:  {r['top1_label']}  (score={r['top1_score']:.3f})")
            if r["ans_reason"]:
                print(f"      reason: {r['ans_reason']}")


if __name__ == "__main__":
    asyncio.run(main())
