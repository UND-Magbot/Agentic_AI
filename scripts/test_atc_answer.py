"""툴체인저 — 대화로 받은 답을 질문 칸에 옮기기(company_knowledge/atc_answer.py) + 추천 근거(atc_selection.basis) 점검.
AI 는 가짜 응답으로 대신한다(사내 AI 없이 돈다).

    docker compose run --rm --no-deps -e PYTHONPATH=/app -v ./backend/app:/app/app -v ./scripts:/scripts backend \\
        python /scripts/test_atc_answer.py
"""
from __future__ import annotations

import asyncio
import json
import sys

from app.company_knowledge import atc_answer as aa
from app.company_knowledge import atc_selection as atc

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


def q(qid: str, scope: str = "tool", i: int = 0) -> dict:
    bank = atc.SPEC["question_bank"] + atc.SPEC["followup_question_bank"]
    src = next(x for x in bank if x["id"] == qid)
    return {"id": qid, "question_ko": src["question_ko"], "scope": scope, "entity_index": i if scope != "project" else None,
            "entity_label": "전동 그리퍼", "fields": src["fields"]}


def intake() -> dict:
    return {"project": {"process": {}, "environment": {}},
            "robots": [{"id": "R1", "type": "cobot", "speed": {}, "cables": {}}],
            "tools": [{"id": "T1", "name": "전동 그리퍼", "intake": {"dimensions_mm": {}}, "mass_components": {}}]}


calls: list[str] = []


def fake(reply: dict):
    async def chat(messages, **_kw):
        calls.append(messages[-1]["content"])
        return json.dumps(reply, ensure_ascii=False)
    return chat


async def main() -> None:
    # 1) 코드로 읽는 꼴 — AI 를 부르지 않는다
    calls.clear()
    r = await aa.read_answer(q("J07"), "없어요", chat=fake({}))
    check(r["values"] == {"tools[].intake.tilts_or_flips": False} and not calls, "칸 하나 boolean '없어요' → false(AI 안 부름)", str(r))
    r = await aa.read_answer(q("J03", "project"), "3개요", chat=fake({}))
    check(r["values"] == {"project.tool_count": 3} and not calls, "칸 하나 숫자 '3개요' → 3", str(r))
    r = await aa.read_answer(q("F02"), "모름", chat=fake({}))
    check(set(r["unknown"]) == {f["path"] for f in q("F02")["fields"]} and not r["values"], "'모름' → 그 질문 칸 전부 모름", str(r))

    # 2) 문장 답은 AI 가 읽고, 코드가 검증(질문 칸만·답에 적힌 숫자만·선택지 값만)
    r = await aa.read_answer(q("J08"), "전기는 쓰고 공압은 안 써요, 센서는 2개",
                             chat=fake({"values": {"tools[].intake.needs_power": True, "tools[].intake.needs_air": False,
                                                   "tools[].intake.has_sensors_or_signals": True, "tools[].name": "엉뚱한 칸"},
                                        "unknown": [], "understood": True}))
    check(r["values"] == {"tools[].intake.needs_power": True, "tools[].intake.needs_air": False,
                          "tools[].intake.has_sensors_or_signals": True} and calls,
          "여러 칸 문장 답 → AI 가 읽고 질문 칸만 남김", str(r))
    r = await aa.read_answer(q("J04"), "툴 무게는 1.2kg, 크기 300x200x120",
                             chat=fake({"values": {"tools[].intake.tool_assembly_mass_kg": 7.5, "tools[].intake.dimensions_mm.x": 300,
                                                   "tools[].intake.dimensions_mm.y": 200, "tools[].intake.dimensions_mm.z": 120}}))
    check("tools[].intake.tool_assembly_mass_kg" not in r["values"] and r["values"].get("tools[].intake.dimensions_mm.x") == 300,
          "답에 없는 숫자(7.5kg)는 버림 — 지어낸 수치 방지", str(r))
    r = await aa.read_answer(q("F05"), "5~6 bar 압축공기",
                             chat=fake({"values": {"tools[].pneumatic.pressure_bar": {"min": 5, "max": 6},
                                                   "tools[].pneumatic.medium": "압축공기"}}))
    check(r["values"].get("tools[].pneumatic.pressure_bar") == {"min": 5.0, "max": 6.0}, "압력 범위 5~6bar", str(r))
    r = await aa.read_answer(q("J02", "project"), "조립이요",
                             chat=fake({"values": {"project.process.category": "welding"}}))
    check(not r["values"] and not r["understood"], "선택지에 없는 값은 버리고 '못 알아들음'", str(r))
    r = await aa.read_answer(q("J02", "project"), "이건 왜 물어봐요?", chat=fake({"values": {}, "understood": False}))
    check(not r["understood"], "질문과 상관없는 말 → 못 알아들음(칸 안 바꿈)", str(r))

    # 3) 칸에 넣기 — 대상 툴에만, 모름 표시
    it = intake()
    out = aa.apply(it, q("J07"), {"values": {"tools[].intake.tilts_or_flips": True}, "unknown": []})
    check(out["tools"][0]["intake"]["tilts_or_flips"] is True and it["tools"][0]["intake"].get("tilts_or_flips") is None
          and out["tools"][0]["intake"]["tilts_or_flips__unknown"] is False, "apply: 새 intake 에 값(원본은 그대로)")
    out = aa.apply(it, q("F02"), {"values": {}, "unknown": ["tools[].electrical.power_circuits"]})
    check(out["tools"][0]["electrical"]["power_circuits__unknown"] is True, "apply: 모름 표시")
    try:
        aa.apply(it, q("J07", "tool", 3), {"values": {}, "unknown": []})
        check(False, "없는 툴 번호는 오류")
    except aa.AnswerError:
        check(True, "없는 툴 번호는 오류")

    # 4) 대화 답이 후속 질문을 띄운다(전기 있음 → F02 전압·전류)
    it = intake()
    it = aa.apply(it, q("J08"), {"values": {"tools[].intake.needs_power": True, "tools[].intake.needs_air": False,
                                           "tools[].intake.has_sensors_or_signals": False}, "unknown": []})
    check("F02" in {x["id"] for x in atc.pending_questions(it)}, "대화로 '전기 있음' → 다음에 F02(전압·전류)를 묻는다")


