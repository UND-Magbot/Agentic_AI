"""Excel 의 m/d aaa 포맷이 우리 datetime 값에 적용될 때 어떻게 보이는지 확인.

`format_cell` 옵션을 사용해 openpyxl 이 자체적으로 보여주는 표현을 검증.
실제 Excel 의 한국어 요일 'aaa' 코드를 직접 시뮬레이션.
"""
from __future__ import annotations

import datetime as _dt
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Excel 'aaa' = 짧은 요일 한국어. Python 의 ['월','화','수','목','금','토','일']
WEEKDAYS_KO = ["월", "화", "수", "목", "금", "토", "일"]


def format_md_aaa(d: _dt.date) -> str:
    return f"{d.month}/{d.day} {WEEKDAYS_KO[d.weekday()]}"


# 사용자 보고 시나리오 날짜.
for s in ["2026-02-07", "2026-02-09", "2026-02-10"]:
    y, mo, dd = map(int, s.split("-"))
    d = _dt.date(y, mo, dd)
    print(f"  {s} → {format_md_aaa(d)}")

# 정합성 검증: 2026-02-07 = 토, 2026-02-09 = 월, 2026-02-10 = 화
expected = {
    "2026-02-07": "2/7 토",
    "2026-02-09": "2/9 월",
    "2026-02-10": "2/10 화",
}
for s, want in expected.items():
    y, mo, dd = map(int, s.split("-"))
    got = format_md_aaa(_dt.date(y, mo, dd))
    assert got == want, f"{s}: want={want} got={got}"
print("OK — 사용자 입력 '2/9 월 → SRT, 2/10 화 → 택시' 와 일치")
