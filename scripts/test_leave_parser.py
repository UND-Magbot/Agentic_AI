"""연차 메일 fast-path 파서 단위 검증 — vLLM/인프라 불필요(순수 함수)."""
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.leave_parser import parse_leave_message  # noqa: E402

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []


def check(cond: bool, label: str) -> None:
    (PASS if cond else FAIL).append(label if cond else (label, "expected truthy"))  # type: ignore


def eq(actual, expected, label: str) -> None:
    if actual == expected:
        PASS.append(label)
    else:
        FAIL.append((label, f"got {actual!r} != {expected!r}"))


SYS = "- 현재 날짜: 2026-02-15 (YYYY-MM-DD).\n- 현재 사용자(작성자 후보): 홍길동."

# 1) 기본 — 명시 날짜 + 작성 요청
a = parse_leave_message("2026-02-20 연차 메일 작성해줘", system_prompt=SYS)
check(a is not None, "기본 파싱 성공")
eq(a and a["date"], "2026-02-20", "기본.date")
eq(a and a["duration_days"], 1.0, "기본.기본일수=1")
eq(a and a["start_time"], "09:00", "기본.start 기본")
eq(a and a["end_time"], "18:00", "기본.end 기본")
eq(a and a["reason"], "개인사유", "기본.reason 기본")
eq(a and a["korean_name"], "홍길동", "기본.korean_name from system")
eq(a and a["report_kind"], "사용예정", "기본.report_kind 기본")

# 2) M월 D일 + 3일 + 사유
b = parse_leave_message("3월 2일부터 3일 연차 쓸게요. 사유: 가족 여행", system_prompt=SYS)
eq(b and b["date"], "2026-03-02", "월일.date")
eq(b and b["duration_days"], 3.0, "월일.duration=3")
eq(b and b["reason"], "가족 여행", "월일.reason 추출")

# 3) M/D + 오전 반차
c = parse_leave_message("2/18 오전 반차 메일 만들어줘", system_prompt=SYS)
eq(c and c["date"], "2026-02-18", "반차.date")
eq(c and c["duration_days"], 0.5, "반차.duration=0.5")
eq(c and c["start_time"], "09:00", "오전반차.start")
eq(c and c["end_time"], "13:00", "오전반차.end")

# 4) 오후 반차
d = parse_leave_message("2/18 오후 반차 연차 작성", system_prompt=SYS)
eq(d and d["start_time"], "14:00", "오후반차.start")
eq(d and d["end_time"], "18:00", "오후반차.end")

# 5) 상대 날짜 '내일'
e = parse_leave_message("내일 연차 메일 작성해줘", system_prompt=SYS)
eq(e and e["date"], "2026-02-16", "내일=today+1")

# 6) 이틀(한국어 일수)
f = parse_leave_message("2026-02-20 이틀 휴가 보고 메일 작성", system_prompt=SYS)
eq(f and f["duration_days"], 2.0, "이틀=2")

# 7) 명시 시간 범위
g = parse_leave_message("2026-02-20 10시부터 17시까지 연차 작성", system_prompt=SYS)
eq(g and g["start_time"], "10:00", "명시시간.start")
eq(g and g["end_time"], "17:00", "명시시간.end")

# 8) report_kind 사용일(사후)
h = parse_leave_message("어제 2026-02-14 연차 썼는데 보고 메일 작성", system_prompt=SYS)
eq(h and h["report_kind"], "사용일", "사후=사용일")

# 9) 날짜 없음 → None(fallback)
n = parse_leave_message("연차 메일 작성해줘", system_prompt=SYS)
check(n is None, "날짜없음 → None(LLM fallback)")

# 10) 사유 라벨 뒤가 명령형이면 사유로 안 잡음
i = parse_leave_message("2026-02-20 연차 메일 작성해줘 사유는 작성해줘", system_prompt=SYS)
eq(i and i["reason"], "개인사유", "사유 오탐 방지(명령형 제외)")

print("\n".join(f"  PASS {p}" for p in PASS))
if FAIL:
    print(f"\n==== {len(PASS)} pass / {len(FAIL)} fail ====")
    for item in FAIL:
        print("  FAIL", item)
    raise SystemExit(1)
print(f"\n==== {len(PASS)} pass / 0 fail ====\nALL OK")
