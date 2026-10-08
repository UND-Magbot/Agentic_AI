"""개념도 요청 체크리스트(diagram.checklist) 단위 검증 — 서버 불필요(LLM 응답 주입)."""
import asyncio
import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.diagram import checklist, llm_spec  # noqa: E402
from app.diagram.spec import ConceptMapSpec  # noqa: E402

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []


def eq(actual, expected, label: str) -> None:
    (PASS.append(label) if actual == expected else FAIL.append((label, f"got {actual!r} != {expected!r}")))


ITEMS = {"items": [
    {"id": "c1", "kind": "fact", "text": "약액 주입 210초", "evidence": "약액 주입 210초"},
    {"id": "c2", "kind": "question", "text": "Class 100 대응 확인", "evidence": "Class 100 되는지 확인"},
    {"id": "c3", "kind": "bogus", "text": "비전 포지셔닝 표기", "evidence": ""},
    {"id": "c4", "kind": "fact", "text": "  ", "evidence": ""},
]}


# LLM 이 '반영' 이라 답한 글자가 실제 스펙에도 있어야 교차 확인을 통과한다.
FAKE_FOOTNOTE = "약액 주입 210초 / Class 100 확인 필요"


def run(verify_rounds: list[dict], max_rounds: int = 2, request: str = "요청",
        footnote: str = FAKE_FOOTNOTE, context: str = ""):
    """verify_rounds: 라운드별 {id: covered}. 호출 기록을 돌려준다."""
    calls = {"gen": [], "verify": 0}
    rounds = list(verify_rounds)

    async def fake_chat(messages, **kw):
        sysmsg = messages[0]["content"]
        if sysmsg == checklist._EXTRACT_PROMPT:
            return json.dumps(ITEMS, ensure_ascii=False)
        calls["verify"] += 1
        payload = messages[1]["content"].split("[체크리스트]\n", 1)[1].split("\n\n[개념도 스펙]", 1)[0]
        calls.setdefault("verified_ids", []).append([d["id"] for d in json.loads(payload)])
        cov = rounds.pop(0)
        return json.dumps({"results": [{"id": k, "covered": v} for k, v in cov.items()]})

    async def fake_gen(request, *, context="", revise=None, **kw):
        calls["gen"].append(revise[1] if revise else None)
        calls.setdefault("ctx", []).append(context)
        return (ConceptMapSpec(title=f"v{len(calls['gen'])}", footnote=footnote),
                [f"note{len(calls['gen'])}"])

    real_chat, real_gen = checklist.proposal_llm.chat, llm_spec.generate_spec
    checklist.proposal_llm.chat, llm_spec.generate_spec = fake_chat, fake_gen
    try:
        spec, notes, rep = asyncio.run(checklist.generate_checked(request, context=context,
                                                                    max_rounds=max_rounds))
    finally:
        checklist.proposal_llm.chat, llm_spec.generate_spec = real_chat, real_gen
    return spec, notes, rep, calls


# 1) 추출: 빈 항목 제거, 모르는 kind → fact
spec, notes, rep, calls = run([{"c1": True, "c2": True, "c3": True}])
eq([i.id for i in rep.items], ["c1", "c2", "c3"], "빈 text 항목 제거")
eq(rep.items[2].kind, "fact", "알 수 없는 kind → fact")

# 2) 처음부터 전부 반영 → 보완 생성 없음
eq(len(calls["gen"]), 1, "전부 반영 시 생성 1회")
eq(rep.rounds, 0, "보완 라운드 0")
eq(spec.title, "v1", "최초 스펙 반환")

