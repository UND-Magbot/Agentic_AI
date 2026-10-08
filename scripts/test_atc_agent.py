"""GPT 식 추천 대화(atc_agent) 점검 — 흘러 들어오는 글 나누기(생각/JSON/답), 코드 검증(칸 값·모델·되돌리기·잠금), 이벤트 순서.

    docker compose run --rm --no-deps -e PYTHONPATH=/app -v ./backend/app:/app/app -v ./scripts:/scripts backend \\
        python /scripts/test_atc_agent.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from app.company_knowledge import atc_agent as ag
from app.company_knowledge import atc_selection as atc

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


CAT = [{"card_id": i, "name": n, "series": s, "series_label": s, "payload_kg": p, "official": True, "wireless": n == "TCW1"}
       for i, (n, s, p) in enumerate([("TCC1", "auto", 5), ("TCV1", "auto", 10), ("TCV2", "auto", 16), ("TCHK100", "industrial", 100)])]
SAMPLES = Path(atc.__file__).with_name("data") / "atc_test_samples.json"
base = json.loads(SAMPLES.read_text(encoding="utf-8"))["samples"][0]["intake"]
OPTS = atc.model_options(CAT, base)
VERS = [{"n": 1, "model": "TCC1", "label": "TCC1 (첫 추천)"}, {"n": 2, "model": "TCV2", "label": "TCV2"}]


def run(text: str, message: str, *, chunk: int = 3, **kw) -> list[dict]:
    async def stream(_msgs, **_kw):
        for i in range(0, len(text), chunk):
            yield text[i:i + chunk]

    async def go():
        return [ev async for ev in ag.agent_turn(base, message, history=[], rec=None, stream=stream, options=OPTS, **kw)]
    return asyncio.run(go())


def joined(evs: list[dict], t: str) -> str:
    return "".join(e["text"] for e in evs if e["t"] == t)


# 1) 나누기 — 조각이 어디서 잘려도 생각·답이 그대로, 구분선·JSON 은 화면에 안 나감
OUT = ("- 사용자가 TCV2로 바꾸길 원함.\n- TCV2 정격 16kg 은 선정 하중보다 큼.\n===\n"
       '{"updates": [], "intent": "switch", "model": "TCV2", "version": null}\n===\nTCV2로 바꿔 다시 추천하겠습니다.')
for size in (1, 2, 5, 17, 400):
    evs = run(OUT, "TCV2로 바꿔 줘", chunk=size)
    th, an = joined(evs, "think"), joined(evs, "answer")
    check(th.strip() == "- 사용자가 TCV2로 바꾸길 원함.\n- TCV2 정격 16kg 은 선정 하중보다 큼." and "=" not in th and "{" not in an
          and an.strip() == "TCV2로 바꿔 다시 추천하겠습니다.", f"조각 크기 {size}: 생각·답만 화면으로", repr((th, an)))
done = evs[-1]
check(done["t"] == "done" and done["intent"] == "switch" and done["model"] == "TCV2", "switch → TCV2", str(done))
order = [e["t"] for e in evs]
check(order.index("think") < order.index("answer") < order.index("step") < order.index("done"), "순서: 생각 → 답 → 단계 → 끝", str(order))

# 구분선을 빠뜨린 답도 JSON·답을 찾아냄
evs = run('- 질문에 답함.\n{"updates": [], "intent": "chat"}\n지금 TCC1 이 맞습니다.', "이거 괜찮아?")
check(evs[-1]["intent"] == "chat" and "TCC1 이 맞습니다" in evs[-1]["answer"], "구분선 없어도 JSON·답 찾음", str(evs[-1]))

# 2) 칸 값 — 말에 없는 숫자는 버림(사고 글에 2.3 을 지어내도)
evs = run('- 무게 변경.\n===\n{"updates": [{"target": "tool", "index": 0, "path": "intake.tool_assembly_mass_kg", "value": 2.3},'
          '{"target": "tool", "index": 0, "path": "mass_components.max_simultaneous_workpieces_kg", "value": 1.5}], "intent": "recommend"}'
          '\n===\n반영했습니다.', "제품 무게 1.5kg 으로 바꿔서 다시 찾아 줘")
d = evs[-1]
labels = [a["display"] for a in d["applied"]]
check(labels == ["1.5"] and d["intent"] == "recommend", "말에 있는 1.5만 반영, 지어낸 2.3 은 버림", str(d["applied"]))
check(any(e["t"] == "step" and not e["ok"] and "근거가 없" in e["text"] for e in evs), "버린 값은 ✗ 단계로 알림")
check(d["intake"]["tools"][0]["mass_components"]["max_simultaneous_workpieces_kg"] == 1.5, "intake 에 실제 반영")

# 3) 모델 — 고를 수 없는 모델은 거절하고 답을 갈아 끼움
evs = run('- 바꿈.\n===\n{"updates": [], "intent": "switch", "model": "TCHK100"}\n===\nTCHK100으로 바꾸겠습니다.', "TCHK100으로 해 줘")
d = evs[-1]
check(d["intent"] == "chat" and d["model"] is None and "고를 수 없습니다" in d["answer"]
      and any(e["t"] == "answer_set" for e in evs), "고를 수 없는 모델 → 거절, answer_set 으로 교체", str(d))

# 4) 되돌리기 — 있는 버전만, 지금 버전은 그대로, 추천 전엔 없음
evs = run('- 첫 추천으로.\n===\n{"updates": [], "intent": "revert", "version": 1}\n===\n1번째로 되돌리겠습니다.', "아까 TCC1일 때로 돌려 줘",
          versions=VERS, current=2)
check(evs[-1]["intent"] == "revert" and evs[-1]["version"] == 1, "되돌리기 → 버전 1", str(evs[-1]))
evs = run('- x\n===\n{"intent": "revert", "version": 7}\n===\n되돌리겠습니다.', "7번으로 돌려 줘", versions=VERS, current=2)
check(evs[-1]["intent"] == "chat" and evs[-1]["version"] is None and "몇 번째" in evs[-1]["answer"], "없는 버전 → 되묻기", str(evs[-1]))
evs = run('- x\n===\n{"intent": "revert", "version": 1}\n===\n되돌리겠습니다.', "처음으로 돌려 줘")
check(evs[-1]["intent"] == "chat" and "추천 결과가 나온 뒤부터" in evs[-1]["answer"], "추천 전에는 되돌릴 버전 없음", str(evs[-1]))
evs = run('- x\n===\n{"intent": "revert", "version": 2}\n===\n되돌리겠습니다.', "TCV2로 돌려 줘", versions=VERS, current=2)
check(evs[-1]["intent"] == "chat" and "지금 보고 있는" in evs[-1]["answer"], "지금 버전이면 그대로", str(evs[-1]))

# 5) 잠금 — 최종 제안 확정 뒤에는 바꾸기·되돌리기 없음
for out, msg in (('- x\n===\n{"intent": "switch", "model": "TCV2"}\n===\n바꾸겠습니다.', "TCV2로 바꿔 줘"),
                 ('- x\n===\n{"intent": "revert", "version": 1}\n===\n되돌리겠습니다.', "처음으로 돌려 줘")):
    evs = run(out, msg, versions=VERS, current=2, locked=True)
    check(evs[-1]["intent"] == "chat" and "최종 제안을 이미 확정" in evs[-1]["answer"], f"잠금: {msg}", str(evs[-1]))

# 6) '분석 중입니다' 같은 빈 약속은 쓰지 않음
evs = run('- x\n===\n{"intent": "chat"}\n===\n분석 중입니다. 잠시만 기다려 주세요.', "음")
check("분석 중" not in evs[-1]["answer"], "빈 약속 답은 교체", evs[-1]["answer"])

# 7) 지시문(화면 안내)을 답에 그대로 옮겨 쓰면 그 줄을 지우고 답을 갈아 끼움(사용자 2026-10-06 실제 사례)
LEAK = ("- x\n===\n{\"intent\": \"chat\"}\n===\n같은 계열 상위 단계는 비교 사유가 없어 후보에 없습니다.\n\n"
        "화면 안내(물으면 이대로 답한다): AI 가 배운 내용은 위쪽 [AI 학습 내용 관리] 탭에서 보고 영업 관리자가 승인·거절한다.\n"
        "질문 답을 고치려면 왼쪽 [Q&A 고치기], 처음부터 하려면 [처음부터]. 예전 결과는 결과 창 위 ‹ › 로도 볼 수 있다.")
evs = run(LEAK, "TCV1은 왜 없어?")
fixed = [e for e in evs if e["t"] == "answer_set"]
check(fixed and "화면 안내" not in evs[-1]["answer"] and "Q&A 고치기" not in evs[-1]["answer"]
      and evs[-1]["answer"].startswith("같은 계열 상위 단계"), "지시문 유출 줄은 답에서 지움", evs[-1]["answer"])
evs = run("- x\n===\n{\"intent\": \"chat\"}\n===\n왼쪽 [Q&A 고치기]로 답을 고칠 수 있습니다.", "답 어떻게 고쳐?")
check(not [e for e in evs if e["t"] == "answer_set"] and "Q&A 고치기" in evs[-1]["answer"], "화면 사용법을 물은 정상 답은 그대로")

# 8) [후보 순서] — 1순위·같은 계열 상위 단계(후보에 없는 이유)·다른 계열은 순위 아님
REC = {"atc": {"screening_payload_kg": 3.2, "screening_candidates": [
    {"series": "auto", "series_label": "자동 툴체인저", "models": [{"name": "TCC1", "payload_kg": 5}], "compare_reasons": []},
    {"series": "mltc", "series_label": "M-LTC 공압 툴체인저", "models": [{"name": "M-LTC-0005A", "payload_kg": 5}]}]}}
txt = ag.candidate_order(REC, OPTS)
check("1순위(규칙 판정): TCC1" in txt and "TCV1(10kg), TCV2(16kg)" in txt and "후보에 넣지 않음" in txt
      and "정격 5kg 의 64%" in txt and "다른 계열 참고 후보(순위 아님" in txt and "TCW1" not in txt
      and "2순위를 물으면: 규칙(JSON)에는 2순위가 없다" in txt and "바로 위 단계 TCV1(10kg)" in txt,
      "후보 순서: 상위 단계가 없는 이유(C11)·다른 계열은 순위 아님·무선 제외", txt)
REC["atc"]["screening_candidates"][0] |= {"compare_reasons": ["선정 하중이 정격에 가까움"], "consider_up_to": [{"name": "TCV1"}]}
txt = ag.candidate_order(REC, OPTS)
check("비교 사유(선정 하중이 정격에 가까움)" in txt and "상위 TCV1와 비교 후 담당자가 확정" in txt, "비교 사유가 있으면 상위 비교 후보로", txt)
msgs = ag.build_messages(base, "TCV1은 왜 없어?", history=[], rec=REC, options=OPTS, suggested=None, versions=[], current=None)
check("[후보 순서]" in msgs[1]["content"] and "'2순위'라고 부르지 않는다" in msgs[0]["content"], "대화 프롬프트에 후보 순서와 2순위 금지 포함")

print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
for label, detail in FAIL:
    print(f"  ✗ {label}: {detail}")
sys.exit(1 if FAIL else 0)