asyncio.run(main())

# 5) 추천 근거 — 입력 → 규칙 → 판단이 줄마다
CAT = [{"card_id": 1, "name": "TCC1", "payload_kg": 5, "series": "auto", "official": True},
       {"card_id": 2, "name": "TCV1", "payload_kg": 10, "series": "auto", "official": True}]
ok_intake = {"project": {"tool_count": 1, "process": {"category": "transfer"}, "environment": {"has_special_conditions": False}},
             "robots": [{"id": "R1", "type": "cobot", "speed": {"value": 60, "unit": "%"},
                         "cables": {"master_4m_sufficient": True, "system_1m_sufficient": True}}],
             "tools": [{"id": "T1", "name": "전동 그리퍼", "installed_quantity": 1, "robot_ids": ["R1"],
                        "intake": {"tool_assembly_mass_kg": 1.2, "dimensions_mm": {"x": 1, "y": 1, "z": 1}, "tilts_or_flips": False,
                                   "needs_power": True, "needs_air": False, "has_sensors_or_signals": True},
                        "mass_components": {"max_simultaneous_workpieces_kg": 2}}]}
try:
    catalog = atc.build_catalog([{"card_id": c["card_id"], "name": c["name"], "official": True,
                                  "card": {"family": "Magbot ATC · 자동 툴체인저", "kg": c["payload_kg"]}} for c in CAT],
                                lambda card: card.get("kg"))
except Exception as e:  # noqa: BLE001
    catalog = []
    check(False, "build_catalog", repr(e))
