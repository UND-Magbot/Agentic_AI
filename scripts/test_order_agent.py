"""수주 진행 대화(sales_deals/order_agent.py) 점검 — 지어낸 값 차단, 명세서 초안 고치기, 결제 조건·입금 확인·출하·발급 제안, 이벤트 순서.

    docker compose run --rm --no-deps -e PYTHONPATH=/app -v ./backend/app:/app/app -v ./scripts:/scripts backend \\
        python /scripts/test_order_agent.py
"""
from __future__ import annotations

import asyncio
import json
import sys

from app.sales_deals import order_agent as oa

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


DEAL = {"deal_no": "S26-0182", "customer": "한빛플라스틱", "stage": "수주", "statement": None, "won": True,
        "flow": {"steps": [{"key": "statement", "label": "거래명세서 발급", "done": False, "ready": True}], "total": 3_800_000, "paid": 0,
                 "terms": [{"index": 0, "label": "선금", "pct": 30, "amount": 1_140_000, "confirmed": False},
                           {"index": 1, "label": "잔금", "pct": 70, "amount": 2_660_000, "confirmed": True}]}}
DRAFT = {"no": "S26-0182", "date": "2026-10-07", "buyer": {"reg_no": "", "name": "한빛플라스틱", "ceo": "", "address": ""},
         "items": [{"name": "TCC1 MASTER T.C", "spec": "Payload 5Kg", "qty": 1, "unit_price": 1_800_000, "note": ""},
                   {"name": "PPM", "spec": "Pogo Pin Male", "qty": 1, "unit_price": 250_000, "note": ""}]}
STATE = {"draft": DRAFT, "ship": {}, "chat": []}

check(oa.reg_no_ok("314-86-54321") and oa.reg_no_ok("101-81-11115") and not oa.reg_no_ok("123-45-67890"), "등록번호 검증 숫자")
hint0 = oa.next_hint(DEAL, DRAFT, opening=True)
check("거래명세서부터" in hint0.replace("**", "") and "**등록번호·주소**" in hint0 and "\n" in hint0,
      "첫 안내: 명세서 + 빠진 칸(강조·줄바꿈)", hint0)
done_buyer = {**DRAFT, "buyer": {**DRAFT["buyer"], "reg_no": "220-81-22228", "address": "대구"}}
check("발급" in oa.next_hint(DEAL, done_buyer) and "결제" not in oa.next_hint(DEAL, done_buyer),
      "발급 전에는 다음 할 일이 '발급'(결제 방식 아님)", oa.next_hint(DEAL, done_buyer))
# 출하 뒤 다음 할 일이 입금 회차(pay:N)일 때 — 예전엔 head + None 으로 TypeError(출하·대화 실패, 2026-10-08)
PAY_DEAL = {**DEAL, "statement": {"no": "S26-0182"},
            "flow": {**DEAL["flow"], "steps": [{"key": "ship", "label": "출하", "done": True, "ready": False},
                                               {"key": "pay:0", "label": "선금 입금", "done": False, "ready": True}]}}
try:
    pay_hint = oa.next_hint(PAY_DEAL, done_buyer)
except TypeError as e:
    pay_hint = f"TypeError: {e}"
check(pay_hint.startswith("**다음:**") and "선금 30% 입금" in pay_hint, "출하 뒤 입금 회차 안내(오류 없음)", pay_hint)
check(oa._plain("반영했습니다. 결제 방식을 선택해 주세요. (after: 바로 납품, split: 선금, prepay: 전액)") == "반영했습니다. 결제 방식을 선택해 주세요."
      and oa._plain("split 방식으로 정했습니다.") == "2. 선금 50% 받고 납품 방식으로 정했습니다.", "답에서 영어 코드 없앰")

# 공급받는자: 말에 있는 값만
msg = "등록번호 314-86-54321, 주소 대구시 달서구 성서로 1 입니다"
st, steps, acts, case = oa.apply_ops(DEAL, STATE, [{"op": "buyer", "reg_no": "314-86-54321", "address": "대구시 달서구 성서로 1",
                                                   "ceo": "홍길동"}], msg)
b = st["draft"]["buyer"]
check(b["reg_no"] == "314-86-54321" and b["address"] == "대구시 달서구 성서로 1" and b["ceo"] == "", "공급받는자: 말한 값만, 지어낸 대표자 이름은 버림", str(b))
check(any(not ok and "대표" in t for ok, t in steps), "버린 값은 ✗ 로 알림")
_, steps, _, _ = oa.apply_ops(DEAL, STATE, [{"op": "buyer", "name": "한빛플라스틱", "reg_no": "314-86-54321"}], "등록번호 314-86-54321")
check(not any("상호" in t for _, t in steps), "이미 같은 값(상호)은 다시 알리지 않음", str(steps))
st, steps, _, _ = oa.apply_ops(DEAL, STATE, [{"op": "buyer", "reg_no": "123-45-67890"}], "등록번호 123-45-67890")
check(st["draft"]["buyer"]["reg_no"] == "123-45-67890" and any(not ok and "검증 숫자" in t for ok, t in steps), "검증 숫자 틀리면 넣되 확인 요청")

