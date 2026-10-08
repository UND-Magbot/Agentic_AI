"""맥봇 ATC 액세서리 통합 선정 v1.2 — 규칙 원본의 예제(worked_examples E01~E24)를 계산기(atc_accessory.py)로 재현.

    docker compose run --rm --no-deps -e PYTHONPATH=/app -v ./backend/app:/app/app -v ./scripts:/scripts backend \\
        python /scripts/test_atc_accessory.py
"""
from __future__ import annotations

import sys

from app.company_knowledge import atc_accessory as acc

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


def tool(name="툴", qty=1, *, power=True, sensors=True, air=False, el=None, pn=None):
    return {"name": name, "installed_quantity": qty,
            "intake": {"needs_power": power, "has_sensors_or_signals": sensors, "needs_air": air},
            "electrical": el or {}, "pneumatic": pn or {}}


def ex(eid: str) -> dict:
    return next(e for e in acc.SPEC["worked_examples"] if e["id"] == eid)


# 상수는 원본에서
check((acc.PINS_PER_MODULE, acc.PIN_CURRENT_A, acc.STD_POSITIONS, acc.PATHS_PER_COUPLER, acc.STD_BAR) == (8, 1, 3, 2, (1, 5)),
      "회사 확정 상수: 8핀·1A·3개소·2유로·1~5bar")
check(acc.to_bar(0.6, "MPa") == 6 and acc.to_bar(400, "kPa") == 4 and acc.to_bar(5, "bar") == 5 and acc.to_bar(1, "psi") is None,
      "E21·PGR23 단위 환산(MPa×10·kPa÷100, 모르는 단위는 None)")
check([acc.pins_for(a) for a in (0.1, 1, 1.5, 3, 2.2)] == [1, 1, 2, 3, 3], "PGR04 경로당 ceil(I/1A)")

# E01 AG-160-95: 정격 0.8·피크 1.5, RS485 2선, 공통선 0 → 6핀·1모듈·여유 2
E01 = acc.tool_electrical(tool(el={"power_circuit_count": 1, "voltage_V": 24, "current_A": 0.8, "peak_A": 1.5,
                                   "comm_interface": "RS485", "rs485_wires": 2, "extra_pins": 0}))
x = ex("E01")["expected"]
check((E01["power_pins"], E01["signal_pins"], E01["extra_pins"], E01["pins"], E01["modules"], E01["spare_pins"])
      == (x["power_pins"], x["signal_pins"], x["extra_pins"], x["total_pins"], x["pogo_module_pairs_lower_bound"], x["spare_pins_at_lower_bound"])
      and E01["status"] == "confirmed", "E01 첨부 AG-160-95 RS485 2선 → 6핀·1모듈·여유 2(피크 1.5A 기준 4핀)", str(E01))
lay = acc.electrical_layout([E01])
check(lay["modules"][0] == ["+24V", "+24V", "0V", "0V", "RS485_A", "RS485_B", "미사용", "미사용"],
      "E01 논리 배정 +24V·+24V·0V·0V·A·B·미사용 2", str(lay["modules"]))

# E02 SG 1핀 추가 → 7핀 / E03 I/O 4 → 8핀 / E04 I/O 4 + COM 1 → 9핀·2모듈 / E05 3A + RS485 → 8핀
for eid, el in (("E02", {"comm_interface": "RS485", "rs485_wires": 2, "extra_pins": 1}),
                ("E03", {"comm_interface": "digital_IO", "dio": {"di": 2, "do": 2}, "extra_pins": 0}),
                ("E04", {"comm_interface": "digital_IO", "dio": {"di": 2, "do": 2}, "extra_pins": 1}),
                ("E05", {"comm_interface": "RS485", "rs485_wires": 2, "extra_pins": 0})):
    x = ex(eid)["expected"]
    cur = ex(eid)["input"]["current_A"]
    r = acc.tool_electrical(tool(el={"power_circuit_count": 1, "current_A": cur, "peak_A": cur, **el}))
    check((r["power_pins"], r["signal_pins"], r["extra_pins"], r["pins"], r["modules"], r["spare_pins"])
          == (x["power_pins"], x["signal_pins"], x["extra_pins"], x["total_pins"], x["pogo_module_pairs_lower_bound"],
              x["spare_pins_at_lower_bound"]), f"{eid} {ex(eid)['name_ko']}", str(r))

