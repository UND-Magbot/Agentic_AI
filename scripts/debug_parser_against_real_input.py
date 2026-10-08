"""실제 v10 로그의 사용자 메시지를 파서에 흘려 결과 확인.

서버 로그에서 잘려 보이는 메시지를 복원해 직접 파싱 테스트.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.expense_parser import (
    parse_expense_message,
    extract_today_from_system,
    extract_user_name_from_system,
    extract_attachment_ids_from_system,
)

# 로그에서 보인 실제 사용자 메시지 (잘린 부분 복원)
USER_MSG = (
    "법인카드 지출 X \n\n"
    "개인카드 지출 \n"
    "해당없음, 2/7 토, 현대 글로비스 공장 출입 위한 안전화 구매, 72000, 워크업 대구 반야월점\n"
    "여비교통비, 2/9 월, 평택지제역 -> 동대구역 SRT 승차권 구매, 29500, 주식회사 에스알\n"
    "여비교통비, 2/10 화, 숙소 근처 -> 평택지제역 콜택시 이용, 30000, 미래 대리우전\n\n"
    "2026년 2월 배재병 익스펜스 보고 작성해줘"
)

# 프론트가 inject 했을 시스템 프롬프트 컨텍스트 블록 (예상)
SYS_PROMPT = """
## 현재 요청 컨텍스트
- 현재 날짜: 2026-05-15 (YYYY-MM-DD).
  사용자가 '2/7', '5/8' 같은 월/일만 적으면 위 연도(2026)를 그대로 채워 'YYYY-MM-DD' 로 정규화한다.
  요일 표기('토','월' 등)는 도구 인자에서 제거한다. date 필드는 YYYY-MM-DD 만.
- 현재 사용자(작성자 후보): 배재병.
  도구 호출 시 author/작성자 인자에 사용. 다른 이름을 추론하지 말 것.
- 첨부된 파일(업로드 순, 총 3개):
  1) id=17 (expense_a1.jpg.png, image/png)
  2) id=18 (expense_a2.jpg.png, image/png)
  3) id=19 (expense_a3.jpg.png, image/png)
- 도구가 attachment_ids 또는 receipt_attachment_ids 인자를 받으면 **위 3개 ID 전부를 빠짐없이** 위 순서 그대로 전달: [17, 18, 19].
  배열 길이는 반드시 3이어야 한다. 일부만 넣거나 순서를 바꾸지 말 것 — 슬롯 매핑이 어긋남.
"""

print("=" * 60)
print("USER MESSAGE:")
print(USER_MSG)
print("=" * 60)

today = extract_today_from_system(SYS_PROMPT)
user = extract_user_name_from_system(SYS_PROMPT)
ids = extract_attachment_ids_from_system(SYS_PROMPT)
print(f"\nExtracted from system prompt:")
print(f"  today = {today}")
print(f"  user  = {user!r}")
print(f"  ids   = {ids}")

print("\n" + "=" * 60)
print("PARSING...")
print("=" * 60)

args = parse_expense_message(
    USER_MSG,
    default_year=today[0] if today else 2026,
    default_month=today[1] if today else 2,
    default_author=user or "",
    attachment_ids=ids,
)

if args is None:
    print("\nFAIL — parser returned None. Fast-path would NOT trigger.")
    sys.exit(1)

print(f"\nSUCCESS — args:")
print(json.dumps(args, ensure_ascii=False, indent=2))
print(f"\n결론: 파서가 정상 동작. 이 입력이면 fast-path 가 LLM 우회 호출됨.")
print(f"     컨테이너만 재시작하면 됨.")