# 품목: 숫자는 말에 있는 것만
st, steps, _, _ = oa.apply_ops(DEAL, STATE, [{"op": "item", "index": 1, "qty": 3, "unit_price": 240000}], "PPM 수량 3개로 바꿔 줘")
check(st["draft"]["items"][1]["qty"] == 3 and st["draft"]["items"][1]["unit_price"] == 250_000, "수량 3 반영, 말 안 한 단가는 그대로", str(st["draft"]["items"][1]))
st, steps, _, _ = oa.apply_ops(DEAL, STATE, [{"op": "add_item", "name": "교육비", "qty": 1, "unit_price": 50000}], "교육비 1식 5만원 추가")
check(st["draft"]["items"][-1]["name"] == "교육비" and st["draft"]["items"][-1]["unit_price"] == 50000, "품목 추가(5만원 → 50,000)")
st, _, _, _ = oa.apply_ops(DEAL, STATE, [{"op": "remove_item", "index": 0}], "마스터 빼 줘")
check(len(st["draft"]["items"]) == 1 and STATE["draft"]["items"][0]["name"] == "TCC1 MASTER T.C", "품목 빼기(원래 상태는 그대로)")

# 명세서 세트로 묶기·되돌리기(사용자 2026-10-08)
SET_DEAL = {**DEAL, "title": "TCC1 외 1"}
st, steps, _, _ = oa.apply_ops(SET_DEAL, STATE, [{"op": "statement", "bundle": True, "bundle_qty": 1}], "명세서 1SET 으로 묶어 줘")
it0 = st["draft"]["items"]
check(len(it0) == 1 and it0[0]["name"] == "TCC1 툴체인저(ATC)" and it0[0]["unit"] == "SET" and it0[0]["unit_price"] == 2_050_000
      and len(st["draft"]["parts"]) == 2 and len(STATE["draft"]["items"]) == 2, "세트로 묶기(기본 이름·합계 단가, 원래 상태 그대로)", str(it0))
st2, _, _, _ = oa.apply_ops(SET_DEAL, st, [{"op": "statement", "bundle": False}], "품목별로 다시 풀어 줘")
check(len(st2["draft"]["items"]) == 2 and "parts" not in st2["draft"], "품목별로 되돌리기")
st3, _, _, _ = oa.apply_ops(SET_DEAL, STATE, [{"op": "statement", "bundle": True, "bundle_name": "지어낸 이름"}], "세트로 해 줘")
check(st3["draft"]["items"][0]["name"] == "TCC1 툴체인저(ATC)", "말에 없는 세트 이름은 안 받음")
check("1 SET" in oa._situation(SET_DEAL, st) and "세트 구성" in oa._situation(SET_DEAL, st), "상황에 세트·구성 표시")

# 결제 조건·입금 확인·출하·발급
_, steps, _, case = oa.apply_ops(DEAL, STATE, [{"op": "terms", "items": [
    {"label": "선금", "pct": 30, "expected_date": "2026-10-20"},
    {"label": "잔금", "pct": 70, "expected_date": "2026-12-31"}]}], "선금 30% 발주 시 10월 20일, 잔금은 나머지 납품 후")
check(isinstance(case, list) and [t["pct"] for t in case] == [30, 70] and case[0]["expected_date"] == "2026-10-20"
      and case[1]["expected_date"] is None, "결제 조건: 나머지 % 계산, 말 안 한 예정일(12/31)은 버림", str(case))
_, steps, _, case = oa.apply_ops(DEAL, STATE, [{"op": "terms", "items": [
    {"label": "선금", "pct": 40}, {"label": "잔금", "pct": 60}]}], "선금 받고 잔금 받을게요")
check(case is None and any(not ok and "결제 조건" in t for ok, t in steps), "말 안 한 % 는 넣지 않음")
_, _, acts, _ = oa.apply_ops(DEAL, STATE, [{"op": "confirm", "index": 0}], "선금 들어왔어")
check(acts == [{"type": "confirm", "index": 0, "label": "선금 30%", "amount": 1_140_000}], "입금 확인 → 확인 버튼(바로 저장 안 함)", str(acts))
_, steps, acts, _ = oa.apply_ops(DEAL, STATE, [{"op": "confirm", "index": 1}], "잔금 들어왔어")
check(not acts and any(not ok and "이미" in t for ok, t in steps), "이미 확인한 회차는 다시 안 함")
_, steps, acts, _ = oa.apply_ops(DEAL, STATE, [{"op": "confirm", "index": 5}], "6번")
check(not acts and any(not ok for ok, _ in steps), "없는 회차 번호는 거절")
st, steps, acts, _ = oa.apply_ops(DEAL, STATE, [{"op": "ship", "date": "2026-10-09", "carrier": "경동택배", "tracking": "4123-5555"}],
                                  "10월 9일 경동택배로 보냈어 운송장 4123-5555")
