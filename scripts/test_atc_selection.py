"""툴체인저 단품 선정(company_knowledge/atc_selection.py) 점검 — 선정데이터 JSON 의 검증 사례(T01~T20, JF01~JF14) 그대로.

    docker compose run --rm --no-deps -e PYTHONPATH=/app -v ./backend/app:/app/app -v ./scripts:/scripts backend \\
        python /scripts/test_atc_selection.py
"""
from __future__ import annotations

import sys

from app.company_knowledge import atc_selection as atc

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


sr = atc.speed_review
# 속도(v0.8 C01·C11 — R03~R07 대체): 자동 단계 상향 없음. 협동 70 초과는 상위 후보 비교, 산업용은 속도 대신 약 2배 하중
check(sr("cobot", 70, "%", True)["compare_upper"] is False, "T01 협동 70 → 기본 후보(R03)")
check(sr("cobot", 71, "%", True)["compare_upper"] is True and sr("cobot", 71, "%", True)["rule"] == "C11",
      "T02(C11) 협동 71 → 자동 +1 아님, 상위 후보 비교")
check(all(sr("industrial", v, "%", True)["compare_upper"] is False and sr("industrial", v, "%", True)["rule"] == "C01"
          for v in (49, 50, 60)), "T03~T05(C01) 산업용 49·50·60 → 속도별 단계 상향 없음(약 2배 하중으로 대체)")
check(sr("cobot", 60, None, True)["reason"] == "P01", "T06 단위 없음 → P01")
check(sr("cobot", 60, "%", False)["compare_upper"] is None and sr("cobot", 60, "%", False)["reason"] == "P01",
      "P01 기준이 없으면 속도 판단 자동 적용 안 함")
pv = [sr("cobot", 60, "%"), sr("cobot", 80, "%"), sr("cobot", 60, "mm/s")]
check(pv[0]["provisional"] == ["P01"] and pv[1]["compare_upper"] and pv[2]["reason"] == "P01",
      "잠정 P01(오버라이드 %)만 남음 — % 가 아닌 단위는 자동 판단 안 함", str(pv))
check(set(atc.PROVISIONAL) == {"P01"}, "v0.8 이 대체한 잠정 기준(P02·P03·P05·P07) 제거")
check(atc.screening_payload(45, "industrial") == 90 and atc.screening_payload(45, "cobot") == 45,
      "C01 산업용 선정 하중 = 툴측 총무게 × 2, 협동로봇은 그대로")

ps = atc.pressure_screen
check(ps("compressed_air", 1, 5)["screening"] == "standard_range_only" and not ps("air", 1, 5)["review"], "T07 1~5bar 표준 범위")
a10 = ps("air", 5.5, 5.5)
check(a10["review"] and a10["screening"] != "standard_range_only" and a10["reason"] == "PGR23", "A10 5.5bar → 표준 밖, 별도 검토(v1.2 PGR23)")
check(ps("air", 6, 6)["review"] and ps("air", 0.5, 3)["review"], "T09 6bar·1bar 미만 → 별도 검토")
vac = ps("진공", None, None)
check(vac["screening"] == "vacuum" and not vac["review"] and "양압" in vac["note"],
      "C06 진공 → 커플러 산정 흐름에 포함, 양압 1~5bar 기준 미적용(자동 검토 아님)")

check(atc.pogo_lower_bound(1 + 1 + 2 + 2) == 1, "T10 핀 6개 → 포고핀 모듈 하한 1")
check(atc.power_pins([0.7, 0.7], shared_return=True) == 4, "T11(C05) 공급 0.7A×2 + 공유 리턴 1.4A → 공급 2핀 + 리턴 2핀")
check([atc.pins_for(a) for a in (0.5, 1, 2, 3, 2.5)] == [1, 1, 2, 3, 3], "C05 병렬: 1A 이하 1핀, 2A 2핀, 3A 3핀, 2.5A 3핀")
check(atc.power_pins([2]) == 4 and atc.power_pins([3]) == 6, "C05 2A 회로 = 공급 2+리턴 2, 3A = 3+3")
t13 = atc.slot_check(2, 2)
check(t13["slot_sum"] == 4 and t13["customization_required"] is True, "A02 포고핀 2 + 에어 2 = 4개소 → 커스텀 필요(C04)")
a01 = atc.slot_check(2, 1)
check(a01["slot_sum"] == 3 and a01["standard_layout_possible"] and not a01["customization_required"],
      "A01 포고핀 2 + 에어 1 = 3개소 → 표준 이내(최종 호환 승인은 아님)")