out = atc.evaluate(ok_intake, catalog)
b = {x["item"]: x for x in out["basis"]}
check("툴측 총무게" in b and "1.2kg + 제품 2kg = 3.2kg" in b["툴측 총무게"]["input"] and b["툴측 총무게"]["rule"].startswith("R02"),
      "근거: 툴측 총무게 계산식(툴+제품)", json.dumps(out["basis"], ensure_ascii=False)[:400])
check(b["로봇 종류"]["input"] == "협동로봇" and b["포고핀"]["judgement"].startswith("필요 — 수량은"),
      "근거: 로봇 종류·전기 → 포고핀", str(b.get("로봇 종류")))
check("정격 가반하중" in b and b["정격 가반하중"]["input"].startswith("TCC1 정격 5kg")
      and "5kg ≥ 선정 하중 3.2kg" in b["정격 가반하중"]["judgement"],
      "근거: 정격 5kg ≥ 선정 하중 3.2kg — 가장 작은 단계(제품 DB 값)", str(b.get("정격 가반하중")))
unk = json.loads(json.dumps(ok_intake))
unk["tools"][0]["intake"]["needs_air"] = None
bu = {x["item"]: x for x in atc.evaluate(unk, catalog)["basis"]}
check(bu["공압"]["ok"] is False and bu["공압"]["input"] == "모름" and bu["공압"]["judgement"].startswith("미정")
      and b["공압"]["ok"] is True and b["공압"]["input"] == "없음",
      "근거: 공압 '모름'은 미정(확인 필요), '없음'은 모듈 없이", str([bu["공압"], b["공압"]]))
empty = atc.evaluate({"project": {}, "robots": [], "tools": []}, catalog)
check(empty["basis"][0]["ok"] is False and "모든 툴의 무게" in empty["basis"][0]["judgement"],
      "근거: 무게를 모르면 '정할 수 없음'으로(지어내지 않음)", json.dumps(empty["basis"], ensure_ascii=False)[:300])

# 6) 포고핀(R11·R12·R13·R16) — 그리퍼 전원·신호로 도체 수 → 모듈 하한(사용자 2026-10-02)
def with_el(el: dict, power=True, sensor=True) -> dict:
    it = json.loads(json.dumps(ok_intake))
    it["tools"][0]["intake"]["needs_power"], it["tools"][0]["intake"]["has_sensors_or_signals"] = power, sensor
    it["tools"][0]["electrical"] = el
    return it


r10 = atc.evaluate(with_el({"power_circuit_count": 1, "current_A": 0.8, "dio": {"di": 2, "do": 2}}), catalog)
pt = r10["pogo_tools"][0]
check(pt["pins"] == 6 and pt["modules"] == 1 and r10["pogo_modules"] == 1 and pt["status"] == "conditional"
      and r10["junior_summary"]["pogo_modules"].startswith("PPM 1개(마스터 공통 배치, 모듈당 8핀)")
      and "전원 1회로×(공급 1+리턴 1) + 신호 4" in r10["junior_summary"]["pogo_modules"],
      "T10(판정까지): 전원 1회로(공급+리턴) + DI 2 + DO 2 = 6핀 → PPM 1개(피크 미확인이라 조건부)", json.dumps(pt, ensure_ascii=False))
r12 = atc.evaluate(with_el({"power_circuits": ["24V 0.8A, 피크 2A (그리퍼 사양서)"], "dio": {"di": 1, "do": 1}}), catalog)
p12 = r12["pogo_tools"][0]
check(p12["circuits"] == 1 and p12["rated_A"] == 0.8 and p12["peak_A"] == 2 and p12["from_text"] and p12["design_A"] == 2
      and p12["power_pins"] == 4 and p12["pins"] == 6
      and "E01" not in {q["id"] for q in r12["followup_questions"]},
      "v1.2 PGR03: 사양 글 '24V 0.8A, 피크 2A' → 설계 2A, 공급 2+리턴 2 + DI1·DO1 = 6핀, 전원 질문(E01) 다시 안 묻음", json.dumps(p12, ensure_ascii=False))