# E06·E07 전기 + 공압 → 장착 위치(3개소 이내 / 4개소 커스텀)
for eid, paths in (("E06", 2), ("E07", 3)):
    x = ex(eid)["expected"]
    t = tool(air=True, el={"power_circuit_count": 1, "current_A": 3, "peak_A": 3, "comm_interface": "digital_IO",
                           "dio": {"di": 2, "do": 2}, "extra_pins": 0}, pn={"required_independent_paths": paths})
    r = acc.evaluate([t], 1, pogo_needed=True, air_needed=True)
    check((r["master"]["ppm_per_master"], r["master"]["pmm_per_master"], r["master"]["positions"], r["master"]["customization_required"])
          == (x["pogo_module_pairs_lower_bound"], x["air_module_pairs_lower_bound"], x["mounting_positions_lower_bound"],
              x["custom_required_by_count"]), f"{eid} {ex(eid)['name_ko']}", str(r["master"]))

# E08 I/O 지원만, 신호 수 모름 → 전원 4핀 초안, 합계 None, 자료 필요
r = acc.tool_electrical(tool(el={"power_circuit_count": 1, "current_A": 0.8, "peak_A": 1.5, "control_method_raw": "I/O"}))
check(r["power_pins"] == 4 and r["pins"] is None and r["modules"] is None and r["status"] == "needs_data"
      and any("DI·DO" in m for m in r["missing"]), "E08 I/O 신호 수 모름 → 전원 4핀만, 합계·모듈 미정(needs_data)", str(r))
# E09 DH-3 통신 방식(실제 통과 구간) 미확인 → 전원 2핀 초안, 합계 None
r = acc.tool_electrical(tool(el={"power_circuit_count": 1, "current_A": 0.5, "peak_A": 1}))
check(r["power_pins"] == 2 and r["pins"] is None and r["status"] == "needs_data", "E09 DH-3 통신 구간 미확인 → 전원 2핀, 합계 미정", str(r))
r = acc.tool_electrical(tool(el={"power_circuit_count": 1, "current_A": 0.5, "peak_A": 1, "comm_interface": "other"}))
check(r["pins"] is None and any("물리 인터페이스" in m for m in r["missing"]), "기타 통신(EtherCAT 등)은 핀 수를 고정하지 않음")

# E10 교체 툴 A(3A RS485)·B(1.5A I/O 4) — 각 하한 1, 마스터 12접점 → PPM 2, PPF A1·B2, 케이블 5
A = tool("툴A", el={"power_circuit_count": 1, "voltage_V": 24, "current_A": 3, "peak_A": 3, "comm_interface": "RS485",
                     "rs485_wires": 2, "extra_pins": 0})
B = tool("툴B", el={"power_circuit_count": 1, "voltage_V": 24, "current_A": 1.5, "peak_A": 1.5, "comm_interface": "digital_IO",
                     "dio": {"di": 2, "do": 2}, "extra_pins": 0})
r = acc.evaluate([A, B], 1, pogo_needed=True, air_needed=False)
x = ex("E10")["expected"]
lay = r["master"]["electrical_modules"]
check([t["modules"] for t in r["electrical_tools"]] == [x["tool_A_individual_lower_bound"], x["tool_B_individual_lower_bound"]]
      and r["master"]["unique_contacts"] == x["fixed_master_unique_net_contacts"] and r["master"]["ppm_per_master"] == x["PPM_candidate"],
      "E10 툴별 하한 1·1, 마스터 고유 접점 12 → PPM 2", str(r["master"]))