check(atc.air_couplers(3) == 2 and atc.air_couplers(2) == 1 and atc.air_couplers(1) == 1 and atc.air_couplers(None) is None,
      "A03(C03) 독립 유로 3 → 커플러 2쌍(2개소), 2 → 1, 1 → 1, 모름 → 미정")
check(atc.slot_check(1, None)["slot_sum"] is None, "T14 공압 모듈 수 미정이면 장착 위치 합도 미정")
check(atc.tool_plates([{"installed_quantity": 1}, {"installed_quantity": 2}], spare=1) == {"operating": 3, "spare": 1, "total": 4},
      "T18 툴 3개(2종)+예비 1 → 툴플레이트 4")

CAT = [{"card_id": i, "name": n, "series": s, "series_label": s, "payload_kg": p, "official": True, "wireless": n == "TCW1"}
       for i, (n, s, p) in enumerate([("TCC1", "auto", 5), ("TCV1", "auto", 10), ("TCW1", "auto", 10), ("TCV2", "auto", 16),
                                      ("TCV3", "auto", 25), ("TCV4", "auto", 30), ("TCHK100", "industrial", 100),
                                      ("TCHK150", "industrial", 150), ("MTC", "manual", 16), ("DTC", "dual", 16),
                                      ("M-LTC-0010E", "mltc", 10), ("M-LTC-0020D", "mltc", 20)])]


def tool(name="그리퍼", qty=1, mass=3.0, wp=2.0, tilt=False, power=False, air=False, sens=False, **kw):
    return {"name": name, "installed_quantity": qty,
            "intake": {"dimensions_mm": {"x": 200, "y": 150, "z": 100}, "tool_assembly_mass_kg": mass,
                       "tilts_or_flips": tilt, "needs_power": power, "needs_air": air, "has_sensors_or_signals": sens},
            "mass_components": {"max_simultaneous_workpieces_kg": wp}, **kw}


def intake(tools, rtype="cobot", speed=60, unit="%", count=None, cables=(True, True), env=False):
    return {"project": {"tool_count": count if count is not None else sum(t["installed_quantity"] for t in tools),
                        "process": {"category": "transfer"}, "environment": {"has_special_conditions": env}},
            "robots": [{"type": rtype, "manufacturer": "두산", "model": "M1013", "quantity": 1,
                        "speed": {"value": speed, "unit": unit},
                        "cables": {"master_4m_sufficient": cables[0], "system_1m_sufficient": cables[1]}}],
            "tools": tools}


def fids(it, idx=None):
    return [f["id"] for f in atc.followups(it) if idx is None or f["entity_index"] == idx]


# JF01~JF06 후속 질문
check({"F03", "F04"} <= set(fids(intake([tool(power=False, sens=True)]))) and "F02" not in fids(intake([tool(power=False, sens=True)])),
      "JF01 전기 없음·센서 있음 → F03·F04, F02 숨김")
check(not {"F05", "F06"} & set(fids(intake([tool(air=False)]))), "JF02 공압 없음 → F05·F06 숨김")
unk = tool()
unk["intake"].update(needs_power=None, needs_air=None, has_sensors_or_signals=None)
check("F12" in fids(intake([unk])) and "F02" not in fids(intake([unk])), "JF03 모두 모름 → F12(모름은 없음이 아님)")
jf4 = intake([tool()], cables=(False, True))
check("F07" in fids(jf4), "JF04 마스터 케이블 부족 → F07")
jf5 = fids(intake([tool(tilt=True)]))
check({"F01", "F08"} <= set(jf5) and not any(x.startswith("Q") for x in jf5), "JF05 기울임 → F01·F08, 엔지니어 질문은 숨김")
jf6 = intake([tool("툴1", air=True), tool("툴2", air=False)])
check(set(fids(jf6, 0)) >= {"F05", "F06"} and not {"F05", "F06"} & set(fids(jf6, 1)), "JF06 공압 질문은 공압 쓰는 툴에만")

# 판정
r7 = atc.evaluate(intake([tool(mass=None)]), CAT)
check(r7["intake_completed"] and r7["final_selected_model_id"] is None and r7["status"] == "needs_information",
      "JF07 툴 무게 모름 → 입력 완료는 되고 최종 선정은 막힘", r7["status"])