r2a = atc.evaluate(with_el({"power_circuits": ["24V 2A (전자석 컨트롤러)"], "dio": {"di": 0, "do": 2}}), catalog)
check(r2a["pogo_tools"][0]["pins"] == 6 and r2a["pogo_tools"][0]["parallel"],
      "PGR04 '24V 2A' → 병렬: 공급 2 + 리턴 2 + DO 2 = 6핀", json.dumps(r2a["pogo_tools"], ensure_ascii=False))
rmiss = atc.evaluate(with_el({"power_circuit_count": 1, "current_A": 0.5}), catalog)
check(rmiss["pogo_modules"] is None and rmiss["junior_summary"]["pogo_modules"].startswith("필요 — 수량은")
      and "제어 방식" in rmiss["junior_summary"]["pogo_modules"]
      and "E02" in {q["id"] for q in rmiss["pending_questions"]},
      "제어 방식·신호 수를 모르면 개수를 지어내지 않고 E02(제어 방식과 신호선)를 묻는다", rmiss["junior_summary"]["pogo_modules"])
r16 = atc.evaluate(with_el({"power_circuit_count": 2, "current_A": 0.5, "dio": {"di": 20, "do": 10}}), catalog)
check(r16["pogo_modules"] == 5 and any(r["rule"] == "PGR13" for r in r16["engineering_review_reasons"])
      and "nonstandard_or_out_of_range" in [x["status"] for x in r16["statuses"]],
      "핀 34 → PPM 5개 > 표준 3개소 → 커스텀 필요(PGR13)", str(r16["slot_check"]))
r13 = atc.evaluate(with_el({"power_circuit_count": 1, "current_A": 0.5, "dio": {"di": 1, "do": 1},
                            "control_method_raw": "RS-485"}), catalog)
p13 = r13["pogo_tools"][0]
check(any(r["ref"] == "R13" for r in r13["engineering_review_reasons"]) and r13["pogo_modules"] == 1
      and p13["signal_pins"] == 4 and p13["pins"] == 6 and p13["status"] == "conditional"
      and any("2선식" in q for q in r13["accessory"]["open_questions"]),
      "v1.2 PGR19: RS-485 선식 미확인 → 2선식(A·B) 가정 + DI1·DO1 = 신호 4·6핀(조건부), 선식 질문 + 통신 검토", json.dumps(p13, ensure_ascii=False))
rnone = atc.evaluate(with_el({}, power=False, sensor=False), catalog)
check(rnone["pogo_tools"] == [] and rnone["junior_summary"]["pogo_modules"].startswith("필요 없음")
      and not {"E01", "E02"} & {q["id"] for q in rnone["followup_questions"]},
      "전기·센서 없음 → 포고핀 없음, 산정 질문 안 띄움")
check({"E01", "E02"} <= {q["id"] for q in atc.evaluate(with_el({}), catalog)["followup_questions"]},
      "전기·센서 있음 → 포고핀 산정 질문 E01(전원)·E02(신호 수)가 추가 질문으로")

# 7) 실제 제품 툴의 공개 사양 외부 검색(tool_spec_search) — 검색·사내 AI 는 가짜로
from app.company_knowledge import tool_spec_search as ts  # noqa: E402

check(ts.looks_real({"name": "전동 그리퍼 (OnRobot RG2)"}) and ts.looks_real({"name": "그리퍼", "model": "EGP-40"}) == "EGP-40"
      and ts.looks_real({"name": "전동 그리퍼 (RS-485 제어)"}) is None and ts.looks_real({"name": "패널 그리퍼 A"}) is None
      and ts.looks_real({"name": "도어 그리퍼 (SUV)"}) is None and ts.looks_real({"name": "흡착 그리퍼 (같은 모델)"}) is None,
      "실제 제품처럼 보이는 툴만 검색 대상(브랜드·모델 코드, 'RS-485'·일반 이름 제외)")
sent: list[str] = []


async def fake_search(q, *, user_id=None):
    sent.append(q)
    return {"answer": "XG5 gripper: weight 780 g, 24 V DC, rated current 0.6 A, peak 1.5 A. Electric, no air.",
            "results": [{"title": "XG5 datasheet", "url": "https://example.com/xg5", "content": "Dimensions 120 x 80 x 150 mm"}]}


