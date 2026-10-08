"""첫 고객 미팅 질문지(채워진 파일) → 미팅 요약 + 선정 입력(intake) 추출 (사용자 2026-10-02).

영업사원이 미팅 때 채운 질문지(docs/atc_meeting 양식, docx·pdf·txt)를 첨부하면 사내 AI 가 읽어 요약하고 J01~J10·후속
답을 atc_selection 입력 형식으로 뽑는다. 원칙(선정데이터 JSON data_policy):
- 원문에 없는 값은 만들지 않는다 — 모름·? ·빈칸은 null. 숫자는 원문에 실제로 있는지 코드로 다시 확인하고, 없으면 null +
  '원문에서 확인 안 됨'으로 남긴다(사람이 [추출 내용 고치기]로 확인·수정).
- AI 는 추출·요약만 한다. 후보 선정은 atc_selection 규칙 계산.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .. import proposal_llm  # noqa: F401  (호출은 product_recommend._default_chat 으로)
from ..diagram.llm_spec import _extract_json
from . import atc_accessory as acc

SOURCE_MAX = 12000

_SYSTEM = """너는 툴체인저 영업 미팅 기록을 정리하는 담당자다. [미팅 질문지]는 영업사원이 고객 미팅 때 채운 질문지 원문이다.
원문에 적힌 내용만 아래 JSON 형식으로 옮긴다. JSON 으로만 답한다.
규칙:
- 원문에 없거나 '모름'·'?'·빈칸이면 null. 추측해서 채우지 않는다. 예시·안내 문구(회색 설명)는 답이 아니다.
- '유'=true, '무'=false, '있음'=true, '없음'=false, '충분'=true, '부족'=false.
- 로봇 종류: 협동→"cobot", 산업용→"industrial", 그 외→"other". 작업: 이송→"transfer", 조립→"assembly", 가공→"machining",
  검사→"inspection", 기타→"other".
- 툴은 2쪽 '툴별 정보' 표의 한 줄이 하나다(같은 툴 여러 개면 installed_quantity). 툴 무게는 제품을 뺀 무게.
- 제품을 들지 않는다('해당 없음')면 workpiece_not_applicable=true. '0.5kg × 4개'처럼 적혔으면 총무게 2, workpiece_count 4.
- 1쪽 답이 '2쪽 표 참고'면 2쪽 표에서 툴마다 읽는다. 7번(기울임)처럼 1쪽에 모든 툴 공통으로 적혔으면 각 툴에 같이 넣는다.
- 속도·케이블은 로봇마다(여러 대면 각각). 압력 '4~5bar'→min 4, max 5 / '6bar'→min 6, max 6. MPa·kPa 면 숫자는 그대로 두고
  pressure_unit 에 "MPa"·"kPa"(코드가 bar 로 환산). 카탈로그 최대 허용 압력은 사용 압력이 아니다.
