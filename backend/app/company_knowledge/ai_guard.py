# -*- coding: utf-8 -*-
"""AI 답에서 지시문(시스템 프롬프트)을 그대로 베낀 부분과 내부 규칙 번호(R02·C11 …)를 걸러 낸다.

사내 AI 가 프롬프트의 안내 문구를 답 끝에 그대로 붙이는 일이 있었다(사용자 2026-10-06:
"화면 안내(물으면 이대로 답한다): AI 가 배운 내용은 …"). 지시문은 '~한다.'로 끝나는 평서문이고
답은 존댓말이라, **존댓말로 끝나지 않으면서 지시문과 긴 구간이 겹치는 문장**을 베낀 문장으로 본다.
화면 사용법을 물어 AI 가 존댓말로 풀어 답한 문장("왼쪽 [Q&A 고치기]를 누르시면 됩니다.")은 남는다.
"""
from __future__ import annotations

import difflib
import re

from .plain_text import hide_codes

ECHO_MIN_CHARS = 15   # 지시문과 이만큼 이어서 같으면 베낀 것으로 본다(짧은 용어 겹침은 제외)

# 답 안에 남은 지시문 표지 — '화면 안내(물으면 이대로 답한다):' 같은 머리말
_MARKER_RE = re.compile(r"\[?화면\s*안내\]?\s*(?:\([^)]*\))?\s*:?|\((?:물|묻)으면 이대로 답한다\)")
# 프롬프트 안쪽 칸 이름 — 답에 '[상황]에 따르면'처럼 나오면 괄호를 벗긴다(화면 버튼 이름 [Q&A 고치기] 등은 그대로)
_LABEL_RE = re.compile(r"\[(상황|말|대화|칸|툴|로봇|버전|반영한 값|조건|현재 추천|회사 제품|후보 순서|고를 수 있는 모델|"
                       r"직전에 AI 가 제안한 모델)\]")
_POLITE_END = re.compile(r"(니다|세요|까요|어요|아요|에요|예요|해요|죠|요)\s*[.!?…]*[\"')\]]*\s*$")
_SENT_SPLIT = re.compile(r"(?<=[.!?。])\s+|\n+")


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _overlap(sentence: str, system: str) -> int:
    a, b = _squash(sentence), _squash(system)
    m = difflib.SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b))
    return m.size


def is_echo(sentence: str, system: str) -> bool:
    s = sentence.strip()
    if not s:
        return False
    if re.search(r"(물|묻)으면 이대로 답한다", s):
        return True
    return not _POLITE_END.search(s) and _overlap(s, system) >= ECHO_MIN_CHARS


def strip_prompt_echo(answer: str | None, system: str) -> str:
    """답 → 지시문을 베낀 문장을 빼고, 남은 머리말·칸 이름 괄호를 정리한 답(전부 베낀 것이면 빈 문자열)."""
    if not answer:
        return ""
    sents = [s.strip() for s in _SENT_SPLIT.split(answer) if s.strip()]
    kept = [s for s in sents if not is_echo(s, system)]
    if len(kept) == len(sents) and not _MARKER_RE.search(answer) and not _LABEL_RE.search(answer):
        return hide_codes(answer.strip())  # 거를 것이 없으면 줄바꿈까지 그대로(규칙 번호만 뺀다)
    text = " ".join(_MARKER_RE.sub("", s).strip() for s in kept)
    text = _LABEL_RE.sub(lambda m: m.group(1), text)
    return hide_codes(re.sub(r"\s{2,}", " ", text).strip())