def fake_spec_chat(reply):
    async def chat(messages, **_kw):
        return json.dumps(reply, ensure_ascii=False)
    return chat


spec = asyncio.run(ts.search_spec("ACME XG5", user_id=None, search=fake_search, chat=fake_spec_chat(
    {"mass_kg": 0.78, "size_mm": {"x": 120, "y": 80, "z": 150}, "voltage_V": 24, "current_A": 0.6, "peak_A": 1.5,
     "needs_air": False, "evidence": {"mass_kg": "weight 780 g"}})))
fv = {f["path"]: f["value"] for f in spec["fields"]}
check(fv.get("tools[].intake.tool_assembly_mass_kg") == 0.78 and fv.get("tools[].electrical.current_A") == 0.6
      and fv.get("tools[].electrical.peak_A") == 1.5 and fv.get("tools[].intake.needs_air") is False
      and fv.get("tools[].intake.dimensions_mm.x") == 120 and spec["sources"][0]["url"] == "https://example.com/xg5"
      and spec["status"] == "estimated" and sent and sent[0].startswith("ACME XG5"),
      "검색 결과 → 칸별 제안(780 g → 0.78kg, 출처 URL, 상태 '추정'), 외부로는 제품명만", json.dumps(spec, ensure_ascii=False)[:300])
spec2 = asyncio.run(ts.search_spec("ACME XG5", user_id=None, search=fake_search, chat=fake_spec_chat(
    {"mass_kg": 1.2, "current_A": 0.8, "pressure_bar": {"min": 6, "max": 6}})))
check(not spec2["fields"], "검색 글에 없는 숫자(1.2kg·0.8A·6bar)는 버림 — 사양을 지어내지 않음(do_not_infer_model_specs)",
      json.dumps(spec2["fields"], ensure_ascii=False))
try:
    asyncio.run(ts.search_spec("", user_id=None, search=fake_search, chat=fake_spec_chat({})))
    check(False, "제품명 없으면 오류")
except ts.SpecSearchError:
    check(True, "제품명 없으면 오류")
# 검색으로 전류만 들어오면 회로 수는 여전히 묻는다(E01 은 회로 수가 있어야 답한 것)
cur_only = atc.evaluate(with_el({"current_A": 0.6, "dio": {"di": 1, "do": 1}}), catalog)
check("E01" in {q["id"] for q in cur_only["pending_questions"]} and cur_only["pogo_modules"] is None,
      "전류만 알고 회로 수를 모르면 E01 을 계속 묻고 포고핀 수를 지어내지 않음")

# 8) AI 와 같이 찾기 대화(atc_chat) — 말에서 칸 채우기 + 의도(다시 추천·학습·대화), 사용자 2026-10-02(ui_v9)
from app.company_knowledge import atc_chat as ac  # noqa: E402

two = {"project": {"process": {}, "environment": {}}, "robots": [{"id": "R1", "type": "cobot", "cables": {}}],
       "tools": [{"id": "T1", "name": "전동 그리퍼", "intake": {"needs_power": True}},
                 {"id": "T2", "name": "진공 그리퍼 (흡착패드 4개)", "intake": {"needs_power": False, "has_sensors_or_signals": True}}]}
msg = "진공 그리퍼 센서 전원은 24V 0.05A 예요"
new, applied = ac.apply_updates(two, [
    {"target": "tool", "index": 1, "path": "electrical.current_A", "value": 0.05},
    {"target": "tool", "index": 1, "path": "electrical.power_circuits", "value": "센서 전원 24V 0.05A"},
    {"target": "tool", "index": 1, "path": "intake.tool_assembly_mass_kg", "value": 1.2},      # 말에 없는 숫자
    {"target": "tool", "index": 7, "path": "electrical.peak_A", "value": 24},                   # 없는 툴
    {"target": "tool", "index": 0, "path": "electrical.secret", "value": 1}], msg)              # 없는 칸
