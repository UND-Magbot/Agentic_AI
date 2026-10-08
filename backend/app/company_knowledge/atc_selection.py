"""맥봇 툴체인저(ATC) 단품 선정 — 주니어 영업 질문지(J01~J10 + 후속 F01~F12)와 선정 규칙(R01~R22) 적용.

원본: docs/sources/UND_MAGBOT_주니어영업질문지_v0.2.docx + docs/sources/UND_MAGBOT_AI_선정데이터_v0.2.json(영업부 제공, 2026-10-01).
JSON 은 data/atc_selection_v0.2.json 으로 복사해 질문·후속 조건·규칙의 원본으로 쓴다(화면 질문도 여기서 내려준다).
추가 확정: docs/sources/UND_MAGBOT_ATC_선정기준_추가확정_v0.8.json(data/atc_selection_v0.8_addendum.json, 2026-10-02) — confirmed_changes
C01~C20 만 v0.2 위에 병합한다(산업용 약 2배, 속도·편하중 종합 판단, 포고핀 병렬, 커플러 2유로, 표준 3개소, 유선 기본, 공통·가변 구성).
제안 항목(G01~G05 질문·검토 분기·기록 틀)은 검토 대기라 구현하지 않는다.
액세서리(포고핀·에어커플러·30cm 케이블)는 v1.2 통합 선정 규칙(PGR01~PGR24, atc_accessory.py)으로 산정한다 — 사용자 2026-10-08
'최종 규칙 확립·전체 적용'. v0.8 C03·C04·C05·C12·C16 의 같은 값(2유로·3개소·1A·툴별 수량·30cm)을 v1.2 가 이어받아 대체한다.

원칙(JSON data_policy·status_policy 그대로):
- 모르는 값은 None — 0·없음·false 로 바꾸지 않는다(R20). 추정은 따로 표시한다.
- 판정은 규칙 계산으로 한다. AI 가 모델명을 만들지 않는다(R09·T17). 후보는 제품 DB 의 툴체인저만.
- 미확정 기준(P01~P09)은 엔지니어가 ENGINEERING_CONFIRMED 에서 켜기 전까지 자동 적용하지 않고 '내부 엔지니어 검토' 사유로 남긴다.
- 승인 모델표(P04)가 등록되기 전에는 최종 모델을 정하지 않는다 — 후보는 '참고 후보(모델표 승인 전)'(사용자 2026-10-02:
  툴체인저 종류는 자동·산업용·수동(MTC)·듀얼(DTC)·M-LTC 모두 후보군).
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from . import atc_accessory as acc

SPEC_PATH = Path(__file__).with_name("data") / "atc_selection_v0.2.json"
SPEC: dict[str, Any] = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
FACTS = SPEC["user_provided_facts"]
ADDENDUM_PATH = Path(__file__).with_name("data") / "atc_selection_v0.8_addendum.json"
ADDENDUM: dict[str, Any] = json.loads(ADDENDUM_PATH.read_text(encoding="utf-8"))
CHANGES: dict[str, dict[str, Any]] = {c["id"]: c for c in ADDENDUM["confirmed_changes"]}
RULES_REVISION = f"{SPEC['schema_version']} + {ADDENDUM['revision']} + 액세서리 {acc.REVISION}"

# v0.8 확정값(원본 JSON 에서 읽는다 — 코드에 숫자를 따로 두지 않음)
INDUSTRIAL_FACTOR = CHANGES["C01"]["industrial_factor"]                         # 산업용 초기 하중 약 2배(R05~R07 대체)
# 액세서리 상수는 v1.2 회사 확정 상수(company_confirmed_constants)에서 — 핀당 1A·8핀·커플러 2유로·공용 3개소
PIN_CURRENT_A = acc.PIN_CURRENT_A
PINS_PER_MODULE = acc.PINS_PER_MODULE
PATHS_PER_COUPLER = acc.PATHS_PER_COUPLER
STD_POSITIONS = acc.STD_POSITIONS
STD_PRESSURE = CHANGES["C06"]["standard_pressure_bar"]                           # 압축공기 1~5bar(R14 대체)
CABLE_STD = {c["segment_id"]: c for c in CHANGES["C15"]["standard_cables"]}      # 마스터당 4m·1m 각 1개
POGO_CABLE_CM = acc.CABLE_CM                                                    # PPM·PPF 마다 30cm 케이블 1개(PGR16)
QUOTE_SCOPE_TEXT = CHANGES["C17"]["quote_scope_text_ko"]
TOOL_STAND = CHANGES["C19"]["standard_product"]                                  # mTS2(툴 2개용) — 고객 선택 시만
# C09 유선 기본 — 무선 모델은 고객·담당자가 무선을 고른 경우에만 후보(제품 DB: TCW1 은 배터리 내장 무선 결합)
WIRELESS_MODELS = ("TCW1",)

# 엔지니어 확정 여부(P01~P09). 확정되면 True 로 바꾸고 관련 규칙이 자동 적용된다.
ENGINEERING_CONFIRMED: dict[str, bool] = {p["id"]: False for p in SPEC["unresolved_configuration"]}
# P06 공압 2포트 구조 — v0.8 C03(독립 2유로)으로 해결(resolves_configuration_ids)
ENGINEERING_CONFIRMED["P06"] = True
# 회사 제품 DB 로 확정되는 부분(사용자 2026-10-02: "제품 DB 사양으로 정할 수 있으면 확정 요소 — 회사 내 데이터"). 나머지는 잠정·미확정.
DB_CONFIRMED = {
    "P04": "모델명·정격(가반하중)과 상향 순서 — 제품 DB 공식 사양 모델(†미확정·'확인 필요' 사양은 제외)",
    "P07": "포고핀 1A × 8핀, 전원·통신 전달(PPM)",
    "P08": "액세서리 적용 = 유선 TCC1~TCV4, Male 은 마스터·Female 은 툴플레이트",
    "P09": "툴체인저 구동 전원 24V 2A(ON/OFF 시간), 로봇 전원으로 지속 공급(불가 시 SMPS·어댑터), 컨트롤박스 출력 Grip OFF / Release ON",
}
UNRESOLVED = {p["id"]: p for p in SPEC["unresolved_configuration"]}

# 잠정 기준(사용자 2026-10-02 '잠정 적용'). v0.8 이 P02·P03(C01·C11), P05(C06), P07 병렬 금지(C05)를 대체해 P01 만 남는다.
PROVISIONAL: dict[str, dict[str, Any]] = {
    "P01": {"speed_unit": "%", "text": "속도 = 티치펜던트 속도 오버라이드 %, 자동운전 프로그램의 최대 설정값 기준"},
}


def applied(pid: str) -> bool:
    """확정 또는 잠정 기준이 있으면 계산에 쓴다."""
    return bool(ENGINEERING_CONFIRMED.get(pid)) or pid in PROVISIONAL


def is_provisional(pid: str) -> bool:
    return not ENGINEERING_CONFIRMED.get(pid) and pid in PROVISIONAL

STATUS_KO = SPEC["output_statuses_ko"]
STATUS_ORDER = ["needs_information", "engineering_review", "nonstandard_or_out_of_range", "standard_candidate"]

# ── 툴체인저 계열(제품 DB 제품군) ─────────────────────────────────────────────────
# order: 같은 계열 안에서 가반하중 순서가 '승인된 모델 순서'(상향 단계) 역할을 한다(모델표 등록 전 참고용).
SERIES = [
    {"key": "auto", "family": "Magbot ATC · 자동 툴체인저", "label": "자동 툴체인저(DC 전기식 스위칭 마그네틱)",
     "robots": ("cobot", "industrial", None), "primary_for": ("cobot",),
     "note": "공압 없이 DC 24V 로 자동 체결·분리. 협동로봇 중심(정격 30kg 이하)"},
    {"key": "industrial", "family": "Magbot ATC · 산업용 툴체인저 (H시리즈)", "label": "산업용 툴체인저(H시리즈)",
     "robots": ("industrial", None), "primary_for": ("industrial",),
     "note": "고중량(100~220kgf) 산업용 로봇용. 구동 공압 4~7bar 필요"},
    {"key": "mltc", "family": "Magbot ATC · M-LTC 공압 툴체인저", "label": "M-LTC 공압 툴체인저",
     "robots": ("cobot", "industrial", None), "primary_for": ("industrial",),
     "note": "공압(4~7bar) 볼 록킹 결합 — 현장 구동 공기 공급이 필요"},
    {"key": "manual", "family": "Magbot ATC · 수동 툴체인저", "label": "수동 툴체인저(MTC)",
     "robots": ("cobot", "industrial", None), "primary_for": (),
     "note": "사람이 직접 교체하는 경우 — 자동 교체가 필요하면 해당 없음"},
    {"key": "dual", "family": "Magbot ATC · 듀얼 툴체인저", "label": "듀얼 툴체인저(DTC)",
     "robots": ("cobot", "industrial", None), "primary_for": (),
     "note": "한 플랜지에 툴 두 개를 함께 쓰는 경우"},
]
_FAMILIES = {s["family"]: s for s in SERIES}


def build_catalog(cards: list[dict[str, Any]], payload_of) -> list[dict[str, Any]]:
    """제품 DB 카드 → 계열별 후보 목록. cards=[{card_id, name, card}], payload_of(card)→kg|None."""
    out = []
    for c in cards:
        s = _FAMILIES.get((c.get("card") or {}).get("family") or "")
        if s is None:
            continue
        out.append({"card_id": c["card_id"], "name": c["name"], "series": s["key"], "series_label": s["label"],
                    "payload_kg": payload_of(c.get("card") or {}), "official": c.get("official", False),
                    "wireless": c["name"] in WIRELESS_MODELS})
    return out


# ── 값 읽기 · 조건 언어(condition_language) ───────────────────────────────────────

def _get(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _local(path: str) -> str:
    """'tools[].intake.needs_power' → 'intake.needs_power' (현재 툴·로봇 기준), 'project.x' → 'x'."""
    for prefix in ("tools[].", "robots[].", "project."):
        if path.startswith(prefix):
            return path[len(prefix):]
    return path


def _cond(cond: dict[str, Any], entity: dict[str, Any]) -> bool:
    if "any" in cond:
        return any(_cond(c, entity) for c in cond["any"])
    if "all" in cond:
        return all(_cond(c, entity) for c in cond["all"])
    v = _get(entity, _local(cond["field"]))
    op = cond["op"]
    if op == "is_unknown":
        return v is None
    if op == "has_unknown_child":
        return not isinstance(v, dict) or any(v.get(k) is None for k in ("x", "y", "z"))
    if op == "eq":
        return v is not None and v == cond["value"]          # eq true/false 는 null 과 맞지 않는다
    if op == "in":
        return v is not None and v in cond["value"]
    return False


# 액세서리 산정 질문(v1.2 junior_questions·required_input_structure) — 전원(E01)·제어 방식과 신호선(E02)·공압 회로(E03).
# 주니어 질문지(J08·F02~F06)만으로는 피크 전류·실제 통신·밸브 위치가 안 나와 핵심 칸만 더 묻는다. 칸은 tools[].electrical·pneumatic 아래.
_ELEC_WHEN = {"any": [{"field": "tools[].intake.needs_power", "op": "eq", "value": True},
                      {"field": "tools[].intake.has_sensors_or_signals", "op": "eq", "value": True},
                      {"field": "tools[].pneumatic.valve_location", "op": "eq", "value": "tool_side"},
                      {"field": "tools[].pneumatic.actuation", "op": "eq", "value": "integrated_valve"}]}


def _opts(pairs: list[tuple[Any, str]]) -> list[Any]:
    return [{"value": v, "label_ko": lab} for v, lab in pairs] + [None]


POGO_FOLLOWUPS = [
    {"id": "E01", "source": "PGR03·PGR04·PGR05", "scope": "tool",
     "question_ko": "툴 전원(구동·센서·밸브 전원 포함)은 몇 회로이고, 전압과 전류(정격·피크)는 얼마인가요?",
     "show_when": _ELEC_WHEN,
     "fields": [{"path": "tools[].electrical.power_circuit_count", "type": "integer", "label_ko": "전원 회로 수", "unit": "회로"},
                {"path": "tools[].electrical.voltage_V", "type": "number", "label_ko": "전압", "unit": "V"},
                {"path": "tools[].electrical.current_A", "type": "number", "label_ko": "정격 전류", "unit": "A"},
                {"path": "tools[].electrical.peak_A", "type": "number", "label_ko": "피크(최대)·기동 전류", "unit": "A"}],
     "help_text_ko": "포고핀은 핀 1개가 1A — 회로마다 공급·리턴을 정격·피크 중 큰 전류로 각각 셉니다(정격 0.8A·피크 1.5A → 공급 2+리턴 2). "
                     "같은 전원을 동시에 쓰는 장치는 전류를 합쳐 적어 주세요. 피크를 모르면 비워 두세요 — 정격 기준 하한으로 계산하고 확인을 요청합니다."},
    {"id": "E02", "source": "PGR06·PGR07·PGR19", "scope": "tool",
     "question_ko": "실제로 쓸 제어 방식과 연결하는 신호선은 무엇인가요?",
     "show_when": {"field": "tools[].intake.has_sensors_or_signals", "op": "eq", "value": True},
     "fields": [{"path": "tools[].electrical.comm_interface", "type": "enum", "label_ko": "실제 제어 방식", "options": _opts(acc.INTERFACES)},
                {"path": "tools[].electrical.rs485_wires", "type": "enum", "label_ko": "RS485 선식(RS485 일 때)",
                 "options": _opts([(2, "2선식(A·B)"), (4, "4선식(TX±·RX±)")])},
                {"path": "tools[].electrical.dio.di", "type": "integer", "label_ko": "입력 DI(센서·완료 신호 — I/O 일 때)", "unit": "개"},
                {"path": "tools[].electrical.dio.do", "type": "integer", "label_ko": "출력 DO(열기·닫기 명령 — I/O 일 때)", "unit": "개"},
                {"path": "tools[].electrical.extra_pins", "type": "integer", "label_ko": "0V 와 따로 연결하는 공통선·접지·실드 선", "unit": "개"}],
     "help_text_ko": "지원 목록이 아니라 실제로 쓸 방식 하나를 고릅니다. I/O 면 실제로 연결할 DI·DO 수, RS485 면 2선식/4선식(모르면 '모름' — "
                     "2선식 가정으로 계산하고 확인 요청). 공통선(COM·SG)·접지·실드를 모르면 비워 두세요. 배선도·선 색상표가 있으면 받아 주세요."},
    {"id": "E03", "source": "PGR09·PGR20·PGR21", "scope": "tool",
     "question_ko": "공압 회로는 어떻게 되나요? (복동·단동, 밸브 위치, 그리퍼 수, 따로 제어 여부)",
     "show_when": {"field": "tools[].intake.needs_air", "op": "eq", "value": True},
     "fields": [{"path": "tools[].pneumatic.actuation", "type": "enum", "label_ko": "동작 방식", "options": _opts(acc.ACTUATIONS)},
                {"path": "tools[].pneumatic.valve_location", "type": "enum", "label_ko": "밸브 위치", "options": _opts(acc.VALVE_LOCATIONS)},
                {"path": "tools[].pneumatic.gripper_count", "type": "integer", "label_ko": "이 툴의 공압 그리퍼 수", "unit": "개"},
                {"path": "tools[].pneumatic.control_relationship", "type": "enum", "label_ko": "그리퍼가 여러 개면",
                 "options": _opts(acc.CONTROLS)},
                {"path": "tools[].pneumatic.extra_paths", "type": "integer", "label_ko": "열림·닫힘 외 블로우·진공·원격 배기 라인", "unit": "개"},
                {"path": "tools[].pneumatic.required_independent_paths", "type": "integer", "label_ko": "실제 통과 유로 수(확인된 경우만)", "unit": "개"}],
     "help_text_ko": "에어커플러 한 쌍이 독립 유로 2개를 잇습니다. 로봇측 밸브 복동은 그리퍼마다 열림·닫힘 2유로, 단동은 1유로, 툴측 밸브는 공급 1유로"
                     "(밸브 전원·제어선은 포고핀에 포함). 보이는 호스 수로 정하지 않습니다. 배관 사진도 괜찮습니다."},
]


def _answered(e: dict[str, Any], path: str) -> bool:
    return _get(e, path) not in (None, [], "") or _is_unknown(e, path)


def followup_done(fid: str, e: dict[str, Any]) -> bool | None:
    """액세서리 산정 질문이 '답한' 상태인지 — 계산에 꼭 필요한 칸 기준. 다른 질문은 None(한 칸이라도 답하면 답한 것)."""
    if fid == "E01":
        # 회로 수 + 전류 + 피크(최대) 전류 — 피크를 모르면 '모름'까지 받아야 답한 것(PGR03: 최대 전류 확인 요청)
        ps = acc.power_spec(e)
        if ps["circuits"] == 0:
            return True
        return ((ps["circuits"] is not None or _is_unknown(e, "electrical.power_circuit_count"))
                and (ps["rated_A"] is not None or ps["peak_A"] is not None or _is_unknown(e, "electrical.current_A"))
                and (ps["peak_A"] is not None or ps["inrush_A"] is not None or _is_unknown(e, "electrical.peak_A")))
    if fid == "E02":
        # 실제 방식 + (I/O 면 DI·DO, RS485 면 선식) + 공통선 — 모르는 칸은 '모름'이면 답한 것(조건부로 계산, PGR07·PGR19)
        iface, _ = acc.interface(e)
        if iface is None:
            return _is_unknown(e, "electrical.comm_interface")
        if iface == "digital_IO" and not all(_answered(e, p) for p in ("electrical.dio.di", "electrical.dio.do")):
            return False
        if iface == "RS485" and acc.rs485_wires(e) is None and not _is_unknown(e, "electrical.rs485_wires"):
            return False
        return iface == "other" or _answered(e, "electrical.extra_pins")
    if fid == "E03":
        return _answered(e, "pneumatic.required_independent_paths") or all(
            _answered(e, p) for p in ("pneumatic.actuation", "pneumatic.valve_location", "pneumatic.gripper_count"))
    return None


def followups(intake: dict[str, Any]) -> list[dict[str, Any]]:
    """답에 해당하는 후속 질문만(툴·로봇마다 따로, 툴끼리 섞지 않는다 — JF06). 질문지 F01~F12 뒤에 액세서리 산정 질문 E01~E03."""
    project = intake.get("project") or {}
    out = []
    for f in [*SPEC["followup_question_bank"], *POGO_FOLLOWUPS]:
        entities = (intake.get("tools") or [] if f["scope"] == "tool"
                    else intake.get("robots") or [] if f["scope"] == "robot" else [project])
        for i, e in enumerate(entities):
            # 전원 사양(F02 글)·제어 방식 글(F04)로 이미 계산할 수 있으면 E01·E02 는 다시 묻지 않는다
            if f["id"] == "E01" and not (e.get("electrical") or {}).get("power_circuit_count") and followup_done("E01", e):
                continue
            if f["id"] == "E02" and not (e.get("electrical") or {}).get("comm_interface") and followup_done("E02", e):
                continue
            if _cond(f["show_when"], e):
                label = (e.get("name") or e.get("model") or f"툴 {i + 1}") if f["scope"] == "tool" else \
                    (" ".join(x for x in (e.get("manufacturer"), e.get("model")) if x) or f"로봇 {i + 1}") \
                    if f["scope"] == "robot" else "프로젝트"
                out.append({"id": f["id"], "question_ko": f["question_ko"], "scope": f["scope"],
                            "entity_index": i if f["scope"] != "project" else None, "entity_label": label,
                            "fields": f["fields"], "help_text_ko": f["help_text_ko"]})
    return out


# ── 미팅 질문지 확인 질문 · 자료 요청 (agent_instruction_ko) ─────────────────────────────
# "미팅 메모에 있는 내용은 먼저 채우고 미확인 값만 확인한다 … 모르면 모델명·사진·도면으로 진행하고 같은 기술 질문을
#  반복하지 않는다. 답변에 해당하는 후속 질문만 표시한다." — 비어 있는 핵심 칸만 짧게 묻고, '모름'은 자료 요청으로.

# 선정에 쓰는 핵심 칸(그 외 칸 — 툴 모델명·제품명 등 — 은 비어 있어도 묻지 않는다)
CORE_FIELDS = {
    "J01": ["robots[].type"],
    "J02": ["project.process.category"],
    "J03": ["project.tool_count"],
    "J04": ["tools[].installed_quantity", "tools[].intake.tool_assembly_mass_kg", "tools[].intake.dimensions_mm.x",
            "tools[].intake.dimensions_mm.y", "tools[].intake.dimensions_mm.z"],
    "J05": ["tools[].mass_components.max_simultaneous_workpieces_kg"],
    "J06": ["robots[].speed.value", "robots[].speed.unit"],
    "J07": ["tools[].intake.tilts_or_flips"],
    "J08": ["tools[].intake.needs_power", "tools[].intake.needs_air", "tools[].intake.has_sensors_or_signals"],
    "J09": ["robots[].cables.master_4m_sufficient", "robots[].cables.system_1m_sufficient"],
    "J10": ["project.environment.has_special_conditions"],
}
# '모름'이면 무엇을 받아 오면 되는지(질문지 도움말 기준)
DATA_REQUEST = {
    "J01": "로봇 명판 사진(모델·종류 확인)", "J02": "작업 영상 또는 공정 설명", "J03": "교체 툴 목록·수량",
    "J04": "툴 사진·도면(크기·무게, 무게에 포함된 부품 확인)", "J05": "제품(워크) 무게 자료",
    "J06": "로봇 속도 설정 화면 사진", "J07": "툴 자세(기울임) 사진·영상",
    "J08": "툴 모델명·사진 또는 사양서(전기·공압·센서)", "J09": "설치 레이아웃·케이블 경로", "J10": "현장 사진",
}


def _is_unknown(entity: dict[str, Any], path: str) -> bool:
    return _get(entity, f"{path}__unknown") is True


def _skip(entity: dict[str, Any], path: str) -> bool:
    """묻지 않을 칸: 제품을 안 드는 툴의 제품 무게."""
    return path.startswith("mass_components.") and entity.get("workpiece_not_applicable") is True


def _label(scope: str, e: dict[str, Any], i: int) -> str:
    if scope == "tool":
        return e.get("name") or e.get("model") or f"툴 {i + 1}"
    if scope == "robot":
        return " ".join(x for x in (e.get("manufacturer"), e.get("model")) if x) or f"로봇 {i + 1}"
    return "프로젝트"


def pending_questions(intake: dict[str, Any]) -> list[dict[str, Any]]:
    """질문지에서 비어 있는 핵심 칸 + 해당하는 후속 질문(질문지에 이미 답한 조건 제외). '모름' 칸은 묻지 않는다."""
    project = intake.get("project") or {}
    out = []
    for q in SPEC["question_bank"]:
        ents = intake.get("tools") or [] if q["scope"] == "tool" else intake.get("robots") or [] if q["scope"] == "robot" else [project]
        core = set(CORE_FIELDS.get(q["id"], ()))
        for i, e in enumerate(ents):
            fields = [f for f in q["fields"] if f["path"] in core
                      and _get(e, _local(f["path"])) is None and not _is_unknown(e, _local(f["path"]))
                      and not _skip(e, _local(f["path"]))]
            if fields:
                out.append({"id": q["id"], "kind": "basic", "question_ko": q["question_ko"], "scope": q["scope"],
                            "entity_index": i if q["scope"] != "project" else None, "entity_label": _label(q["scope"], e, i),
                            "fields": fields, "help_text_ko": q["help_text_ko"]})
    answered = set(project.get("followups_answered") or [])
    for f in followups(intake):
        if f["id"] in answered:
            continue
        ents = intake.get("tools") or [] if f["scope"] == "tool" else intake.get("robots") or [] if f["scope"] == "robot" else [project]
        e = ents[f["entity_index"]] if f["entity_index"] is not None else project
        # 한 칸이라도 답했거나 '모름'이면 답한 질문(ask_once_per_entity) — 나머지 빈칸 때문에 다시 묻지 않는다.
        # 액세서리 산정 질문(E01~E03)은 계산에 꼭 필요한 칸이 채워지거나 '모름'이어야 답한 것(followup_done)
        done = followup_done(f["id"], e)
        if done is None:
            done = any(_answered(e, _local(x["path"])) for x in f["fields"])
        if done:
            continue
        out.append({**f, "kind": "followup"})
    return out


def data_requests(intake: dict[str, Any]) -> list[str]:
    """'모름' 칸 → 받아 올 자료(같은 기술 질문을 반복하지 않고 자료 요청으로 전환)."""
    project = intake.get("project") or {}
    out = []
    for qid, paths in CORE_FIELDS.items():
        scope = next(q["scope"] for q in SPEC["question_bank"] if q["id"] == qid)
        ents = intake.get("tools") or [] if scope == "tool" else intake.get("robots") or [] if scope == "robot" else [project]
        for i, e in enumerate(ents):
            if any(_is_unknown(e, _local(p)) and _get(e, _local(p)) is None for p in [*paths, *REQUEST_ONLY.get(qid, ())]):
                who = _label(scope, e, i)
                out.append(f"{who} — {DATA_REQUEST[qid]}" if scope != "project" else DATA_REQUEST[qid])
    # 후속 질문에서 '모름'(자료를 못 받음) — 그 질문 자체를 자료 요청으로
    for f in followups(intake):
        ents = intake.get("tools") or [] if f["scope"] == "tool" else intake.get("robots") or [] if f["scope"] == "robot" else [project]
        e = ents[f["entity_index"]] if f["entity_index"] is not None else project
        if any(_is_unknown(e, _local(x["path"])) for x in f["fields"]):
            label = f["entity_label"] if f["scope"] != "project" else ""
            out.append(f"{label + ' — ' if label else ''}{f['question_ko'].rstrip('?')}(자료 요청)")
    return list(dict.fromkeys(out))


# 묻지는 않지만 '모름'이면 자료 요청으로 남길 칸(모델명 — 선정 계산엔 안 쓰지만 확인에 필요)
REQUEST_ONLY = {"J01": ["robots[].model"]}


# ── 규칙 계산(결정적) ────────────────────────────────────────────────────────────

def speed_review(robot_type: str | None, speed: float | None, unit: str | None,
                 basis_approved: bool | None = None) -> dict[str, Any]:
    """속도 판단(v0.8 C01·C11 — R03~R07 대체). 자동 단계 상향은 없다.
    - 산업용: 속도별 1~2단계 상향 대신 툴측 총무게 약 2배로 초기 후보를 본다(C01, 두 방식 중복 금지 — A08).
    - 협동로봇: 70 이하는 기본 후보(R03), 70 초과는 상위 후보와 비교해 담당자가 확정하는 계기(C11, 자동 점수화 불승인).
    반환 {compare_upper, reason, rule, provisional[]}. 협동로봇 속도 기준(P01)이 없거나 % 가 아니면 compare_upper=None."""
    prov = ["P01"] if basis_approved is None and is_provisional("P01") else []
    approved = applied("P01") if basis_approved is None else basis_approved
    if robot_type == "industrial":
        return {"compare_upper": False, "reason": None, "rule": "C01", "provisional": []}
    if robot_type is None or speed is None:
        return {"compare_upper": None, "reason": "needs_information", "rule": None, "provisional": []}
    if robot_type != "cobot":
        return {"compare_upper": None, "reason": "engineering_review", "rule": None, "provisional": []}
    # P01 잠정 기준은 '오버라이드 %' — 다른 단위(mm/s 등)면 자동 판단하지 않는다
    if not unit or not approved or (prov and "%" not in str(unit)):
        return {"compare_upper": None, "reason": "P01", "rule": "C11", "provisional": []}
    if speed <= 70:
        return {"compare_upper": False, "reason": None, "rule": "R03", "provisional": prov}
    return {"compare_upper": True, "reason": None, "rule": "C11", "provisional": prov}


def screening_payload(tool_side_kg: float | None, robot_type: str | None) -> float | None:
    """C01·C10 — 후보 비교에 쓰는 하중. 산업용은 툴측 총무게 × 약 2배(초기 후보 검토용, 동하중 계산값 아님)."""
    if tool_side_kg is None:
        return None
    return round(tool_side_kg * (INDUSTRIAL_FACTOR if robot_type == "industrial" else 1), 3)


def pressure_screen(medium: str | None, pmin: float | None, pmax: float | None) -> dict[str, Any]:
    """PGR10·PGR23(C06 대체) — 실제 양압 공급을 1~5bar 와 비교, 진공은 양압 기준 미적용. atc_accessory.pressure_check 와 같다."""
    p = acc.pressure_check(medium, pmin, pmax)
    screening = "vacuum" if p["fit"] is None and p.get("note") else "standard_range_only" if p["fit"] else \
        "out_of_standard" if p["fit"] is False else None
    return {"screening": screening, "review": p["review"] and p["fit"] is False, "reason": p["reason"], "note": p.get("note")}


def pogo_lower_bound(pins: int | None) -> int | None:
    """R11·C05: 필요 핀을 받는 최소 모듈 수 = ceil(핀 수 / 8). 모듈 하나 = 장착 1개소."""
    return None if pins is None else math.ceil(pins / PINS_PER_MODULE)


def pins_for(current_A: float) -> int:
    """PGR04: 한 경로(공급 또는 리턴)의 핀 수 = max(1, ceil(전류 / 핀당 1A)) — 1.5A 2핀, 3A 3핀."""
    return acc.pins_for(current_A)


def power_pins(supply_A: list[float], shared_return: bool = False) -> int:
    """PGR04 전원 부분: 공급선마다 ceil(A/1A) + 리턴선마다 ceil(A/1A). 공유 리턴(승인된 경우)은 동시 합산 전류로 한 번."""
    supply = sum(pins_for(a) for a in supply_A)
    ret = pins_for(sum(supply_A)) if shared_return and supply_A else supply
    return supply + ret


power_spec = acc.power_spec          # 툴 전원 사양(회로·전압·정격/피크/기동 전류) — v1.2 계산기와 같은 읽기


def tool_pins(tool: dict[str, Any]) -> dict[str, Any]:
    """툴 하나의 포고핀 요구(v1.2 PGR02~PGR08·PGR19·PGR22) — atc_accessory.tool_electrical."""
    return acc.tool_electrical(tool)


def air_couplers(paths: int | None) -> int | None:
    """C03: 표준 커플러(PMM·PMF 한 쌍) = 독립 유로 2개 → ceil(필요 독립 유로 / 2). 모르면 None."""
    return None if paths is None else math.ceil(paths / PATHS_PER_COUPLER)


def slot_check(pogo_modules: int | None, air_modules: int | None, other: int = 0) -> dict[str, Any]:
    """C04(R16 대체): 포고핀 모듈 + 에어 커플러 + 기타가 차지하는 장착 위치 합 ≤ 표준 3개소. 수·암 한 쌍 = 1개소.
    넘으면 커스텀 필요. 3개소 이내여도 모든 툴의 배치·핀 배열·호환 확인은 따로(최종 호환 승인 아님)."""
    if pogo_modules is None or air_modules is None:
        return {"slot_sum": None, "standard_layout_possible": None, "customization_required": None}
    s = pogo_modules + air_modules + other
    return {"slot_sum": s, "standard_layout_possible": s <= STD_POSITIONS, "customization_required": s > STD_POSITIONS}


def tool_plates(tools: list[dict[str, Any]], spare: int | None = None) -> dict[str, Any]:
    """R10 + T18: 실제 교체하는 툴 조립체 수(같은 모델 여러 개도 각각). 예비품은 따로."""
    qs = [t.get("installed_quantity") for t in tools]
    operating = sum(qs) if qs and all(q is not None for q in qs) else None
    return {"operating": operating, "spare": spare,
            "total": None if operating is None or spare is None else operating + spare}


def tool_side_mass(tool: dict[str, Any]) -> dict[str, Any]:
    """J04 툴 무게(제품 제외) + J05 한 번에 드는 제품 총무게. 미확인은 None, 포함 부품 미확인이면 '잠정 하한'(JF14)."""
    assembly = _get(tool, "intake.tool_assembly_mass_kg")
    wp = 0.0 if tool.get("workpiece_not_applicable") else _get(tool, "mass_components.max_simultaneous_workpieces_kg")
    if assembly is None or wp is None:
        known = [v for v in (assembly, wp) if v is not None]
        lb = sum(known) if known else None
        return {"kg": None, "lower_bound_kg": lb if lb else None, "provisional": True,      # 0kg 하한은 '모름'으로
                "missing": [n for n, v in (("툴 무게(J04)", assembly), ("제품 무게(J05)", wp)) if v is None]}
    return {"kg": round(assembly + wp, 3), "lower_bound_kg": round(assembly + wp, 3),
            "provisional": not tool.get("mass_inclusions_confirmed"), "missing": []}


def _ladder(catalog: list[dict[str, Any]], series: str) -> list[list[dict[str, Any]]]:
    """계열 안 가반하중 단계(같은 가반하중 변형은 한 단계로 묶음). 가반하중 미상 모델은 뺀다."""
    items = sorted((c for c in catalog if c["series"] == series and c["payload_kg"] is not None),
                   key=lambda c: (c["payload_kg"], c["name"]))
    steps: list[list[dict[str, Any]]] = []
    for c in items:
        if steps and steps[-1][0]["payload_kg"] == c["payload_kg"]:
            steps[-1].append(c)
        else:
            steps.append([c])
    return steps


def screen_candidates(catalog: list[dict[str, Any]], screening_kg: float | None, robot_type: str | None,
                      speed: dict[str, Any], *, wireless: bool = False) -> list[dict[str, Any]]:
    """R02(선정 하중 이상인 가장 작은 단계) + C11(속도·경계 하중은 상위 후보 비교, 자동 상향 없음) + R09 + C09(유선 기본).
    screening_kg 는 산업용이면 이미 약 2배(C01). 계열마다 하나씩 참고 후보."""
    out = []
    for s in SERIES:
        if robot_type not in s["robots"]:
            continue
        cat = [c for c in catalog if wireless or not c.get("wireless")]
        ladder = _ladder(cat, s["key"])
        if not ladder:
            continue
        cand: dict[str, Any] = {"series": s["key"], "series_label": s["label"], "series_note": s["note"],
                                "primary": robot_type in s["primary_for"], "models": [], "notes": [], "compare_reasons": []}
        hidden = [c["name"] for c in catalog if c["series"] == s["key"] and c.get("wireless") and not wireless]
        if hidden:
            cand["notes"].append(f"무선 {', '.join(hidden)} 은 무선을 따로 고른 경우에만 검토(C09 유선 기본)")
        if screening_kg is None:
            cand["notes"].append("툴측 총무게를 몰라 하중 기준 후보를 정할 수 없음")
            cand["range_kg"] = [ladder[0][0]["payload_kg"], ladder[-1][0]["payload_kg"]]
            out.append(cand)
            continue
        base = next((i for i, st in enumerate(ladder) if st[0]["payload_kg"] >= screening_kg), None)
        if base is None:
            cand["notes"].append(f"이 계열 최대 {ladder[-1][0]['payload_kg']:g}kg 로는 선정 하중 {screening_kg:g}kg 를 수용 못함(R09 — 내부 검토)")
            cand["out_of_range"] = True
            out.append(cand)
            continue
        cand["models"] = ladder[base]
        top = ladder[base][0]["payload_kg"]
        # C11 — 상향은 자동이 아니라 동급·상위 후보 비교 후 담당자 확정. 길이·치우침·틸팅으로 단계를 연속 가산하지 않는다.
        if speed.get("compare_upper"):
            cand["compare_reasons"].append("협동로봇 속도 70 초과")
        # 툴 무게(J04)에 툴플레이트·어댑터가 빠져 있을 수 있다(C10) — 정격에 걸리면 상위 후보와 비교(기존 화면 표시 기준 80% 유지)
        if screening_kg >= top * 0.8:
            cand["compare_reasons"].append("선정 하중이 정격에 가까움")
        if cand["compare_reasons"]:
            nxt = ladder[base + 1] if base + 1 < len(ladder) else []
            cand["consider_up_to"] = nxt
            cand["notes"].append(f"{'·'.join(cand['compare_reasons'])} — "
                                 + (f"상위 {' / '.join(m['name'] for m in nxt)}와 조건 비교 후 담당자 확정(C11, 자동 상향 아님)" if nxt
                                    else "이 계열에 상위 모델이 없어 내부 검토(R09)"))
        elif speed.get("reason") == "P01":
            cand["notes"].append("속도 단위·기준(P01) 확정 전 — 상위 후보 비교 여부는 내부 검토")
        if robot_type == "cobot" and top > FACTS["cobot_oriented_nominal_range_up_to_kg"]:
            cand["notes"].append(f"협동용 범위({FACTS['cobot_oriented_nominal_range_up_to_kg']}kg) 초과 — 내부 검토(R09)")
        out.append(cand)
    # 로봇 종류에 맞는 주 계열을 앞으로, 수용 불가 계열은 뒤로
    out.sort(key=lambda c: (bool(c.get("out_of_range")), not c["primary"],
                            [s["key"] for s in SERIES].index(c["series"])))
    return out


_SERIES_BY_KEY = {s["key"]: s for s in SERIES}


def _robot_type(intake: dict[str, Any]) -> str | None:
    types = {r.get("type") for r in intake.get("robots") or []}
    return "industrial" if "industrial" in types else "cobot" if "cobot" in types else None


def model_options(catalog: list[dict[str, Any]], intake: dict[str, Any]) -> list[dict[str, Any]]:
    """대화로 고를 수 있는 툴체인저 — 제품 DB 모델마다 이 로봇·선정 하중에 쓸 수 있는지(fits)와 이유.
    AI 는 fits 인 모델만 제안·선택할 수 있다(모델명을 지어내지 않게)."""
    o = evaluate(intake, catalog) if intake.get("tools") else None
    screening, rt = (o or {}).get("screening_payload_kg"), _robot_type(intake)
    out = []
    for c in sorted(catalog, key=lambda x: (x["series"], x["payload_kg"] if x["payload_kg"] is not None else 1e9, x["name"])):
        s = _SERIES_BY_KEY.get(c["series"])
        why = None
        if s is None or rt not in s["robots"]:
            why = f"{ROBOT_KO.get(rt, '이 로봇')}에 쓰는 계열이 아님"
        elif c["payload_kg"] is None:
            why = "정격(가반하중) 정보 없음"
        elif screening is not None and c["payload_kg"] < screening:
            why = f"정격 {c['payload_kg']:g}kg < 선정 하중 {screening:g}kg"
        out.append({"name": c["name"], "payload_kg": c["payload_kg"], "series": c["series"],
                    "series_label": s["label"] if s else c.get("series_label"), "wireless": bool(c.get("wireless")),
                    "fits": why is None, "reason": why})
    return out


def alternatives(candidates: list[dict[str, Any]], catalog: list[dict[str, Any]], screening: float | None,
                 robot_type: str | None, n: int = 2) -> list[dict[str, Any]]:
    """2·3순위 후보(사용자 2026-10-07: '다른 계열 후보' 대신) — 1순위(규칙 또는 담당자 선택)를 뺀, 이 로봇·선정 하중에 쓸 수 있는
    제품 DB 모델 중에서: 같은 계열의 더 큰 모델(정격이 가까운 순 — 여유가 더 있는 대안) → 다른 계열(계열 순서·정격 순).
    무선은 유선 뒤(유선이 기본, v0.8 C09). 수동·듀얼처럼 쓰임이 다른 계열은 넣지 않는다. 사실(정격·여유·계열 특징)만 적는다."""
    first = next((c for c in candidates if c["models"] and not c.get("out_of_range")), None)
    if first is None or screening is None:
        return []
    taken = {m["name"] for m in first["models"]}
    order = [s["key"] for s in SERIES]
    pool = []
    for c in catalog:
        s = _SERIES_BY_KEY.get(c["series"])
        if (c["name"] in taken or s is None or not s["primary_for"] or robot_type not in s["robots"]
                or c["payload_kg"] is None or c["payload_kg"] < screening):
            continue
        same = c["series"] == first["series"]
        pool.append(((0 if same else 1, order.index(c["series"]), bool(c.get("wireless")), c["payload_kg"], c["name"]), c, s))
    pool.sort(key=lambda x: x[0])
    out = []
    for rank, (key, c, s) in enumerate(pool[:n], start=2):
        p, margin = c["payload_kg"], c["payload_kg"] - screening
        if key[0] == 0:
            # 담당자가 큰 모델을 골랐으면 규칙 모델이 더 작은 대안으로 온다
            size = "더 큰" if p > (first["models"][0].get("payload_kg") or 0) else "더 작은"
            why = f"같은 계열의 {size} 모델 — 정격 {p:g}kg(선정 하중 {screening:g}kg 대비 여유 {margin:g}kg)"
        else:
            why = f"다른 계열 — 정격 {p:g}kg · {s['note']}"
        if c.get("wireless"):
            why += " · 무선(유선이 기본이라 따로 고를 때만)"
        out.append({"rank": rank, "name": c["name"], "payload_kg": p, "series": c["series"], "series_label": s["label"],
                    "wireless": bool(c.get("wireless")), "reason": why})
    return out


def apply_preference(candidates: list[dict[str, Any]], catalog: list[dict[str, Any]], pref: str, screening: float | None,
                     robot_type: str | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """담당자가 고른 모델을 1순위 후보로(C11). 제품 DB 에 있고, 이 로봇 계열이고, 정격 ≥ 선정 하중이어야 한다.
    반환 (후보 목록, {model, applied, reason, rule_model})."""
    rule = next((c for c in candidates if c["models"] and not c.get("out_of_range")), None)
    rule_model = rule["models"][0]["name"] if rule else None
    m = next((c for c in catalog if c["name"].lower() == pref.lower()), None)
    info = {"model": m["name"] if m else pref, "applied": False, "reason": None, "rule_model": rule_model}
    s = _SERIES_BY_KEY.get(m["series"]) if m else None
    if m is None:
        info["reason"] = "제품 DB 툴체인저에 없는 모델"
    elif s is None or robot_type not in s["robots"]:
        info["reason"] = f"{ROBOT_KO.get(robot_type, '이 로봇')}에 쓰는 계열이 아님"
    elif m["payload_kg"] is None:
        info["reason"] = "정격(가반하중) 정보 없음"
    elif screening is not None and m["payload_kg"] < screening:
        info["reason"] = f"정격 {m['payload_kg']:g}kg < 선정 하중 {screening:g}kg"
    if info["reason"]:
        return candidates, info
    info["applied"] = True
    if rule and any(x["name"] == m["name"] for x in rule["models"]):
        rule = {**rule, "models": [m], "chosen_by_user": True}             # 규칙 후보와 같으면 그 모델 하나로만
        return [rule] + [c for c in candidates if c["series"] != rule["series"]], info
    base = next((c for c in candidates if c["series"] == m["series"]), None) or {
        "series": s["key"], "series_label": s["label"], "series_note": s["note"], "primary": robot_type in s["primary_for"],
        "notes": [], "compare_reasons": []}
    chosen = {**base, "models": [m], "out_of_range": False, "consider_up_to": [], "chosen_by_user": True,
              "notes": [f"담당자 선택(C11) — 규칙상 기본 후보는 {rule_model or '없음'}"]}
    return [chosen] + [c for c in candidates if c is not base], info


# ── 전체 판정 ────────────────────────────────────────────────────────────────────

def _reason(rs: list[dict[str, Any]], status: str, text: str, rule: str | None = None, ref: str | None = None) -> None:
    if not any(r["text_ko"] == text for r in rs):
        rs.append({"status": status, "status_ko": STATUS_KO[status], "text_ko": text, "rule": rule, "ref": ref})


def evaluate(intake: dict[str, Any], catalog: list[dict[str, Any]], *, catalog_approved: bool | None = None) -> dict[str, Any]:
    """입력 → output_record(JSON output_record_template 형식 + 화면용 junior_summary)."""
    project = intake.get("project") or {}
    robots = intake.get("robots") or []
    tools = intake.get("tools") or []
    reasons: list[dict[str, Any]] = []
    missing: list[str] = []
    trace: list[str] = ["R01", "R20", "R21"]
    assumptions: list[str] = []

    # R01 공급 범위 — 시스템 요청은 따로 기록. 툴스탠드는 표준 mTS2·커스텀·고객 제작 중 고객 선택(C19, 자동 포함 안 함)
    if (project.get("system_request") or "").strip():
        _reason(reasons, "engineering_review",
                f"시스템·툴스탠드 등 단품 외 요청은 별도 요구사항으로 기록 — 툴스탠드는 표준 {TOOL_STAND['model']}"
                f"({TOOL_STAND['name_ko']})·커스텀 주문제작·고객 제작 중 공급 범위 확인(C19)", "R01", "C19")

    # 로봇
    types = {r.get("type") for r in robots}
    robot_type = "industrial" if "industrial" in types else "cobot" if "cobot" in types else None
    if not robots or any(r.get("type") is None for r in robots):
        missing.append("로봇 종류(J01)")
    if len({r.get("type") for r in robots if r.get("type")}) > 1:
        _reason(reasons, "engineering_review", "협동·산업용 로봇이 섞여 있음 — 로봇별로 따로 검토", "J01")

    # 툴 수량(J03 ↔ 툴별 수량 합, JF08·JF09)
    tp = tool_plates(tools, project.get("spare_tool_plates"))
    count = project.get("tool_count")
    if count is None:
        missing.append("교체 툴 수(J03)")
    if count is not None and tp["operating"] is not None and tp["operating"] != count:
        _reason(reasons, "needs_information",
                f"툴 수량 불일치: 전체 {count}개 ↔ 툴별 합계 {tp['operating']}개 — 다시 확인", "R10", "JF09")
    trace.append("R10")

    # 툴측 총무게(R02·C10) — 모든 교체 툴 중 가장 무거운 툴로 공통 마스터를 고른다
    masses = [tool_side_mass(t) for t in tools]
    known = [m["kg"] for m in masses if m["kg"] is not None]
    required = max(known) if known and len(known) == len(masses) and masses else None
    lower = max((m["lower_bound_kg"] for m in masses if m["lower_bound_kg"] is not None), default=None)
    for t, m in zip(tools, masses):
        for miss in m["missing"]:
            missing.append(f"{t.get('name') or '툴'} — {miss}")
    if not tools:
        missing.append("툴별 크기·무게(J04)")
    if known:
        assumptions.append("툴측 총무게는 그리퍼·대상물·T.P·툴측 브라켓·고객 제작 툴측 어댑터·액세서리를 포함해 정격 정의와 맞춰 "
                           "비교(C10·C20) — 고객 조립체 무게에 이미 든 부품은 다시 더하지 않음, 사진·도면 확인 전까지 잠정값")
    # C01 산업용 약 2배 — 초기 후보 검토용. 속도별 단계 상향과 중복하지 않는다(A08)
    screening = screening_payload(required, robot_type)
    if robot_type == "industrial" and required is not None:
        assumptions.append(f"산업용 초기 후보는 툴측 총무게 {required:g}kg × 약 {INDUSTRIAL_FACTOR}배 = {screening:g}kg 로 검토(C01) — "
                           "동하중 계산값·모든 동작 적합 보장 계수가 아니며 적용 질량과 정격 정의는 기술 담당자 확인")
    trace += ["R02", "C10", "C01"]

    prov_used: set[str] = set()                 # 이번 판정에 실제로 쓴 잠정 기준
    # 속도(C01·C11) — 자동 상향 없음
    reviews = []
    for r in robots:
        sp = r.get("speed") or {}
        u = speed_review(r.get("type"), sp.get("value"), sp.get("unit"))
        reviews.append(u)
        prov_used.update(u.get("provisional") or [])
        if sp.get("value") is None:
            missing.append(f"{r.get('model') or '로봇'} 운전 속도(J06)")
        elif u["reason"] in UNRESOLVED:
            _reason(reasons, "engineering_review",
                    f"속도 기준 미확정({u['reason']}: {UNRESOLVED[u['reason']]['topic']}) — 상위 후보 비교 여부는 내부 검토", u["rule"], u["reason"])
        elif u.get("compare_upper"):
            _reason(reasons, "engineering_review",
                    f"{r.get('model') or '협동로봇'} 속도 {sp['value']:g}{sp.get('unit') or ''} — 70 초과라 무게중심·정격 여유와 함께 "
                    "동급·상위 후보를 비교해 담당자가 확정(C11, 자동 상향 아님)", "C11")
    speed = {"compare_upper": any(u.get("compare_upper") for u in reviews),
             "reason": next((u["reason"] for u in reviews if u.get("reason")), None) if reviews else "needs_information",
             "rules": sorted({u["rule"] for u in reviews if u.get("rule")}),
             "provisional": sorted({x for u in reviews for x in u.get("provisional") or []})}
    trace.append("C11")

    # 편하중·틸팅·밴딩(R08·C07·C08) — 정성 입력만으로 초기 상담·후보 제시는 막지 않고, 최종 적합 확정과 분리한다
    for t in tools:
        nm = t.get("name") or "툴"
        if _get(t, "intake.tilts_or_flips") is True:
            _reason(reasons, "engineering_review",
                    f"{nm}: 기울임·뒤집기 있음 — 편하중·결합 안정성(결합면 벌어짐·접점·에어 누설) 검토 대상, 사진·도면·영상 연결. "
                    "후보는 제시하되 최종 적합은 기술 검토 후, 상위 모델로 바꾸는 것만으로 해결로 보지 않음(R08·C08)", "R08", "C08")
        elif _get(t, "intake.tilts_or_flips") is None:
            missing.append(f"{nm} 기울임 여부(J07)")
    if (project.get("process") or {}).get("has_contact_force") is True:
        _reason(reasons, "engineering_review",
                "누르기·끼우기·자르기 등 공정 외력 있음 — 편하중·결합 안정성 검토, 동작 설명·영상 확보(R08·C08)", "R08", "C08")
    trace += ["R08", "C07", "C08"]

    # 액세서리(v1.2 PGR01~PGR24) — 툴별 전기·공압 요구 → 공통 마스터 배치 → 3개소 → 통합 수량표
    for t in tools:
        nm = t.get("name") or "툴"
        for k, lab in (("needs_power", "전기"), ("needs_air", "공압·진공"), ("has_sensors_or_signals", "센서·신호")):
            if _get(t, f"intake.{k}") is None:
                missing.append(f"{nm} {lab} 필요 여부(J08)")
        if _get(t, "intake.needs_air") is True and acc._air_valve(t) == "tool_side" \
                and _get(t, "intake.needs_power") is False and _get(t, "intake.has_sensors_or_signals") is False:
            _reason(reasons, "engineering_review", f"{nm}: 밸브가 툴측인데 전기·센서 없음으로 답함 — 밸브 전원·제어선이 결합면을 지나므로 "
                                                   "포고핀 산정에 넣어야 함, 확인(PGR22)", "PGR22")
    pogo_needed = any(acc.needs_electrical(t) for t in tools)
    pneu_needed = any(_get(t, "intake.needs_air") is True for t in tools)
    n_master = sum(int(r.get("quantity") or 1) for r in robots) or 1
    accessory = acc.evaluate(tools, n_master, pogo_needed=pogo_needed, air_needed=pneu_needed)
    tools_el = {(t.get("name") or "툴"): (t.get("electrical") or {}).get("control_method_raw") for t in tools}
    for m in accessory["missing"]:
        missing.append(f"{m}(액세서리 산정)")
    for a in accessory["air_tools"]:
        p = a["pressure"]
        if p["reason"] == "needs_information":
            missing.append(f"{a['tool']} 실제 공급 압력(F05)")
        elif p["review"]:
            _reason(reasons, "engineering_review", f"{a['tool']}: {p['note']}", p["reason"] or "PGR10")
        elif p.get("note"):
            assumptions.append(f"{a['tool']}: {p['note']}")
    for e in accessory["electrical_tools"]:
        if e["interface"] in ("RS485", "CAN", "other"):
            _reason(reasons, "engineering_review",
                    f"{e['tool']}: 통신({_IFACE_KO[e['interface']]}{' — ' + str((tools_el.get(e['tool']) or '')) if tools_el.get(e['tool']) else ''}) — "
                    "실제 통신 옵션·변환기 위치·결선·통신 품질은 엔지니어 검토(PGR19·R13), 핀 수가 맞아도 통신 적합은 별도", "PGR19", "R13")
    if accessory["assumptions"]:
        _reason(reasons, "engineering_review", "액세서리 수량은 조건부 초안 — " + "; ".join(accessory["assumptions"])
                + " (담당자 확인 후 확정, PGR17)", "PGR17")
    if pogo_needed or pneu_needed:
        _reason(reasons, "engineering_review",
                "액세서리 수량은 규칙 계산값 — 병렬 전원·핀맵·통신·공압 적합은 엔지니어 검토, 견적 수량은 담당자 확인 후 확정(PGR18·PGR24)"
                + "".join(f" · {n}" for n in accessory["review_notes"]), "PGR18", "PGR24")
    master = accessory["master"]
    if master["customization_required"]:
        _reason(reasons, "nonstandard_or_out_of_range",
                f"PPM {master['ppm_per_master']} + PMM {master['pmm_per_master']} = {master['positions']}개소 — 표준 {STD_POSITIONS}개소 초과, "
                "커스텀 필요(PGR13) — 표준 모델·견적과 분리해 검토 요청", "PGR13")
    pogo_tools, pneu_tools = accessory["electrical_tools"], accessory["air_tools"]
    pogo_modules = master["ppm_per_master"] if pogo_needed else 0
    pneu_modules = master["pmm_per_master"] if pneu_needed else 0
    slots = {"slot_sum": master["positions"], "customization_required": master["customization_required"],
             "standard_layout_possible": None if master["positions"] is None else not master["customization_required"]}
    trace += [f"PGR{i:02d}" for i in range(1, 25)]

    # 케이블(R17·C15) — 마스터당 4m(마스터 작동 전원·제어 전용)·1m 각 1개. 길이 변경은 전압 강하 검토·고객 협의
    m4, m1 = CABLE_STD["master_to_controlbox"]["length_m"], CABLE_STD["controlbox_to_robot_cabinet_or_plc"]["length_m"]
    for r in robots:
        cb = r.get("cables") or {}
        nm = r.get("model") or "로봇"
        for key, need_key, std, lab in (("master_4m_sufficient", "master_to_controller_required_m", m4, "MASTER T.C→CONTROLBOX"),
                                        ("system_1m_sufficient", "controller_to_system_required_m", m1, "CONTROLBOX→로봇 제어반·PLC")):
            if cb.get(key) is None:
                missing.append(f"{nm} 케이블 {lab} {std:g}m 충분 여부(J09)")
            elif cb.get(key) is False or (cb.get(need_key) is not None and cb[need_key] > std):
                need = cb.get(need_key)
                _reason(reasons, "engineering_review",
                        f"{nm}: {lab} 표준 {std:g}m 부족" + (f"(필요 {need:g}m)" if need else "")
                        + " — 길이 변경은 전압 강하 검토 후 고객과 길이·사양 협의(C15)", "R17", "C15")
    trace += ["R17", "C15"]

    # 환경(J10)
    env = project.get("environment") or {}
    if env.get("has_special_conditions") is True:
        tags = ", ".join(env.get("tags") or []) or "특별 환경"
        _reason(reasons, "engineering_review", f"사용 환경({tags}) — 환경 등급 확인 필요", None, "Q25")
    elif env.get("has_special_conditions") is None:
        missing.append("사용 환경(J10)")

    if prov_used:
        _reason(reasons, "engineering_review",
                f"잠정 기준 적용({'·'.join(sorted(prov_used))}) — 영업부 잠정값으로 계산, 엔지니어 확정 전", None, "provisional")
    # 후보(R02·R09·C09·C11) — 모델표 승인 전에는 참고 후보만
    wireless = project.get("wireless_requested") is True
    candidates = screen_candidates(catalog, screening, robot_type, speed, wireless=wireless)
    # 담당자가 대화로 고른 모델(C11 — 동급·상위 후보 비교 후 담당자 확정). 쓸 수 있는 모델이면 1순위로, 아니면 이유를 남기고 규칙 후보 유지
    preference = None
    if str(project.get("preferred_model") or "").strip():
        candidates, preference = apply_preference(candidates, catalog, str(project["preferred_model"]).strip(), screening, robot_type)
        if not preference["applied"]:
            _reason(reasons, "engineering_review", f"요청한 모델 {preference['model']} 적용 불가 — {preference['reason']} (규칙 후보 유지)", "C11")
    first0 = next((c for c in candidates if c["models"] and not c.get("out_of_range")), None)
    if first0 and "선정 하중이 정격에 가까움" in first0.get("compare_reasons", []):
        _reason(reasons, "engineering_review",
                f"선정 하중 {screening:g}kg 가 {first0['models'][0]['name']} 정격 {_kg(first0['models'][0]['payload_kg'])}에 걸림 — "
                "동급·상위 후보 조건 비교 후 담당자 확정(C11)", "C11")
    # P04 — 제품 DB 공식 사양 모델이면 모델명·정격·상향 순서는 확정(사용자 2026-10-02). †미확정·'확인 필요' 사양 모델은 확인 후
    if catalog_approved is None:
        catalog_approved = bool(first0) and all(m.get("official") for m in first0["models"])
    if first0 and not catalog_approved:
        _reason(reasons, "engineering_review",
                f"{' / '.join(m['name'] for m in first0['models'])} 는 제품 DB 사양이 미확정(†·확인 필요) — 정격 확인 후 확정(P04)", "R02", "P04")
    if required is None:
        missing.append("툴측 총무게(툴 무게+제품 무게) — 하중 기준 후보 산정에 필요")
    trace += ["R09", "C09", "R18", "C14", "R22"]

    missing = list(dict.fromkeys(missing))
    statuses = []
    if missing:
        statuses.append("needs_information")
    statuses += [s for s in STATUS_ORDER if any(r["status"] == s for r in reasons) and s not in statuses]
    if any(c.get("out_of_range") for c in candidates if c["primary"]) and "nonstandard_or_out_of_range" not in statuses:
        statuses.append("nonstandard_or_out_of_range")
    if not statuses:
        statuses = ["standard_candidate"] if catalog_approved else ["engineering_review"]
    first = first0
    return {
        "rules_revision": RULES_REVISION, "catalog_revision": None,
        "status": statuses[0], "statuses": [{"status": s, "status_ko": STATUS_KO[s]} for s in statuses],
        "screening_candidates": candidates,
        "alternatives": alternatives(candidates, catalog, screening, robot_type),
        "final_selected_model_id": None,                       # 담당자 확정 전에는 비움(T17·C11)
        "tool_side_mass": [{"tool": t.get("name") or f"툴 {i + 1}", **m} for i, (t, m) in enumerate(zip(tools, masses))],
        "required_payload_kg": required, "required_payload_lower_bound_kg": lower,
        "screening_payload_kg": screening, "preference": preference,
        "industrial_factor": INDUSTRIAL_FACTOR if robot_type == "industrial" else None,
        "speed_review": speed, "tool_plates": tp,
        "pogo_modules": pogo_modules, "pogo_needed": pogo_needed, "pogo_tools": pogo_tools,
        "slot_check": slots,
        "pneumatic_modules": pneu_modules, "pneumatic_needed": pneu_needed, "pneumatic_tools": pneu_tools,
        "accessory": accessory,
        "catalog_approved": bool(catalog_approved),
        "db_confirmed": [{"id": k, "text": v} for k, v in DB_CONFIRMED.items() if k != "P04" or catalog_approved],
        "provisional_applied": [{"id": x, "text": PROVISIONAL[x]["text"]} for x in sorted(prov_used)],
        "engineering_review_reasons": [r for r in reasons if r["status"] != "needs_information"],
        "information_reasons": [r for r in reasons if r["status"] == "needs_information"],
        "missing_fields": missing,
        "followup_questions": followups(intake),
        "pending_questions": pending_questions(intake),
        "data_requests": data_requests(intake),
        "rule_trace": list(dict.fromkeys(trace)), "assumptions": assumptions,
        "junior_summary": junior_summary(first, tp, pogo_needed, pneu_needed, required, lower, speed, robots, pogo_tools,
                                         pneu_modules=pneu_modules, catalog_ok=bool(catalog_approved), screening=screening,
                                         accessory=accessory,
                                         pogo_unknown=not pogo_needed and any(_get(t, f"intake.{k}") is None for t in tools
                                                                             for k in ("needs_power", "has_sensors_or_signals")),
                                         pneu_unknown=not pneu_needed and any(_get(t, "intake.needs_air") is None for t in tools)),
        "basis": basis(first, candidates, tools, masses, required, robot_type, speed, robots, pogo_needed, pneu_needed,
                       pogo_tools, pogo_modules, pneu_modules, pneu_tools, screening, accessory),
        "similar_cases": [], "intake_completed": True,
    }


ROBOT_KO = {"cobot": "협동로봇", "industrial": "산업용 로봇"}


def _kg(v: float | None) -> str:
    return f"{v:g}kg" if v is not None else "?kg"


def _model_kg(m: dict[str, Any]) -> str:
    return f"{m['name']}({_kg(m.get('payload_kg'))})"


def basis(first: dict[str, Any] | None, candidates: list[dict[str, Any]], tools: list[dict[str, Any]],
          masses: list[dict[str, Any]], required: float | None, robot_type: str | None, speed: dict[str, Any],
          robots: list[dict[str, Any]], pogo_needed: bool, pneu_needed: bool,
          pogo_tools: list[dict[str, Any]] | None = None, pogo_modules: int | None = None,
          pneu_modules: int | None = None, pneu_tools: list[dict[str, Any]] | None = None,
          screening: float | None = None, accessory: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """추천 근거 — 입력값 → 규칙 → 판단을 한 줄씩(사용자 2026-10-02: 추천할 때 뒷받침 근거가 명확해야 함).
    문장은 계산 결과로만 만든다(AI 생성 아님). 각 줄: {item, input, judgement, rule, ok}."""
    out: list[dict[str, Any]] = []
    industrial = robot_type == "industrial"
    # 1) 툴측 총무게 = 툴 무게 + 한 번에 드는 제품 무게, 가장 무거운 툴이 기준. 산업용은 약 2배(C01)
    parts = []
    for t, m in zip(tools, masses):
        nm = t.get("name") or "툴"
        it = t.get("intake") or {}
        a, wp = it.get("tool_assembly_mass_kg"), (t.get("mass_components") or {}).get("max_simultaneous_workpieces_kg")
        wp_txt = "제품 없음" if t.get("workpiece_not_applicable") else f"제품 {wp:g}kg" if wp is not None else "제품 ?kg"
        parts.append(f"{nm}: 툴 {a:g}kg + {wp_txt}" + (f" = {m['kg']:g}kg" if m["kg"] is not None else "")
                     if a is not None else f"{nm}: 툴 무게 모름")
    judge = "모든 툴의 무게를 알아야 기준을 정할 수 있음"
    if required is not None:
        judge = f"가장 무거운 툴 기준 {required:g}kg(잠정 — T.P·어댑터 포함 여부 확인 전)"
        if industrial:
            judge += f" → 산업용 × 약 {INDUSTRIAL_FACTOR}배 = 선정 하중 {screening:g}kg(C01)"
    out.append({"item": "툴측 총무게", "input": " / ".join(parts) or "툴 정보 없음", "judgement": judge,
                "rule": "R02·C10" + ("·C01" if industrial else ""), "ok": required is not None})
    # 2) 로봇 종류 → 주 계열
    out.append({"item": "로봇 종류", "input": ", ".join(ROBOT_KO.get(r.get("type"), "모름") for r in robots) or "모름",
                "judgement": (f"{ROBOT_KO[robot_type]}에 맞는 계열부터 검토 → {first['series_label']}(유선 기본, C09)"
                              if first and robot_type in ROBOT_KO
                              else "로봇 종류를 알아야 계열을 정할 수 있음" if robot_type is None else "맞는 계열에서 수용 모델 없음"),
                "rule": "J01·C09", "ok": robot_type is not None and first is not None})
    # 3) 정격 가반하중 — 선정 하중 이상인 가장 작은 단계(제품 DB 사양)
    if first and screening is not None:
        m = first["models"][0]
        nxt = first.get("consider_up_to") or []
        names = " / ".join(x["name"] for x in first["models"])
        note = f" · 비교 후보 {', '.join(_model_kg(x) for x in nxt)}(담당자 확정)" if nxt else ""
        out.append({"item": "정격 가반하중", "input": f"{names} 정격 {_kg(m['payload_kg'])}(제품 DB)",
                    "judgement": f"{_kg(m['payload_kg'])} ≥ 선정 하중 {screening:g}kg — 이 계열에서 받을 수 있는 가장 작은 단계{note}",
                    "rule": "R02·C11", "ok": True})
    # 4) 속도 — 자동 상향 없음(C01·C11)
    sp = [f"{(r.get('speed') or {}).get('value')}{(r.get('speed') or {}).get('unit') or ''}" for r in robots
          if (r.get("speed") or {}).get("value") is not None]
    if industrial:
        j, ok = f"산업용은 속도별 단계 상향 대신 약 {INDUSTRIAL_FACTOR}배 하중으로 검토(C01, 중복 적용 안 함)", True
    elif speed.get("compare_upper"):
        j, ok = "70 초과 — 상위 후보와 비교해 담당자 확정(C11, 자동 상향 아님)", True
    elif speed.get("reason") == "needs_information":
        j, ok = "속도를 알아야 상위 후보 비교 여부를 정할 수 있음", False
    elif speed.get("reason"):
        j, ok = f"속도 기준 미확정({speed.get('reason')}) — 상위 후보 비교 여부는 내부 검토", False
    else:
        j, ok = "70 이하 — 기본 후보 그대로", True
    if speed.get("provisional") and not industrial:
        j += f"(잠정 기준 {'·'.join(speed['provisional'])})"
    out.append({"item": "운전 속도", "input": ", ".join(sp) or "모름", "judgement": j,
                "rule": "C01" if industrial else "R03·C11", "ok": ok})
    # 5) 액세서리(v1.2) — 포고핀·에어커플러
    master = (accessory or {}).get("master") or {}
    pogo_unknown = not pogo_needed and any(_get(t, f"intake.{k}") is None for t in tools for k in ("needs_power", "has_sensors_or_signals"))
    pneu_unknown = not pneu_needed and any(_get(t, "intake.needs_air") is None for t in tools)
    out.append({"item": "포고핀",
                "input": " / ".join(_pin_input(pt) for pt in pogo_tools or []) if pogo_needed else "모름" if pogo_unknown or not tools else "전기·센서 없음",
                "judgement": pogo_text(pogo_tools or [], master) if pogo_needed else
                             "미정 — 툴의 전기·센서 사용 여부를 알아야 포고핀 모듈을 정함" if pogo_unknown or not tools else "포고핀 모듈 없이 구성",
                "rule": "PGR03~PGR08·PGR11~PGR16",
                "ok": (pogo_modules is not None and all(pt["status"] == "confirmed" for pt in pogo_tools or [])) if pogo_needed
                else not (pogo_unknown or not tools)})
    out.append({"item": "공압",
                "input": " / ".join(_air_input(x) for x in pneu_tools or []) if pneu_needed else "모름" if pneu_unknown or not tools else "없음",
                "judgement": air_text(pneu_tools or [], master) if pneu_needed else
                             "미정 — 툴의 공압 사용 여부를 알아야 에어커플러를 정함" if pneu_unknown or not tools else "에어커플러 없이 구성",
                "rule": "PGR09·PGR10·PGR20~PGR23",
                "ok": (pneu_modules is not None and all(x["status"] == "confirmed" for x in pneu_tools or [])) if pneu_needed
                else not (pneu_unknown or not tools)})
    # 6) 다른 계열을 고르지 않은 이유
    for c in candidates:
        if c is first:
            continue
        why = "; ".join(c.get("notes") or []) or ("이 로봇 종류의 주 계열이 아님" if not c.get("primary") else "더 작은 단계가 있는 계열을 먼저 고름")
        out.append({"item": f"다른 계열 · {c['series_label']}", "input": " / ".join(_model_kg(m) for m in c.get("models") or []) or "-",
                    "judgement": f"1순위 아님 — {why}", "rule": "R02·R09", "ok": None})
    # 한눈에 볼 핵심값(short) — 화면은 이걸 굵게, 설명(input·judgement)은 접어서 보인다(사용자 2026-10-02: 핵심만 간단히)
    lb = master.get("electrical_lower_bound")
    for b in out:
        it = b["item"]
        if it == "툴측 총무게":
            b["short"] = ("미정" if required is None else
                          f"{required:g}kg ×{INDUSTRIAL_FACTOR} = {screening:g}kg" if industrial else f"{required:g}kg")
        elif it == "로봇 종류":
            b["short"] = (f"{ROBOT_KO[robot_type]} → {first['series_label'].split('(')[0]}" if first and robot_type in ROBOT_KO
                          else "미정" if robot_type is None else "맞는 모델 없음")
        elif it == "정격 가반하중":
            b["short"] = f"{first['models'][0]['name']} {_kg(first['models'][0]['payload_kg'])} ≥ {screening:g}kg"
        elif it == "운전 속도":
            b["short"] = (f"×{INDUSTRIAL_FACTOR} 하중으로 대체" if industrial
                          else "상위 후보 비교" if speed.get("compare_upper")
                          else "속도 모름" if speed.get("reason") == "needs_information"
                          else "기준 미정" if speed.get("reason") else "기본 후보")
        elif it == "포고핀":
            cond = any(pt["status"] == "conditional" for pt in pogo_tools or [])
            b["short"] = ((f"PPM {pogo_modules}개" + (" (조건부)" if cond else "") if pogo_modules is not None else
                           f"미정(확인된 툴 하한 {lb}개)" if lb else "미정") if pogo_needed
                          else "미정" if pogo_unknown or not tools else "없음")
        elif it == "공압":
            cond = any(x["status"] == "conditional" for x in pneu_tools or [])
            b["short"] = ((f"PMM {pneu_modules}개" + (" (조건부)" if cond else "") if pneu_modules is not None else "필요(수량 미정)")
                          if pneu_needed else "미정" if pneu_unknown or not tools else "없음")
        else:
            b["short"] = "1순위 아님"
    sentences = basis_sentences(first, tools, masses, required, robot_type, speed, robots, pogo_needed, pneu_needed,
                                pogo_unknown, pneu_unknown, pogo_tools or [], pogo_modules, pneu_modules, pneu_tools or [],
                                screening, accessory)
    for b in out:
        b["sentence"] = sentences.get(b["item"]) or b["judgement"]
        # selection: 이 모델을 고른 근거 / config: 고른 뒤 함께 들어갈 구성품 수량의 근거 / other: 다른 계열(참고)
        b["group"] = ("config" if b["item"] in ("포고핀", "공압") else "other" if b["ok"] is None else "selection")
    return out


def _short_name(name: str) -> str:
    return re.split(r"\s*\(", name or "툴")[0].strip() or "툴"


def basis_sentences(first, tools, masses, required, robot_type, speed, robots, pogo_needed, pneu_needed,
                    pogo_unknown, pneu_unknown, pogo_tools, pogo_modules, pneu_modules, pneu_tools,
                    screening=None, accessory=None) -> dict[str, str]:
    """추천 근거를 '왜 이 제품인가'로 이어지는 문장으로(사용자 2026-10-02: 숫자만으로는 왜 골랐는지 안 보임).
    계산 결과로만 만든다(AI 생성 아님)."""
    out: dict[str, str] = {}
    industrial = robot_type == "industrial"
    # 1) 툴측 총무게 — 무엇을 받아야 하는가
    if required is not None:
        i = max(range(len(masses)), key=lambda k: masses[k]["kg"] or 0)
        t = tools[i]
        a = _get(t, "intake.tool_assembly_mass_kg")
        wp = _get(t, "mass_components.max_simultaneous_workpieces_kg")
        what = (f"툴 {a:g}kg, 제품을 들지 않음" if t.get("workpiece_not_applicable")
                else f"툴 {a:g}kg + 한 번에 드는 제품 {wp:g}kg")
        out["툴측 총무게"] = (f"툴체인저는 툴과 그 툴이 드는 제품을 함께 받쳐야 합니다. 가장 무거운 {_short_name(t.get('name'))}({what})가 "
                         f"{required:g}kg" + (f"이고, 산업용 로봇이라 약 {INDUSTRIAL_FACTOR}배인 {screening:g}kg 기준으로 후보를 봅니다"
                                              if industrial else f"이라, {required:g}kg 이상을 받을 수 있는 모델이 필요합니다")
                         + "(T.P·어댑터 무게는 확인 전이라 잠정).")
    else:
        out["툴측 총무게"] = "툴 무게나 한 번에 드는 제품 무게를 몰라, 툴체인저가 받아야 할 무게를 정할 수 없습니다 — 그래서 아직 후보를 고르지 못합니다."
    # 2) 로봇 종류 — 어느 계열에서 찾았나
    if first and robot_type in ROBOT_KO:
        label = first["series_label"]
        out["로봇 종류"] = (f"{ROBOT_KO[robot_type]}에 다는 툴체인저라 {ROBOT_KO[robot_type]}용 계열인 {label}에서 찾았습니다(유선 기본)."
                        if first.get("primary") else
                        f"{ROBOT_KO[robot_type]}용 주 계열은 최대 정격이 {screening:g}kg에 못 미쳐, 받을 수 있는 다른 계열인 {label}에서 찾았습니다"
                        f"(표준 범위 밖이라 내부 검토)." if screening is not None else
                        f"{ROBOT_KO[robot_type]}용 계열인 {label}에서 찾았습니다.")
    elif robot_type is None:
        out["로봇 종류"] = "로봇이 협동로봇인지 산업용인지 몰라 어느 계열에서 찾을지 정하지 못했습니다."
    # 3) 정격 — 왜 이 모델인가
    if first and screening is not None:
        m = first["models"][0]
        names = " / ".join(x["name"] for x in first["models"])
        out["정격 가반하중"] = (f"이 계열에서 {screening:g}kg 이상을 받는 모델 중 가장 작은 단계가 {names}(정격 {_kg(m['payload_kg'])})라 "
                           f"{names}을 골랐습니다 — 규칙상 필요한 무게를 받는 가장 작은 단계를 고릅니다.")
        if first.get("chosen_by_user"):
            rule_note = next((n for n in first.get("notes") or [] if n.startswith("담당자 선택")), "")
            out["정격 가반하중"] = (f"담당자가 대화에서 {names}(정격 {_kg(m['payload_kg'])})을 골랐습니다 — 선정 하중 {screening:g}kg 이상을 "
                               f"받아 쓸 수 있습니다." + (f" ({rule_note.split('— ', 1)[-1]})" if "— " in rule_note else ""))
        nxt = first.get("consider_up_to") or []
        if nxt:
            out["정격 가반하중"] += (f" 다만 {'·'.join(first.get('compare_reasons') or [])}이라 상위 {' / '.join(x['name'] for x in nxt)}"
                                f"와 조건을 비교해 담당자가 최종 결정합니다.")
        elif first.get("compare_reasons"):
            out["정격 가반하중"] += f" 다만 {'·'.join(first['compare_reasons'])}인데 이 계열에 상위 모델이 없어 내부 검토가 필요합니다."
    # 4) 속도
    sp = next(((r.get("speed") or {}) for r in robots if (r.get("speed") or {}).get("value") is not None), {})
    sp_txt = f"{sp.get('value')}{sp.get('unit') or ''}" if sp else ""
    prov = f"(잠정 기준 {'·'.join(speed['provisional'])})" if speed.get("provisional") else ""
    if industrial:
        out["운전 속도"] = (f"산업용 로봇은 속도에 따라 단계를 올리는 대신 약 {INDUSTRIAL_FACTOR}배 하중으로 후보를 봅니다 — "
                        "두 방식을 겹쳐 적용하지 않습니다.")
    elif speed.get("compare_upper"):
        out["운전 속도"] = (f"운전 속도 {sp_txt}는 70%를 넘어 상위 후보도 함께 비교합니다 — 무게중심·정격 여유와 같이 보고 담당자가 "
                        f"정하며, 자동으로 올리지 않습니다{prov}.")
    elif speed.get("reason") == "needs_information":
        out["운전 속도"] = "운전 속도를 몰라 상위 후보와 비교할지 정하지 못했습니다 — 속도 설정 화면 확인이 필요합니다."
    elif speed.get("reason"):
        out["운전 속도"] = (f"운전 속도 {sp_txt}의 단위·기준이 정해지지 않아(퍼센트가 아님 등) 상위 후보 비교 여부는 내부 엔지니어가 검토합니다."
                        if sp_txt else "운전 속도 기준이 정해지지 않아 상위 후보 비교 여부는 내부 엔지니어가 검토합니다.")
    else:
        model = " / ".join(x["name"] for x in first["models"]) if first else ""
        out["운전 속도"] = f"운전 속도 {sp_txt}는 협동로봇 기준 70% 이하라 기본 후보를 그대로 둡니다" + (f" — {model}" if model else "") + f"{prov}."
    # 5) 포고핀(v1.2)
    if pogo_needed:
        parts = [f"{_short_name(pt['tool'])}는 {pt['pins']}핀({_pin_formula(pt)})" for pt in pogo_tools if pt["pins"] is not None]
        need = [f"{_short_name(pt['tool'])}의 {'·'.join(pt['missing'])}" for pt in pogo_tools if pt["pins"] is None]
        if pogo_modules is not None:
            ppf = [x for x in (accessory or {}).get("tool_layouts") or [] if x["PPF"]]
            txt = (f"{', '.join(parts)}이 필요합니다. 핀 1개가 1A라 전원은 정격·피크 중 큰 전류로 공급·리턴을 각각 세고, 8핀 모듈에 담습니다. "
                   f"교체 툴 전체를 받는 마스터 공통 배치로 PPM {pogo_modules}개"
                   + (f", 툴측은 {', '.join(f'{_short_name(x['tool'])} PPF {x['PPF']}개' for x in ppf)}" if ppf else "")
                   + "입니다(배치는 논리 배정 — 핀맵·병렬 전원은 엔지니어 확인).")
        else:
            txt = f"포고핀 수를 정하려면 {', '.join(need)}를 알아야 합니다." + (f" 지금 알 수 있는 것: {', '.join(parts)}." if parts else "")
        cond = [a for pt in pogo_tools for a in pt.get("assumptions") or []]
        if cond:
            txt += " 조건부: " + "; ".join(dict.fromkeys(cond)) + "."
        out["포고핀"] = txt
    else:
        out["포고핀"] = ("툴이 전기·센서를 쓰는지 몰라 포고핀이 필요한지 정하지 못했습니다." if pogo_unknown or not tools
                       else "툴이 전기·센서를 쓰지 않아 포고핀 모듈이 필요 없습니다.")
    # 6) 공압(v1.2)
    if pneu_needed:
        if pneu_modules is not None:
            parts = [f"{_short_name(x['tool'])}는 {_air_why(x)} 유로 {x['paths']}개라 {x['modules']}개" for x in pneu_tools]
            out["공압"] = (f"에어커플러(PMM·PMF) 한 쌍이 독립 유로 {PATHS_PER_COUPLER}개를 잇습니다. {', '.join(parts)}가 필요해, "
                         f"마스터에는 에어커플러 {pneu_modules}개를 둡니다(진공도 같은 방식, 압력·유량은 별도 확인).")
        else:
            need = [f"{_short_name(x['tool'])}의 {'·'.join(x['missing'])}" for x in pneu_tools if x["missing"]]
            sc = [f"{_short_name(x['tool'])}: " + " / ".join(f"{s['label']} {s['paths']}유로" for s in x["scenarios"])
                  for x in pneu_tools if x["scenarios"]]
            out["공압"] = (f"공압을 쓰지만 {', '.join(need) or '독립 유로 수'}를 몰라 에어커플러 수는 확인 후 정합니다."
                         + (f" 경우별로는 {'; '.join(sc)}." if sc else ""))
        cond = [a for x in pneu_tools for a in x.get("assumptions") or []]
        if cond:
            out["공압"] += " 조건부: " + "; ".join(dict.fromkeys(cond)) + "."
    else:
        out["공압"] = ("툴이 공압을 쓰는지 몰라 에어커플러가 필요한지 정하지 못했습니다." if pneu_unknown or not tools
                     else "툴이 공압을 쓰지 않아 에어커플러가 필요 없습니다.")
    return out


_IFACE_KO = {"digital_IO": "I/O", "RS485": "RS485", "CAN": "CAN", "other": "기타 통신", "none": "신호 없음"}
_AIR_KO = {"AIR_DOUBLE_ROBOT_VALVE": "로봇측 밸브 복동", "AIR_SINGLE_ROBOT_VALVE": "로봇측 밸브 단동",
           "AIR_TOOL_VALVE_SINGLE_SUPPLY": "툴측 밸브(공급 1선)", "AIR_APPROVED_SHARED_BRANCH": "공통 A/B 분기",
           "AIR_VACUUM_OTHER": "진공·특수", "AIR_UNKNOWN_LAYOUT": "밸브 위치 미확인", "confirmed_paths": "확인된 통과 유로"}


def _pin_input(pt: dict[str, Any]) -> str:
    sig = {"digital_IO": f"I/O DI {pt['di'] if pt['di'] is not None else '?'}·DO {pt['do'] if pt['do'] is not None else '?'}",
           "RS485": f"RS485 {pt['rs485_wires'] or '?'}선"}.get(pt["interface"], _IFACE_KO.get(pt["interface"], "제어 방식 ?"))
    cur = f", {pt['current_basis']}" if pt.get("current_basis") else ""
    return (f"{pt['tool']}: 전원 {pt['circuits'] if pt['circuits'] is not None else '?'}회로{cur}, {sig}"
            + (" (사양 글에서 읽음)" if pt.get("from_text") else ""))


def _air_input(x: dict[str, Any]) -> str:
    bits = [_AIR_KO.get(x.get("profile"), "회로 미확인")]
    if x.get("gripper_count"):
        bits.append(f"그리퍼 {x['gripper_count']}개")
    return f"{x['tool']}: " + ", ".join(bits) + (f" → 유로 {x['paths']}" if x.get("paths") is not None else "")


def _air_why(x: dict[str, Any]) -> str:
    return _AIR_KO.get(x.get("profile"), "") + (f" 그리퍼 {x['gripper_count']}개" if x.get("gripper_count") and x.get("profile") != "confirmed_paths" else "")


def _pin_formula(pt: dict[str, Any]) -> str:
    """'전원 1회로×(공급 2+리턴 2) + 신호 2' — 병렬이면 경로당 핀 수가 보인다."""
    k = pt.get("pins_per_path") or 0
    pw = f"전원 {pt['circuits']}회로×(공급 {k}+리턴 {k})" if pt["circuits"] else "전원 없음"
    return f"{pw} + 신호 {pt['signal_pins']}" + (f" + 공통선 {pt['extra_pins']}" if pt.get("extra_pins") else "")


def pogo_text(pogo_tools: list[dict[str, Any]], master: dict[str, Any] | None = None) -> str:
    """'PPM 1개(마스터 공통 배치, 모듈당 8핀) — 전동 그리퍼: 6핀 = …' 식. 모르면 무엇을 알아야 하는지."""
    parts, need = [], []
    for pt in pogo_tools:
        if pt["pins"] is None:
            need.append(f"{pt['tool']} {'·'.join(pt['missing'])}")
            continue
        parts.append(f"{pt['tool']}: {pt['pins']}핀 = {_pin_formula(pt)}" + (" (조건부)" if pt["status"] == "conditional" else ""))
    ppm = (master or {}).get("ppm_per_master")
    if not need and ppm is not None:
        return f"PPM {ppm}개(마스터 공통 배치, 모듈당 8핀) — " + " / ".join(parts)
    lb = (master or {}).get("electrical_lower_bound")
    return ("필요 — 수량은 " + ", ".join(need) + " 확인 후(모듈당 8핀)" + (f" · 확인된 툴 하한 {lb}개" if lb else "")
            + (f" · 확인된 것: {' / '.join(parts)}" if parts else ""))


def air_text(pneu_tools: list[dict[str, Any]], master: dict[str, Any] | None = None) -> str:
    pmm = (master or {}).get("pmm_per_master")
    if pmm is not None and all(x["paths"] is not None for x in pneu_tools):
        return (f"에어커플러 PMM {pmm}개 — " + " / ".join(f"{x['tool']}: 유로 {x['paths']}개 → {x['modules']}개" for x in pneu_tools)
                + f" (커플러 1쌍 = 독립 유로 {PATHS_PER_COUPLER}개)")
    need = [f"{x['tool']} {'·'.join(x['missing'])}" for x in pneu_tools if x["missing"]]
    return "에어커플러 필요 — 수량은 " + (", ".join(need) or "툴별 독립 유로 수") + " 확인 후"


def junior_summary(first: dict[str, Any] | None, tp: dict[str, Any], pogo_needed: bool, pneu_needed: bool,
                   required: float | None, lower: float | None, speed: dict[str, Any],
                   robots: list[dict[str, Any]], pogo_tools: list[dict[str, Any]] | None = None, *,
                   pogo_unknown: bool = False, pneu_unknown: bool = False, pneu_modules: int | None = None,
                   catalog_ok: bool = False, screening: float | None = None,
                   accessory: dict[str, Any] | None = None) -> dict[str, Any]:
    """영업 화면용 결과(질문지 '영업 화면에 보여줄 결과' 표). 문장은 규칙 결과로만 만든다(AI 생성 아님)."""
    names = " / ".join(m["name"] for m in first["models"]) if first else None
    if first and screening is not None:
        load = (f"툴측 총무게 약 {required:g}kg × 산업용 {INDUSTRIAL_FACTOR}배 = {screening:g}kg(잠정)" if screening != required
                else f"툴측 총무게 약 {required:g}kg(잠정)")
        why = (f"{load} 이상을 받는 {first['series_label']} 중 담당자가 대화에서 고른 모델입니다." if first.get("chosen_by_user")
               else f"{load} 이상을 받는 {first['series_label']} 중 가장 작은 단계입니다.")
        if first.get("consider_up_to"):
            why += (f" {'·'.join(first.get('compare_reasons') or [])}이라 {' / '.join(m['name'] for m in first['consider_up_to'])}"
                    " 와 비교해 담당자가 확정합니다.")
    elif lower is not None:
        why = f"툴측 총무게를 일부만 알아(최소 {lower:g}kg) 아직 후보를 고를 수 없습니다. 빠진 무게를 확인해 주세요."
    else:
        why = "툴 무게와 한 번에 드는 제품 무게를 알아야 하중 기준 후보를 고를 수 있습니다."
    m4, m1 = CABLE_STD["master_to_controlbox"]["length_m"], CABLE_STD["controlbox_to_robot_cabinet_or_plc"]["length_m"]
    ctrl = f"마스터당 CONTROLBOX 1 · {m4:g}m(마스터 전원·제어 전용) 1 · {m1:g}m 1"
    if any((r.get("cables") or {}).get(k) is False for r in robots for k in ("master_4m_sufficient", "system_1m_sufficient")):
        ctrl += " — 부족 구간 길이 변경은 전압 강하 검토·고객 협의"
    return {
        "candidate_model": names,
        "candidate_series": first["series_label"] if first else None,
        "reason_in_plain_korean": why,
        "tool_plate_quantity": tp["operating"],
        "tool_plate_spare": tp["spare"],
        "pogo_modules": (pogo_text(pogo_tools or [], (accessory or {}).get("master")) if pogo_needed else
                         "미정 — 툴의 전기·센서 사용 여부 확인 후" if pogo_unknown else "필요 없음(전기·센서 없음)"),
        "pneumatic_modules": (air_text((accessory or {}).get("air_tools") or [], (accessory or {}).get("master")) if pneu_needed else
                              "미정 — 툴의 공압 사용 여부 확인 후" if pneu_unknown else "필요 없음(공압 없음)"),
        "accessory_status": (accessory or {}).get("status_ko"),
        "controller_and_cables": ctrl,
        "evidence_status": "제품 DB 공식 사양 모델" if catalog_ok else "참고 후보(사양 확인 필요)",
    }


def intake_summary(intake: dict[str, Any]) -> str:
    """대화창·기록용 한 덩어리 요약(사람이 읽는 문장)."""
    p = intake.get("project") or {}
    lines = []
    for r in intake.get("robots") or []:
        sp = r.get("speed") or {}
        lines.append(f"로봇: {r.get('manufacturer') or '?'} {r.get('model') or ''} ({ {'cobot': '협동', 'industrial': '산업용'}.get(r.get('type'), '종류 모름') }) "
                     f"{r.get('quantity') or '?'}대, 속도 {sp.get('value') if sp.get('value') is not None else '?'}{sp.get('unit') or ''}")
    proc = p.get("process") or {}
    lines.append(f"작업: {proc.get('category') or '?'} — {proc.get('description') or ''}")
    lines.append(f"교체 툴 수: {p.get('tool_count') if p.get('tool_count') is not None else '?'}")
    for t in intake.get("tools") or []:
        it = t.get("intake") or {}
        d = it.get("dimensions_mm") or {}
        wp = "해당 없음" if t.get("workpiece_not_applicable") else (t.get("mass_components") or {}).get("max_simultaneous_workpieces_kg")
        lines.append(f"툴 {t.get('name') or ''}({t.get('model') or ''}) ×{t.get('installed_quantity') or '?'}: "
                     f"{d.get('x') or '?'}×{d.get('y') or '?'}×{d.get('z') or '?'}mm, 툴 {it.get('tool_assembly_mass_kg') or '?'}kg, "
                     f"제품 {wp if wp is not None else '?'}kg, 기울임 {it.get('tilts_or_flips')}, 전기 {it.get('needs_power')}, "
                     f"공압 {it.get('needs_air')}, 센서 {it.get('has_sensors_or_signals')}")
    env = p.get("environment") or {}
    lines.append(f"환경: {', '.join(env.get('tags') or []) or ('없음' if env.get('has_special_conditions') is False else '?')}")
    return "\n".join(lines)


# ── ATC 구성품(C14 공통·가변 구성) — 사용자 2026-10-02: 결과에 액세서리도 나와야 함 ─────────────────────
# 고정 판매 세트 없음(C14). 공통: 마스터당 MASTER T.C·CONTROLBOX·4m·1m 각 1. 가변: T.P(실제 교체 툴)·PPM/PPF·PMM/PMF·
# 포고핀 모듈마다 30cm 케이블(C16)·로봇측 어댑터(견적 후 판매, C18). 툴스탠드(C19)·툴측 어댑터(고객 제작, C20)는 자동 포함 안 함.
# 품번·판매단가는 판매단가표 연결 전이라 만들지 않는다(C13·O04). 액세서리 카드 적용 범위 '유선 ATC TCC1 ~ TCV4'.
ACC_WIRED_AUTO = ("TCC1", "TCV1", "TCV2", "TCV3", "TCV4")


def accessory_bom(out: dict[str, Any], intake: dict[str, Any], cards: list[dict[str, Any]]) -> dict[str, Any]:
    """판정 결과 + 액세서리 카드({card_id, name, code}) → {applicable, items[{code,name,card_id,side,qty,basis}], notes}."""
    first = next((c for c in out.get("screening_candidates") or [] if c["models"] and not c.get("out_of_range")), None)
    res: dict[str, Any] = {"applicable": False, "items": [], "notes": []}
    if not first:
        res["notes"].append("후보가 정해지지 않아 구성품도 정할 수 없습니다.")
        return res
    model = first["models"][0]["name"]
    by = {c["code"]: c for c in cards if c.get("code")}
    tools = intake.get("tools") or []
    robots = intake.get("robots") or []
    n_master = sum(int(r.get("quantity") or 1) for r in robots) or 1
    qty_of = {(t.get("name") or "툴"): t.get("installed_quantity") for t in tools}
    tp = out.get("tool_plates") or {}

    def card(code: str) -> dict[str, Any]:
        c = by.get(code) or {}
        return {"code": code, "name": c.get("name") or code, "card_id": c.get("card_id")}

    def add(name: str, side: str, qty: int | None, basis: str, code: str | None = None, *,
            price_item: str | None = None, optional: bool = False, remark: str = "", conditional: bool = False) -> None:
        """price_item: 단가표 품목(product_prices.item) — 견적 단가를 붙일 때 (모델, 품목)으로 찾는다.
        optional: 단가표에는 있지만 견적에 넣을지는 건별로 정하는 품목(사용자 2026-10-06: CONTROLBOX·4m·IB)."""
        res["items"].append({**(card(code) if code else {"code": None, "name": name, "card_id": None}),
                             "side": side, "qty": qty, "basis": basis, "price_model": model if price_item else None,
                             "price_item": price_item, "optional": optional, "remark": remark, "conditional": conditional})

    def tool_sum(rows: list[dict[str, Any]]) -> int | None:
        total = 0
        for r in rows:
            q = qty_of.get(r["tool"])
            if r["modules"] is None or q is None:
                return None
            total += r["modules"] * int(q)
        return total

    applicable = first["series"] == "auto" and model in ACC_WIRED_AUTO
    res["applicable"] = applicable
    # 공통 구성(C14·C15) — 마스터 수 기준, T.P 수와 무관
    # 유선 ATC 마스터에는 CONTROLBOX·4m 케이블(C14·C15)이 기본 포함 — 견적에 따로 적지 않는다(사용자 2026-10-06, 회사 확인).
    # 1m 케이블은 단가표에 없어(제공 안 함) 견적 품목이 아니다.
    m4 = CABLE_STD["master_to_controlbox"]["length_m"]
    add(f"{model} MASTER T.C", "로봇측", n_master, f"툴체인저를 다는 로봇 {n_master}대 × 1", price_item="MASTER T.C",
        remark=f"CONTROLBOX·{m4:g}m 케이블 기본 포함" if applicable else "")
    if applicable:
        # IB = 로봇 플랜지와 툴체인저 마스터 사이 브라켓, 별도 옵션(사용자 2026-10-06) — v0.8 C18 '로봇측 어댑터' 자리
        add("IB (Interface Bracket)", "로봇측", n_master,
            f"로봇 플랜지–마스터 브라켓, 마스터 {n_master}개 × 1 — 별도 옵션, 고객 요청 시. 표준 IB 가 맞지 않는 로봇은 커스텀 견적(C18)",
            price_item="IB", optional=True)
    plates = tp.get("operating")
    add(f"{model} T.P", "툴측", plates,
        (f"실제 교체 툴 {plates}개 × 1" if plates is not None else "툴 수량 확인 필요")
        + (f" (예비 {tp['spare']}개 별도)" if tp.get("spare") else " (추가·예비는 고객 요청 시)"), price_item="T.P")
    if not applicable:
        res["notes"].append(f"제품 DB 의 ATC 액세서리·CONTROLBOX 구성은 유선 자동 툴체인저 TCC1~TCV4 용입니다 — "
                            f"{model}({first['series_label']})용 구성은 정보가 없어 내부 확인이 필요합니다.")
    else:
        # v1.2 통합 수량표(PGR15·PGR16) — 마스터측·툴측 분리, 케이블은 PPM·PPF 마다 1개. 조건부면 표시하고 견적 발행 전 확인(PGR24)
        a = out.get("accessory") or {}
        bom, ms = a.get("bom") or {}, a.get("master") or {}
        cond = a.get("status") in ("conditional_draft", "needs_data")
        flag = " — 조건부(담당자 확인 후 확정)" if a.get("status") == "conditional_draft" else ""
        lay = [x for x in a.get("tool_layouts") or []]
        if out.get("pogo_needed"):
            m = ms.get("ppm_per_master")
            add("", "로봇측(Master)", bom.get("PPM"),
                (f"마스터 공통 배치 PPM {m}개 × 마스터 {n_master}개(PGR12·PGR15)" if m is not None
                 else "포고핀 산정 값 확인 후(전원·제어 방식·신호선)") + flag, "PPM", price_item="PPM/PPF", conditional=cond)
            rows = [x for x in lay if x["PPF"]]
            add("", "툴측(Tool Plate)", bom.get("PPF"),
                (" + ".join(f"{_short_tool(x['tool'])} {x['PPF']}개×{x['quantity']}" for x in rows) + " — 툴마다 쓰는 모듈만(PGR14·PGR15)"
                 if bom.get("PPF") is not None else "툴별 포고핀 배치 확인 후") + flag, "PPF", price_item="PPM/PPF", conditional=cond)
            c1, c2 = bom.get("PPM_30cm_cable"), bom.get("PPF_30cm_cable")
            add(f"Cable {POGO_CABLE_CM / 100:g}m", "포고핀", c1 + c2 if c1 is not None and c2 is not None else None,
                f"PPM용 {c1 if c1 is not None else '?'} + PPF용 {c2 if c2 is not None else '?'} — PPM·PPF 1개당 1EA(PGR16). "
                "PPM 이후 제어반 연장·PPF 이후 그리퍼 결선은 고객(C17)" + flag, price_item="Cable 0.3m", conditional=cond)
        if out.get("pneumatic_needed"):
            mm = ms.get("pmm_per_master")
            add("", "로봇측(Master)", bom.get("PMM"),
                (f"마스터 공통 배치 PMM {mm}개 × 마스터 {n_master}개 — 유로가 가장 많은 툴 기준(PGR15·E20)" if mm is not None
                 else "툴별 공압 회로(동작 방식·밸브 위치·그리퍼 수) 확인 후") + flag, "PMM", price_item="PMM/PMF", conditional=cond)
            rows = [x for x in lay if x["PMF"]]
            add("", "툴측(Tool Plate)", bom.get("PMF"),
                (" + ".join(f"{_short_tool(x['tool'])} {x['PMF']}개×{x['quantity']}" for x in rows) + " — 툴마다 쓰는 모듈만(PGR15)"
                 if bom.get("PMF") is not None else "툴별 공압 회로 확인 후") + flag, "PMF", price_item="PMM/PMF", conditional=cond)
        slots = out.get("slot_check") or {}
        if slots.get("slot_sum") is not None:
            res["notes"].append(f"마스터 장착 위치 {slots['slot_sum']}개소 / 표준 {STD_POSITIONS}개소(PGR13)"
                                + (" — 초과, 커스텀 필요" if slots.get("customization_required")
                                   else " — 이내여도 모든 툴의 배치·핀 배열·호환은 확인"))
        if out.get("pogo_needed"):
            res["notes"].append(QUOTE_SCOPE_TEXT)
        if (out.get("accessory") or {}).get("status") in ("conditional_draft", "needs_data"):
            res["notes"].append("액세서리 수량은 조건부·미확정 — 확인되지 않은 값은 견적 발행 전에 담당자가 확정합니다(PGR24).")
    res["notes"].append("T.P–그리퍼 사이 툴측 어댑터는 고객 제작 범위(UND 견적 미포함) — 무게·돌출 길이는 툴측 총무게·형상 검토에 반영(C20).")
    res["notes"].append(f"툴스탠드는 자동 포함하지 않음 — 표준 {TOOL_STAND['model']}({TOOL_STAND['name_ko']})·커스텀 주문제작·"
                        "고객 제작 중 고객 선택, 수량은 거치 자리·배치로 확정(C19).")
    if applicable:
        res["notes"].append("CONTROLBOX·4m 케이블은 MASTER T.C 기본 포함이라 따로 적지 않음. IB 는 별도 옵션(견적서에서 넣음). "
                            "1m 케이블(CONTROLBOX→제어반)은 단가표 미제공이라 견적 품목 아님.")
    res["notes"].append("고정 판매 세트 없음 — 단가는 회사 단가표(고객사가, 국내) 기준이며, 최종 견적은 담당자가 모든 값을 확정한 뒤 발행(C13·C14).")
    return res


def refresh_accessory(out: dict[str, Any], intake: dict[str, Any]) -> dict[str, Any]:
    """저장된 판정 결과의 액세서리 부분만 지금 규칙(v1.2)으로 다시 계산한다 — 고른 모델·후보는 그대로.
    예전 기록(accessory 칸 없음)을 다시 열거나 견적을 만들 때 구성품 수량이 '미정'으로 바뀌지 않게."""
    tools, robots = intake.get("tools") or [], intake.get("robots") or []
    pogo = any(acc.needs_electrical(t) for t in tools)
    air = any(_get(t, "intake.needs_air") is True for t in tools)
    n_master = sum(int(r.get("quantity") or 1) for r in robots) or 1
    a = acc.evaluate(tools, n_master, pogo_needed=pogo, air_needed=air)
    m = a["master"]
    return {**out, "accessory": a, "pogo_needed": pogo, "pneumatic_needed": air,
            "pogo_tools": a["electrical_tools"], "pneumatic_tools": a["air_tools"],
            "pogo_modules": m["ppm_per_master"] if pogo else 0, "pneumatic_modules": m["pmm_per_master"] if air else 0,
            "slot_check": {"slot_sum": m["positions"], "customization_required": m["customization_required"],
                           "standard_layout_possible": None if m["positions"] is None else not m["customization_required"]}}


def _short_tool(name: str) -> str:
    return re.split(r"\s*\(", name or "툴")[0].strip() or "툴"
