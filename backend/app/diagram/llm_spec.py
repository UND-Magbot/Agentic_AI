"""자연어 요청 → 개념도 스펙(JSON) 생성.

LLM 에게 "그림" 을 그리게 하지 않는다. 그리게 하면 12B 모델은 좌표를 못 맞춘다.
대신 "무엇이 어떤 순서로 있고 어디를 보는가" 만 구조화해서 뱉게 하고,
픽셀은 layout.py + render.py 가 결정론적으로 찍는다.

안정화 장치 세 가지:
  1) Ollama `format: "json"` 으로 JSON 외 토큰 자체를 차단
  2) Pydantic 검증 실패 시 오류 메시지를 그대로 되먹여 재시도
  3) 참조 무결성 자동 교정 — 존재하지 않는 station 을 가리키는 카메라/흐름 제거
"""
from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from .. import proposal_llm
from .spec import ConceptMapSpec

STATION_KINDS = [
    "conveyor", "capper", "robot", "filler", "tank", "reject", "outfeed", "table",
]
SYS_NODE_KINDS = ["camera", "ipc", "plc", "controller", "hmi"]

_SCHEMA_GUIDE = """
출력은 아래 구조의 JSON 객체 하나뿐이다. 주석·설명·markdown 코드펜스 금지.

{
  "title": "개념도 제목",
  "subtitle": "우측 상단 한 줄 요약 (선택)",
  "process_steps": [                      // 상단 공정 카드. 5~9개 권장
    {"title": "단계명", "bullets": ["설명 1", "설명 2"],
     "image": {"symbol": "conveyor"},
     "badge": "선택 (예: 주입시간\\n210초)", "badge_tone": "amber|green|blue|"}
  ],
  "line_layout": {
    "stations": [                          // 왼→오른쪽 설비 배치
      {"id": "infeed", "kind": "conveyor", "label": "투입 존",
       "sublabel": "(반제 적치)", "width": 1.15, "show_zone_label": true}
    ],
    "cameras": [                           // 비전 카메라. at 은 반드시 station id
      {"id": "v1", "at": "infeed", "label": "비전 1",
       "caption": "(정렬/자세 확인)", "offset": 0.3, "cone": true}
    ],
    "callouts": [                          // 설비 사양 말풍선
      {"at": "rbt", "offset": 0.5, "lines": ["협동로봇", "가반중량 : 14 kg"]}
    ],
    "flows": [                             // 흐름 화살표. src/dst 는 station id
      {"src": "infeed", "dst": "cap", "style": "solid", "label": ""}
    ],
    "outfeed_label": "완제품 배출"
  },
  "inspections": [                         // 좌하단 검사 항목 카드
    {"title": "비전 1 : 투입/자세", "detail": "짧은 설명",
     "image": {"symbol": "conveyor"}}
  ],
  "system_diagram": {                      // 중하단 구성도. col 은 좌→우 단계
    "nodes": [{"id": "c1", "label": "비전 카메라", "sublabel": "4 EA",
               "kind": "camera", "col": 0}],   // kind: camera|ipc|plc|controller|hmi
    "edges": [{"src": "c1", "dst": "ipc", "label": "영상"}]
  },
  "tables": [                              // 우하단 표
    {"title": "예상 Cycle Time", "headers": ["공정", "시간"],
     "rows": [["투입 · 정렬", "10 ~ 15 초"]], "accent": true}
  ],
  "footnote": "※ 단서 조항 (선택)"
}

규칙
- stations[].kind 와 image.symbol 은 반드시 다음 중 하나:
  conveyor, capper, robot, filler, tank, reject, outfeed, table
  (세척기·건조기·검사대처럼 딱 맞는 것이 없으면 table 을 쓴다)
- system_diagram.nodes[].kind 는 반드시 다음 중 하나:
  camera, ipc, plc, controller, hmi  (PC·서버류는 ipc)
- badge_tone 은 amber, green, blue, 빈 문자열 중 하나.
- 사진은 시스템이 나중에 붙이므로 symbol 만 지정한다.
- station id 는 영문 소문자 짧은 단어(infeed, cap, rbt, fill, tank, rej, out).
- cameras[].at, flows[].src, flows[].dst 는 stations 에 실제 존재하는 id 여야 한다.
- offset 은 0.0~1.0 (해당 설비 안에서의 가로 위치).
- width 는 설비의 가로 비중(0.6~1.4). 컨베이어·배출부는 넓게, 탱크는 좁게.
- 실패/불량 경로는 style="dashed" 로 표시한다.
- 수치를 모르면 표에 넣지 말고, 지어내지 않는다.
- 모든 텍스트는 한국어.

★ 자주 틀리는 것 — 반드시 지킬 것
1. 비전/카메라는 **절대 stations 에 넣지 않는다.** 카메라는 설비가 아니라 설비를 내려다보는
   장치다. 반드시 cameras[] 에만 넣고, at 에는 그 카메라가 감시하는 설비의 id 를 적는다.
   (틀린 예: stations 에 {"id":"v1","kind":"table","label":"비전 1"} → 라인에 빈 작업대가 생긴다)
2. 설비 대수를 늘리지 않는다. 로봇 1대가 여러 공정을 오가면 **station 은 robot 1개**다.
   "로봇 1", "로봇 2" 로 쪼개지 말 것.
3. 같은 설비를 두 번 방문하는 공정(예: 캐핑에서 뚜껑을 풀고 → 주입 후 → 캐핑으로 돌아와 잠금)
   이면 **station 은 1개만** 만들고, 왕복은 flows 로 표현한다.
   sublabel 에 "(뚜껑 풀기/잠그기)" 처럼 두 역할을 함께 적는다.
4. stations 는 실제 물리 설비만: 투입부, 캐핑기, 로봇, 주입장비, 탱크, 리젝부, 배출부.

다음은 올바른 line_layout 예시다. 이 형태를 따른다.
{
  "stations": [
    {"id":"infeed","kind":"conveyor","label":"투입 존","sublabel":"(반제 적치)","width":1.15},
    {"id":"cap","kind":"capper","label":"캐핑 존","sublabel":"(뚜껑 풀기/잠그기)","width":1.0},
    {"id":"rbt","kind":"robot","label":"","width":1.15,"show_zone_label":false},
    {"id":"fill","kind":"filler","label":"약액 주입 존","sublabel":"(주입시간: 210초)","width":1.1},
    {"id":"rej","kind":"reject","label":"","width":0.72,"show_zone_label":false},
    {"id":"out","kind":"outfeed","label":"","width":1.25,"show_zone_label":false}
  ],
  "cameras": [
    {"id":"v1","at":"infeed","label":"비전 1","caption":"(정렬/자세 확인)","offset":0.3,"cone":true},
    {"id":"v2","at":"fill","label":"비전 2","caption":"(주입 전 X·Y 확인)","offset":0.46,"cone":true},
    {"id":"v3","at":"cap","label":"비전 3","caption":"(캡 체결 확인)","offset":0.6,"cone":true}
  ],
  "callouts": [{"at":"rbt","offset":0.5,"lines":["협동로봇","가반중량 : 14 kg"]}],
  "flows": [
    {"src":"infeed","dst":"cap","style":"solid","label":""},
    {"src":"cap","dst":"fill","style":"solid","label":"로봇 이송"},
    {"src":"fill","dst":"rej","style":"dashed","label":"주입 실패 시"},
    {"src":"fill","dst":"cap","style":"dashed","label":"주입 후 캐핑 복귀"},
    {"src":"cap","dst":"out","style":"solid","label":""}
  ],
  "outfeed_label": "완제품 배출"
}
"""