check(lay[0] == ["+24V"] * 3 + ["0V"] * 3 + ["RS485_A", "RS485_B"] and lay[1][:4] == ["DO1", "DO2", "DI1", "DI2"],
      "E10 공통 배치 E1(전원·RS485)·E2(I/O)", str(lay))
tl = {t["tool"]: t["PPF"] for t in r["tool_layouts"]}
check((tl["툴A"], tl["툴B"], r["bom"]["PPF"], r["bom"]["PPM_30cm_cable"] + r["bom"]["PPF_30cm_cable"])
      == (x["tool_A_PPF_with_this_layout"], x["tool_B_PPF_with_this_layout"], x["PPF_total_for_one_each"], x["cable_total"]),
      "E10 이 배치에서 PPF 툴A 1·툴B 2 = 3, 30cm 케이블 5", str(r["bom"]))

# E11 툴A 전기 2모듈·툴B 공압 2모듈 → 마스터 4개소 커스텀
A = tool("툴A", el={"power_circuit_count": 1, "current_A": 3, "peak_A": 3, "comm_interface": "digital_IO", "dio": {"di": 2, "do": 2},
                     "extra_pins": 0})
B = tool("툴B", power=False, sensors=False, air=True, pn={"required_independent_paths": 4})
r = acc.evaluate([A, B], 1, pogo_needed=True, air_needed=True)
check(r["master"]["ppm_per_master"] == 2 and r["master"]["pmm_per_master"] == 2 and r["master"]["positions"] == 4
      and r["master"]["customization_required"], "E11 서로 다른 툴의 전기·공압 자원 합집합 → 4개소 커스텀", str(r["master"]))

# E12 AG 하나씩 단 교체 툴 2개 → PPM 1·PPF 2·케이블 3
ag = {"power_circuit_count": 1, "voltage_V": 24, "current_A": 0.8, "peak_A": 1.5, "comm_interface": "RS485", "rs485_wires": 2, "extra_pins": 0}
r = acc.evaluate([tool("툴1", el=ag), tool("툴2", el=ag)], 1, pogo_needed=True, air_needed=False)
x = ex("E12")["expected"]
check((r["bom"]["PPM"], r["bom"]["PPF"], r["bom"]["PPM_30cm_cable"], r["bom"]["PPF_30cm_cable"], r["bom"]["PMM"], r["bom"]["PMF"])
      == (x["PPM"], x["PPF"], x["PPM_30cm_cable"], x["PPF_30cm_cable"], x["PMM"], x["PMF"]), "E12 교체 툴 2개 → PPM 1·PPF 2·케이블 1+2", str(r["bom"]))
# 같은 툴 2개(수량 2)도 같은 결과 — 툴측은 실물 수량만큼
r2 = acc.evaluate([tool("툴", qty=2, el=ag)], 1, pogo_needed=True, air_needed=False)
check(r2["bom"]["PPF"] == 2 and r2["bom"]["PPM"] == 1, "PGR15 툴측 수량 = 툴 수량 × 툴별 모듈")

# E13 한 T.P 에 AG 두 개 동시 동작(같은 공급·리턴 3A): 공통 버스 8핀·1쌍 / 독립 링크 10핀·2쌍
x = ex("E13")["expected"]
shared = acc.tool_electrical(tool(el={"power_circuit_count": 1, "current_A": 3, "peak_A": 3, "comm_interface": "RS485",
                                      "rs485_wires": 2, "extra_pins": 0}))
two = acc.tool_electrical(tool(el={"power_circuit_count": 1, "current_A": 3, "peak_A": 3, "comm_interface": "RS485",
                                   "rs485_wires": 4, "extra_pins": 0}))
check(shared["power_pins"] == x["power_contacts"] and shared["pins"] == x["shared_approved_RS485_bus"]["total_contacts"]
      and two["pins"] == x["two_independent_RS485_links"]["total_contacts"] and two["modules"] == 2,
      "E13 동시 부하 3A → 전원 6핀, 공통 버스 8핀·1쌍 / 신호 4선 10핀·2쌍", f"{shared['pins']} {two['pins']}")

