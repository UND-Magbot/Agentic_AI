"""맥봇 ATC 액세서리 통합 선정(v1.2) — 포고핀(PPM·PPF)·에어커플러(PMM·PMF)·30cm 케이블 수량.

원본: docs/sources/UND_MAGBOT_ATC_액세서리_통합선정규칙_v1.2.json → data/atc_accessory_v1.2.json(사용자 2026-10-08: 최종 규칙으로
전체 적용). 숫자 상수는 JSON 에서 읽는다. 규칙 번호 PGR01~PGR24 를 계산 근거(rule)로 남긴다.

흐름(unified_selection_workflow): 툴 사용 조건 → 결합면 통과 요구(툴별 고유 전기 경로·독립 유로) → 하한(ceil(핀/8)·ceil(유로/2))
→ 공통 마스터 배치와 3개소 검토 → 통합 수량표 → (담당자·엔지니어 확인 후) 견적.

원칙:
- 모르는 값은 None — 0 으로 바꾸지 않는다(unknown_value_representation). 계산은 이 코드가, 사실 추출·질문은 AI·담당자가.
- 확인 안 된 조건은 '가정'으로 따로 두고 조건부 초안(conditional_draft)으로 낸다. 조건부 결과를 최종 견적으로 자동 확정하지 않는다(PGR24).
- 엔지니어 승인(engineering_confirmed)은 자동으로 주지 않는다 — 최선이 engineering_review.
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any
import json

SPEC_PATH = Path(__file__).with_name("data") / "atc_accessory_v1.2.json"
SPEC: dict[str, Any] = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
REVISION = SPEC["version"]
_K = SPEC["company_confirmed_constants"]
PINS_PER_MODULE: int = _K["pins_per_pogo_module"]              # PPM·PPF 한 쌍 = 8 통과 경로
PIN_CURRENT_A: float = _K["current_per_pin_A"]                 # 핀당 1A, 병렬 산정 허용
STD_POSITIONS: int = _K["shared_mounting_positions"]           # 포고핀·공압 공용 3개소
PATHS_PER_COUPLER: int = _K["air_paths_per_coupler_pair"]      # PMM·PMF 한 쌍 = 독립 유로 2
STD_BAR: tuple[float, float] = tuple(_K["positive_pressure_standard_bar"])   # 양압 1~5bar
CABLE_CM: int = _K["pogo_cable_length_cm"]
CABLES_PER_PPM: int = _K["cables_per_PPM"]
CABLES_PER_PPF: int = _K["cables_per_PPF"]
COMM = {p["interface"]: p for p in SPEC["communication_profiles"]}
AIR_PROFILES = {p["id"]: p for p in SPEC["pneumatic_profiles"]}
UNIT_TO_BAR = {"bar": 1.0, "mpa": 10.0, "kpa": 0.01}             # pneumatic_pressure_policy.normalization_to_bar
JUNIOR_QUESTIONS: list[str] = SPEC["junior_questions"]

STATUS_KO = {"needs_data": "자료 필요", "conditional_draft": "조건부 초안", "engineering_review": "엔지니어 검토",
             "engineering_confirmed": "엔지니어 확정"}
_STATUS_ORDER = ("needs_data", "conditional_draft", "engineering_review")

# 화면·질문에 쓰는 선택지(값은 계산 키, 글은 주니어 질문 문구 기준)
INTERFACES = [("digital_IO", "I/O 신호(DI·DO)"), ("RS485", "RS485·Modbus RTU"), ("CAN", "CAN"),
              ("other", "기타 통신(EtherCAT·TCP/IP·PROFINET·USB 등)")]
ACTUATIONS = [("double_acting", "복동(공기로 열고 닫음)"), ("single_acting", "단동(한쪽은 스프링)"),
              ("integrated_valve", "밸브 내장 그리퍼"), ("vacuum_other", "진공·블로우·특수")]
VALVE_LOCATIONS = [("robot_side", "로봇측(ATC 앞)"), ("tool_side", "툴측(교체하는 그리퍼 쪽)")]
CONTROLS = [("independent", "그리퍼마다 따로 제어"), ("approved_shared", "공통 배관으로 같이 움직임(구성 확정)")]


def _ceil(x: float) -> int:
    return math.ceil(round(x, 6))


def to_bar(value: float | None, unit: str | None) -> float | None:
    """PGR23 — MPa×10, kPa÷100 → bar. 단위를 모르면 None(묻는다)."""
    if value is None:
        return None
    f = UNIT_TO_BAR.get(str(unit or "bar").strip().lower())
    return None if f is None else round(float(value) * f, 4)


def pins_for(current_A: float) -> int:
    """PGR04 — 한 경로(공급 또는 리턴)의 접점 수 = max(1, ceil(I/1A)). 1.5A → 2, 3A → 3."""
    return max(1, _ceil(current_A / PIN_CURRENT_A))


# ── 전원 사양 읽기 ──────────────────────────────────────────────────────────────────

_AMP_RE = re.compile(r"(피크|기동|돌입|최대|정격)?\s*[:은는]?\s*(\d+(?:\.\d+)?)\s*(mA|A)(?![a-zA-Z])")
_VOLT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*V(?![a-zA-Z])")


def power_spec(tool: dict[str, Any]) -> dict[str, Any]:
    """툴 전원 — 회로 수·전압·정격/피크/기동 전류(A). 숫자 칸(E01)이 우선, 없으면 F02 사양 글(예: '24V 0.8A, 피크 1.5A')에서.
    글에서 읽은 값은 from_text. 회로 수를 따로 적지 않았으면 적힌 전원 사양 1건 = 1회로."""
    el = tool.get("electrical") or {}
    circuits, rated, peak, inrush, volt = (el.get(k) for k in ("power_circuit_count", "current_A", "peak_A", "inrush_A", "voltage_V"))
    texts = [str(x) for x in (el.get("power_circuits") or []) if str(x).strip()]
    from_text = False
    if texts:
        joined = " ".join(texts)
        amps = [(lab, float(v) / (1000 if u == "mA" else 1)) for lab, v, u in _AMP_RE.findall(joined)]
        if rated is None:
            c = [v for lab, v in amps if lab in ("", "정격")]
            rated = max(c) if c else None
            from_text = from_text or rated is not None
        if peak is None:
            pk = [v for lab, v in amps if lab in ("피크", "최대")]
            peak = max(pk) if pk else None
        if inrush is None:
            ir = [v for lab, v in amps if lab in ("기동", "돌입")]
            inrush = max(ir) if ir else None
        if volt is None:
            mv = _VOLT_RE.search(joined)
            volt = float(mv.group(1)) if mv else None
        if circuits is None and amps:
            m = re.search(r"(\d+)\s*회로", joined)
            circuits = int(m.group(1)) if m else len(texts)
            from_text = True
    # 구동 전원도 센서도 없을 때만 0 회로 — 센서만 있어도 센서 전원선은 포고핀을 쓴다(PGR22)
    if _get(tool, "intake.needs_power") is False and _get(tool, "intake.has_sensors_or_signals") is False             and _air_valve(tool) != "tool_side":
        circuits = 0 if circuits is None else circuits
    return {"circuits": circuits, "voltage_V": volt, "rated_A": rated, "peak_A": peak, "inrush_A": inrush,
            "from_text": from_text}


def _get(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


# ── 통신 방식(PGR06·PGR19) ─────────────────────────────────────────────────────────

_IFACE_PATTERNS = [
    ("RS485", re.compile(r"485|modbus", re.I)),
    ("CAN", re.compile(r"(?<![a-z])can(?![a-z])", re.I)),
    ("other", re.compile(r"ethercat|ether|tcp|profinet|usb|io-?link|rs-?232|rs-?422|devicenet|cc-?link", re.I)),
    ("digital_IO", re.compile(r"dio|i/o|(?<![a-z])io(?![a-z])|디지털|접점|(?<![a-z])d[io](?![a-z])", re.I)),
]


def interface(tool: dict[str, Any]) -> tuple[str | None, bool]:
    """(실제 ATC 구간 통신, 글에서 추정했는지). 선택값(comm_interface)이 우선. 지원 목록을 동시 사용으로 보지 않는다(PGR01) —
    글에 여러 방식이 있으면 맨 앞 규칙 순서(RS485 > CAN > 기타 > I/O)로 하나만 고르고 추정으로 둔다."""
    el = tool.get("electrical") or {}
    v = el.get("comm_interface")
    if v in COMM or v in ("RS485", "other"):
        return v, False
    raw = str(el.get("control_method_raw") or "")
    for key, rx in _IFACE_PATTERNS:
        if rx.search(raw):
            return key, True
    if _get(el, "dio.di") is not None or _get(el, "dio.do") is not None:
        return "digital_IO", True
    return None, False


def rs485_wires(tool: dict[str, Any]) -> int | None:
    el = tool.get("electrical") or {}
    w = el.get("rs485_wires")
    if w in (2, 4):
        return w
    raw = str(el.get("control_method_raw") or "")
    if re.search(r"4\s*선|4-?wire|full\s*duplex|tx\+|rx\+", raw, re.I):
        return 4
    if re.search(r"2\s*선|2-?wire|half\s*duplex", raw, re.I):
        return 2
    return None


# ── 툴 하나의 전기 요구(PGR02~PGR08·PGR19·PGR22) ────────────────────────────────────────

def _net(key: str, label: str, role: str, contacts: int) -> dict[str, Any]:
    return {"key": key, "label": label, "role": role, "contacts": contacts}


def tool_electrical(tool: dict[str, Any]) -> dict[str, Any]:
    """툴 하나가 ATC 결합면으로 넘기는 고유 전기 경로와 포고핀 하한.
    status: confirmed(값 확정) | conditional(가정 포함) | needs_data(계산에 필요한 값 없음 — 합계 None)."""
    nm = tool.get("name") or "툴"
    el = tool.get("electrical") or {}
    ps = power_spec(tool)
    missing: list[str] = []
    assumptions: list[str] = []
    questions: list[str] = []
    rules = ["PGR02", "PGR03", "PGR04", "PGR08"]

    # 전원 — I_design = max(문서로 확보한 정격·피크·기동), 공급·리턴 각각 ceil(I/1A)(PGR03·PGR04)
    circuits = ps["circuits"]
    known = [(lab, v) for lab, v in (("정격", ps["rated_A"]), ("피크", ps["peak_A"]), ("기동", ps["inrush_A"])) if v is not None]
    design = max(v for _, v in known) if known else None
    basis = " · ".join(f"{lab} {v:g}A" for lab, v in known)
    power_nets: list[dict[str, Any]] | None = []
    if circuits is None:
        missing.append("전원 회로 수")
        power_nets = None
    elif circuits > 0 and design is None:
        missing.append("전원 전류(정격·피크)")
        power_nets = None
    elif circuits > 0:
        if ps["peak_A"] is None and ps["inrush_A"] is None:
            assumptions.append(f"피크·기동 전류 미확인 — 정격 {design:g}A 기준 하한(최대 전류 확인 필요, PGR03)")
            questions.append(f"{nm}: 피크(최대)·기동 전류는 몇 A인가요? 사양서 표의 Peak current 를 알려 주세요.")
        v = ps["voltage_V"]
        if v is None:                                # 맥봇 ATC 기본 전원 24V 로 가정 — 다른 전압이면 공통 배치에서 따로 잡아야 함
            v = 24.0
            assumptions.append("전원 전압 미확인 — 24V 로 가정(다른 전압이면 별도 전원 경로, PGR04)")
            questions.append(f"{nm}: 전원 전압은 몇 V인가요?")
        per = pins_for(design)
        for i in range(circuits):
            sfx = "" if i == 0 else f"#{i + 1}"
            power_nets.append(_net(f"supply:{v:g}{sfx}", f"+{v:g}V{sfx}", "supply", per))
            power_nets.append(_net(f"return:{v:g}{sfx}", f"0V{sfx}", "return", per))

    # 신호 — 실제로 연결하는 선만(PGR06·PGR19). 신호·센서가 없다고 답했으면 0
    iface, inferred = interface(tool)
    di, do = _get(el, "dio.di"), _get(el, "dio.do")
    sig_nets: list[dict[str, Any]] | None = []
    scenarios: list[dict[str, Any]] = []
    wires = None
    if _get(tool, "intake.has_sensors_or_signals") is False and iface is None:
        iface = "none"
    elif iface is None:
        missing.append("제어 방식(I/O·RS485 등)")
        sig_nets = None
    elif iface == "digital_IO":
        if di is None or do is None:
            missing.append("I/O 신호 수(DI·DO)")
            sig_nets = None
        rules.append("PGR06")
    elif iface == "RS485":
        rules.append("PGR19")
        wires = rs485_wires(tool)
        if wires is None:
            assumptions.append("RS485 선식 미확인 — 2선식(A·B) 가정 계산, 4선식이면 2핀 추가(PGR19)")
            questions.append(f"{nm}: RS485 는 2선식(A·B)인가요, 4선식(TX±·RX±)인가요? 결선도·선 색상표가 있으면 받아 주세요.")
        prof = COMM["RS485_4wire" if wires == 4 else "RS485_2wire"]
    elif iface == "CAN":
        prof = COMM["CAN"]
    else:                                            # 기타 통신 — 프로토콜 이름만으로 핀 수를 고정하지 않는다
        missing.append("실제 통신 구간의 물리 인터페이스·선 수(결선도)")
        sig_nets = None
    if sig_nets is not None and iface in ("RS485", "CAN"):
        sig_nets += [_net(f"comm:{lab}", lab if iface != "RS485" else f"RS485_{lab}" if len(lab) == 1 else lab, "comm", 1)
                     for lab in prof["labels"]]
    if sig_nets is not None and iface in ("digital_IO", "RS485", "CAN"):
        # I/O 툴은 DI·DO 전부, 통신 툴은 별도 하드웨어 I/O 를 쓴다고 한 만큼만(레지스터로 읽는 피드백은 추가 안 함)
        sig_nets += [_net(f"do:{i}", f"DO{i}", "do", 1) for i in range(1, int(do or 0) + 1)]
        sig_nets += [_net(f"di:{i}", f"DI{i}", "di", 1) for i in range(1, int(di or 0) + 1)]

    # 공통선·실드·접지 — 따로 필요한 경로만(PGR07). 모르면 0 으로 가정하고 묻는다
    extra = el.get("extra_pins")
    extra_nets: list[dict[str, Any]] = []
    if iface not in (None, "none"):
        rules.append("PGR07")
        if extra is None:
            assumptions.append("별도 COM·SG·접지·실드 핀 없음으로 가정 — 결선도로 확인(PGR07)")
            questions.append(f"{nm}: 전원 0V 와 따로 연결하는 공통선(COM·SG)·접지·실드 선이 있나요? 있으면 몇 개인가요?")
        extra_nets = [_net(f"extra:{i}", f"COM{i}", "extra", 1) for i in range(1, int(extra or 0) + 1)]

    p_power = sum(n["contacts"] for n in power_nets) if power_nets is not None else None
    p_sig = sum(n["contacts"] for n in sig_nets) if sig_nets is not None else None
    p_extra = len(extra_nets)
    pins = p_power + p_sig + p_extra if p_power is not None and p_sig is not None else None
    nets = (power_nets or []) + (sig_nets or []) + extra_nets if pins is not None else None
    modules = _ceil(pins / PINS_PER_MODULE) if pins is not None else None
    if iface == "RS485" and wires is None and pins is not None:
        scenarios = [{"label": "2선식 RS485(A·B)", "pins": pins, "modules": modules},
                     {"label": "4선식 RS485(TX±·RX±)", "pins": pins + 2, "modules": _ceil((pins + 2) / PINS_PER_MODULE)}]
    status = "needs_data" if missing else "conditional" if assumptions else "confirmed"
    return {"tool": nm, "status": status, "circuits": circuits, "voltage_V": ps["voltage_V"],
            "rated_A": ps["rated_A"], "peak_A": ps["peak_A"], "inrush_A": ps["inrush_A"], "design_A": design,
            "current_basis": basis, "from_text": ps["from_text"], "pins_per_path": pins_for(design) if design else None,
            "interface": iface, "interface_inferred": inferred, "rs485_wires": wires, "di": di, "do": do,
            "power_pins": p_power, "signal_pins": p_sig, "extra_pins": p_extra if iface not in (None, "none") else 0,
            "pins": pins, "modules": modules, "spare_pins": modules * PINS_PER_MODULE - pins if pins is not None else None,
            "nets": nets, "scenarios": scenarios, "missing": missing, "assumptions": assumptions, "questions": questions,
            "parallel": bool(design and pins_for(design) > 1), "rules": rules}


def needs_electrical(tool: dict[str, Any]) -> bool | None:
    """포고핀 산정 대상 — 전원·센서·신호를 쓰거나, 밸브가 툴측이라 밸브 전원·제어가 결합면을 지나면(PGR22)."""
    p, s = _get(tool, "intake.needs_power"), _get(tool, "intake.has_sensors_or_signals")
    if p is True or s is True:
        return True
    if _get(tool, "intake.needs_air") is True and _air_valve(tool) == "tool_side":
        return True
    return None if p is None or s is None else False


# ── 툴 하나의 공압 요구(PGR09·PGR10·PGR20·PGR21·PGR23) ───────────────────────────────

def _air_valve(tool: dict[str, Any]) -> str | None:
    pn = tool.get("pneumatic") or {}
    if pn.get("actuation") == "integrated_valve":
        return "tool_side"
    v = pn.get("valve_location")
    return v if v in ("robot_side", "tool_side") else None


def pressure_check(medium: str | None, pmin: float | None, pmax: float | None) -> dict[str, Any]:
    """PGR10·PGR23 — 실제 양압 공급(설정·최대 공급)을 1~5bar 와 비교. 진공은 양압 기준을 적용하지 않는다.
    범위 밖이면 별도 검토이며 모듈 수를 늘려 해결하지 않는다. 압력 적합은 유량·파지력 승인이 아니다."""
    if medium and re.search(r"진공|vacuum", medium, re.I):
        return {"fit": None, "review": True, "reason": None,
                "note": "진공 — 양압 1~5bar 기준은 적용하지 않음, 허용 진공·유량은 별도 검토(PGR10)"}
    if pmin is None and pmax is None:
        return {"fit": None, "review": False, "reason": "needs_information"}
    lo, hi = (pmin if pmin is not None else pmax), (pmax if pmax is not None else pmin)
    lo_s, hi_s = STD_BAR
    if lo >= lo_s and hi <= hi_s:
        return {"fit": True, "review": False, "reason": None,
                "note": f"실제 공급 {lo:g}~{hi:g}bar — 표준 {lo_s:g}~{hi_s:g}bar 이내(압력 범위만, 유량·파지력은 별도 확인)"}
    return {"fit": False, "review": True, "reason": "PGR23",
            "note": f"실제 공급 {lo:g}~{hi:g}bar — 표준 {lo_s:g}~{hi_s:g}bar 밖, 별도 검토(모듈 추가로 해결하지 않음, PGR23)"}


def tool_air(tool: dict[str, Any]) -> dict[str, Any]:
    """툴 하나가 ATC 결합면으로 넘기는 독립 유로 수와 에어커플러 하한 ceil(L/2).
    확인된 통과 유로 수가 있으면 그대로, 없으면 동작 방식·밸브 위치·그리퍼 수·제어 관계로 프로파일 산정."""
    nm = tool.get("name") or "툴"
    pn = tool.get("pneumatic") or {}
    missing: list[str] = []
    assumptions: list[str] = []
    questions: list[str] = []
    profile = None
    scenarios: list[dict[str, Any]] = []
    explicit = pn.get("required_independent_paths")
    act, valve, n, ctrl, extra = (pn.get("actuation"), _air_valve(tool), pn.get("gripper_count"),
                                  pn.get("control_relationship"), pn.get("extra_paths"))
    paths = None
    if explicit is not None:
        paths, profile = int(explicit), "confirmed_paths"
    elif act is None:
        missing.append("공압 동작 방식(복동·단동·밸브 내장)")
        questions.append(f"{nm}: 공압그리퍼는 공기로 열고 닫는 복동인가요, 한쪽은 스프링인 단동인가요? 밸브 내장형인가요?")
    elif act == "vacuum_other":
        profile = "AIR_VACUUM_OTHER"
        missing.append("진공·블로우의 실제 통과 유로 수")
        questions.append(f"{nm}: 진공·에어블로우 라인 중 실제로 ATC 를 지나는 독립 라인은 몇 개인가요? 발생기·밸브 위치도 알려 주세요.")
    elif valve is None:
        profile = "AIR_UNKNOWN_LAYOUT"
        missing.append("밸브 위치(로봇측·툴측)")
        questions.append(f"{nm}: 열고 닫는 밸브가 로봇측에 있나요, 교체하는 그리퍼측에 있나요? 배관 사진도 괜찮습니다.")
        per = 2 if act == "double_acting" else 1
        k = n if isinstance(n, int) and n > 0 else 1
        scenarios = [{"label": "로봇측 밸브", "paths": per * k, "modules": _ceil(per * k / PATHS_PER_COUPLER)},
                     {"label": "툴측 밸브(공급 1선)", "paths": 1, "modules": 1}]
    elif valve == "tool_side":
        profile = "AIR_TOOL_VALVE_SINGLE_SUPPLY"
        paths = 1
        assumptions.append("툴측 밸브 — 공급 1유로·툴측 배기 가정, 밸브 전원·제어·센서선은 포고핀에 포함(PGR09·PGR22)")
    else:
        per = 2 if act == "double_acting" else 1
        profile = "AIR_DOUBLE_ROBOT_VALVE" if per == 2 else "AIR_SINGLE_ROBOT_VALVE"
        if n is None:
            missing.append("툴당 공압 그리퍼 수")
            questions.append(f"{nm}: 이 툴에 공압 그리퍼가 몇 개 달리나요?")
        elif n <= 1:
            paths = per
        elif ctrl == "approved_shared":
            paths, profile = per, "AIR_APPROVED_SHARED_BRANCH"
            assumptions.append("공통 A/B 분기 — 같은 명령·압력으로 동작 가능한지와 유량 검토 전제(PGR21)")
        else:
            paths = per * int(n)
            if ctrl is None:
                assumptions.append(f"그리퍼 {n}개 개별 제어로 가정 — 공통 배관 분기는 회로가 확정된 경우만(PGR21)")
                questions.append(f"{nm}: 그리퍼 {n}개를 각각 따로 제어하나요, 공통 배관으로 같이 움직이나요?")
    if paths is not None and explicit is None:
        if extra is None:
            assumptions.append("추가 블로우·진공·원격 배기 유로 없음으로 가정(PGR09)")
            questions.append(f"{nm}: 열림·닫힘 외에 에어블로우·진공·원격 배기 라인이 ATC 를 지나나요?")
        else:
            paths += int(extra)
    pr = pn.get("pressure_bar") or {}
    press = pressure_check(pn.get("medium"), pr.get("min"), pr.get("max"))
    modules = _ceil(paths / PATHS_PER_COUPLER) if paths is not None else None
    status = "needs_data" if missing else "conditional" if assumptions else "confirmed"
    return {"tool": nm, "status": status, "profile": profile, "actuation": act, "valve_location": valve,
            "gripper_count": n, "control_relationship": ctrl, "paths": paths, "modules": modules,
            "lower_bound_modules": modules if modules is not None else (min(s["modules"] for s in scenarios) if scenarios else None),
            "scenarios": scenarios, "medium": pn.get("medium"), "pressure": press,
            "missing": missing, "assumptions": assumptions, "questions": questions,
            "rules": ["PGR09", "PGR10", "PGR20", "PGR21", "PGR23"]}


# ── 공통 마스터 배치(PGR11·PGR12·PGR13·PGR14) ────────────────────────────────────────

_ROLE_ORDER = {"supply": 0, "return": 0, "comm": 1, "do": 2, "di": 3, "extra": 4}


def electrical_layout(tools_e: list[dict[str, Any]]) -> dict[str, Any]:
    """교체 툴 전체를 받는 고정 마스터 배치 — net_key 별 필요 접점의 최댓값, 다른 net_key 는 합집합(PGR11).
    전원·통신·출력·입력·공통선 순으로 8접점 모듈에 차례로 배정하고(한 경로는 가능하면 한 모듈 안에), 각 툴이 쓰는 모듈 수가 PPF(PGR12).
    논리 배정일 뿐 실제 커넥터 핀 번호가 아니다. 툴 하나라도 값이 없으면 배치를 확정하지 않는다."""
    if not tools_e:
        return {"modules": [], "ppm": 0, "tool_ppf": {}, "contacts": 0, "lower_bound": 0, "notes": []}
    if any(t["nets"] is None for t in tools_e):
        known = [t["modules"] for t in tools_e if t["modules"] is not None]
        return {"modules": None, "ppm": None, "tool_ppf": {t["tool"]: None for t in tools_e}, "contacts": None,
                "lower_bound": max(known) if known else None, "notes": []}
    merged: dict[str, dict[str, Any]] = {}
    for t in tools_e:
        for n in t["nets"]:
            m = merged.setdefault(n["key"], {**n})
            m["contacts"] = max(m["contacts"], n["contacts"])
    order = sorted(merged.values(), key=lambda n: _ROLE_ORDER.get(n["role"], 9))   # 같은 역할 안에서는 처음 나온 순서
    modules: list[list[str]] = []
    where: dict[str, set[int]] = {}
    cur: list[str] = []
    for n in order:
        left = n["contacts"]
        while left > 0:
            space = PINS_PER_MODULE - len(cur)
            if space == 0 or (left <= PINS_PER_MODULE and left > space):
                modules.append(cur)
                cur = []
                continue
            take = min(left, space)
            cur += [n["label"]] * take
            where.setdefault(n["key"], set()).add(len(modules))
            left -= take
    if cur:
        modules.append(cur)
    modules = [m + ["미사용"] * (PINS_PER_MODULE - len(m)) for m in modules]
    tool_ppf = {t["tool"]: len({i for n in t["nets"] for i in where[n["key"]]}) for t in tools_e}
    notes = []
    if len(tools_e) > 1:
        notes.append("같은 전압의 전원·0V 경로와 같은 통신선은 교체 툴끼리 같은 접점을 쓴다고 보고 배치(PGR11) — "
                     "모든 툴의 결선이 고정 마스터 배치에 맞는지 엔지니어 확인")
    return {"modules": modules, "ppm": len(modules), "tool_ppf": tool_ppf,
            "contacts": sum(n["contacts"] for n in merged.values()),
            "lower_bound": max(t["modules"] for t in tools_e), "notes": notes}


def air_layout(tools_a: list[dict[str, Any]]) -> dict[str, Any]:
    """공압 공통 배치 — 교체 툴의 같은 위치 유로를 공통으로 쓴다고 보고 마스터 PMM = 툴 중 최댓값, 툴측 PMF = 각 툴의 모듈 수(E20).
    교체 툴 수만큼 마스터 PMM 을 늘리지 않는다. 유로 매핑이 맞는지는 확인 대상."""
    if not tools_a:
        return {"pmm": 0, "tool_pmf": {}, "notes": []}
    if any(t["modules"] is None for t in tools_a):
        return {"pmm": None, "tool_pmf": {t["tool"]: t["modules"] for t in tools_a}, "notes": []}
    notes = ["교체 툴끼리 같은 위치의 유로를 공통으로 쓴다고 보고 배치(E20) — 유로 매핑 확인"] if len(tools_a) > 1 else []
    return {"pmm": max(t["modules"] for t in tools_a), "tool_pmf": {t["tool"]: t["modules"] for t in tools_a}, "notes": notes}


# ── 통합 결과(output_contract.unified_accessory_result) ─────────────────────────────

def evaluate(tools: list[dict[str, Any]], n_master: int, *, pogo_needed: bool, air_needed: bool) -> dict[str, Any]:
    """툴 목록 → 툴별 전기·공압 요구 + 공통 마스터 배치 + 3개소 판정 + 통합 수량표.
    pogo_needed/air_needed: 툴 중 하나라도 전기·공압을 쓰는지(모르면 False 로 넘기고 상태는 호출 쪽에서 '미정')."""
    e_tools = [tool_electrical(t) for t in tools if needs_electrical(t)] if pogo_needed else []
    a_tools = [tool_air(t) for t in tools if _get(t, "intake.needs_air") is True] if air_needed else []
    el = electrical_layout(e_tools)
    ai = air_layout(a_tools)
    ppm, pmm = (el["ppm"] if pogo_needed else 0), (ai["pmm"] if air_needed else 0)
    positions = ppm + pmm if ppm is not None and pmm is not None else None
    qty = {(t.get("name") or "툴"): t.get("installed_quantity") for t in tools}

    def side_sum(per_tool: dict[str, int | None]) -> int | None:
        total = 0
        for name, k in per_tool.items():
            q = qty.get(name)
            if k is None or q is None:
                return None
            total += int(k) * int(q)
        return total

    ppf = side_sum(el["tool_ppf"]) if pogo_needed else 0
    pmf = side_sum(ai["tool_pmf"]) if air_needed else 0
    bom = {"PPM": ppm * n_master if ppm is not None else None, "PPF": ppf,
           "PMM": pmm * n_master if pmm is not None else None, "PMF": pmf}
    bom["PPM_30cm_cable"] = bom["PPM"] * CABLES_PER_PPM if bom["PPM"] is not None else None
    bom["PPF_30cm_cable"] = bom["PPF"] * CABLES_PER_PPF if bom["PPF"] is not None else None

    assumptions = [f"{t['tool']}: {a}" for t in e_tools + a_tools for a in t["assumptions"]]
    review_notes = el["notes"] + ai["notes"]          # 공통 배치 확인(PGR11·E20) — 입력 가정이 아니라 엔지니어 확인 사항
    questions = [q for t in e_tools + a_tools for q in t["questions"]]
    missing = [f"{t['tool']} {m}" for t in e_tools + a_tools for m in t["missing"]]
    custom = positions is not None and positions > STD_POSITIONS
    status = ("needs_data" if missing or positions is None else "conditional_draft" if assumptions else "engineering_review")
    basis = []
    for t in e_tools:
        if t["pins"] is not None:
            basis.append(f"{t['tool']}: 전원 {t['power_pins']} + 신호 {t['signal_pins']} + 공통선 {t['extra_pins']} = {t['pins']}핀 "
                         f"→ 하한 ceil({t['pins']}/{PINS_PER_MODULE}) = {t['modules']}개"
                         + (f" (설계 전류 {t['design_A']:g}A = max({t['current_basis']}), 경로당 {t['pins_per_path']}핀)" if t["design_A"] else ""))
    if el["modules"]:
        basis.append(f"마스터 공통 배치: 고유 접점 {el['contacts']}개 → PPM {el['ppm']}개"
                     + "".join(f" / {nm} PPF {k}" for nm, k in el["tool_ppf"].items()))
    for t in a_tools:
        if t["paths"] is not None:
            basis.append(f"{t['tool']}: 독립 유로 {t['paths']}개 → ceil({t['paths']}/{PATHS_PER_COUPLER}) = 에어커플러 {t['modules']}개")
    if positions is not None:
        basis.append(f"장착 위치: PPM {ppm} + PMM {pmm} = {positions}개소 / 표준 {STD_POSITIONS}개소"
                     + (" — 초과, 커스텀 필요(PGR13)" if custom else ""))
    return {
        "revision": REVISION, "category": SPEC["accessory_catalog_structure"]["category_id"],
        "status": status, "status_ko": STATUS_KO[status],
        "electrical_tools": e_tools, "air_tools": a_tools,
        "master": {"electrical_modules": el["modules"], "ppm_per_master": ppm, "pmm_per_master": pmm,
                   "unique_contacts": el["contacts"], "electrical_lower_bound": el["lower_bound"],
                   "positions": positions, "standard_positions": STD_POSITIONS, "customization_required": custom if positions is not None else None},
        "tool_layouts": [{"tool": (t.get("name") or "툴"), "quantity": t.get("installed_quantity"),
                          "PPF": el["tool_ppf"].get(t.get("name") or "툴", 0) if pogo_needed else 0,
                          "PMF": ai["tool_pmf"].get(t.get("name") or "툴", 0) if air_needed else 0} for t in tools],
        "bom": bom, "calculation_basis": basis, "assumptions": list(dict.fromkeys(assumptions)), "review_notes": review_notes,
        "open_questions": list(dict.fromkeys(questions)), "missing": missing,
        "quote_release": {"all_required_values_resolved": status != "needs_data" and not assumptions,
                          "note_ko": SPEC["output_contract"]["quote_release"]["note_ko"]},
        "engineering_review": SPEC["engineering_review_before_use"],
    }