vt = new["tools"][1]
check(vt["electrical"]["current_A"] == 0.05 and vt["electrical"]["power_circuits"] == ["센서 전원 24V 0.05A"]
      and "tool_assembly_mass_kg" not in vt["intake"] and len(applied) == 2 and "electrical" not in two["tools"][1]
      and atc.power_spec(vt)["circuits"] == 1 and atc.power_spec(vt)["rated_A"] == 0.05,
      "대화 '진공 그리퍼 센서 전원 24V 0.05A' → 그 툴 칸에 반영(소수 포함), 말에 없는 숫자·없는 툴·없는 칸은 버림",
      json.dumps([applied, vt], ensure_ascii=False))
check(not ac.wants_learn("학습 내용은 어디서 봐요?", True) and not ac.wants_learn("학습하지 말고 그냥 물어본 거예요", True)
      and not ac.wants_learn("이 정보까지 포함해서 다시 추천해 주세요", False)
      and ac.wants_learn("다음부터는 이렇게 판단하도록 학습해 줘", False) and ac.wants_learn("이 기준 기억해 둬", False)
      and ac.wants_learn("A 말고 B 가 맞아요", True),
      "학습 제안: 분명한 요구('학습해 줘'·'기억해 둬'·'다음부터')만, '학습' 단어가 든 질문·거절은 아님")


def fake_turn(extract: dict, intent: dict):
    calls = iter([extract, intent])

    async def chat(messages, **_kw):
        return json.dumps(next(calls), ensure_ascii=False)
    return chat


t1 = asyncio.run(ac.chat_turn(two, "아니요 이 정보까지 포함해 다시 제품 찾아주세요 — 진공 그리퍼 센서 전원 24V 0.05A", history=[], rec=None,
                              chat=fake_turn({"updates": [{"target": "tool", "index": 1, "path": "electrical.current_A", "value": 0.05}]},
                                             {"intent": "recommend", "answer": "반영해서 다시 찾겠습니다."})))
check(t1["intent"] == "recommend" and t1["intake"]["tools"][1]["electrical"]["current_A"] == 0.05 and t1["applied"],
      "말로 정보를 더하고 다시 찾아 달라면 → 칸 반영 + 다시 추천(의도 recommend)", json.dumps(t1, ensure_ascii=False)[:300])
t2 = asyncio.run(ac.chat_turn(two, "학습 내용은 어디서 보나요?", history=[], rec=None,
                              chat=fake_turn({"updates": []}, {"intent": "learn", "answer": "관리 탭에서 봅니다."})))
check(t2["intent"] == "chat", "AI 가 learn 이라 해도 '학습 내용 어디서 봐' 같은 질문이면 대화로", t2["intent"])

# 9) v1.2 PGR03 — 설계 전류 = max(정격·피크·기동), 그 전류로 공급·리턴 각각 ceil(I/1A)
def peak_of(el):
    o = atc.evaluate(with_el({"power_circuit_count": 1, "dio": {"di": 1, "do": 1}, **el}), catalog)
    return o["pogo_tools"][0], o


ok1, o1 = peak_of({"current_A": 0.6, "peak_A": 0.9})
over, oo = peak_of({"current_A": 0.6, "peak_A": 1.2})
cont, _ = peak_of({"current_A": 1.2})
txt = atc.power_spec({"electrical": {"power_circuits": ["24V 1회로, 연속 0.6A, 피크 1.2A"]}, "intake": {"needs_power": True}})
check(ok1["pins"] == 4 and ok1["status"] != "needs_data" and over["pins"] == 6 and over["design_A"] == 1.2
      and cont["pins"] == 6 and cont["status"] == "conditional" and txt["rated_A"] == 0.6 and txt["peak_A"] == 1.2 and txt["voltage_V"] == 24,
      "PGR03: 피크 0.9A → 경로당 1핀(4핀), 피크 1.2A → 2핀(6핀), 정격 1.2A 만 알면 6핀 하한(조건부), 사양 글에서 전압·정격·피크 읽기",
      str([ok1, over, cont, txt]))
