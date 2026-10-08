"""툴체인저 선정 — AI 와의 대화에서 받은 답을 질문 칸 값으로 옮긴다(사용자 2026-10-02, 영업부 팀장 요청).

질문지 Q&A 를 순서대로 진행하다 비워 둔 질문은 그 뒤 AI 와 대화하면서 AI 가 하나씩 묻는다. 버튼으로 고른 답은 화면이
바로 칸에 넣고, 사용자가 문장으로 답하면(예: "24V 0.6A 사양서 받았어요") 여기서 그 질문의 칸으로 옮긴다.
- 확실한 꼴(모름·있음/없음·숫자 하나)은 코드로 읽고, 나머지만 사내 AI 가 읽는다.
- 숫자는 답 문장에 적힌 것만 받는다(지어낸 수치 방지). 선택지 칸은 선택지 값만 받는다.
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any

from .atc_selection import _local
from .product_recommend import _extract_json

_UNKNOWN_RE = re.compile(r"^\s*(모름|몰라|모르겠|확인\s*필요|아직\s*몰|잘\s*모르)")
_YES_RE = re.compile(r"^\s*(있음|있어|있습니다|있어요|네|예|응|yes|필요|사용|씀|충분)", re.I)
_NO_RE = re.compile(r"^\s*(없음|없어|없습니다|없어요|아니|아뇨|no|필요\s*없|사용\s*안|안\s*씀|부족|해당\s*없)", re.I)
_NUM_RE = re.compile(r"-?\d+(?:[.,]\d+)?")

_SYSTEM = """너는 툴체인저 선정 질문에 대한 영업 담당자의 [답]을 정해진 [칸] 값으로 옮긴다. JSON 으로만 답한다.
- [답]에 있는 내용만 옮긴다. 답에 없는 칸은 values 에 넣지 않는다. 값을 지어내지 않는다.
- 숫자는 답에 적힌 숫자 그대로(단위 변환하지 않음). 선택지가 있는 칸은 선택지의 value 중 하나만.
- boolean 칸은 true/false. object(압력) 칸은 {"min": 숫자, "max": 숫자}(한 값이면 둘 다 같은 값).
- 글 칸(string·array·attachment)은 답의 해당 부분을 짧게 그대로.
- 답이 '모름·확인 필요'이면 unknown 에 그 칸 path 를 넣는다.
- 답이 질문과 상관없는 말(질문·잡담 등)이면 understood=false.
{"values": {"<path>": 값}, "unknown": ["<path>"], "understood": true}"""


class AnswerError(Exception):
    """답을 칸에 옮기지 못함(화면에 보여 줄 문구)."""


def _numbers(text: str) -> set[float]:
    return {float(n.replace(",", "")) for n in _NUM_RE.findall(text or "")}


_PRESSURE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:~|-|∼)?\s*(\d+(?:\.\d+)?)?\s*(MPa|kPa|bar)", re.I)


def pressure_numbers(text: str) -> set[float]:
    """말에 적힌 압력을 bar 로 — '0.6MPa'→6, '400kPa'→4, '0.4~0.5MPa'→4·5 (v1.2 PGR23 환산). 말에 있는 숫자와 함께 인정한다."""
    from .atc_accessory import to_bar

    out: set[float] = set()
    for a, b, unit in _PRESSURE_RE.findall(text or ""):
        for v in (a, b):
            if v:
                bar = to_bar(float(v), unit)
                if bar is not None:
                    out.add(bar)
    return out


def _options(field: dict[str, Any]) -> list[Any]:
    return [o["value"] if isinstance(o, dict) else o for o in field.get("options") or []]


def _quick(fields: list[dict[str, Any]], text: str) -> dict[str, Any] | None:
    """코드로 확실히 읽히는 꼴만: 모름 / 칸 하나짜리 있음·없음 / 칸 하나짜리 숫자."""
    if _UNKNOWN_RE.match(text):
        return {"values": {}, "unknown": [f["path"] for f in fields], "understood": True}
    if len(fields) != 1:
        return None
    f = fields[0]
    if f["type"] == "boolean":
        if _NO_RE.match(text):
            return {"values": {f["path"]: False}, "unknown": [], "understood": True}
        if _YES_RE.match(text):
            return {"values": {f["path"]: True}, "unknown": [], "understood": True}
    if f["type"] in ("number", "integer"):
        nums = _NUM_RE.findall(text)
        if len(nums) == 1:
            n = float(nums[0].replace(",", ""))
            return {"values": {f["path"]: int(n) if f["type"] == "integer" else n}, "unknown": [], "understood": True}
    return None


def clean(fields: list[dict[str, Any]], data: dict[str, Any], text: str) -> dict[str, Any]:
    """AI 가 읽은 값 검증: 질문 칸만, 선택지 값만, 답에 적힌 숫자만."""
    by_path = {f["path"]: f for f in fields}
    nums = _numbers(text)
    values: dict[str, Any] = {}
    for path, v in (data.get("values") or {}).items():
        f = by_path.get(path)
        if f is None or v is None or v == "":
            continue
        t = f["type"]
        if t == "enum":
            if v in [o for o in _options(f) if o is not None]:
                values[path] = v
        elif t == "boolean":
            if isinstance(v, bool):
                values[path] = v
        elif t in ("number", "integer"):
            try:
                n = float(v)
            except (TypeError, ValueError):
                continue
            if n in nums:
                values[path] = int(n) if t == "integer" else n
        elif t == "object" and path.endswith("pressure_bar"):
            if isinstance(v, dict):
                lo, hi = v.get("min"), v.get("max")
                try:
                    lo = float(lo) if lo is not None else None
                    hi = float(hi) if hi is not None else lo
                except (TypeError, ValueError):
                    continue
                ok = nums | pressure_numbers(text)
                if lo is not None and lo in ok and (hi is None or hi in ok):
                    values[path] = {"min": lo, "max": hi if hi is not None else lo}
        elif t == "array":
            items = v if isinstance(v, list) else [v]
            items = [str(x).strip()[:200] for x in items if str(x).strip()]
            if items:
                values[path] = items
        else:
            s = str(v).strip()[:300]
            if s:
                values[path] = s
    unknown = [p for p in data.get("unknown") or [] if p in by_path and p not in values]
    return {"values": values, "unknown": unknown, "understood": bool(values or unknown) and data.get("understood", True) is not False}


def _set(obj: dict[str, Any], path: str, value: Any) -> None:
    *head, last = path.split(".")
    cur = obj
    for k in head:
        if not isinstance(cur.get(k), dict):
            cur[k] = {}
        cur = cur[k]
    cur[last] = value


def apply(intake: dict[str, Any], question: dict[str, Any], parsed: dict[str, Any]) -> dict[str, Any]:
    """읽은 값을 질문 대상(툴·로봇·프로젝트)에 넣은 새 intake. '모름'은 <칸>__unknown=True."""
    out = copy.deepcopy(intake)
    scope = question.get("scope")
    if scope == "project":
        entity = out.setdefault("project", {})
    else:
        key = "tools" if scope == "tool" else "robots"
        ents = out.get(key) or []
        i = question.get("entity_index") or 0
        if i >= len(ents):
            raise AnswerError("질문 대상을 찾지 못했습니다. 질문지를 다시 확인해 주세요.")
        entity = ents[i]
    for path, v in parsed["values"].items():
        _set(entity, _local(path), v)
        _set(entity, f"{_local(path)}__unknown", False)
    for path in parsed["unknown"]:
        _set(entity, _local(path), None)
        _set(entity, f"{_local(path)}__unknown", True)
    return out


async def read_answer(question: dict[str, Any], text: str, *, chat) -> dict[str, Any]:
    """질문 + 사용자의 문장 답 → {values, unknown, understood}."""
    text = (text or "").strip()[:1000]
    fields = [f for f in question.get("fields") or [] if isinstance(f, dict) and f.get("path")]
    if not text or not fields:
        raise AnswerError("답을 적어 주세요.")
    quick = _quick(fields, text)
    if quick is not None:
        return quick
    spec = [{"path": f["path"], "type": f["type"], "label": f.get("label_ko"), "unit": f.get("unit"),
             **({"options": _options(f)} if f.get("options") else {})} for f in fields]
    user = (f"[질문]\n{question.get('question_ko', '')}\n대상: {question.get('entity_label') or '프로젝트'}\n\n"
            f"[칸]\n{json.dumps(spec, ensure_ascii=False)}\n\n[답]\n{text}")
    data = _extract_json(await chat([{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}],
                                    num_predict=600))
    if not isinstance(data, dict):
        return {"values": {}, "unknown": [], "understood": False}
    return clean(fields, data, text)
