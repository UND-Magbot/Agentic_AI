"""툴체인저 — AI 와 같이 찾기 대화(사용자 2026-10-02, ui_v9 지적 반영).

사용자가 대화창에 적은 말에서
1) 선정에 쓰는 칸 값(전원·전류·신호 수·무게·압력…)을 찾아 질문 칸에 채운다 → 다시 판정할 때 그대로 쓰인다.
2) 의도를 읽는다 — 결과·추천을 (다시) 보여 달라 / 학습시켜 달라 / 다른 모델 제안(suggest) / 이 모델로 바꿔 추천(switch) / 그냥 대화.
   단어('추천'·'학습')가 아니라 뜻으로. suggest·switch 의 모델은 [고를 수 있는 모델](제품 DB·이 로봇·정격 ≥ 선정 하중)에 있는 것만 —
   코드가 다시 확인한다(사용자 2026-10-06: 최종 제안 확정 전까지 대화로 몇 번이고 바꿀 수 있게, 확정 뒤에는 잠금).
   - 추천: 화면이 다시 판정 → 후보가 있으면 '이 후보로 추천해도 될까요?'를 다시 묻는다.
   - 학습: 앞으로의 판단에 반영해 달라고 분명히 요구할 때만('학습 내용은 어디서 봐?' 같은 질문은 아님).
숫자는 사용자가 적은 것만 받는다(atc_answer.clean). 선정 계산은 규칙(atc_selection)이 하고 AI 는 칸 채우기·말 이해만.
"""
from __future__ import annotations

import json
import re
from typing import Any

from . import atc_accessory as acc
from . import atc_answer as aa
from .ai_guard import strip_prompt_echo
from .atc_selection import intake_summary
from .product_recommend import _ai_text, _clean, _extract_json

# 대화에서 채울 수 있는 칸(질문지 J·F·E 칸 중 선정에 쓰는 것). 경로는 대상(툴·로봇·프로젝트) 기준.
TOOL_FIELDS = [
    ("installed_quantity", "integer", "수량", None), ("model", "string", "모델명", None),
    ("intake.tool_assembly_mass_kg", "number", "툴 무게(kg, 제품 제외)", None),
    ("mass_components.max_simultaneous_workpieces_kg", "number", "한 번에 드는 제품 무게(kg)", None),
    ("intake.dimensions_mm.x", "number", "가로(mm)", None), ("intake.dimensions_mm.y", "number", "세로(mm)", None),
    ("intake.dimensions_mm.z", "number", "높이(mm)", None),
    ("intake.tilts_or_flips", "boolean", "기울임·뒤집기", None),
    ("intake.needs_power", "boolean", "전기(구동) 필요", None), ("intake.needs_air", "boolean", "공압 필요", None),
    ("intake.has_sensors_or_signals", "boolean", "센서·신호 사용", None),
    ("electrical.power_circuits", "array", "전원 사양 글(예: 24V 1회로 0.5A)", None),
    ("electrical.power_circuit_count", "integer", "전원 회로 수", None),
    ("electrical.voltage_V", "number", "전압(V)", None),
    ("electrical.current_A", "number", "정격 전류(A)", None), ("electrical.peak_A", "number", "피크(최대)·기동 전류(A)", None),
    ("electrical.dio.di", "integer", "입력 DI 수", None), ("electrical.dio.do", "integer", "출력 DO 수", None),
    ("electrical.control_method_raw", "string", "제어 방식 글(DIO·RS-485 등, 들은 그대로)", None),
    # v1.2 액세서리 산정 칸(PGR06·PGR07·PGR19·PGR20·PGR21)
    ("electrical.comm_interface", "enum", "실제로 쓸 제어 방식", [k for k, _ in acc.INTERFACES]),
    ("electrical.rs485_wires", "enum", "RS485 선식(2선·4선)", [2, 4]),
    ("electrical.extra_pins", "integer", "0V 와 따로 연결하는 공통선·접지·실드 선 수", None),
    ("pneumatic.pressure_bar", "object", "실제 공급 압력(bar — MPa 는 ×10, kPa 는 ÷100 해서)", None),
    ("pneumatic.medium", "string", "압축공기/진공", None),
    ("pneumatic.observed_hose_count", "integer", "보이는 에어호스 수", None),
    ("pneumatic.actuation", "enum", "공압 동작 방식", [k for k, _ in acc.ACTUATIONS]),
    ("pneumatic.valve_location", "enum", "밸브 위치(로봇측·툴측)", [k for k, _ in acc.VALVE_LOCATIONS]),
    ("pneumatic.gripper_count", "integer", "툴 하나의 공압 그리퍼 수", None),
    ("pneumatic.control_relationship", "enum", "그리퍼 여러 개 제어(따로·공통 배관)", [k for k, _ in acc.CONTROLS]),
    ("pneumatic.extra_paths", "integer", "블로우·진공·원격 배기 추가 라인 수", None),
    ("pneumatic.required_independent_paths", "integer", "실제 ATC 통과 유로 수(확인된 경우만)", None),
]
ROBOT_FIELDS = [
    ("type", "enum", "로봇 종류", ["cobot", "industrial", "other"]), ("model", "string", "로봇 모델명", None),
    ("speed.value", "number", "운전 속도 값", None), ("speed.unit", "string", "속도 단위", None),
    ("cables.master_4m_sufficient", "boolean", "툴체인저→컨트롤러 4m 충분", None),
    ("cables.system_1m_sufficient", "boolean", "컨트롤러→PLC 1m 충분", None),
]
PROJECT_FIELDS = [
    ("tool_count", "integer", "교체 툴 수", None),
    ("process.category", "enum", "작업 종류", ["transfer", "assembly", "machining", "inspection", "other"]),
    ("environment.has_special_conditions", "boolean", "특별한 환경 있음", None),
]
_SPECS = {"tool": TOOL_FIELDS, "robot": ROBOT_FIELDS, "project": PROJECT_FIELDS}
_PREFIX = {"tool": "tools[].", "robot": "robots[].", "project": "project."}