e_only = {"electrical": {"power_circuit_count": 1}, "intake": {"needs_power": True, "has_sensors_or_signals": True}}
rated_only = {"electrical": {"power_circuit_count": 1, "current_A": 0.5}, "intake": {"needs_power": True}}
check(atc.followup_done("E01", e_only) is False and atc.followup_done("E01", rated_only) is False
      and atc.followup_done("E01", {**rated_only, "electrical": {**rated_only["electrical"], "peak_A": 1.0}}) is True
      and atc.followup_done("E01", {**rated_only, "electrical": {**rated_only["electrical"], "peak_A__unknown": True}}) is True,
      "E01 은 회로 수·전류·피크(모르면 '모름')까지 답해야 답한 것(PGR03 최대 전류 확인)")

t3 = asyncio.run(ac.chat_turn(two, "분석해줘", history=[], rec=None,
                              chat=fake_turn({"updates": []}, {"intent": "chat", "answer": "분석 중입니다. 잠시만 기다려 주세요."})))
t4 = asyncio.run(ac.chat_turn(two, "추천 근거 좀 보여줘", history=[], rec=None,
                              chat=fake_turn({"updates": []}, {"intent": "chat", "answer": "왼쪽 추천 근거를 보시면 됩니다."})))
check(t3["intent"] == "recommend" and t4["intent"] == "chat",
      "'분석해줘'(AI 가 '분석 중'이라고만 답해도) → 실제로 다시 판정, '근거 보여줘'는 설명이라 그대로 대화", str([t3["intent"], t4["intent"]]))

groups = {x["item"]: x["group"] for x in atc.evaluate(ok_intake, catalog)["basis"]}
check(groups["툴측 총무게"] == groups["정격 가반하중"] == groups["운전 속도"] == "selection" and groups["포고핀"] == groups["공압"] == "config",
      "근거 나누기: 모델을 고른 근거(무게·계열·정격·속도) / 구성품 수량 근거(포고핀·공압)", str(groups))
# 10) 근거 문장 — '왜 이 제품인가'(사용자 2026-10-02: 숫자만으로는 이유가 안 보임)
bs = {x["item"]: x["sentence"] for x in atc.evaluate(ok_intake, catalog)["basis"]}
check("가장 무거운 전동 그리퍼(툴 1.2kg + 한 번에 드는 제품 2kg)" in bs["툴측 총무게"] and "3.2kg 이상을 받을 수 있는 모델이 필요" in bs["툴측 총무게"]
      and "가장 작은 단계가 TCC1(정격 5kg)" in bs["정격 가반하중"] and "70% 이하라 기본 후보를 그대로 둡니다 — TCC1" in bs["운전 속도"]
      and "협동로봇용 계열" in bs["로봇 종류"],
      "근거 문장: 무게 → 계열 → 가장 작은 단계 → 속도 순으로 왜 골랐는지", json.dumps(bs, ensure_ascii=False)[:500])

# 11) ATC 구성품(v0.8 C14~C20) — 공통(마스터당 CONTROLBOX·4m·1m) + 가변(T.P·PPM/PPF·30cm 케이블·PMM/PMF)
ACC = [{"card_id": 341, "name": "Pogo Pin Male (PPM)", "code": "PPM"}, {"card_id": 343, "name": "Pogo Pin Female (PPF)", "code": "PPF"},
       {"card_id": 337, "name": "PneuMatic Male (PMM)", "code": "PMM"}, {"card_id": 339, "name": "PneuMatic Female (PMF)", "code": "PMF"}]
