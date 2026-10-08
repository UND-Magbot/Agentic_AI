"""연차/휴가 메일 fast-path 파서 — 사용자 메시지 → compose/send_leave_email 인자.

expense_parser 와 같은 철학: LLM 을 거치지 않고 서버에서 결정론적으로 인자를 만든다.
Ollama(Gemma)는 named tool_choice 강제를 못 하므로, 연차 메일도 expense 처럼 fast-path 로
처리해 LLM tool-calling 의존을 없앤다(마이그레이션 가이드 §5).

파싱 실패(필수 날짜 없음 등) 시 None → 호출자는 기존 LLM 흐름으로 fallback.
인자 스키마는 tools._LEAVE_EMAIL_PARAMS 와 동일:
  date(YYYY-MM-DD, 필수), duration_days, start_time, end_time, reason,
  english_name, korean_name, report_kind(사용예정|사용일).
"""
from __future__ import annotations

import datetime as _dt
import logging
import re
from typing import Any

from .expense_parser import (
    extract_today_from_system,
    extract_user_name_from_system,
)

_log = logging.getLogger("leave_parser")

# 날짜: YYYY-MM-DD / YYYY.M.D / M/D / M월 D일.
_ISO_DATE_RE = re.compile(r"(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})")
_MD_SLASH_RE = re.compile(r"(?<!\d)(\d{1,2})\s*[./]\s*(\d{1,2})(?!\d)")
_MD_KO_RE = re.compile(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일")

# 상대 요일: '다음 주 수요일', '이번 주 금요일', '담주 월요일', 또는 그냥 '수요일'.
# (오늘/내일/모레는 별도 처리.) '요일' 글자가 있어야 매치 — 본문 다른 글자 오인 방지.
_WEEKDAY_RE = re.compile(
    r"(다음\s*주|담주|차주|이번\s*주|금주)?\s*([월화수목금토일])\s*요일"
)
_WD_INDEX = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}

# 기간: 'N일', 하루~닷새, 반차.
_DURATION_NUM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*일")
_KO_DURATION = {"하루": 1, "이틀": 2, "사흘": 3, "나흘": 4, "닷새": 5}
# 시간: '9시', '09:00', '9:30'.
_TIME_RE = re.compile(r"(\d{1,2})\s*(?::|시)\s*(\d{2})?")
# 사유: '사유[:는] X' 또는 'X 때문에/(으)로'. 최소한의 추출 — 실패 시 기본값.
_REASON_LABEL_RE = re.compile(r"사유\s*[:：]?\s*([^\n,/]{1,40})")

_DEFAULT_REASON = "개인사유"
_DEFAULT_START = "09:00"
_DEFAULT_END = "18:00"
_HALF_AM = ("09:00", "13:00")
_HALF_PM = ("14:00", "18:00")


def _parse_date(text: str, today: tuple[int, int, int] | None) -> str | None:
    """본문에서 시작일 추출 → YYYY-MM-DD. '오늘/내일/모레'는 today 기준 상대 계산."""
    base = _dt.date(*today) if today else _dt.date.today()
    if "모레" in text:
        d = base + _dt.timedelta(days=2)
        return d.isoformat()
    if "내일" in text:
        d = base + _dt.timedelta(days=1)
        return d.isoformat()
    if "오늘" in text:
        return base.isoformat()

    # 상대 요일 — '다음 주 수요일' / '이번 주 금요일' / '담주 월요일' / 그냥 '수요일'(다가오는 요일).
    wm = _WEEKDAY_RE.search(text)
    if wm:
        prefix = wm.group(1) or ""
        target_wd = _WD_INDEX[wm.group(2)]
        base_wd = base.weekday()
        if "다음" in prefix or "담주" in prefix or "차주" in prefix:
            monday = base - _dt.timedelta(days=base_wd) + _dt.timedelta(days=7)
            return (monday + _dt.timedelta(days=target_wd)).isoformat()
        if "이번" in prefix or "금주" in prefix:
            monday = base - _dt.timedelta(days=base_wd)
            return (monday + _dt.timedelta(days=target_wd)).isoformat()
        # 접두사 없음 → 오늘 포함 다가오는 해당 요일.
        days_ahead = (target_wd - base_wd) % 7
        return (base + _dt.timedelta(days=days_ahead)).isoformat()

    m = _ISO_DATE_RE.search(text)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = _MD_KO_RE.search(text) or _MD_SLASH_RE.search(text)
        if not m:
            return None
        mo, d = int(m.group(1)), int(m.group(2))
        y = base.year
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return None
    try:
        return _dt.date(y, mo, d).isoformat()
    except ValueError:
        return None