_EXTRACT_SYSTEM = """너는 툴체인저 선정 대화에서 사용자의 [말]에 들어 있는 사실을 정해진 [칸]으로 옮긴다. JSON 으로만 답한다.
- [말]에 분명히 적힌 사실만 옮긴다. 추측·지어내기 금지. 질문·요청·잡담에는 updates 를 비운다.
- 대상은 [툴]·[로봇] 목록의 번호(index)로. 툴 이름이 없고 툴이 하나뿐이면 그 툴. 어느 툴인지 모호하면 넣지 않는다.
- 숫자는 [말]에 적힌 숫자 그대로. 선택지 칸은 선택지 값만. boolean 은 true/false. 압력은 {"min": n, "max": n}.
- 전원 사양을 말하면(예: "센서 전원 24V 0.05A") electrical.power_circuits 에 그 글을 그대로, 전류는 electrical.current_A 에도.
  피크·최대·기동 전류는 electrical.peak_A. 압력은 실제 공급 압력을 bar 로 — MPa 는 ×10, kPa 는 ÷100(예: 0.6MPa → 6).
  카탈로그 최대 허용 압력은 실제 공급 압력이 아니다.
- 제어 방식: 실제로 쓸 방식 하나만 electrical.comm_interface(I/O→digital_IO, RS485·Modbus RTU→RS485, CAN→CAN, 그 밖 통신→other).
  '지원'·'옵션' 목록은 실제 사용이 아니다. 2선/4선을 말하면 electrical.rs485_wires 에 2 또는 4.
- 공압: 복동→double_acting, 단동(스프링)→single_acting, 밸브 내장→integrated_valve, 진공·블로우→vacuum_other.
  밸브가 로봇측이면 robot_side, 그리퍼(툴)측이면 tool_side. 그리퍼 여러 개를 따로 제어하면 independent, 공통 배관으로 같이면 approved_shared.
{"updates": [{"target": "tool", "index": 0, "path": "electrical.current_A", "value": 0.05}]}"""