_SYSTEM = (
    "당신은 자동화 설비 제안서의 공정 개념도를 설계하는 엔지니어다. "
    "사용자의 공정 설명을 읽고, 그것을 개념도 스펙 JSON 으로 옮긴다.\n"
    + _SCHEMA_GUIDE
)


def _extract_json(raw: str) -> dict[str, Any]:
    """모델 출력에서 JSON 객체를 꺼낸다. 코드펜스가 섞여도 견딘다."""
    s = raw.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.MULTILINE).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    start = s.find("{")
    end = s.rfind("}")
    if start >= 0 and end > start:
        return json.loads(s[start : end + 1])
    raise ValueError("JSON 객체를 찾지 못했습니다.")


_CAM_NAME_RE = re.compile(r"^\s*(비전|카메라|vision|camera)", re.IGNORECASE)


def _dissolve_camera_stations(line: dict[str, Any], dropped: list[str]) -> None:
    """라인에 설비로 잘못 끼워 넣은 '비전 N' 을 카메라로 되돌린다.

    12B 모델이 프롬프트 경고에도 가장 끈질기게 반복하는 오류다. 그대로 두면
    개념도 한가운데 빈 작업대가 늘어서고 흐름이 카메라를 거쳐 가는 그림이 된다.
    제거하면서 앞뒤 흐름을 직접 이어 붙이고, 카메라 목록에 없으면 승격시킨다.
    """
    stations = line.get("stations") or []
    cams = line.get("cameras") or []
    flows = line.get("flows") or []

    victims = [
        s for s in stations
        if isinstance(s, dict)
        and s.get("kind") in ("table", "conveyor")
        and _CAM_NAME_RE.match(str(s.get("label") or ""))
    ]
    if not victims:
        return

    known = {str(c.get("label")) for c in cams if isinstance(c, dict)}
    for v in victims:
        vid = v.get("id")
        ins = [f for f in flows if f.get("dst") == vid]
        outs = [f for f in flows if f.get("src") == vid]

        # 카메라를 건너뛰고 앞 설비 → 뒤 설비를 직접 잇는다.
        for i in ins:
            for o in outs:
                if i.get("src") != o.get("dst"):
                    flows.append({
                        "src": i.get("src"), "dst": o.get("dst"),
                        "style": i.get("style", "solid"), "label": i.get("label", ""),
                    })
        flows = [f for f in flows if vid not in (f.get("src"), f.get("dst"))]

        label = str(v.get("label") or "비전")
        if label not in known:
            anchor = (ins[0].get("src") if ins
                      else outs[0].get("dst") if outs else None)
            if anchor:
                cams.append({
                    "id": vid, "at": anchor, "label": label,
                    "caption": str(v.get("sublabel") or ""),
                    "offset": 0.5, "cone": True,
                })
                known.add(label)
        stations = [s for s in stations if s.get("id") != vid]
        dropped.append(f"'{label}' 을 설비에서 비전 카메라로 전환")

    # 우회 연결 과정에서 생긴 중복 흐름 제거.
    seen: set[tuple] = set()
    uniq = []
    for f in flows:
        key = (f.get("src"), f.get("dst"), f.get("style"))
        if key not in seen:
            seen.add(key)
            uniq.append(f)

    line["stations"] = stations
    line["cameras"] = cams
    line["flows"] = uniq