ai = with_el({"power_circuit_count": 1, "current_A": 0.5, "dio": {"di": 2, "do": 2}})
ai["tools"][0]["installed_quantity"] = 2
ai["tools"][0]["intake"]["needs_air"] = True
ai["tools"][0]["pneumatic"] = {"pressure_bar": {"min": 4, "max": 4}, "required_independent_paths": 1}
ai["robots"][0]["quantity"] = 1
ai["project"]["tool_count"] = 2
ao = atc.evaluate(ai, catalog)
ab = atc.accessory_bom(ao, ai, ACC)
bom = {x["name"]: x for x in ab["items"]}
check(bom["TCC1 MASTER T.C"]["qty"] == 1 and bom["TCC1 MASTER T.C"]["remark"] == "CONTROLBOX·4m 케이블 기본 포함"
      and not any(n == "CONTROLBOX" or n.startswith(("4m CABLE", "1m CABLE")) or n == "로봇측 어댑터" for n in bom)
      and bom["IB (Interface Bracket)"]["optional"] and bom["IB (Interface Bracket)"]["price_item"] == "IB"
      and bom["TCC1 T.P"]["qty"] == 2 and not bom["TCC1 T.P"]["optional"],
      "마스터에 CONTROLBOX·4m 기본 포함(따로 줄 없음), IB 만 별도 옵션, 1m·로봇측 어댑터 줄 없음, T.P 는 실제 교체 툴 2",
      json.dumps(bom, ensure_ascii=False)[:500])
check(bom["Pogo Pin Male (PPM)"]["qty"] == 1 and bom["Pogo Pin Female (PPF)"]["qty"] == 2
      and bom["Cable 0.3m"]["qty"] == 3 and bom["Cable 0.3m"]["price_item"] == "Cable 0.3m" and not bom["Cable 0.3m"]["optional"]
      and not bom["Pogo Pin Male (PPM)"]["remark"]
      and bom["PneuMatic Male (PMM)"]["qty"] == 1 and bom["PneuMatic Female (PMF)"]["qty"] == 2,
      "C16: Cable 0.3m 별도 줄 = PPM 1 + PPF 2 = 3(성균관대 견적과 같은 방식), PMM·PMF 엔 케이블 없음, C12 툴측은 툴마다",
      json.dumps(bom, ensure_ascii=False)[:500])
check(any("고객 제작" in n for n in ab["notes"])
      and any("포고핀 연장 배선 및 그리퍼 결선은 고객사 수행 범위" in n for n in ab["notes"])
      and any("mTS2" in n and "자동 포함하지 않음" in n for n in ab["notes"]) and not any("mTS2" in x["name"] for x in ab["items"]),
      "C20 툴측 어댑터 고객 제작, C19 mTS2 자동 포함 안 함, C17 배선 고객 범위 문구")
mltc = atc.build_catalog([{"card_id": 9, "name": "M-LTC-0010E", "official": True,
                           "card": {"family": "Magbot ATC · M-LTC 공압 툴체인저", "kg": 10}}], lambda card: card.get("kg"))
ind = atc.evaluate({**ok_intake, "robots": [{**ok_intake["robots"][0], "type": "industrial"}]}, mltc)
nb = atc.accessory_bom(ind, ok_intake, ACC)
check(ind["screening_candidates"][0]["models"][0]["name"] == "M-LTC-0010E" and not nb["applicable"]
      and not any(x["name"] in ("CONTROLBOX", "Pogo Pin Male (PPM)") for x in nb["items"]),
      "유선 자동 TCC1~TCV4 가 아니면 액세서리·CONTROLBOX 구성 적용 안 함(내부 확인)", str(nb))
ai3 = json.loads(json.dumps(ai))
ai3["tools"][0]["pneumatic"]["required_independent_paths"] = 3
b3 = {x["name"]: x for x in atc.accessory_bom(atc.evaluate(ai3, catalog), ai3, ACC)["items"]}
check(b3["PneuMatic Male (PMM)"]["qty"] == 2 and b3["PneuMatic Female (PMF)"]["qty"] == 4,
      "C03 유로 3개 → 커플러 2쌍: PMM 2(마스터)·PMF 4(툴 2개 × 2)", json.dumps(b3, ensure_ascii=False)[:300])

print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
for label, detail in FAIL:
    print(f"  ✗ {label}: {detail}")
sys.exit(1 if FAIL else 0)