# E14 RS485만, 선식 미확인 → 조건부 2선(8핀·1쌍)과 4선(10핀·2쌍) 두 계산, 질문
r = acc.tool_electrical(tool(el={"power_circuit_count": 1, "current_A": 3, "peak_A": 3, "control_method_raw": "RS485", "extra_pins": 0}))
x = ex("E14")["expected"]
check(r["status"] == "conditional" and [(s["pins"], s["modules"]) for s in r["scenarios"]]
      == [(x["conditional_2wire_without_extras"]["total_contacts"], 1), (x["conditional_4wire_without_extras"]["total_contacts"], 2)]
      and any("2선식" in q for q in r["questions"]), "E14 RS485 선식 미확인 → 조건부 2선 8핀·1쌍 / 4선 10핀·2쌍 + 질문", str(r["scenarios"]))

# E15~E19 공압 프로파일
air = lambda pn: acc.tool_air(tool(power=False, sensors=False, air=True, pn={"extra_paths": 0, **pn}))  # noqa: E731
cases = [("E15", {"actuation": "double_acting", "valve_location": "robot_side", "gripper_count": 1}, 2, 1),
         ("E16", {"actuation": "double_acting", "valve_location": "robot_side", "gripper_count": 2, "control_relationship": "independent"}, 4, 2),
         ("E17", {"actuation": "double_acting", "valve_location": "robot_side", "gripper_count": 2, "control_relationship": "approved_shared"}, 2, 1),
         ("E18", {"actuation": "single_acting", "valve_location": "robot_side", "gripper_count": 1}, 1, 1),
         ("E19", {"actuation": "double_acting", "valve_location": "tool_side", "gripper_count": 1}, 1, 1)]
for eid, pn, paths, mods in cases:
    r = air(pn)
    x = ex(eid)["expected"]
    check(r["paths"] == ex(eid)["input"]["confirmed_ATC_crossing_air_paths"] == paths and r["modules"] == x["air_module_pairs_lower_bound"] == mods,
          f"{eid} {ex(eid)['name_ko']} → 유로 {paths}·커플러 {mods}", str(r))
r = air({"actuation": "double_acting", "valve_location": "robot_side", "gripper_count": 2})
check(r["paths"] == 4 and r["status"] == "conditional", "PGR21 그리퍼 2개 제어 관계 모름 → 개별 제어(4유로) 가정, 공통 분기로 줄이지 않음")
# E19 툴측 밸브 → 밸브 전원·제어선은 포고핀 산정 대상
check(acc.needs_electrical(tool(power=False, sensors=False, air=True, pn={"valve_location": "tool_side"})) is True,
      "PGR22 툴측 밸브면 공압그리퍼도 포고핀 산정 대상")

# E20 공통 유로가 호환되는 교체 공압 툴 2개 → PMM 1·PMF 2
t1 = tool("툴1", power=False, sensors=False, air=True, pn={"required_independent_paths": 2})
t2 = tool("툴2", power=False, sensors=False, air=True, pn={"required_independent_paths": 2})
r = acc.evaluate([t1, t2], 1, pogo_needed=False, air_needed=True)
check((r["bom"]["PMM"], r["bom"]["PMF"], r["master"]["pmm_per_master"]) == (1, 2, 1), "E20 교체 공압 툴 2개 → PMM 1·PMF 2", str(r["bom"]))

# E21 실제 공급 0.6MPa → 6bar, 표준 밖 검토(모듈은 1 그대로)
r = air({"actuation": "double_acting", "valve_location": "robot_side", "gripper_count": 1,
         "pressure_bar": {"min": acc.to_bar(0.6, "MPa"), "max": acc.to_bar(0.6, "MPa")}})