def repair(data: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """참조 무결성 자동 교정. 버린 항목을 함께 돌려준다.

    LLM 이 가장 자주 내는 오류가 "존재하지 않는 station 을 가리키는 카메라/흐름" 이다.
    스펙을 통째로 버리는 대신 어긋난 항목만 떼어내 그림은 살린다.
    """
    dropped: list[str] = []

    # 열거형 값은 모델이 가장 자주 벗어나는 지점이다. 버리지 말고 가까운 값으로 끌어온다.
    def _fix_symbol(holder: Any, where: str) -> None:
        if not isinstance(holder, dict):
            return
        img = holder.get("image")
        if isinstance(img, dict) and img.get("symbol") and img["symbol"] not in STATION_KINDS:
            dropped.append(f"{where} 심볼 '{img['symbol']}' → table 로 대체")
            img["symbol"] = "table"

    for step in data.get("process_steps") or []:
        _fix_symbol(step, "공정 카드")
        if isinstance(step, dict) and step.get("badge_tone") not in ("amber", "green", "blue", "", None):
            step["badge_tone"] = "blue"
    for it in data.get("inspections") or []:
        _fix_symbol(it, "검사 항목")

    sysd = data.get("system_diagram") or {}
    for n in sysd.get("nodes") or []:
        if isinstance(n, dict) and n.get("kind") not in SYS_NODE_KINDS:
            dropped.append(f"구성도 노드 '{n.get('label')}' 의 kind={n.get('kind')} → ipc 로 대체")
            n["kind"] = "ipc"

    line = data.get("line_layout") or {}
    _dissolve_camera_stations(line, dropped)
    stations = line.get("stations") or []
    ids = {s.get("id") for s in stations if isinstance(s, dict)}

    for st in stations:
        if isinstance(st, dict) and st.get("kind") not in STATION_KINDS:
            dropped.append(f"설비 '{st.get('id')}' 의 kind={st.get('kind')} → table 로 대체")
            st["kind"] = "table"
        _fix_symbol(st, f"설비 '{st.get('id') if isinstance(st, dict) else '?'}'")

    cams = [c for c in (line.get("cameras") or []) if c.get("at") in ids]
    if len(cams) != len(line.get("cameras") or []):
        dropped.append("존재하지 않는 설비를 가리키는 카메라 제거")
    line["cameras"] = cams

    cos = [c for c in (line.get("callouts") or []) if c.get("at") in ids]
    if len(cos) != len(line.get("callouts") or []):
        dropped.append("존재하지 않는 설비를 가리키는 말풍선 제거")
    line["callouts"] = cos

    flows = [
        f for f in (line.get("flows") or [])
        if f.get("src") in ids and f.get("dst") in ids
    ]
    if len(flows) != len(line.get("flows") or []):
        dropped.append("끊어진 흐름 화살표 제거")
    line["flows"] = flows

    if stations:
        data["line_layout"] = line
    return data, dropped


async def _call(messages: list[dict[str, str]]) -> str:
    # 제안서 전용 모델·사고 모드·일시 오류 자동 재시도는 proposal_llm 이 맡는다.
    # format="json" 으로 JSON 외 토큰 차단 — 파싱 실패율이 급감한다.
    # 사고 모드는 생각에만 수천 토큰을 쓴다 — 8K 로는 본문을 쓰기 전에 한도에 걸린다(실측).
    return await proposal_llm.chat(messages, fmt="json", temperature=0.2, num_ctx=16384)


async def generate_spec(
    request: str,
    *,
    context: str = "",
    max_retries: int = 2,
    revise: tuple[ConceptMapSpec, str] | None = None,
) -> tuple[ConceptMapSpec, list[str]]:
    """자연어 요청 → 검증된 ConceptMapSpec.

    Args:
        request: "약액 주입 공정 개념도 그려줘. 협동로봇 14kg, 비전 3대" 같은 요청.
        context: RAG 로 끌어온 과거 제안서·설비 사양 텍스트 (선택).
        revise: (직전 스펙, 고칠 점). 주면 처음부터 새로 짜지 않고 직전 스펙을 고치게 한다.

    Returns:
        (spec, notes) — notes 는 자동 교정 내역.
    """
    user = request if not context else f"참고 자료:\n{context}\n\n---\n요청: {request}"
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": user},
    ]
    if revise is not None:
        prev, feedback = revise
        messages += [
            {"role": "assistant", "content": prev.model_dump_json()},
            {"role": "user", "content": f"{feedback}\n\n고친 전체 JSON 을 다시 출력하세요. JSON 만 출력합니다."},
        ]
    base = len(messages)

    last_err = ""
    for attempt in range(max_retries + 1):
        raw = await _call(messages)
        try:
            data = _extract_json(raw)
            data, notes = repair(data)
            return ConceptMapSpec.model_validate(data), notes
        except (ValueError, ValidationError) as e:
            last_err = str(e)[:1200]
            if attempt == max_retries:
                break
            # 오류를 그대로 되먹여 같은 실수를 반복하지 않게 한다.
            messages = messages[:base] + [
                {"role": "assistant", "content": raw[:2000]},
                {
                    "role": "user",
                    "content": (
                        f"위 JSON 이 스키마 검증에 실패했습니다:\n{last_err}\n\n"
                        "오류를 고쳐 전체 JSON 을 다시 출력하세요. JSON 만 출력합니다."
                    ),
                },
            ]
    raise ValueError(f"개념도 스펙 생성 실패 ({max_retries + 1}회 시도): {last_err}")
