"""대화로 모델 바꾸기(사용자 2026-10-06) 점검 — 담당자 선택 반영(apply_preference)·고를 수 있는 모델(model_options)·
대화 의도 검증(atc_chat.resolve_model / chat_turn: suggest·switch·잠금).

    docker compose run --rm --no-deps -e PYTHONPATH=/app -v ./backend/app:/app/app -v ./scripts:/scripts backend \\
        python /scripts/test_atc_switch.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from app.company_knowledge import atc_chat as ac
from app.company_knowledge import atc_selection as atc

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


CAT = [{"card_id": i, "name": n, "series": s, "series_label": s, "payload_kg": p, "official": True, "wireless": n == "TCW1"}
       for i, (n, s, p) in enumerate([("TCC1", "auto", 5), ("TCV1", "auto", 10), ("TCW1", "auto", 10), ("TCV2", "auto", 16),
                                      ("TCV3", "auto", 25), ("TCHK100", "industrial", 100), ("M-LTC-0010E", "mltc", 10)])]
SAMPLES = Path(atc.__file__).with_name("data") / "atc_test_samples.json"
base = json.loads(SAMPLES.read_text(encoding="utf-8"))["samples"][0]["intake"]       # 샘플1.1 — 툴측 3.2kg, 협동, 규칙 TCC1


def with_pref(model: str | None) -> dict:
    it = json.loads(json.dumps(base))
    if model:
        it["project"]["preferred_model"] = model
    return it


def first(o: dict) -> dict:
    return next(c for c in o["screening_candidates"] if c["models"] and not c.get("out_of_range"))


# 1) 담당자 선택 반영
o0 = atc.evaluate(with_pref(None), CAT)
check(first(o0)["models"][0]["name"] == "TCC1" and o0["preference"] is None, "선택 없음 → 규칙 후보 TCC1")
o2 = atc.evaluate(with_pref("TCV2"), CAT)
f2 = first(o2)
check(f2["models"][0]["name"] == "TCV2" and f2.get("chosen_by_user") and o2["preference"]["applied"]
      and o2["preference"]["rule_model"] == "TCC1" and "담당자가 대화에서 TCV2" in next(b["sentence"] for b in o2["basis"] if b["item"] == "정격 가반하중")
      and "담당자가 대화에서 고른" in o2["junior_summary"]["reason_in_plain_korean"],
      "TCV2 선택 → 1순위 TCV2, 근거·요약에 '담당자 선택(규칙 후보 TCC1)'", str(o2["preference"]))
check(first(atc.evaluate(with_pref("tcv1"), CAT))["models"][0]["name"] == "TCV1", "대소문자 무관")
check(first(atc.evaluate(with_pref("TCW1"), CAT))["models"][0]["name"] == "TCW1", "무선 TCW1 도 담당자가 고르면 후보(C09 별도 선택)")
for pref, why in (("TCHK100", "계열이 아님"), ("XYZ9", "없는 모델")):
    o = atc.evaluate(with_pref(pref), CAT)
    check(first(o)["models"][0]["name"] == "TCC1" and not o["preference"]["applied"] and why in o["preference"]["reason"]
          and any("적용 불가" in r["text_ko"] for r in o["engineering_review_reasons"]),
          f"{pref} → 적용 불가({why}), 규칙 후보 유지·검토 사유", str(o["preference"]))
heavy = json.loads(json.dumps(base))
heavy["tools"][0]["intake"]["tool_assembly_mass_kg"] = 12         # 툴측 12.5kg → TCC1(5)·TCV1(10) 정격 부족
oh = atc.evaluate({**heavy, "project": {**heavy["project"], "preferred_model": "TCC1"}}, CAT)
check(not oh["preference"]["applied"] and "선정 하중" in oh["preference"]["reason"] and first(oh)["models"][0]["name"] != "TCC1",
      "정격이 선정 하중보다 작은 모델은 고를 수 없음", str(oh["preference"]))

# 1-1) 2·3순위 후보(사용자 2026-10-07) — 같은 계열 큰 모델 → 다른 계열, 무선은 유선 뒤, 산업용 계열은 협동로봇에 안 나옴
alt = o0["alternatives"]
check([a["name"] for a in alt] == ["TCV1", "TCV2"] and [a["rank"] for a in alt] == [2, 3]
      and "더 큰 모델" in alt[0]["reason"] and "여유 6.8kg" in alt[0]["reason"], "규칙 1순위 TCC1 → 2순위 TCV1, 3순위 TCV2", str(alt))
alt2 = o2["alternatives"]
check(alt2[0]["name"] == "TCC1" and "더 작은 모델" in alt2[0]["reason"] and all(a["name"] != "TCV2" for a in alt2),
      "담당자가 TCV2 를 고르면 규칙 모델 TCC1 이 '더 작은' 대안, 1순위는 빠짐", str(alt2))
check(all(a["name"] != "TCHK100" for a in alt + alt2), "협동로봇엔 산업용 계열 대안 없음")

# 2) 고를 수 있는 모델
opts = {o["name"]: o for o in atc.model_options(CAT, with_pref(None))}
check(opts["TCV2"]["fits"] and opts["TCC1"]["fits"] and not opts["TCHK100"]["fits"] and opts["M-LTC-0010E"]["fits"],
      "고를 수 있는 모델: 협동로봇 계열·정격 ≥ 선정 하중만", str({k: v["fits"] for k, v in opts.items()}))
hopts = {o["name"]: o for o in atc.model_options(CAT, heavy)}
check(not hopts["TCC1"]["fits"] and not hopts["TCV1"]["fits"] and hopts["TCV2"]["fits"], "무거우면 작은 모델은 제외")

# 3) 대화 의도 검증 — AI 가 무엇을 내든 코드가 다시 확인
olist = list(opts.values())
check(ac.resolve_model("chat", None, "TCV2로 할게요", olist, None)[:2] == ("switch", opts["TCV2"]), "AI 가 놓쳐도 'TCV2로 할게요' → 바꾸기")
check(ac.resolve_model("chat", None, "이걸로 추천해 줘", olist, "TCV1")[1] == opts["TCV1"], "'이걸로 추천해 줘' → 직전 제안 모델")
i, p, extra = ac.resolve_model("switch", "TCHK100", "TCHK100으로 바꿔 줘", olist, None)
check(i == "chat" and p is None and "고를 수 없습니다" in extra, "고를 수 없는 모델로 바꾸기 → 이유와 함께 거절", extra)
i, p, _ = ac.resolve_model("suggest", "TCV9", "이거 괜찮아?", olist, None)
check(i == "chat" and p is None, "AI 가 없는 모델을 제안하면 버림")
check(ac.resolve_model("suggest", "TCV2", "더 좋은 거 없어?", olist, None)[:2] == ("suggest", opts["TCV2"]), "제안은 고를 수 있는 모델일 때만")
check(ac.resolve_model("chat", None, "TCV2 정격이 몇이야?", olist, None)[0] == "chat", "모델 이름을 묻는 말은 바꾸기가 아님")


def fake(intent: dict):
    calls = iter([{"updates": []}, intent])

    async def chat(messages, **_kw):
        return json.dumps(next(calls), ensure_ascii=False)
    return chat


t = asyncio.run(ac.chat_turn(base, "TCV2로 추천해 줘", history=[], rec=None, chat=fake({"intent": "switch", "model": "TCV2", "answer": ""}),
                             options=olist))
check(t["intent"] == "switch" and t["model"] == "TCV2" and "TCV2" in t["answer"], "chat_turn: 바꾸기 → model TCV2", str(t))
lk = asyncio.run(ac.chat_turn(base, "TCV2로 바꿔 줘", history=[], rec=None, chat=fake({"intent": "switch", "model": "TCV2", "answer": "바꾸겠습니다"}),
                              options=olist, locked=True))
check(lk["intent"] == "chat" and lk["model"] is None and "최종 제안을 이미 확정" in lk["answer"], "최종 제안 확정 뒤에는 바꾸기 잠금", str(lk))
rj = asyncio.run(ac.chat_turn(base, "TCHK100으로 해 줘", history=[], rec=None,
                              chat=fake({"intent": "switch", "model": "TCHK100", "answer": "TCHK100으로 변경해 추천하겠습니다."}), options=olist))
check(rj["intent"] == "chat" and "고를 수 없습니다" in rj["answer"] and "변경해 추천하겠습니다" not in rj["answer"],
      "거절할 때는 AI 의 '바꾸겠습니다' 문장을 버리고 이유만", rj["answer"])
sg = asyncio.run(ac.chat_turn(base, "이거 괜찮아?", history=[], rec=None,
                              chat=fake({"intent": "suggest", "model": "TCV1", "answer": "정격 여유를 두려면 TCV1이 낫습니다."}), options=olist))
check(sg["intent"] == "suggest" and sg["model"] == "TCV1" and "TCV1" in sg["answer"], "chat_turn: 제안 → model TCV1 + 이유", str(sg))

print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
for label, detail in FAIL:
    print(f"  ✗ {label}: {detail}")
sys.exit(1 if FAIL else 0)