check(atc.evaluate(intake([tool(qty=1), tool(qty=2)], count=3), CAT)["information_reasons"] == [], "JF08 툴 수량 일치")
check(any("불일치" in r["text_ko"] for r in atc.evaluate(intake([tool(qty=1), tool(qty=1)], count=3), CAT)["information_reasons"]),
      "JF09 툴 수량 불일치 → 다시 확인")
r12 = atc.evaluate(intake([tool(air=True, pneumatic={"pressure_bar": {"min": 4, "max": 5}, "observed_hose_count": 2})]), CAT)
check(r12["pneumatic_modules"] is None and any("공압 동작 방식" in m for m in r12["missing_fields"])
      and "E03" in {q["id"] for q in r12["pending_questions"]},
      "JF12 호스 2개 보여도 공압 모듈 수는 자동 산정 안 함 — 공압 회로(복동·단동·밸브 위치, E03)를 묻는다")
r12b = atc.evaluate(intake([tool(air=True, pneumatic={"pressure_bar": {"min": 4, "max": 5}, "required_independent_paths": 3})]), CAT)
check(r12b["pneumatic_modules"] == 2 and r12b["slot_check"]["slot_sum"] == 2,
      "C03: 독립 유로 3개 → 에어 커플러 PMM·PMF 2쌍(2개소)", str(r12b["pneumatic_tools"]))
vt = intake([tool(air=True, pneumatic={"medium": "진공", "required_independent_paths": 1})])
rv = atc.evaluate(vt, CAT)
check(rv["pneumatic_modules"] == 1 and not any(r["rule"] == "C06" for r in rv["engineering_review_reasons"])
      and not any("사용 압력" in m for m in rv["missing_fields"]),
      "C06 진공 툴 → 커플러 1쌍 산정, 양압 압력 칸을 요구하지 않음")
check(atc.tool_side_mass(tool(mass=3.0, wp=2.0))["kg"] == 5.0, "JF14 툴 3kg + 제품 2kg = 5kg(포함 부품 중복 없이)")
check(atc.tool_side_mass(tool(wp=None, workpiece_not_applicable=True))["kg"] == 3.0, "제품을 안 드는 툴은 제품 무게 0(해당 없음)")
check(atc.tool_side_mass(tool(wp=None))["kg"] is None and atc.tool_side_mass(tool(wp=None))["lower_bound_kg"] == 3.0,
      "제품 무게 모름 → 총무게 미정, 하한만")

r16 = atc.evaluate(intake([tool(tilt=True)]), CAT)
check(r16["final_selected_model_id"] is None and any(r["rule"] == "R08" for r in r16["engineering_review_reasons"]),
      "T16 기울임·무게중심 모름 → 최종 모델 없음, 검토")
check(atc.evaluate(intake([tool()]), [])["screening_candidates"] == [] and atc.evaluate(intake([tool()]), [])["final_selected_model_id"] is None,
      "T17 모델표 비면 후보·최종 모델 없음(모델명 만들지 않음)")
r19 = atc.evaluate(intake([tool(power=True, electrical={"control_method_raw": "RS-485"})]), CAT)
check(any(r["rule"] == "PGR19" and r["ref"] == "R13" for r in r19["engineering_review_reasons"]), "T19 RS-485 → 통신 검증 검토(PGR19)")
r15 = intake([tool()])
r15["robots"][0]["cables"]["master_to_controller_required_m"] = 4.5
check(any(r["rule"] == "R17" for r in atc.evaluate(r15, CAT)["engineering_review_reasons"]), "T15 마스터 케이블 4.5m → 검토")

# 후보(사용자 2026-10-02: 툴체인저 전 종류 후보군)
c5 = atc.evaluate(intake([tool(mass=3, wp=2)]), CAT)
auto = next(c for c in c5["screening_candidates"] if c["series"] == "auto")
check([m["name"] for m in auto["models"]] == ["TCC1"] and c5["screening_candidates"][0]["series"] == "auto"
      and [m["name"] for m in auto["consider_up_to"]] == ["TCV1"]
      and any(r["rule"] == "C11" for r in c5["engineering_review_reasons"]),
      "협동로봇 5kg → 가장 작은 TCC1(R02), 정격에 걸려 상위 TCV1 과 비교·담당자 확정(C11, 무선 TCW1 은 비교에서도 제외)",
      str([m["name"] for m in auto["models"]]))