check(r["modules"] == 1 and r["pressure"]["fit"] is False and r["pressure"]["review"], "E21 0.6MPa=6bar → 표준 밖 검토, 모듈 수는 그대로 1", str(r["pressure"]))

# E22 복동 그리퍼(로봇측 밸브) + 3선식 센서 2개 → 포고핀 4핀·PPM/PPF 1, 공압 1, 2개소
t = tool(power=False, sensors=True, air=True,
         el={"power_circuit_count": 1, "current_A": 0.1, "peak_A": 0.1, "comm_interface": "digital_IO", "dio": {"di": 2, "do": 0}, "extra_pins": 0},
         pn={"actuation": "double_acting", "valve_location": "robot_side", "gripper_count": 1, "extra_paths": 0})
r = acc.evaluate([t], 1, pogo_needed=True, air_needed=True)
x = ex("E22")["expected"]
e = r["electrical_tools"][0]
check((e["power_pins"], e["signal_pins"], e["pins"], r["bom"]["PPM"], r["bom"]["PPF"], r["bom"]["PMM"], r["bom"]["PMF"],
       r["bom"]["PPM_30cm_cable"], r["bom"]["PPF_30cm_cable"], r["master"]["positions"])
      == (x["power_contacts"], x["signal_contacts"], x["total_contacts"], x["PPM"], x["PPF"], x["PMM"], x["PMF"],
          x["PPM_30cm_cable"], x["PPF_30cm_cable"], x["master_positions"]), "E22 공압그리퍼 + 센서 2개 → 포고핀 4핀·공압 1·2개소", str(r["bom"]))

# E23 복동인데 밸브 위치 모름 → 확정 유로·모듈 None, 질문, 두 시나리오(로봇측 2·툴측 1, 둘 다 1모듈 하한)
r = air({"actuation": "double_acting", "gripper_count": 1})
x = ex("E23")["expected"]
check(r["paths"] is None and r["modules"] is None and r["status"] == "needs_data"
      and [s["paths"] for s in r["scenarios"]] == [x["conditional_robot_side_valve_paths"], x["conditional_tool_side_single_supply_paths"]]
      and r["lower_bound_modules"] == 1 and any("밸브가 로봇측" in q for q in r["questions"]),
      "E23 밸브 위치 누락 → 확정값 None, 시나리오 2유로/1유로, 질문", str(r))

# E24 카탈로그 최대 7bar, 실제 4bar → 압력 범위 후보(유량·파지력 승인 아님)
p = acc.pressure_check("압축공기", 4, 4)
check(p["fit"] is True and not p["review"] and "유량" in p["note"], "E24 실제 4bar → 표준 범위 후보(카탈로그 최대 7bar 를 실제로 보지 않음)")
check(acc.pressure_check("진공", None, None)["fit"] is None, "PGR10 진공은 양압 범위 미적용")

# PGR03 피크 미확인 → 정격 기준 하한(조건부)·질문, 0A 로 보지 않음
r = acc.tool_electrical(tool(el={"power_circuit_count": 1, "current_A": 0.8, "comm_interface": "RS485", "rs485_wires": 2, "extra_pins": 0}))
check(r["power_pins"] == 2 and r["status"] == "conditional" and any("피크" in q for q in r["questions"]),
      "PGR03 피크 미확인 → 정격 0.8A 기준 하한 2핀(조건부) + 피크 질문", str(r))
r = acc.tool_electrical(tool(el={"power_circuit_count": 1, "comm_interface": "RS485", "rs485_wires": 2}))
check(r["pins"] is None and any("전류" in m for m in r["missing"]), "전류를 모르면 합계 미정(0A 로 보지 않음)")
# 글에서 읽기: '24V 정격 0.8A 피크 1.5A'
r = acc.tool_electrical(tool(el={"power_circuits": ["24V 정격 0.8A, 피크 1.5A"], "control_method_raw": "Modbus RTU(RS485 2선)", "extra_pins": 0}))
check(r["design_A"] == 1.5 and r["voltage_V"] == 24 and r["interface"] == "RS485" and r["rs485_wires"] == 2 and r["pins"] == 6,
      "사양 글·제어 방식 글에서 전압·정격·피크·RS485 2선을 읽어 6핀", str(r))