check(st["ship"] == {"tracking": "4123-5555", "carrier": "경동택배", "date": "2026-10-09"} and acts[-1]["type"] == "ship",
      "출하 정보 채우고 [출하 기록] 버튼", str(st["ship"]))
st, _, _, _ = oa.apply_ops(DEAL, STATE, [{"op": "ship", "tracking": "9999"}], "택배로 보냈어")
check("tracking" not in st["ship"], "말에 없는 운송장은 버림")
st, _, _, _ = oa.apply_ops(DEAL, STATE, [{"op": "ship", "carrier": "경동택배", "bundle": True, "bundle_qty": 1}], "ATC 1세트로 묶어서 경동택배로 보냈어")
check(st["ship"].get("mode") == "set" and st["ship"].get("set_qty") == "1 SET", "세트로 묶어서 → 1 SET", str(st["ship"]))
st, _, _, _ = oa.apply_ops(DEAL, STATE, [{"op": "ship", "bundle": True, "bundle_qty": 2}], "경동택배로 보냈어")
check("mode" not in st["ship"], "세트라는 말이 없으면 세트로 바꾸지 않음", str(st["ship"]))
_, steps, acts, _ = oa.apply_ops(DEAL, STATE, [{"op": "issue"}], "발급해 줘")
check(not acts and any("등록번호" in t for _, t in steps), "빈 칸이 있으면 발급 버튼 대신 이유")
full = json.loads(json.dumps(STATE))
full["draft"]["buyer"].update(reg_no="314-86-54321", address="대구")
_, _, acts, _ = oa.apply_ops(DEAL, full, [{"op": "issue"}], "발급해 줘")
check(acts == [{"type": "issue"}], "다 채우면 [발급] 버튼")


# 대화 한 번(가짜 스트림)
def run(text: str, message: str, case_err: Exception | None = None) -> list[dict]:
    async def stream(_m, **_k):
        for i in range(0, len(text), 4):
            yield text[i:i + 4]

    async def set_case(c):
        if case_err:
            raise case_err

    async def go():
        return [ev async for ev in oa.agent_turn(DEAL, STATE, message, stream=stream, set_case=set_case)]
    return asyncio.run(go())


evs = run('- 결제 조건을 정함.\n===\n{"ops": [{"op": "terms", "items": [{"label": "전액", "pct": 100, "when": "납품 전"}]}]}\n===\n선입금 방식으로 정했습니다.', "100% 다 받고 보낼게요")
order = [e["t"] for e in evs]
check(order[0] == "think" and order[-1] == "done" and "step" in order and any(e["t"] == "step" and e["ok"] for e in evs),
      "순서: 생각 → 답 → 단계 → 끝", str(order))
done = evs[-1]["state"]["chat"]
check(done[-2] == {"role": "user", "text": "100% 다 받고 보낼게요"} and done[-1]["answer"].startswith("선입금 방식으로 정했습니다.\n\n**다음:**")
      and done[-1]["think"],
      "대화 기록에 생각·답·단계 저장")
evs = run('- x\n===\n{"ops": [{"op": "terms", "items": [{"label": "전액", "pct": 100}]}]}\n===\n바꿨습니다.', "납품 후 100% 입금으로", case_err=ValueError("거래명세서를 먼저 발급해 주세요."))
check(any(e["t"] == "step" and not e["ok"] and "먼저 발급" in e["text"] for e in evs), "결제 조건 저장 실패 이유를 ✗ 로")

evs = run('- 두 가지.\n===\n{"ops": [{"op": "item", "index": 1, "qty": 2}]}\n{"ops": [{"op": "issue"}]}\n===\n바꿨습니다.',
          "PPM 2개로 바꾸고 발급해줘")
check(evs[-1]["state"]["draft"]["items"][1]["qty"] == 2 and any(e["t"] == "step" and "등록번호" in e["text"] for e in evs),
      "JSON 을 여러 개 써도 ops 를 모두 받음(수량 반영 + 발급 검사)", str([e for e in evs if e["t"] == "step"]))

print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
for label, detail in FAIL:
    print(f"  ✗ {label}: {detail}")
sys.exit(1 if FAIL else 0)