check({c["series"] for c in c5["screening_candidates"]} >= {"auto", "mltc", "manual", "dual"}
      and "industrial" not in {c["series"] for c in c5["screening_candidates"]},
      "협동로봇: 자동·M-LTC·MTC·DTC 후보, 산업용 H시리즈는 제외")
ci = atc.evaluate(intake([tool(mass=25, wp=10)], rtype="industrial", speed=60), CAT)
i0 = ci["screening_candidates"][0]
check(i0["series"] == "industrial" and i0["models"][0]["name"] == "TCHK100" and ci["screening_payload_kg"] == 70
      and ci["required_payload_kg"] == 35 and not i0.get("consider_up_to"),
      "A08(C01) 산업용 35kg × 2 = 70kg → TCHK100, 속도 60% 로 단계를 또 올리지 않음",
      str([(c["series"], [m["name"] for m in c["models"]]) for c in ci["screening_candidates"]]))
check(any(c.get("out_of_range") for c in ci["screening_candidates"] if c["series"] == "auto"), "70kg 은 자동 툴체인저 범위 밖으로 표시")
ci2 = atc.evaluate(intake([tool(mass=60, wp=30)], rtype="industrial", speed=40), CAT)
check(next(c for c in ci2["screening_candidates"] if c["series"] == "industrial").get("out_of_range"),
      "산업용 90kg × 2 = 180kg → H시리즈 최대 150kg 초과, 내부 검토(R09)")
check(c5["final_selected_model_id"] is None and not any(r.get("ref") == "P04" for r in c5["engineering_review_reasons"])
      and c5["catalog_approved"] and c5["junior_summary"]["evidence_status"] == "제품 DB 공식 사양 모델"
      and any(x["id"] == "P04" for x in c5["db_confirmed"]),
      "P04(제품 DB): 공식 사양 모델은 모델·정격 확정 — 최종 모델 확정은 담당자(비움)")
CAT_U = [{**x, "official": False} if x["series"] == "auto" else x for x in CAT]
cu = atc.evaluate(intake([tool(mass=3, wp=1)]), CAT_U)
check(any(r.get("ref") == "P04" for r in cu["engineering_review_reasons"]) and not cu["catalog_approved"]
      and cu["junior_summary"]["evidence_status"].startswith("참고 후보"),
      "P04: 제품 DB 사양이 미확정(†·확인 필요)인 모델은 정격 확인 전 참고 후보")
fast = atc.evaluate(intake([tool(mass=3, wp=3)], speed=80), CAT)
fa = next(c for c in fast["screening_candidates"] if c["series"] == "auto")
check([m["name"] for m in fa["models"]] == ["TCV1"] and [m["name"] for m in fa["consider_up_to"]] == ["TCV2"]
      and fast["final_selected_model_id"] is None and any("70 초과" in r["text_ko"] for r in fast["engineering_review_reasons"]),
      "C11 협동 80% 6kg → 기본 TCV1 유지, 상위 TCV2 비교(자동 상향 없음), 최종은 담당자", str(fa))
top = atc.screen_candidates(CAT, 28, "cobot", {"compare_upper": True})
ta = next(c for c in top if c["series"] == "auto")
check([m["name"] for m in ta["models"]] == ["TCV4"] and not ta.get("out_of_range") and any("R09" in n for n in ta["notes"]),
      "상위 비교할 모델 없음 → 기본 TCV4 유지, 내부 검토(R09)")
check(all(m["name"] != "TCW1" for c in atc.screen_candidates(CAT, 8, "cobot", {}) for m in c["models"])
      and [m["name"] for m in next(c for c in atc.screen_candidates(CAT, 8, "cobot", {}, wireless=True)
                                   if c["series"] == "auto")["models"]] == ["TCV1", "TCW1"],
      "C09 유선 기본: 무선 TCW1 은 무선을 고른 경우에만 후보")
# 포고핀(C05) — 병렬 핀
pp = atc.evaluate(intake([tool(power=True, electrical={"power_circuit_count": 1, "current_A": 3, "dio": {"di": 2, "do": 2}})]), CAT)
p0 = pp["pogo_tools"][0]
check(p0["pins"] == 10 and p0["modules"] == 2 and pp["pogo_modules"] == 2 and p0["parallel"]
      and not any("병렬 핀으로 자동 증설하지 않음" in r["text_ko"] for r in pp["engineering_review_reasons"]),
      "C05 3A 1회로 + DI2·DO2 → 공급 3+리턴 3+4 = 10핀 → 모듈 2개(2개소)", str(p0))
