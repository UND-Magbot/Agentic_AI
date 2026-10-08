"""AI 답에서 지시문을 베낀 문장 거르기(company_knowledge/ai_guard.py) 점검 — 실제로 나온 유출 사례 기준.

    PYTHONPATH=backend python scripts/test_ai_guard.py
"""
from __future__ import annotations

import asyncio
import json
import sys

from app.company_knowledge import ai_guard as g
from app.company_knowledge import atc_agent as ag
from app.company_knowledge import atc_chat as ac

sys.stdout.reconfigure(encoding="utf-8")
PASS: list[str] = []
FAIL: list[tuple[str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


LEAK_TAIL = ("화면 안내(물으면 이대로 답한다): AI 가 배운 내용은 위쪽 [AI 학습 내용 관리] 탭에서 보고 영업 관리자가 승인·거절한다. "
             "질문 답을 고치려면 왼쪽 [Q&A 고치기], 처음부터 하려면 [처음부터]. 예전 결과는 결과 창 위 ‹ › 로도 볼 수 있다.")
# 1) 사용자가 실제로 받은 답(2026-10-06) — 앞 두 문장은 남고 지시문 꼬리는 모두 빠짐
REAL = ("질문하신 내용은 시스템이 제공하는 [고를 수 있는 모델] 목록에 TCC2가 포함되어 있지 않기 때문에 발생한 안내입니다. "
        "현재 2순위 후보로 추천된 M-LTC-0005A는 공압 툴체인저 계열 중 조건에 맞는 다음 순위 제품입니다. " + LEAK_TAIL)
out = g.strip_prompt_echo(REAL, ag.SYSTEM)
check("화면 안내" not in out and "물으면" not in out and "Q&A 고치기" not in out and "볼 수 있다" not in out
      and out.startswith("질문하신 내용은") and "M-LTC-0005A" in out, "실제 유출 답: 지시문 꼬리만 빠짐", out)
check("[고를 수 있는 모델]" not in out and "고를 수 있는 모델 목록" in out, "프롬프트 안쪽 칸 이름은 괄호를 벗김", out)

# 2) 옛 대화(atc_chat) 지시문 표기 '묻으면'·'추천 근거' 문장도
OLD = ("지금 TCC1 이 맞습니다. 화면 안내(묻으면 이대로 답한다): AI 가 배운 내용은 화면 위쪽 [AI 학습 내용 관리] 탭에서 보고, "
       "영업 관리자가 승인·거절한다. 추천 근거는 왼쪽 결과의 '추천 근거' 줄을 누르면 계산 과정이 보인다.")
out = g.strip_prompt_echo(OLD, ac._INTENT_SYSTEM)
check(out == "지금 TCC1 이 맞습니다.", "옛 대화 지시문 유출도 빠짐", out)

# 3) 화면 사용법을 물어 존댓말로 풀어 쓴 답은 그대로(버튼 이름 괄호도 유지)
OK = "AI 가 배운 내용은 위쪽 [AI 학습 내용 관리] 탭에서 보실 수 있습니다. 답을 고치려면 왼쪽 [Q&A 고치기]를 누르세요."
check(g.strip_prompt_echo(OK, ag.SYSTEM) == OK, "존댓말 안내 답은 그대로", g.strip_prompt_echo(OK, ag.SYSTEM))
NORMAL = "선정 하중 3.2kg 은 TCC1 정격 5kg 의 64% 라서 상위 단계 비교 사유가 없습니다.\n다음으로 볼 모델은 TCV1(10kg)입니다."
check(g.strip_prompt_echo(NORMAL, ag.SYSTEM) == NORMAL, "거를 것 없는 답은 줄바꿈까지 그대로")
check(g.strip_prompt_echo(LEAK_TAIL, ag.SYSTEM) == "", "전부 지시문이면 빈 답(호출부가 대체 문구로)")
check(g.strip_prompt_echo(None, ag.SYSTEM) == "", "빈 답")

# 4) 짧은 용어 겹침('툴측 총무게' 등)만 있는 평서문은 지우지 않음
check(g.strip_prompt_echo("결론: TCC1(5kg).", ag.SYSTEM) == "결론: TCC1(5kg).", "짧은 평서문은 남김")


# 5) 옛 대화 경로(chat_turn)에 실제로 연결됐는지 — 가짜 AI 가 지시문을 붙여 답함
async def fake_chat(msgs, **_kw):
    if "칸으로 옮긴다" in msgs[0]["content"]:
        return json.dumps({"updates": []})
    return json.dumps({"intent": "chat", "model": None, "answer": OLD}, ensure_ascii=False)  # 이 경로 지시문을 베낀 답

base = {"project": {"process": {}, "environment": {}}, "robots": [{"id": "R1", "type": "cobot", "cables": {}}], "tools": []}
res = asyncio.run(ac.chat_turn(base, "이거 괜찮아?", history=[], rec=None, chat=fake_chat, options=[], suggested=None))
check(res["answer"] == "지금 TCC1 이 맞습니다.", "옛 대화(chat_turn) 답에서도 지시문 빠짐", res["answer"])

print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
for label, detail in FAIL:
    print(f"  ✗ {label}: {detail}")
sys.exit(1 if FAIL else 0)
