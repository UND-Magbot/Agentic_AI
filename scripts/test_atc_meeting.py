"""첫 고객 미팅 질문지 읽기(company_knowledge/atc_meeting.py) 점검 — AI 없이 되는 부분(양식 표 읽기·숫자 대조).

    docker compose run --rm --no-deps -e PYTHONPATH=/app -v ./backend/app:/app/app -v ./scripts:/scripts -v ./docs:/docs backend \\
        python /scripts/test_atc_meeting.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from app.company_knowledge import atc_meeting as am
from app.company_knowledge import atc_selection as atc

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []
DOCS = Path("/docs/atc_meeting")


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


def form(name: str) -> dict:
    return am.parse_form((DOCS / f"{name}.docx").read_bytes())


blank = am.parse_form((DOCS / "MAGBOT_툴체인저_첫고객미팅_질문지_양식.docx").read_bytes())
check(blank is not None and blank["tools"] == [] and blank["followups"] == {}, "빈 양식 → 툴·추가 답 없음(빈 줄은 건너뜀)", str(blank))

s1 = form("샘플1_협동_사출품이송")
t = {x["name"]: x for x in s1["tools"]}
rg2, vac = t["전동 그리퍼"], t["진공 그리퍼 (흡착패드 4개)"]
check(rg2["size"] == {"x": 220, "y": 90, "z": 150} and rg2["mass"] == 0.8 and rg2["wp"] == 0.5
      and (rg2["power"], rg2["air"], rg2["sensor"], rg2["tilt"]) == (True, False, True, False), "샘플1 전동 그리퍼 표 값", str(rg2))
check(vac["wp"] == 2 and vac["wp_count"] == 4 and (vac["power"], vac["air"], vac["sensor"]) == (False, True, True),
      "샘플1 진공 그리퍼: '2 (0.5kg × 4개)' → 총 2kg·4개, 전기 무·공압 유·센서 유", str(vac))

s5 = form("샘플5_다수툴_혼합로봇")
t5 = {x["name"]: x for x in s5["tools"]}
check((t5["흡착 그리퍼 (같은 모델)"]["power"], t5["흡착 그리퍼 (같은 모델)"]["air"], t5["흡착 그리퍼 (같은 모델)"]["sensor"]) == (False, True, True)
      and t5["흡착 그리퍼 (같은 모델)"]["qty"] == 2,
      "샘플5 흡착 그리퍼: 전기 무·공압 유·센서 유, 수량 2 (AI 가 칸을 바꿔 읽던 문제)", str(t5["흡착 그리퍼 (같은 모델)"]))
check(t5["마그네틱 그리퍼"]["size"] == {"x": 120, "y": 120, "z": 80}, "원형 'Ø120 × 80' → 120×120×80")

s3 = form("샘플3_정보부족_조립")
t3 = {x["name"]: x for x in s3["tools"]}
check(t3["드라이버 툴"]["wp_na"] and t3["드라이버 툴"]["mass"] is None and t3["핑거 그리퍼 (모델 모름)"]["mass_estimated"]
      and t3["핑거 그리퍼 (모델 모름)"]["power"] is None,
      "샘플3: '해당 없음'·'모름'·'약 1.5'·'?' 구분", str(t3))

# overlay — 추가 질문 표 답을 해당하는 툴에(샘플5: 4bar·호스 2개·DIO, 전기 조건은 못 받아 옴)
base = {"project": {"process": {}, "environment": {}},
        "robots": [{"id": "R1", "model": "HCR-12", "cables": {}}, {"id": "R2", "model": "RB10", "cables": {}}], "tools": []}
am.overlay(base, s5)
vt = {x["name"]: x for x in base["tools"]}
check(vt["흡착 그리퍼 (같은 모델)"]["pneumatic"]["pressure_bar"] == {"min": 4, "max": 4}
      and vt["흡착 그리퍼 (같은 모델)"]["pneumatic"]["observed_hose_count"] == 2
      and "pneumatic" not in vt["핑거 그리퍼"] and vt["핑거 그리퍼"]["electrical"]["control_method_raw"] == "DIO",
      "추가 질문 '4bar, 호스 2개'는 공압 툴에만, 'DIO'는 전기·센서 툴에", str(vt))
check(set(base["project"]["followups_answered"]) == {"F03", "F04", "F05", "F06"}
      and {"F02"} <= {q["id"] for q in atc.pending_questions(base)},
      "샘플5: 답한 추가 질문은 다시 안 묻고, 비워 둔 전기 조건(F02)만 묻는다",
      str([(q["id"], q["entity_label"]) for q in atc.pending_questions(base)]))
# 샘플1: 추가 질문 표를 비워 둠 → AI 가 전기·센서·공압 후속 질문을 한다
b1 = {"project": {"process": {}, "environment": {}}, "robots": [{"id": "R1", "model": "M1013", "cables": {}}], "tools": []}
am.overlay(b1, s1)
p1 = {(q["id"], q["entity_label"]) for q in atc.pending_questions(b1) if q["kind"] == "followup"}
check({("F02", "전동 그리퍼"), ("F03", "전동 그리퍼"), ("F04", "전동 그리퍼"),
       ("F05", "진공 그리퍼 (흡착패드 4개)"), ("F06", "진공 그리퍼 (흡착패드 4개)")} <= p1
      and not any(i == "F05" and n.startswith("전동") for i, n in p1),
      "샘플1(추가 질문 표 비움): 전동 그리퍼엔 전기·센서, 진공 그리퍼엔 압력·호스를 묻는다", str(sorted(p1)))
# 샘플3: '모름' 칸은 묻지 않고 자료 요청
b3u = {"project": {"process": {}, "environment": {}}, "robots": [{"id": "R1", "cables": {}, "speed": {}}], "tools": []}
am.overlay(b3u, s3)
check(not any(q["kind"] == "basic" and q["id"] in ("J07", "J08") for q in atc.pending_questions(b3u))
      and any("자세" in r for r in atc.data_requests(b3u)) and any("모델명·사진" in r for r in atc.data_requests(b3u)),
      "샘플3: 기울임·전기 '모름'/'?' → 다시 안 묻고 자료 요청", str(atc.data_requests(b3u)))
b3 = {"project": {"process": {}, "environment": {}}, "robots": [{"id": "R1", "cables": {}}], "tools": []}
am.overlay(b3, s3)
check(b3["project"]["process"].get("has_contact_force") is True, "샘플3 '부품 끼우기(압입)' → 공정 반력 있음", str(b3["project"]))
b2 = {"project": {"process": {}, "environment": {}}, "robots": [{"id": "R1", "model": "HS220", "cables": {}}], "tools": []}
am.overlay(b2, form("샘플2_산업용_차체패널"))
check(b2["robots"][0]["cables"].get("master_to_controller_required_m") == 6
      and all(t["pneumatic"]["pressure_bar"] == {"min": 5, "max": 5} for t in b2["tools"]),
      "샘플2: 케이블 6m 필요, 5bar(표준 범위)", str(b2["robots"]))
b4 = {"project": {"process": {}, "environment": {}}, "robots": [{"id": "R1", "model": "UR10e", "cables": {}}], "tools": []}
am.overlay(b4, form("샘플4_압력경계_통신"))
r4 = atc.evaluate(b4 | {"project": {**b4["project"], "tool_count": 2}}, [])
check(any(r["rule"] == "PGR23" for r in r4["engineering_review_reasons"])
      and any(r["ref"] == "R13" for r in r4["engineering_review_reasons"])
      and b4["robots"][0]["cables"].get("controller_to_system_required_m") == 2,
      "샘플4: 5.5bar → 표준 1~5bar 밖(PGR23), RS-485 → 통신 검토, 컨트롤러→PLC 2m", str([r["text_ko"] for r in r4["engineering_review_reasons"]]))

# 숫자 대조 — 원문에 없는 숫자는 비운다
_, n = am.normalize({"tools": [{"name": "툴A", "tool_assembly_mass_kg": 7.5, "installed_quantity": 1}]}, "툴A 1개 무게 모름")
check(any("7.5" in x for x in n), "원문에 없는 숫자(7.5kg)는 비우고 메모", str(n))
it, _ = am.normalize({"tools": [{"name": "툴A", "tool_assembly_mass_kg": 3, "installed_quantity": 1}]}, "툴A 1개 3kg")
check(it["tools"][0]["intake"]["tool_assembly_mass_kg"] == 3.0, "원문에 있는 숫자는 그대로")

# 툴마다 나눠 적은 추가 질문 답('전동 그리퍼: … / 진공 그리퍼: …') — 그 툴 몫만, 'ON/OFF' 는 안 쪼갬
NAMES = ["전동 그리퍼 (RS-485 제어)", "진공 그리퍼"]
check(am.for_tool("전동 그리퍼: RS-485 / 진공 그리퍼: DIO", NAMES[1], NAMES) == "진공 그리퍼: DIO"
      and am.for_tool("툴마다 DI 5, DO 2", NAMES[0], NAMES) == "툴마다 DI 5, DO 2"
      and am.for_tool("전동 그리퍼: 24V 1회로", NAMES[1], NAMES) is None
      and am.for_tool("진공 그리퍼: 흡착 ON/OFF DO 1", NAMES[1], NAMES) == "진공 그리퍼: 흡착 ON/OFF DO 1",
      "추가 질문 답을 툴마다 나눠 읽기")


# 샘플마다 JSON 규칙대로 나와야 하는 결과(docs/atc_meeting/샘플_기대결과.md) — 제품 DB 툴체인저로 판정(--db)
_CAT: list = []


def sample_eval(name: str, robots: list, tool_count: int, env: bool | None):
    if not _CAT:
        from app.company_knowledge import product_recommend as pr
        from app.database import SessionLocal

        async def cat():
            async with SessionLocal() as db:
                return await pr.atc_catalog(db)
        _CAT.extend(asyncio.run(cat()))         # 제품 DB 는 한 번만(이벤트 루프를 여러 번 만들면 DB 연결이 꼬임)
    b = {"project": {"process": {"category": "transfer"}, "tool_count": tool_count, "environment": {"has_special_conditions": env}},
         "robots": robots, "tools": []}
    am.overlay(b, form(name))
    o = atc.evaluate(b, _CAT)
    first = next((c for c in o["screening_candidates"] if c["models"] and not c.get("out_of_range")), None)
    rules = {r["rule"] for r in o["engineering_review_reasons"]} | {r["ref"] for r in o["engineering_review_reasons"]}
    return o, (first["models"][0]["name"] if first else None), rules


if "--db" in sys.argv:
    import asyncio

    ok = {"master_4m_sufficient": True, "system_1m_sufficient": True}
    cobot = lambda m, v: {"id": "R1", "model": m, "type": "cobot", "quantity": 1, "speed": {"value": v, "unit": "%"}, "cables": dict(ok)}  # noqa: E731
    cases = {
        "샘플1.1_협동_사출품이송_추가질문완료": ([cobot("M1013", 60)], 2, False),
        "샘플1_협동_사출품이송": ([cobot("M1013", 60)], 2, False),
        "샘플2.1_산업용_차체패널_추가질문완료": ([{"id": "R1", "model": "HS220", "type": "industrial", "quantity": 2,
                                          "speed": {"value": 40, "unit": "%"},
                                          "cables": {"master_4m_sufficient": False, "system_1m_sufficient": True}}], 3, True),
        "샘플4.1_압력경계_통신_추가질문완료": ([{**cobot("UR10e", 50), "cables": {"master_4m_sufficient": True, "system_1m_sufficient": False}}], 2, True),
        "샘플5.1_다수툴_혼합로봇_추가질문완료": ([cobot("HCR-12", 60), {**cobot("RB10", 60), "id": "R2"}], 4, True),
        "샘플5_다수툴_혼합로봇": ([cobot("HCR-12", 60), {**cobot("RB10", 60), "id": "R2"}], 4, True),
        "샘플6_협동_박스팔레타이징": ([cobot("H2017", 50)], 2, False),
        "샘플7_협동_대형부품이송": ([cobot("CRX-30iA", 40)], 2, False),
        "샘플8_협동_고중량_범위초과": ([cobot("CR-35iB", 30)], 2, False),
        "샘플9_산업용_도어이송": ([{**cobot("R-2000iC", 45), "type": "industrial"}], 2, False),
        "샘플10_산업용_초고중량": ([{**cobot("KR 500", 40), "type": "industrial"}], 2, False),
    }
    res = {k: sample_eval(k, *v) for k, v in cases.items()}
    o, m, r = res["샘플1.1_협동_사출품이송_추가질문완료"]
    check(m == "TCC1" and o["pogo_modules"] == 1 and o["pneumatic_modules"] == 1 and not o["pending_questions"] and "R12" not in r
          and [s["status"] for s in o["statuses"]] == ["engineering_review"],
          "샘플1.1: TCC1(툴측 3.2kg ≤ 5kg, 협동 60% 상향 없음), 포고핀 최소 1개, 공압 모듈 1개(유로 1), 물을 것 없음",
          str((m, o["pogo_modules"], o["pneumatic_modules"], r, o["pending_questions"])))
    o, m, r = res["샘플1_협동_사출품이송"]
    check(m == "TCC1" and {"E01", "E02", "F02", "F05"} <= {q["id"] for q in o["pending_questions"]},
          "샘플1: 같은 후보, 추가 질문 표를 비워 둬 전원·신호 수·압력을 대화에서 묻는다")
    o, m, r = res["샘플2.1_산업용_차체패널_추가질문완료"]
    check(m == "TCHK150" and o["pogo_modules"] == 2 and o["pneumatic_modules"] == 1 and {"R08", "R17", "Q25"} <= r
          and "R12" not in r and not o["pending_questions"],
          "샘플2.1: 툴측 75kg × 산업용 2배(C01) = 150kg → TCHK150(속도 상향 중복 없음), 포고핀 2개, 공압 1개, 기울임·케이블·환경 검토",
          str((m, o["pogo_modules"], r, o["pending_questions"])))
    o, m, r = res["샘플4.1_압력경계_통신_추가질문완료"]
    check(m == "TCC1" and o["pogo_modules"] == 1 and {"PGR19", "R13", "PGR23", "R08", "R17"} <= r and not o["pending_questions"],
          "샘플4.1(경계): 후보 TCC1 + 피크 2A 는 핀 수에 반영(v1.2)·RS-485 통신 검토(PGR19)·5.5bar(PGR23)·기울임·PLC 2m 내부 검토", str((m, r)))
    o, m, r = res["샘플5.1_다수툴_혼합로봇_추가질문완료"]
    check(m == "TCV1" and o["pogo_modules"] == 1 and "R12" not in r and not o["pending_questions"],
          "샘플5.1: 툴측 7.5kg → TCV1, 포고핀 최소 1개(도체 3·6·3), 1A 이내", str((m, o["pogo_modules"], r)))
    # 샘플6~10 — 무게·로봇 종류에 따라 다른 툴체인저 모델(사용자 2026-10-02)
    for name, model, pogo, out in (("샘플6_협동_박스팔레타이징", "TCV2", 1, False), ("샘플7_협동_대형부품이송", "TCV3", 1, False),
                                   ("샘플8_협동_고중량_범위초과", "M-LTC-0040A", 1, True), ("샘플9_산업용_도어이송", "M-LTC-0300G", 1, True),
                                   ("샘플10_산업용_초고중량", "M-LTC-0630F", 1, True)):
        o, m, r = res[name]
        sts = [x["status"] for x in o["statuses"]]
        check(m == model and o["pogo_modules"] == pogo and not o["pending_questions"] and "R12" not in r
              and ("nonstandard_or_out_of_range" in sts) == out,
              f"{name[:3]}: 툴측 {o['required_payload_kg']:g}kg(선정 {o['screening_payload_kg']:g}kg) → {model}" + (" (주 계열 최대 초과 → 다른 계열, 표준 밖)" if out else ""),
              str((m, o["pogo_modules"], sts, o["pending_questions"])))
    o, m, r = res["샘플5_다수툴_혼합로봇"]
    check({("F02", "핑거 그리퍼"), ("F02", "마그네틱 그리퍼")} <= {(q["id"], q["entity_label"]) for q in o["pending_questions"]},
          "샘플5: 비워 둔 전원 조건(F02)을 전기 툴에만 묻는다")
    # 액세서리 v1.2(2026-10-08) — 추가 질문까지 채운 샘플은 더 물을 것 없이 '엔지니어 검토'(조건부 가정 없음), 기대 수량
    acc_expect = {  # 파일: (PPM, PPF, PMM, PMF, 장착 위치) — docs/atc_meeting/샘플_기대결과.md
        "샘플1.1_협동_사출품이송_추가질문완료": (1, 2, 1, 1, 2), "샘플2.1_산업용_차체패널_추가질문완료": (4, 6, 2, 3, 3),
        "샘플4.1_압력경계_통신_추가질문완료": (1, 2, 1, 1, 2), "샘플5.1_다수툴_혼합로봇_추가질문완료": (2, 4, 2, 2, 2),
        "샘플6_협동_박스팔레타이징": (1, 2, 1, 1, 2), "샘플7_협동_대형부품이송": (1, 2, 1, 1, 2), "샘플8_협동_고중량_범위초과": (1, 2, 1, 2, 2),
        "샘플9_산업용_도어이송": (1, 2, 1, 2, 2), "샘플10_산업용_초고중량": (1, 2, 0, 0, 1)}
    for name, (ppm, ppf, pmm, pmf, pos) in acc_expect.items():
        a = res[name][0]["accessory"]
        b = a["bom"]
        check((b["PPM"], b["PPF"], b["PMM"], b["PMF"], a["master"]["positions"]) == (ppm, ppf, pmm, pmf, pos)
              and a["status"] == "engineering_review" and not a["assumptions"] and not res[name][0]["pending_questions"],
              f"{name[:5]} 액세서리 v1.2: PPM {ppm}·PPF {ppf}·PMM {pmm}·PMF {pmf}·{pos}개소, 질문·가정 없음",
              str((b, a["master"]["positions"], a["status"], a["assumptions"])))
    a7 = res["샘플7_협동_대형부품이송"][0]["accessory"]["air_tools"][0]
    check(a7["profile"] == "AIR_DOUBLE_ROBOT_VALVE" and a7["paths"] == 2 and a7["pressure"]["fit"] is True,
          "샘플7: '0.5MPa, 복동, 로봇측 밸브, 그리퍼 1개' → 5bar·2유로·커플러 1(PGR21·PGR23)", str(a7))
    e4 = res["샘플4.1_압력경계_통신_추가질문완료"][0]["accessory"]["electrical_tools"][0]
    check(e4["interface"] == "RS485" and e4["rs485_wires"] == 2 and e4["design_A"] == 2 and e4["pins"] == 7,
          "샘플4.1: 피크 2A·RS-485 2선식·DI 1 → 전원 4+A·B 2+DI 1 = 7핀", str(e4))
    a4 = sample_eval("샘플4_압력경계_통신", *cases["샘플4.1_압력경계_통신_추가질문완료"])[0]
    check(a4["accessory"]["status"] == "needs_data" and any("2선식" in q for q in a4["accessory"]["open_questions"])
          and "E02" in {q["id"] for q in a4["pending_questions"]},
          "샘플4(추가 질문 덜 채움): RS-485 선식 모름 → 2선식 가정 + 선식 질문(E02), 진공 그리퍼 DI·DO 모름 → 자료 필요")

print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
for label, detail in FAIL:
    print(f"  ✗ {label}: {detail}")
sys.exit(1 if FAIL else 0)