pk = atc.tool_pins(tool(power=True, electrical={"power_circuit_count": 1, "current_A": 0.8, "peak_A": 2.5, "dio": {"di": 0, "do": 0}}))
check(pk["design_A"] == 2.5 and pk["power_pins"] == 6 and pk["pins"] == 6,
      "v1.2 PGR03: 정격 0.8A·피크 2.5A → 설계 전류 2.5A, 공급 3+리턴 3 = 6핀(피크로 핀 수를 정함)", str(pk))
nc = atc.tool_pins(tool(power=True, electrical={"power_circuit_count": 1, "dio": {"di": 1, "do": 1}}))
check(nc["pins"] is None and "전원 전류(정격·피크)" in nc["missing"], "PGR03 전류를 모르면 핀 수를 정하지 않음(0A 로 보지 않음)")
sixteen = atc.slot_check(atc.pogo_lower_bound(16), 1)
check(sixteen["slot_sum"] == 3 and not sixteen["customization_required"], "C05 16핀 = 8핀 모듈 2개·2개소(+에어 1 = 3개소 표준)")
js = c5["junior_summary"]
check(js["candidate_model"].startswith("TCC1") and js["tool_plate_quantity"] == 1 and "필요 없음" in js["pogo_modules"],
      "영업 화면 결과: 후보·툴플레이트·포고핀·공압·케이블", str(js))
# AI 확인 질문 — 비어 있는 핵심 칸 + 답이 없는 후속 질문만, '모름'은 묻지 않고 자료 요청으로
pq = intake([tool(power=True, sens=True)])
ids = [q["id"] for q in atc.pending_questions(pq)]
check({"F02", "F03", "F04"} <= set(ids) and not any(q["kind"] == "basic" for q in atc.pending_questions(pq)),
      "전기·센서 툴, 추가 질문 표가 비어 있으면 → F02·F03·F04 를 묻는다(기본 칸은 다 채워져 묻지 않음)", str(ids))
pq["project"]["followups_answered"] = ["F02", "F03", "F04"]
check(not {"F02", "F03", "F04"} & {q["id"] for q in atc.pending_questions(pq)}, "질문지 추가 질문 표에 답이 있으면 다시 묻지 않는다")
blank = intake([tool()])
blank["robots"][0]["speed"] = {"value": None, "unit": None}
check(any(q["id"] == "J06" and q["kind"] == "basic" for q in atc.pending_questions(blank)), "속도가 빈칸이면 J06 을 묻는다")
blank["robots"][0]["speed"] = {"value": None, "unit": None, "value__unknown": True, "unit__unknown": True}
check(not any(q["id"] == "J06" for q in atc.pending_questions(blank))
      and any("속도 설정 화면 사진" in r for r in atc.data_requests(blank)),
      "속도가 '모름'이면 다시 묻지 않고 '속도 화면 사진' 자료 요청")
part = intake([tool(power=True)])
part["tools"][0]["electrical"] = {"power_circuits": ["24V 1.5A"]}
check("F02" not in {q["id"] for q in atc.pending_questions(part)},
      "후속 질문에 한 칸이라도 답했으면 다시 묻지 않는다(라벨 칸이 비어 있어도 — 결과 추가 확인사항에 안 나옴)")
unk3 = tool(mass=None, wp=None, workpiece_not_applicable=True)
unk3["intake"].update(needs_power=None, needs_air=None, has_sensors_or_signals=None)
r3 = atc.evaluate(intake([unk3]), CAT)
check(r3["junior_summary"]["pogo_modules"].startswith("미정") and r3["junior_summary"]["pneumatic_modules"].startswith("미정")
      and r3["tool_side_mass"][0]["lower_bound_kg"] is None,
      "전기·공압 '모름'은 '필요 없음'이 아니라 '미정', 무게 모름+제품 해당 없음은 '최소 0kg' 아닌 모름", str(r3["junior_summary"]))
na = intake([tool(wp=None, workpiece_not_applicable=True)])
check(not any(q["id"] == "J05" for q in atc.pending_questions(na)), "제품을 안 드는 툴은 제품 무게를 묻지 않는다")
check(len(atc.SPEC["question_bank"]) == 10 and len(atc.SPEC["followup_question_bank"]) == 12, "질문지 J01~J10 · F01~F12 원본")

print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
for label, detail in FAIL:
    print(f"  ✗ {label}: {detail}")
sys.exit(1 if FAIL else 0)