- 실제로 쓸 제어 방식만 control_interface("I/O"·"RS485"·"CAN"·"기타") — '지원'·'옵션' 목록은 넣지 않는다. 2선/4선이 적혔으면 rs485_wires 2·4.
- 공압 회로가 적혔으면 air_actuation("복동"·"단동"·"밸브내장"·"진공"), valve_location("로봇측"·"툴측"), air_gripper_count(툴 하나의 공압 그리퍼 수).
- 추가 질문 표의 답(전압·전류, 센서 목록, 제어 방식, 압력·호스, 자세, 케이블 필요 길이)은 해당하는 툴·로봇에 넣는다.
- '그 밖의 메모'의 시스템·툴스탠드 요청은 project.system_request, 예비 툴플레이트 수는 project.spare_tool_plates.
- 10번 환경이 '해당 없음'이면 has_special_conditions=false, 철가루·물·기름·고온 등이 적혔으면 true 와 tags.
- summary: 미팅 내용을 3~5문장으로(고객 공정, 로봇, 툴과 무게, 전기·공압, 특이사항, 모르는 것). 원문에 없는 내용 금지.
{"meeting": {"customer": null, "writer": null, "date": null},
 "summary": "",
 "project": {"tool_count": null, "process": {"category": null, "description": null, "has_contact_force": null,
             "contact_operation": null},
             "environment": {"has_special_conditions": null, "tags": [], "details": null},
             "system_request": null, "spare_tool_plates": null},
 "robots": [{"manufacturer": null, "model": null, "type": null, "quantity": null,
             "speed": {"value": null, "unit": null},
             "cables": {"master_4m_sufficient": null, "system_1m_sufficient": null,
                        "master_to_controller_required_m": null, "controller_to_system_required_m": null}}],
 "tools": [{"name": null, "model": null, "robot_models": [], "installed_quantity": null,
            "dimensions_mm": {"x": null, "y": null, "z": null}, "tool_assembly_mass_kg": null,
            "workpiece_not_applicable": false, "workpiece_count": null, "max_simultaneous_workpieces_kg": null,
            "tilts_or_flips": null, "needs_power": null, "needs_air": null, "has_sensors_or_signals": null,
            "power_note": null, "sensor_list": null, "control_method_raw": null,
            "pneumatic_medium": null, "pressure_min_bar": null, "pressure_max_bar": null, "pressure_unit": null, "observed_hose_count": null,
            "control_interface": null, "rs485_wires": null, "air_actuation": null, "valve_location": null, "air_gripper_count": null,
            "orientation_note": null}]}"""

_ENV_TAGS = {"철가루": "철가루", "물": "물·기름", "기름": "물·기름", "고온": "고온", "기타": "기타"}
# v1.2 액세서리 칸 — AI 가 쓴 말 → 계산 키(모르는 말이면 None, 지어내지 않음)
_IFACE = {"I/O": "digital_IO", "IO": "digital_IO", "DIO": "digital_IO", "RS485": "RS485", "RS-485": "RS485", "CAN": "CAN", "기타": "other"}
_ACT = {"복동": "double_acting", "단동": "single_acting", "밸브내장": "integrated_valve", "진공": "vacuum_other"}
_VALVE = {"로봇측": "robot_side", "툴측": "tool_side", "그리퍼측": "tool_side"}


def air_circuit(text: str) -> dict[str, Any]:
    """추가 질문 답의 공압 회로 글 → {actuation, valve_location, gripper_count}(적힌 것만). 예: '복동, 로봇측 밸브, 그리퍼 2개'."""
    out: dict[str, Any] = {}
    if re.search(r"밸브\s*내장|내장\s*밸브", text):
        out["actuation"] = "integrated_valve"
    elif "복동" in text:
        out["actuation"] = "double_acting"
    elif "단동" in text:
        out["actuation"] = "single_acting"
    if re.search(r"로봇\s*측\s*밸브|밸브[^,·.]*로봇\s*측", text):
        out["valve_location"] = "robot_side"
    elif re.search(r"(?:툴|그리퍼)\s*측\s*밸브|밸브[^,·.]*(?:툴|그리퍼)\s*측", text):
        out["valve_location"] = "tool_side"
    g = re.search(r"그리퍼\s*(\d+)\s*개", text)
    if g:
        out["gripper_count"] = int(g.group(1))
    if re.search(r"추가\s*라인\s*없음", text):
        out["extra_paths"] = 0
    else:
        x = re.search(r"추가\s*라인\s*(\d+)", text)
        if x:
            out["extra_paths"] = int(x.group(1))
    return out


def extra_pins(text: str) -> int | None:
    """제어 방식 답의 '공통선 없음'·'공통선 1개'·'COM 1' → 0V 와 따로 연결하는 공통선·접지·실드 수(PGR07). 안 적혔으면 None."""
    if re.search(r"공통선\s*(?:없음|0\s*개?)", text):
        return 0
    m = re.search(r"(?:공통선|COM|SG)\s*(\d+)", text, re.I)
    return int(m.group(1)) if m else None


class MeetingError(Exception):
    """사용자에게 그대로 보여도 되는 한국어 문구."""


def _clean(v: Any, limit: int = 300) -> str | None:
    s = re.sub(r"\s+", " ", str(v)).strip() if v is not None else ""
    return s[:limit] or None


def _numbers_in(text: str) -> set[str]:
    return {n.replace(",", "").rstrip("0").rstrip(".") if "." in n else n.replace(",", "")
            for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}


def _num(v: Any, source_nums: set[str], label: str, notes: list[str]) -> float | None:
    """숫자 칸: 숫자로 바꾸고, 원문에 그 숫자가 있는지 확인(없으면 null + 메모)."""
    if v is None or v == "":
        return None
    try:
        f = float(str(v).replace(",", ""))
    except ValueError:
        return None
    key = f"{f:g}"
    if key not in source_nums:
        notes.append(f"{label} {key} — 원문에서 확인 안 됨(비워 둠)")
        return None
    return f


def _bool(v: Any) -> bool | None:
    return v if isinstance(v, bool) else None


def _enum(v: Any, allowed: tuple[str, ...]) -> str | None:
    return v if v in allowed else None


def normalize(raw: dict[str, Any], source: str) -> tuple[dict[str, Any], list[str]]:
    """AI 추출 결과 → atc_selection 입력(intake) + 확인 메모. 숫자는 원문 대조."""
    nums = _numbers_in(source)
    notes: list[str] = []
    p = raw.get("project") or {}
    proc = p.get("process") or {}
    env = p.get("environment") or {}
    tags = []
    for t in env.get("tags") or []:
        tag = next((v for k, v in _ENV_TAGS.items() if k in str(t)), None)
        if tag and tag not in tags:
            tags.append(tag)
    tool_count = _num(p.get("tool_count"), nums, "교체 툴 수", notes)
    spare = _num(p.get("spare_tool_plates"), nums, "예비 툴플레이트", notes)
    project = {
        "tool_count": int(tool_count) if tool_count is not None else None,
        "process": {"category": _enum(proc.get("category"), ("transfer", "assembly", "machining", "inspection", "other")),
                    "description": _clean(proc.get("description")),
                    "has_contact_force": _bool(proc.get("has_contact_force")),
                    "contact_operation": _clean(proc.get("contact_operation"))},
        "environment": {"has_special_conditions": _bool(env.get("has_special_conditions")), "tags": tags,
                        "details": _clean(env.get("details"))},
        "system_request": _clean(p.get("system_request")),
        "spare_tool_plates": int(spare) if spare is not None else None,
    }
    if tags and project["environment"]["has_special_conditions"] is None:
        project["environment"]["has_special_conditions"] = True

    robots = []
    for i, r in enumerate(raw.get("robots") or []):
        if not isinstance(r, dict):
            continue
        sp, cb = r.get("speed") or {}, r.get("cables") or {}
        nm = _clean(r.get("model")) or f"로봇 {i + 1}"
        q = _num(r.get("quantity"), nums, f"{nm} 수량", notes)
        robots.append({
            "id": f"R{len(robots) + 1}", "manufacturer": _clean(r.get("manufacturer"), 60), "model": _clean(r.get("model"), 60),
            "type": _enum(r.get("type"), ("cobot", "industrial", "other")), "quantity": int(q) if q is not None else None,
            "speed": {"value": _num(sp.get("value"), nums, f"{nm} 속도", notes), "unit": _clean(sp.get("unit"), 20)},
            "cables": {"master_4m_sufficient": _bool(cb.get("master_4m_sufficient")),
                       "system_1m_sufficient": _bool(cb.get("system_1m_sufficient")),
                       "master_to_controller_required_m": _num(cb.get("master_to_controller_required_m"), nums, f"{nm} 케이블", notes),
                       "controller_to_system_required_m": _num(cb.get("controller_to_system_required_m"), nums, f"{nm} 케이블", notes)},
        })
    if not robots:
        robots.append({"id": "R1", "speed": {}, "cables": {}})

    def robot_ids(models: list[Any]) -> list[str]:
        if len(robots) == 1:
            return [robots[0]["id"]]
        ids = [r["id"] for r in robots for m in models or []
               if r.get("model") and str(r["model"]).lower().replace(" ", "") in str(m).lower().replace(" ", "")]
        return list(dict.fromkeys(ids))

    tools = []
    for i, t in enumerate(raw.get("tools") or []):
        if not isinstance(t, dict) or not any(t.get(k) not in (None, "", [], False) for k in t):
            continue
        nm = _clean(t.get("name"), 80) or f"툴 {i + 1}"
        d = t.get("dimensions_mm") or {}
        qty = _num(t.get("installed_quantity"), nums, f"{nm} 수량", notes)
        wpc = _num(t.get("workpiece_count"), nums, f"{nm} 제품 수", notes)
        unit = str(t.get("pressure_unit") or "bar")
        pmin = acc.to_bar(_num(t.get("pressure_min_bar"), nums, f"{nm} 압력", notes), unit)
        pmax = acc.to_bar(_num(t.get("pressure_max_bar"), nums, f"{nm} 압력", notes), unit)
        gcount = _num(t.get("air_gripper_count"), nums, f"{nm} 공압 그리퍼 수", notes)
        wires = _num(t.get("rs485_wires"), nums, f"{nm} RS485 선식", notes)
        hoses = _num(t.get("observed_hose_count"), nums, f"{nm} 호스 수", notes)
        na = t.get("workpiece_not_applicable") is True
        tools.append({
            "id": f"T{len(tools) + 1}", "name": nm, "model": _clean(t.get("model"), 80),
            "robot_ids": robot_ids(t.get("robot_models") or []),
            "installed_quantity": int(qty) if qty is not None else None,
            "workpiece_count": int(wpc) if wpc is not None else None,
            "workpiece_not_applicable": na,
            "intake": {"dimensions_mm": {k: _num(d.get(k), nums, f"{nm} 크기", notes) for k in ("x", "y", "z")},
                       "tool_assembly_mass_kg": _num(t.get("tool_assembly_mass_kg"), nums, f"{nm} 툴 무게", notes),
                       "tilts_or_flips": _bool(t.get("tilts_or_flips")), "needs_power": _bool(t.get("needs_power")),
                       "needs_air": _bool(t.get("needs_air")), "has_sensors_or_signals": _bool(t.get("has_sensors_or_signals"))},
            "mass_components": {"max_simultaneous_workpieces_kg":
                                None if na else _num(t.get("max_simultaneous_workpieces_kg"), nums, f"{nm} 제품 무게", notes)},
            "electrical": {"power_circuits": [_clean(t["power_note"])] if _clean(t.get("power_note")) else None,
                           "sensor_list": [_clean(t["sensor_list"])] if _clean(t.get("sensor_list")) else None,
                           "control_method_raw": _clean(t.get("control_method_raw"), 80),
                           "comm_interface": _IFACE.get(str(t.get("control_interface") or "").replace(" ", "").upper()),
                           "rs485_wires": int(wires) if wires in (2, 4) else None},
            "pneumatic": {"medium": _clean(t.get("pneumatic_medium"), 40),
                          "pressure_bar": {"min": pmin, "max": pmax} if (pmin is not None or pmax is not None) else None,
                          "observed_hose_count": int(hoses) if hoses is not None else None,
                          "actuation": _ACT.get(str(t.get("air_actuation") or "").replace(" ", "")),
                          "valve_location": _VALVE.get(str(t.get("valve_location") or "").replace(" ", "")),
                          "gripper_count": int(gcount) if gcount is not None else None},
            "orientation": _clean(t.get("orientation_note")),
        })
    if not tools:
        tools.append({"id": "T1", "robot_ids": [robots[0]["id"]], "intake": {"dimensions_mm": {}}, "mass_components": {}})
    return {"project": project, "robots": robots, "tools": tools}, list(dict.fromkeys(notes))


# ── 양식 표 직접 읽기(docx) — 표의 칸은 AI 가 아니라 코드가 읽는다 ─────────────────────────────
# 2026-10-02 실측: AI 가 '툴별 정보' 표의 전기·공압 칸을 서로 바꿔 읽음(샘플5), 추가 질문 표의 '압입' 답을 놓침(샘플3).
# 우리 양식(scripts/make_atc_meeting_forms.py)은 표 머리글이 정해져 있어 확실히 읽을 수 있다.

_YN = {"유": True, "있음": True, "무": False, "없음": False}


def _yn(s: str) -> bool | None:
    s = (s or "").strip()
    return next((v for k, v in _YN.items() if s.startswith(k)), None)


def _first_num(s: str) -> float | None:
    m = re.search(r"\d[\d,]*(?:\.\d+)?", s or "")
    return float(m.group(0).replace(",", "")) if m else None


def _size(s: str) -> dict[str, float | None]:
    """'220 × 90 × 150' / 'Ø120 × 80'(원형: 가로=세로=지름) / '모름' → x,y,z."""
    nums = [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", s or "")]
    if "Ø" in (s or "") and len(nums) == 2:
        return {"x": nums[0], "y": nums[0], "z": nums[1]}
    return {k: (nums[i] if i < len(nums) else None) for i, k in enumerate(("x", "y", "z"))}


def parse_form(data: bytes) -> dict[str, Any] | None:
    """양식 docx → {tools: [...], followups: {조건: 답}}. 우리 양식이 아니면 None."""
    import io

    import docx

    try:
        d = docx.Document(io.BytesIO(data))
    except Exception:  # noqa: BLE001 — docx 가 아니면 AI 추출만
        return None
    tables = {}
    for t in d.tables:
        head = tuple(c.text.strip() for c in t.rows[0].cells)
        tables[head[0]] = [[c.text.strip() for c in r.cells] for r in t.rows[1:]]
    spec_rows = tables.get("툴 이름과 모델")
    util_rows = tables.get("툴")
    if spec_rows is None or util_rows is None:
        return None
    tools = []
    for i, row in enumerate(spec_rows):
        name, qty, size, mass, wp = (row + [""] * 5)[:5]
        util = util_rows[i] if i < len(util_rows) else [""] * 6
        if not any(x.strip() for x in (qty, size, mass, wp, *util[1:])) and re.fullmatch(r"툴 ?\d*", name.strip()):
            continue                                            # 빈 줄(양식 그대로)
        count = re.search(r"[×x]\s*(\d+)\s*개", wp or "")
        unk = lambda v: bool(re.search(r"모름|^\s*\?\s*$", v or ""))  # noqa: E731 — '모름'·'?' 는 빈칸과 다르다
        tools.append({
            "unknown": {"size": unk(size), "mass": unk(mass), "wp": unk(wp), "tilt": unk(util[2] if len(util) > 2 else ""),
                        "power": unk(util[3] if len(util) > 3 else ""), "air": unk(util[4] if len(util) > 4 else ""),
                        "sensor": unk(util[5] if len(util) > 5 else "")},
            "name": name.strip() or f"툴 {i + 1}", "qty": _first_num(qty), "size": _size(size),
            "mass": None if "모름" in (mass or "") else _first_num(mass),
            "mass_estimated": "약" in (mass or ""),
            "wp_na": "해당 없음" in (wp or ""), "wp": None if ("해당 없음" in (wp or "") or "모름" in (wp or "")) else _first_num(wp),
            "wp_count": int(count.group(1)) if count else None,
            "robot": (util[1] if len(util) > 1 else "").strip(),
            "tilt": _yn(util[2] if len(util) > 2 else ""), "power": _yn(util[3] if len(util) > 3 else ""),
            "air": _yn(util[4] if len(util) > 4 else ""), "sensor": _yn(util[5] if len(util) > 5 else ""),
        })
    followups = {r[0]: r[2] for r in tables.get("고객 답변", []) if len(r) >= 3 and r[2].strip()}
    # 1쪽 질문 표 — 번호별 답(질문 칸에 질문+안내가 함께 있다)
    answers = {}
    for r in tables.get("질문", []):
        m = re.match(r"\s*(\d+)\.", r[0] if r else "")
        if m and len(r) > 1:
            answers[m.group(1)] = r[1].strip()
    return {"tools": tools, "followups": followups, "answers": answers}


# 1쪽 질문 번호 → '모름'일 때 자료 요청으로 돌릴 칸
Q_UNKNOWN_FIELDS = {
    "1": [("robot", "type"), ("robot", "model"), ("robot", "quantity")],
    "2": [("project", "process.category")],
    "3": [("project", "tool_count")],
    "6": [("robot", "speed.value"), ("robot", "speed.unit")],
    "7": [("tool", "intake.tilts_or_flips")],
    "8": [("tool", "intake.needs_power"), ("tool", "intake.needs_air"), ("tool", "intake.has_sensors_or_signals")],
    "9": [("robot", "cables.master_4m_sufficient"), ("robot", "cables.system_1m_sufficient")],
    "10": [("project", "environment.has_special_conditions")],
}
# 양식 '추가로 질문' 표의 조건 → 선정데이터 후속 질문 id
FOLLOWUP_IDS = {
    "전기가 필요함": ("F02",), "센서나 신호가 필요함": ("F03",), "전기 또는 센서를 사용함": ("F04",),
    "공압이 필요함": ("F05", "F06"), "기울이거나 뒤집음": ("F08",), "누르거나 끼우거나 자름": ("F09",),
    "케이블 길이가 부족함": ("F07",), "정보를 모름": ("F01", "F11", "F12"),
}


def _dig(obj: Any, path: str) -> Any:
    for k in path.split("."):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(k)
    return obj


def mark_unknown(entity: dict[str, Any], path: str) -> None:
    """'모름' 표시 — 값 칸 옆에 '<칸>__unknown': True (화면 질문의 '모름' 버튼과 같은 표시)."""
    *head, last = path.split(".")
    cur = entity
    for k in head:
        if cur.get(k) is None:
            cur[k] = {}
        if not isinstance(cur[k], dict):
            return
        cur = cur[k]
    cur[f"{last}__unknown"] = True


def _base_name(name: str) -> str:
    """'전동 그리퍼 (모델)' → '전동 그리퍼' — 추가 질문 답에서 툴을 부를 때 쓰는 이름."""
    return re.split(r"\s*\(", name or "")[0].strip()


def for_tool(text: str, name: str, names: list[str]) -> str | None:
    """추가 질문 한 칸에 툴마다 나눠 적은 답('전동 그리퍼: … / 진공 그리퍼: …')에서 이 툴 몫만.
    툴 이름이 하나도 안 적혀 있으면 칸 전체가 해당하는 툴 모두의 답, 다른 툴 이름만 있으면 None.
    '툴마다 …'처럼 적으면 모든 툴에 같은 답."""
    # 띄어 쓴 ' / ' 만 툴 구분 — 'ON/OFF' 같은 말은 쪼개지 않는다
    parts = [x.strip() for x in re.split(r"\s+/\s+|\s*;\s*|\n", text or "") if x.strip()]
    bases = [b for b in {_base_name(n) for n in names} if b]
    named = [x for x in parts if any(b and b in x for b in bases)]
    if not named:
        return text
    mine = [x for x in named if _base_name(name) and _base_name(name) in x]
    return " / ".join(mine) if mine else None


_DI_RE = re.compile(r"DI\s*[:=]?\s*(\d+)", re.I)
_DO_RE = re.compile(r"DO\s*[:=]?\s*(\d+)", re.I)


def overlay(intake: dict[str, Any], form: dict[str, Any]) -> list[str]:
    """양식 표에서 읽은 값으로 툴·추가 질문 답을 덮어쓴다(표가 정답). 반환: 확인 메모."""
    notes = []
    llm_tools = {t.get("name"): t for t in intake["tools"]}
    robots = intake["robots"]
    tools = []
    for i, f in enumerate(form["tools"]):
        base = llm_tools.get(f["name"]) or (intake["tools"][i] if i < len(intake["tools"]) else {})
        ids = ([robots[0]["id"]] if len(robots) == 1 else
               [r["id"] for r in robots if r.get("model") and str(r["model"]).replace(" ", "").lower()
                in f["robot"].replace(" ", "").lower()]) or base.get("robot_ids") or []
        t = {**base, "id": f"T{i + 1}", "name": f["name"], "robot_ids": ids,
             "installed_quantity": int(f["qty"]) if f["qty"] is not None else None,
             "workpiece_count": f["wp_count"] or base.get("workpiece_count"),
             "workpiece_not_applicable": f["wp_na"],
             "intake": {"dimensions_mm": f["size"], "tool_assembly_mass_kg": f["mass"], "tilts_or_flips": f["tilt"],
                        "needs_power": f["power"], "needs_air": f["air"], "has_sensors_or_signals": f["sensor"]},
             "mass_components": {"max_simultaneous_workpieces_kg": None if f["wp_na"] else f["wp"]}}
        if f["mass_estimated"]:
            notes.append(f"{f['name']} 툴 무게는 '약' — 추정값")
        # 표에 '모름'·'?' 로 적힌 칸 — 다시 묻지 않고 자료 요청으로(화면 질문 칸의 '모름' 표시와 같은 키)
        u = f["unknown"]
        for key, paths in (("size", ["intake.dimensions_mm.x", "intake.dimensions_mm.y", "intake.dimensions_mm.z"]),
                           ("mass", ["intake.tool_assembly_mass_kg"]), ("wp", ["mass_components.max_simultaneous_workpieces_kg"]),
                           ("tilt", ["intake.tilts_or_flips"]), ("power", ["intake.needs_power"]),
                           ("air", ["intake.needs_air"]), ("sensor", ["intake.has_sensors_or_signals"])):
            if u.get(key):
                for pth in paths:
                    mark_unknown(t, pth)
        tools.append(t)
    if tools:
        dropped = [t.get("name") for t in intake["tools"] if t.get("name") not in {x["name"] for x in tools}]
        if dropped:
            notes.append(f"툴별 정보 표에 없는 툴은 뺌: {', '.join(n for n in dropped if n)}")
        intake["tools"] = tools

    # 1쪽 답에 '모름' — 그 질문의 빈 칸은 다시 묻지 않는다
    for no, ans in (form.get("answers") or {}).items():
        if "모름" not in ans:
            continue
        for scope, path in Q_UNKNOWN_FIELDS.get(no, ()):
            targets = robots if scope == "robot" else intake["tools"] if scope == "tool" else [intake["project"]]
            for e in targets:
                if _dig(e, path) is None:
                    mark_unknown(e, path)
    # 10번(사용 환경) 답의 상세 — 양식의 추가 질문 표엔 환경 줄이 없어 1쪽 답을 그대로 환경 상세(F10)로 쓴다
    env_ans = (form.get("answers") or {}).get("10", "").strip()
    if env_ans and not re.match(r"\s*(해당 없음|없음|모름)", env_ans):
        intake["project"].setdefault("environment", {})["details"] = env_ans
    # 추가 질문 표에 답이 있는 조건 — 같은 후속 질문을 다시 하지 않는다
    intake["project"]["followups_answered"] = sorted({fid for cond in form["followups"] for fid in FOLLOWUP_IDS.get(cond, ())})
    fu = form["followups"]
    # 추가 질문 표 — 한 칸 답이 해당하는 툴 전부에 적용된다
    if "누르거나 끼우거나 자름" in fu and not re.match(r"\s*(없음|무|해당 없음)", fu["누르거나 끼우거나 자름"]):
        intake["project"]["process"]["has_contact_force"] = True
        intake["project"]["process"]["contact_operation"] = fu["누르거나 끼우거나 자름"]
    names = [t.get("name") or "" for t in intake["tools"]]
    for t in intake["tools"]:
        air = for_tool(fu.get("공압이 필요함", ""), t.get("name") or "", names) or ""
        if not air:
            continue
        rng = re.search(r"(\d+(?:\.\d+)?)\s*(?:~|-|∼)\s*(\d+(?:\.\d+)?)\s*(bar|MPa|kPa)", air, re.I)
        one = re.search(r"(\d+(?:\.\d+)?)\s*(bar|MPa|kPa)", air, re.I)
        hoses = re.search(r"호스\s*(\d+)", air)
        if t["intake"]["needs_air"] is True:
            pn = t.setdefault("pneumatic", {})
            if rng:      # MPa·kPa 는 bar 로(PGR23)
                pn["pressure_bar"] = {"min": acc.to_bar(float(rng.group(1)), rng.group(3)), "max": acc.to_bar(float(rng.group(2)), rng.group(3))}
            elif one:
                pn["pressure_bar"] = {"min": acc.to_bar(float(one.group(1)), one.group(2)), "max": acc.to_bar(float(one.group(1)), one.group(2))}
            pn.update(air_circuit(air))
            if hoses:
                pn["observed_hose_count"] = int(hoses.group(1))
            paths = re.search(r"(?:독립\s*)?유로\s*(\d+)", air)
            if paths:
                pn["required_independent_paths"] = int(paths.group(1))
            pn["medium"] = "진공" if "진공" in air else pn.get("medium") or "압축공기"
    # 전기·센서 답 — 툴마다 나눠 적었으면 그 툴 몫만. 'DI n'·'DO n' 은 포고핀 산정용 신호 수(E02)로도 읽는다
    for t in intake["tools"]:
        it = t["intake"]
        if not (it["needs_power"] or it["has_sensors_or_signals"]):
            continue
        nm = t.get("name") or ""
        el = t.setdefault("electrical", {})
        power = for_tool(fu.get("전기가 필요함", ""), nm, names)
        sensor = for_tool(fu.get("센서나 신호가 필요함", ""), nm, names)
        ctrl = for_tool(fu.get("전기 또는 센서를 사용함", ""), nm, names)
        # 구동 전원이 없는 툴은 그 툴 이름을 붙여 적었거나 '센서 전원'이라고 적은 전원만 받는다
        if power and (it["needs_power"] or power != fu.get("전기가 필요함") or "센서 전원" in power):
            el["power_circuits"] = [power]
        if sensor and it["has_sensors_or_signals"]:
            el["sensor_list"] = [sensor]
        if ctrl:
            el["control_method_raw"] = ctrl
            if extra_pins(ctrl) is not None:
                el["extra_pins"] = extra_pins(ctrl)
        sig = " ".join(x for x in (sensor, ctrl) if x)
        di, do = _DI_RE.search(sig), _DO_RE.search(sig)
        if di and do:
            el["dio"] = {"di": int(di.group(1)), "do": int(do.group(1))}
    cable = fu.get("케이블 길이가 부족함", "")
    for r in robots:
        cb = r.setdefault("cables", {})
        m1 = re.search(r"툴체인저\s*→?\s*컨트롤러\s*(\d+(?:\.\d+)?)\s*m", cable)
        m2 = re.search(r"컨트롤러\s*→?\s*(?:PLC|로봇)[^\d]*(\d+(?:\.\d+)?)\s*m", cable)
        if m1:
            cb["master_to_controller_required_m"] = float(m1.group(1))
        if m2:
            cb["controller_to_system_required_m"] = float(m2.group(1))
    return notes


async def extract(source: str, *, chat, form: dict[str, Any] | None = None) -> dict[str, Any]:
    """질문지 원문 → {meeting, summary, intake, notes}."""
    source = (source or "").strip()
    if len(source) < 30:
        raise MeetingError("질문지 내용을 읽지 못했습니다. 채운 질문지 파일(docx·pdf·txt)인지 확인해 주세요.")
    if len(source) > SOURCE_MAX:
        source = source[:SOURCE_MAX]
    raw = _extract_json(await chat([{"role": "system", "content": _SYSTEM},
                                    {"role": "user", "content": f"[미팅 질문지]\n{source}"}], num_predict=4000))
    if not isinstance(raw, dict):
        raise MeetingError("질문지 내용을 정리하지 못했습니다. 다시 시도해 주세요.")
    intake, notes = normalize(raw, source)
    if form is not None:
        notes += overlay(intake, form)
    m = raw.get("meeting") or {}
    from .product_recommend import fix_terms

    return {"meeting": {"customer": _clean(m.get("customer"), 80), "writer": _clean(m.get("writer"), 40),
                        "date": _clean(m.get("date"), 20)},
            "summary": fix_terms(_clean(raw.get("summary"), 900) or ""),
            "intake": intake, "notes": notes, "source_chars": len(source)}


def read_upload(data: bytes, filename: str, mime: str) -> str:
    from ..proposal_project.extract import read_document

    text = read_document(data, filename, mime)
    if text is None:
        raise MeetingError("읽을 수 없는 파일 형식입니다. docx·pdf·txt 질문지를 올려 주세요.")
    return text


def dumps(o: Any) -> str:
    return json.dumps(o, ensure_ascii=False)