def _strip_dates(text: str) -> str:
    """날짜 표현(YYYY-MM-DD / M월D일 / M/D)을 제거한 잔여 텍스트.

    '3월 2일부터 3일'에서 날짜의 '2일'을 기간(3일)으로 오인하지 않도록, 기간/시간 파싱 전에
    날짜 토큰을 먼저 들어낸다.
    """
    t = _ISO_DATE_RE.sub(" ", text)
    t = _MD_KO_RE.sub(" ", t)
    t = _MD_SLASH_RE.sub(" ", t)
    return t


def _parse_duration_and_times(text: str) -> tuple[float, str, str]:
    """(duration_days, start_time, end_time). 반차/명시 시간/일수 처리, 없으면 기본값.

    주의: text 는 날짜가 제거된 잔여 텍스트여야 한다(날짜의 'D일'을 기간으로 오인 방지)."""
    # 반차 우선(시간·일수 기본을 덮어씀).
    if "오전" in text and "반차" in text:
        return 0.5, _HALF_AM[0], _HALF_AM[1]
    if "오후" in text and "반차" in text:
        return 0.5, _HALF_PM[0], _HALF_PM[1]
    if "반차" in text:
        return 0.5, _HALF_AM[0], _HALF_AM[1]

    duration: float = 1.0
    mnum = _DURATION_NUM_RE.search(text)
    if mnum:
        try:
            duration = float(mnum.group(1))
        except ValueError:
            duration = 1.0
    else:
        for word, n in _KO_DURATION.items():
            if word in text:
                duration = float(n)
                break

    # 명시 시간 범위 '9시부터 18시', '09:00~18:00' — 두 개 잡히면 start/end.
    times = _TIME_RE.findall(text)
    start, end = _DEFAULT_START, _DEFAULT_END
    if len(times) >= 2:
        def _fmt(t: tuple[str, str]) -> str:
            h = int(t[0]); mm = t[1] or "00"
            if 0 <= h <= 23:
                return f"{h:02d}:{mm}"
            return ""
        s, e = _fmt(times[0]), _fmt(times[1])
        if s and e:
            start, end = s, e
    return duration, start, end


def _parse_reason(text: str) -> str:
    m = _REASON_LABEL_RE.search(text)
    if m:
        r = m.group(1).strip().strip(".:：")
        # '사유' 라벨 뒤가 명령형 동사(작성해/보내)면 사유가 아님 — 기본값.
        if r and not re.search(r"(작성|발송|보내|만들|해줘|부탁)", r):
            return r[:40]
    return _DEFAULT_REASON


def _parse_report_kind(text: str) -> str:
    if re.search(r"(사용일|사후|어제|지난|썼|사용했)", text):
        return "사용일"
    return "사용예정"


def parse_leave_message(
    text: str,
    *,
    system_prompt: str = "",
) -> dict[str, Any] | None:
    """연차 메일 인자 dict 생성. 필수 날짜 추출 실패 시 None(→ LLM fallback)."""
    if not text or not text.strip():
        return None

    today = extract_today_from_system(system_prompt or "")
    date = _parse_date(text, today)
    if not date:
        return None  # 날짜 없으면 fast-path 부적합 → LLM 으로.

    # 날짜 토큰 제거 후 기간/시간 파싱(날짜의 'D일'을 기간으로 오인 방지).
    residual = _strip_dates(text)
    duration, start_time, end_time = _parse_duration_and_times(residual)
    reason = _parse_reason(text)
    report_kind = _parse_report_kind(text)
    korean_name = (extract_user_name_from_system(system_prompt or "") or "").strip()

    args: dict[str, Any] = {
        "date": date,
        "duration_days": duration,
        "start_time": start_time,
        "end_time": end_time,
        "reason": reason,
        "english_name": "",          # system prompt 에 영어이름이 없으면 빈값(빌더가 placeholder).
        "korean_name": korean_name,
        "report_kind": report_kind,
    }
    _log.info(
        "[leave_parser] OK — date=%s dur=%s %s~%s reason=%r kind=%s kor=%s",
        date, duration, start_time, end_time, reason, report_kind, korean_name,
    )
    return args


__all__ = ["parse_leave_message"]