# 3) 1차에 Class 100 누락 → 보완 1회 후 반영
spec, notes, rep, calls = run([{"c1": True, "c2": False, "c3": True}, {"c1": True, "c2": True, "c3": True}])
eq(len(calls["gen"]), 2, "누락 시 보완 생성")
eq("Class 100 대응 확인" in (calls["gen"][1] or ""), True, "보완 지시에 누락 항목 포함")
eq("확인 필요" in (calls["gen"][1] or ""), True, "보완 지시에 '확인 필요' 규칙 포함")
eq(spec.title, "v2", "보완된 스펙 반환")
eq(notes, ["note1", "note2"], "교정 내역 누적")
eq(rep.missing, [], "최종 누락 없음")
eq(rep.history, [["Class 100 대응 확인"], []], "라운드별 누락 기록")

eq(calls["verified_ids"], [["c1", "c2", "c3"], ["c2"]], "2라운드부터는 직전 누락 항목만 AI 판정")

# 3-1) 보완 재생성에는 참고 자료를 다시 넣지 않는다 (12b 컨텍스트 한도 재시도 실측)
spec, notes, rep, calls = run([{"c1": True, "c2": False, "c3": True}, {"c1": True, "c2": True, "c3": True}],
                              context="사례집 참고")
eq(calls["ctx"], ["사례집 참고", ""], "참고 자료는 첫 설계에만")

# 4) 계속 누락 → max_rounds 에서 멈추고 누락을 보고
spec, notes, rep, calls = run([{"c1": True, "c2": False, "c3": True}] * 3, max_rounds=2)
eq(len(calls["gen"]), 3, "최대 2회 보완(생성 3회)")
eq(rep.missing, [], "끝까지 빠진 항목도 남기지 않음")
eq(rep.auto_filled, ["Class 100 대응 확인"], "코드가 자동 보완한 항목으로 보고")
review = next((t for t in spec.tables if t.title == "검토 사항"), None)
eq(bool(review) and review.rows[-1][0] == "Class 100 대응 확인" and review.rows[-1][2] == "확인 필요",
   True, "자동 보완은 검토 사항 표에 '확인 필요' 로")

# 5) 판정 누락된 항목은 보수적으로 누락 처리
spec, notes, rep, calls = run([{"c1": True, "c3": True}, {"c1": True, "c2": True, "c3": True}])
eq(len(calls["gen"]), 2, "판정 없는 항목은 누락으로 보고 보완")

# 6) 교차 확인: LLM 이 '반영' 이라 해도 숫자가 스펙에 없으면 누락으로 뒤집는다 (12b 실측 재현)
spec, notes, rep, calls = run([{"c1": True, "c2": True, "c3": True}] * 3, footnote="Class 100 확인 필요")
eq("약액 주입 210초" in rep.overturned, True, "숫자 없는 '반영' 판정을 뒤집음")
eq(len(calls["gen"]) > 1, True, "뒤집힌 항목으로 보완 생성")
eq("210초" in (calls["gen"][1] or ""), True, "보완 지시에 뒤집힌 항목 포함")

# 7) 원문 규칙: LLM 이 뽑지 않은 수치·일정·불량 배출도 코드가 잡고 넣는다
REQ = "로봇 투입 -> 뚜껑 풀기 -> 액 주입 -> 불량 시 병 치우기. 빈 병 1.5kg, 액 3.7kg. 금주 금요일까지."
spec, notes, rep, calls = run([{"c1": True, "c2": True, "c3": True}] * 3, request=REQ)
kinds = {i.kind for i in rep.rule_items}
eq({"fact", "deliverable", "structure", "process"} <= kinds, True, "원문 규칙 항목 종류")
eq(len(calls["gen"]), 3, "원문 규칙 누락도 보완 생성을 돌린다")
eq(rep.rule_missing, [], "보완 후 원문 규칙 누락 없음")
eq(all(x in spec.footnote for x in ("1.5kg", "3.7kg", "금요일")), True, "수치·일정은 각주로 보완",
   )
eq("불량 배출 설비" in rep.auto_filled, True, "빠진 설비는 자동 보완으로 보고")

print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
for label, why in FAIL:
    print(f"  FAIL {label}: {why}")
sys.exit(1 if FAIL else 0)