_INTENT_SYSTEM = """너는 유엔디로보틱스 툴체인저 선정 담당 기술영업이다. [상황]과 [대화]를 보고 사용자의 마지막 [말]에 답한다. JSON 으로만 답한다.
intent 는 다섯 중 하나 — 단어가 아니라 뜻으로 판단한다:
- "recommend": 결과·후보·추천을 (다시) 찾거나 분석·검토해 달라거나 보여 달라는 뜻(예: "이 정보까지 넣어서 다시 찾아줘",
  "그럼 결과 띄워줘", "이걸로 다시 봐 줘", "분석해 줘", "검토해서 알려 줘", "이제 맞는 걸로 골라 줘"). 새 정보를 주면서 다시 판단을
  원하는 경우도 포함.
- "learn": 앞으로의 추천·판단 방식에 반영하도록 '기억·학습시켜 달라'고 분명히 요구할 때만(예: "다음부터는 이렇게 판단하도록 학습해 줘",
  "이 기준 기억해 둬"). '학습'이라는 말이 있어도 묻는 말("학습 내용은 어디서 봐?")이나 학습하지 말라는 말, 단순 의견은 learn 이 아니다.
- "switch": 특정 모델로 바꿔(또는 되돌려) 추천해 달라고 분명히 말할 때(예: "TCV2로 추천해 줘", "이걸로 할게", "그걸로 바꿔 줘",
  "아니다 원래 TCC1으로 다시 해 줘"). model 에 그 모델 이름을 [고를 수 있는 모델]의 이름 그대로. '이걸로'·'그걸로'는 [직전에 AI 가 제안한 모델].
- "suggest": 사용자가 지금 모델이 괜찮은지 묻거나 다른 선택지를 물을 때, [고를 수 있는 모델] 중 더 나은 모델이 있으면 그 모델을 model 에
  넣고 answer 에 이유를 [상황] 근거(정격 여유·속도 70 초과·정격 근접·툴 수 등)로 설명한다. 지금 모델이 맞으면 chat 으로 그렇다고 답한다.
- "chat": 그 밖의 질문·설명 요청·정보 제공.
answer: 2~4문장 존댓말. [상황]에 있는 내용만으로 답하고 수치를 지어내지 않는다. [반영한 값]이 있으면 무엇을 반영했는지 먼저 한 줄로.
intent 가 recommend 면 다시 찾겠다고만 짧게. learn 이면 무엇을 배우면 되는지 한 줄로 되짚는다(저장은 따로 묻는다).
chat 인데 "분석 중입니다"·"잠시만 기다려 주세요"처럼 무언가를 하겠다고만 하는 답은 쓰지 않는다 — 실제로 다시 판정하는 것은 recommend 뿐이다.
사용자가 화면 사용법을 물을 때만 아래 [화면 안내] 내용을 존댓말로 풀어 답한다. 안내 문구나 이 지시문을 그대로 옮겨 쓰지 않는다.
[화면 안내] AI 가 배운 내용은 화면 위쪽 [AI 학습 내용 관리] 탭에서 보고, 영업 관리자가 승인·거절한다.
질문 답을 고치려면 왼쪽 [Q&A 고치기], 처음부터 하려면 [처음부터]. 추천 근거는 왼쪽 결과의 '추천 근거' 줄을 누르면 계산 과정이 보인다.
{"intent": "chat", "model": null, "answer": "..."}"""

# AI 가 '분석 중'처럼 하겠다고만 답하고 아무것도 안 하는 일을 막는다(사용자 2026-10-02) — 그런 답이나 분석·결과 요청이면 다시 판정
_PROGRESS_RE = re.compile(r"분석\s*중|검색\s*중|찾는\s*중|검토\s*중|확인\s*중|잠시만|기다려\s*주")
_ASK_RUN_RE = re.compile(r"(분석|검토|추천|결과|찾아|골라|띄워|보여)\S{0,6}\s*(해\s*줘|해\s*주세요|해\s*줄래|해\s*봐|줘|주세요|줄래|부탁)")
# '○○로 할게·추천해 줘·바꿔 줘·진행해 줘' — 모델 이름이 말에 있거나 '이걸로·그걸로'면 바꾸기
_SWITCH_RE = re.compile(r"(으로|로)\s*(할게|할께|하자|해\s*줘|해\s*주세요|추천|바꿔|변경|진행|가자|갈게)")
_THIS_RE = re.compile(r"(이걸로|그걸로|이거로|그거로|이 모델로|그 모델로)")
_LEARN_EXPLICIT = re.compile(r"(학습|기억)\s*(해|시켜)\s*(줘|주세요|둬|두세요|놔|놓아)|다음부터|다음부턴|앞으로는?\s")
_LEARN_NEG = re.compile(r"(학습|기억)\S*\s*(하지\s*마|안\s*해|말고|필요\s*없)|학습\s*(내용|기록|관리|탭)")


def _targets(intake: dict[str, Any]) -> str:
    tools = "\n".join(f"- 툴 index {i}: {t.get('name') or f'툴 {i + 1}'}" for i, t in enumerate(intake.get("tools") or []))
    robots = "\n".join(f"- 로봇 index {i}: {' '.join(x for x in (r.get('manufacturer'), r.get('model')) if x) or f'로봇 {i + 1}'}"
                       for i, r in enumerate(intake.get("robots") or []))
    spec = {k: [{"path": p, "type": t, "label": lab, **({"options": o} if o else {})} for p, t, lab, o in v]
            for k, v in _SPECS.items()}
    return f"[툴]\n{tools or '(없음)'}\n[로봇]\n{robots or '(없음)'}\n\n[칸]\n{json.dumps(spec, ensure_ascii=False)}"