# 신호·센서 없음 + 전원만 → 신호 0
r = acc.tool_electrical(tool(sensors=False, el={"power_circuit_count": 1, "current_A": 2, "peak_A": 2}))
check(r["pins"] == 4 and r["signal_pins"] == 0, "신호·센서 없다고 답한 전원 전용 툴 → 전원 4핀만")
# 상대 모듈 없는 툴 허용(PGR14) — 통합 결과의 상태는 조건 포함이면 조건부 초안, 엔지니어 확정은 자동으로 안 줌
check(acc.evaluate([tool(el=ag)], 1, pogo_needed=True, air_needed=False)["status"] == "engineering_review",
      "값이 다 확정돼도 상태는 엔지니어 검토(자동 확정 없음)")

# 질문지·대화에서 v1.2 칸 읽기 — 공압 회로 글, MPa 환산값도 말에 있는 숫자로 인정(PGR23)
from app.company_knowledge import atc_answer as aa, atc_meeting as am  # noqa: E402

check(am.air_circuit("복동, 로봇측 밸브, 그리퍼 2개") == {"actuation": "double_acting", "valve_location": "robot_side", "gripper_count": 2}
      and am.air_circuit("단동 그리퍼, 툴측 밸브") == {"actuation": "single_acting", "valve_location": "tool_side"}
      and am.air_circuit("에어 4bar") == {}, "미팅 답 '복동, 로봇측 밸브, 그리퍼 2개' → 공압 회로 칸(적힌 것만)")
f = [{"path": "tools[].pneumatic.pressure_bar", "type": "object", "label_ko": "압력"}]
ok6 = aa.clean(f, {"values": {"tools[].pneumatic.pressure_bar": {"min": 6, "max": 6}}}, "실제 공급 0.6MPa 입니다")
bad = aa.clean(f, {"values": {"tools[].pneumatic.pressure_bar": {"min": 7, "max": 7}}}, "실제 공급 0.6MPa 입니다")
check(ok6["values"] == {"tools[].pneumatic.pressure_bar": {"min": 6.0, "max": 6.0}} and not bad["values"],
      "대화 '0.6MPa' → 6bar 는 받고, 말에 없는 7bar 는 버림")
fi = [{"path": "tools[].electrical.comm_interface", "type": "enum", "label_ko": "방식", "options": [k for k, _ in acc.INTERFACES]}]
check(aa.clean(fi, {"values": {"tools[].electrical.comm_interface": "RS485"}}, "RS485로 써요")["values"]
      and not aa.clean(fi, {"values": {"tools[].electrical.comm_interface": "Profibus"}}, "x")["values"],
      "제어 방식은 선택지 값만")

# 예전 기록(accessory 칸 없음)을 지금 규칙으로 — 고른 후보는 그대로, 액세서리만 다시
from app.company_knowledge import atc_selection as atc  # noqa: E402

old = {"screening_candidates": [{"series": "auto", "models": [{"name": "TCV1"}]}], "pogo_needed": True, "pogo_modules": 1}
it = {"robots": [{"quantity": 1}], "tools": [tool("AG", qty=2, el=ag)]}
new = atc.refresh_accessory(old, it)
check(new["screening_candidates"] == old["screening_candidates"] and new["accessory"]["bom"]["PPF"] == 2
      and new["pogo_modules"] == 1 and new["slot_check"]["slot_sum"] == 1, "예전 기록 → 액세서리만 v1.2 로 다시(후보 그대로)", str(new["accessory"]["bom"]))

print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
for label, detail in FAIL:
    print(f"  ✗ {label}: {detail}")
sys.exit(1 if FAIL else 0)
