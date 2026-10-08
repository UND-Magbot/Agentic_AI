# -*- coding: utf-8 -*-
"""화면 문구에서 내부 규칙 번호(R02·C11·P04·J07·F02·E01·O03·PGR03 …)를 뺀다(사용자 2026-10-06: 개발자만 아는 표기는 화면에 안 보이게).

규칙 번호는 선정 JSON(v0.2·v0.8)의 항목 id 다. 계산·기록에는 그대로 쓰고(rule·ref·id 칸), 사람이 읽는 문장에서만 뺀다.
    "무선 TCW1 은 … 검토(C09 유선 기본)"   → "무선 TCW1 은 … 검토(유선 기본)"
    "속도 단위·기준(P01) 확정 전"           → "속도 단위·기준 확정 전"
    "… 비교 후 담당자 확정(C11, 자동 상향 아님)" → "… 비교 후 담당자 확정(자동 상향 아님)"
제품·로봇 모델명(TCV1·MG16·UR10e·M1013·S600)은 앞에 영문이 붙거나 숫자가 두 자리가 아니라 건드리지 않는다.
"""
from __future__ import annotations

import re
from typing import Any

# 뒤에 '-숫자'가 오면 규칙 번호가 아니라 건 번호·견적번호(S26-0182) — 그대로 둔다(사용자 2026-10-07: 미팅 정보에 '-0182'로 보임)
# PGR = 액세서리 통합 선정 v1.2 규칙(PGR01~PGR24)
_CODE = r"(?<![A-Za-z0-9\-])(?:PGR|JF|[QRTCFUJAPOGSE])\d{2}(?![0-9A-Za-z]|-\d)"
_LIST = rf"{_CODE}(?:\s*[·,/~]\s*{_CODE})*"
_STEPS = [
    (re.compile(rf"\s*\(\s*{_LIST}\s*\)"), ""),                      # "(R02)" "(C05·O03)"
    (re.compile(rf"\(\s*{_LIST}\s*[,:·—\-]?\s*"), "("),              # "(C09 유선 기본)" "(C11, 자동 상향 아님)"
    (re.compile(rf"\s*[,·]\s*{_LIST}\s*\)"), ")"),                   # "(…, R09)"
    (re.compile(rf"{_LIST}\s*[—:]\s*"), ""),                          # "R09 — 내부 검토"
    (re.compile(rf"\s*{_LIST}"), ""),                                 # 남은 낱개 "잠정 P01"
    (re.compile(r"\(\s*\)"), ""),                                     # 빈 괄호
    (re.compile(r"\s+([,.)·])"), r"\1"),
    (re.compile(r"\(\s+"), "("),
    (re.compile(r"[ \t]{2,}"), " "),
]
_FULL = re.compile(rf"\s*{_LIST}\s*")

# 사람이 읽는 문장이 아닌 칸(계산·요청에 다시 쓰이는 값) — 건드리지 않는다
SKIP_KEYS = frozenset({"id", "ids", "rule", "ref", "refs", "key", "path", "status", "series", "code", "kind", "intent",
                       "intake", "meeting", "history", "request", "followups", "followups_answered", "question_id",
                       "entity_path", "target", "model", "name", "card_id", "image_id", "unit",
                       "deal_no", "quote_no", "no", "file_name"})


def hide_codes(text: str, *, strip: bool = True) -> str:
    """사람이 읽는 문장 → 규칙 번호를 뺀 문장. 문장 전체가 번호뿐이면(값으로 쓰는 id) 그대로 둔다.
    strip=False 는 흘려 보내는 조각용(앞뒤 공백을 지우면 조각끼리 단어가 붙는다)."""
    if not text or _FULL.fullmatch(text):
        return text
    out = text
    for pat, rep in _STEPS:
        out = pat.sub(rep, out)
    if out == text:
        return text
    return out.strip() if strip else out


def scrub(obj: Any, _key: str | None = None) -> Any:
    """API 응답(dict·list) 안의 문장들에서 규칙 번호를 뺀다. SKIP_KEYS 칸과 그 아래는 그대로."""
    if _key in SKIP_KEYS:
        return obj
    if isinstance(obj, str):
        return hide_codes(obj)
    if isinstance(obj, dict):
        return {k: scrub(v, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub(v, _key) for v in obj]
    return obj