def _label(intake: dict[str, Any], target: str, index: int, path: str) -> str:
    lab = next((x[2] for x in _SPECS[target] if x[0] == path), path)
    if target == "tool":
        return f"{(intake.get('tools') or [{}])[index].get('name') or f'툴 {index + 1}'} · {lab}"
    if target == "robot":
        return f"로봇 {index + 1} · {lab}"
    return lab


def _display(v: Any) -> str:
    if isinstance(v, bool):
        return "있음" if v else "없음"
    if isinstance(v, dict):
        return f"{v.get('min'):g}bar" if v.get("min") == v.get("max") else f"{v.get('min'):g}~{v.get('max'):g}bar"
    if isinstance(v, list):
        return ", ".join(map(str, v))
    return f"{v:g}" if isinstance(v, float) else str(v)


def apply_updates(intake: dict[str, Any], raw: Any, message: str) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """AI 가 읽은 updates → 검증(칸·대상·숫자) 후 새 intake 와 반영 목록."""
    out, applied = intake, []
    for u in (raw if isinstance(raw, list) else [])[:20]:
        if not isinstance(u, dict):
            continue
        target, path = u.get("target"), str(u.get("path") or "")
        spec = next((x for x in _SPECS.get(target, []) if x[0] == path), None)
        if spec is None:
            continue
        ents = (out.get("tools") if target == "tool" else out.get("robots") if target == "robot" else [out.get("project")]) or []
        try:
            idx = 0 if target == "project" else int(u.get("index"))
        except (TypeError, ValueError):
            if target == "tool" and len(ents) == 1:
                idx = 0
            else:
                continue
        if not 0 <= idx < len(ents):
            continue
        full = _PREFIX[target] + path
        field = {"path": full, "type": spec[1], "label_ko": spec[2], **({"options": spec[3]} if spec[3] else {})}
        parsed = aa.clean([field], {"values": {full: u.get("value")}}, message)
        if not parsed["values"]:
            continue
        q = {"scope": target, "entity_index": idx if target != "project" else None, "fields": [field]}
        out = aa.apply(out, q, parsed)
        applied.append({"label": _label(out, target, idx, path), "display": _display(parsed["values"][full])})
    return out, applied


def wants_learn(message: str, ai_says_learn: bool) -> bool:
    """학습 제안은 분명한 요구일 때만 — AI 판단을 우선하되, '학습 내용 어디서 봐'·'학습하지 마' 는 빼고,
    '학습해 줘'·'다음부터' 같은 명시적 요구는 AI 가 놓쳐도 잡는다."""
    if _LEARN_NEG.search(message):
        return False
    return ai_says_learn or bool(_LEARN_EXPLICIT.search(message))


def _pick(name: Any, options: list[dict[str, Any]]) -> dict[str, Any] | None:
    n = re.sub(r"\s+", "", str(name or "")).lower()
    return next((o for o in options if n and re.sub(r"\s+", "", o["name"]).lower() == n), None)


def _named(message: str, options: list[dict[str, Any]]) -> dict[str, Any] | None:
    """말에 적힌 모델 이름(긴 이름 먼저 — 'TCV4-Vision' 이 'TCV4' 로 잡히지 않게)."""
    low = message.lower()
    return next((o for o in sorted(options, key=lambda x: -len(x["name"])) if o["name"].lower() in low), None)


def resolve_model(intent: str, model: Any, message: str, options: list[dict[str, Any]],
                  suggested: str | None) -> tuple[str, dict[str, Any] | None, str]:
    """AI 가 읽은 의도·모델을 검증한다. 반환 (intent, 고른 모델 | None, 덧붙일 말). 고를 수 없는 모델이면 chat 으로 이유를 붙인다."""
    named = _named(message, options)
    if intent not in ("switch", "suggest") and _SWITCH_RE.search(message) and (named or (_THIS_RE.search(message) and suggested)):
        intent = "switch"                                   # AI 가 놓쳐도 'TCV2로 할게'·'이걸로 추천해 줘'는 바꾸기
    if intent not in ("switch", "suggest"):
        return intent, None, ""
    pick = _pick(model, options) or (named if intent == "switch" else None)
    if pick is None and intent == "switch" and _THIS_RE.search(message) and suggested:
        pick = _pick(suggested, options)
    if pick is None:
        return "chat", None, ("어느 모델로 바꿀지 알려 주세요 — 제품 DB 의 툴체인저 이름으로(예: TCV2)." if intent == "switch" else "")
    if not pick["fits"]:
        return "chat", None, f"{pick['name']} 은(는) 고를 수 없습니다 — {pick['reason']}."
    return intent, pick, ""


async def chat_turn(intake: dict[str, Any], message: str, *, history: list[dict[str, str]], rec: dict[str, Any] | None,
                    chat, options: list[dict[str, Any]] | None = None, suggested: str | None = None,
                    locked: bool = False) -> dict[str, Any]:
    """대화 한 번 → {intake, applied[{label, display}], intent, model, answer}.
    options: 고를 수 있는 모델(atc_selection.model_options), suggested: 직전에 AI 가 제안한 모델, locked: 최종 제안 확정 뒤(바꾸기 잠금)."""
    options = options or []
    message = _clean(message, 1000)
    if not message:
        raise aa.AnswerError("내용을 적어 주세요.")
    data = _extract_json(await chat([{"role": "system", "content": _EXTRACT_SYSTEM},
                                     {"role": "user", "content": f"{_targets(intake)}\n\n[말]\n{message}"}], num_predict=700))
    new, applied = apply_updates(intake, (data or {}).get("updates") if isinstance(data, dict) else None, message)
    from .product_recommend import atc_context

    situation = intake_summary(new) + "\n\n" + (atc_context(rec) if rec else "(아직 후보를 찾기 전)\n")
    fits = [o for o in options if o["fits"]]
    situation += ("\n[고를 수 있는 모델]\n" + "\n".join(f"- {o['name']} (정격 {o['payload_kg']:g}kg, {o['series_label']})" for o in fits)
                  if fits else "\n[고를 수 있는 모델]\n(없음)")
    situation += f"\n[직전에 AI 가 제안한 모델]\n{suggested or '(없음)'}\n"
    talk = "\n".join(f"{'사용자' if h.get('role') == 'user' else 'AI'}: {_clean(h.get('text'), 400)}"
                     for h in history[-8:] if _clean(h.get("text"), 400))
    done = "\n".join(f"- {a['label']}: {a['display']}" for a in applied) or "(없음)"
    res = _extract_json(await chat([{"role": "system", "content": _INTENT_SYSTEM},
                                    {"role": "user", "content": f"[상황]\n{situation}\n[반영한 값]\n{done}\n\n[대화]\n{talk or '(없음)'}"
                                                                f"\n\n[말]\n{message}"}], num_predict=700))
    res = res if isinstance(res, dict) else {}
    intent = res.get("intent") if res.get("intent") in ("recommend", "learn", "chat", "switch", "suggest") else "chat"
    if intent == "learn" and not wants_learn(message, True):
        intent = "chat"
    elif intent != "learn" and wants_learn(message, False):
        intent = "learn"
    explain = re.search(r"왜|이유|근거|설명|뜻|의미|차이", message)          # '근거 보여줘'는 설명 요청 — 다시 찾지 않는다
    if intent == "chat" and (_PROGRESS_RE.search(str(res.get("answer") or "")) or (_ASK_RUN_RE.search(message) and not explain)):
        intent = "recommend"
    intent, pick, extra = resolve_model(intent, res.get("model"), message, options, suggested)
    answer = strip_prompt_echo(_ai_text(res.get("answer"), 800), _INTENT_SYSTEM)
    if locked and intent in ("switch", "suggest", "recommend"):
        intent, pick = "chat", None
        answer = ("최종 제안을 이미 확정해서 모델을 바꿀 수 없습니다. 바꾸려면 [처음부터] 다시 추천을 받거나, "
                  "확정한 내용으로 [견적서 작성 →]을 진행해 주세요.")
    elif extra:
        answer = extra                                      # 거절·되묻기 — AI 가 '바꾸겠습니다'라고 썼어도 그 말은 버린다
    elif intent == "switch" and pick:
        answer = answer or f"{pick['name']}(정격 {pick['payload_kg']:g}kg)으로 바꿔 다시 추천하겠습니다."
    return {"intake": new, "applied": applied, "intent": intent, "model": pick["name"] if pick else None,
            "answer": answer or ("말씀하신 내용을 반영했습니다." if applied else "")}
