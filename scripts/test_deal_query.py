# -*- coding: utf-8 -*-
"""채팅에서 영업 데이터 바로 답하기(사용자 2026-10-28) — 말 알아듣기·DB 답(금액·품목·출하·입금·미입금·담당자·건 수·최근 번호).

    MSYS_NO_PATHCONV=1 docker compose run --rm -T --no-deps -e PYTHONPATH=/app -e PYTHONIOENCODING=utf-8 \\
        -v ./backend/app:/app/app -v ./scripts:/scripts backend python /scripts/test_deal_query.py

개발 DB(agent_ai_dev)에 테스트 건을 만들고 돌린 뒤 지운다.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import sys

from sqlalchemy import text

from app.database import SessionLocal
from app.sales_deals import deal_query as dq

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []
CUST = "__테스트_데이터가나__"
OWNER = "가나다라마바"
TODAY = dt.date(2026, 10, 28)


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


def say(t: str) -> list[dict]:
    return [{"role": "user", "content": t}]


def detect_cases() -> None:
    cases = {
        "이번 분기 수주 금액 얼마야?": "kpi", "올해 매출 얼마야?": "kpi", "미입금 건 알려줘": "unpaid",
        "입금 예정일 지난 건 있어?": "unpaid", "진행 중인 영업 건 몇 개야?": "count", "Victor가 담당하는 건 뭐 있어?": "owner",
        "가장 최근 견적서 발행번호 뭐야?": "recent", "A/S 진행 중인 제품 있어?": "as",
        "퍼씨엠 견적 금액 얼마였지?": "deal", "제동물류 건 언제 출하했어?": "deal", "선진물류 입금 다 들어왔어?": "deal",
        "퍼씨엠 견적서에 무슨 품목 들어갔어?": "deal",
    }
    for t, kind in cases.items():
        d = dq.detect(say(t))
        check(bool(d) and d["kind"] == kind, f"알아듣기 {kind}: {t}", str(d))
    check(dq.detect(say("Victor가 담당하는 건 뭐 있어?"))["name"] == "Victor", "담당자 이름에서 조사 떼기")
    d = dq.detect(say("퍼씨엠 견적 금액 얼마였지?"))
    check(d["facet"] == "amount" and d["names"] == ["퍼씨엠"], "건 이름·물은 것(금액)", str(d))
    check(dq.detect(say("선진물류 입금 다 들어왔어?"))["facet"] == "pay", "입금 물음")
    check(dq.detect(say("제동물류 건 언제 출하했어?"))["facet"] == "ship", "출하 물음")
    # 가로채지 않을 말 — 고치기·이동·방법·단가·대여 목록·재무 이야기
    for t in ("퍼씨엠 견적 금액 좀 바꾸고 싶은데", "퍼씨엠 견적서 수정하려고 해", "견적서 수정하는 방법 알려줘",
              "결제 조건을 선금 30% 잔금 70%로 하려면?", "대여 견적서 월 납입금은 어떻게 계산돼?", "단가표에서 TCV3 가격 얼마야?",
              "지금 대여 중인 제품 알려줘", "견적서 NEGO 항목은 뭐야?", "무상 기간은 언제부터 시작해?", "연차 쓰려면 어떻게 해?",
              "퍼씨엠 거래명세서 발급해줘", "출하 기록에 S/N은 언제 등록해?"):
        d = dq.detect(say(t))
        check(d is None or (d["kind"] == "deal" and not d["lead"]), f"가로채지 않음: {t}", str(d))
    prev = {"role": "assistant", "content": "검색해 보니 … 건이 있습니다.\n[건 열기](/proposals/recommend?tab=deals&deal=9)"}
    d = dq.detect([{"role": "user", "content": "퍼씨엠 건 열어줘"}, prev, {"role": "user", "content": "그 건 입금은 어떻게 됐어?"}])
    check(d and d["kind"] == "deal" and d.get("deal_id") == 9 and d["facet"] == "pay", "'그 건 입금은?' → 앞 답의 건", str(d))


async def cleanup(db) -> None:
    ids = (await db.execute(text("SELECT id FROM sales_deals WHERE customer = :c"), {"c": CUST})).scalars().all()
    if ids:
        await db.execute(text("DELETE FROM sales_deals WHERE id = ANY(:i)"), {"i": ids})
    await db.commit()


async def new_deal(db, title: str, no: str, *, won: bool | None = None, payments: list | None = None,
                   shipments: list | None = None, terms: list | None = None) -> int:
    quote = {"no": no, "date": "2026-10-01", "amount": 10_000_000, "currency": "KRW", "revision": 0, "biz": "sale",
             "items": [{"name": "TCV1 MASTER\n- Robot Side", "qty": 2, "amount": 6_000_000},
                       {"name": "물류비", "qty": 1, "amount": 4_000_000}]}
    deal_id = (await db.execute(text(
        "INSERT INTO sales_deals (deal_no, kind, customer, owner, title, base_date, quote, won, won_date, pay_case, pay_terms, payments, shipments) "
        "VALUES (:no, 'quote', :c, :o, :t, '2026-10-01', CAST(:q AS jsonb), :w, :wd, :pc, CAST(:pt AS jsonb), CAST(:p AS jsonb), CAST(:s AS jsonb)) "
        "RETURNING id"),
        {"no": no, "c": CUST, "o": OWNER, "t": title, "q": json.dumps(quote, ensure_ascii=False), "w": won,
         "wd": dt.date(2026, 10, 5) if won else None, "pc": "terms" if terms else None,
         "pt": json.dumps(terms or []), "p": json.dumps(payments or []), "s": json.dumps(shipments or [], ensure_ascii=False)})).scalar_one()
    await db.commit()
    return deal_id


async def ask(db, t: str, messages: list[dict] | None = None) -> str:
    intent = dq.detect(messages or say(t))
    assert intent, t
    return await dq.answer(db, intent, TODAY) or ""


async def db_cases() -> None:
    async with SessionLocal() as db:
        await cleanup(db)
        try:
            terms = [{"label": "선금", "pct": 30, "when": "발주 시", "expected_date": "2026-10-10"},
                     {"label": "잔금", "pct": 70, "when": "납품 후", "expected_date": "2026-10-20"}]
            a = await new_deal(db, "툴체인저 데이터", "QT-ZY20261001-01", won=True, terms=terms,
                               payments=[{"term": 0, "amount": 3_000_000, "date": "2026-10-09"}],
                               shipments=[{"date": "2026-10-15", "lines": [{"name": "TCV1 MASTER", "qty": 2}]}])

            out = await ask(db, f"{CUST} 견적 금액 얼마야?")
            check("10,000,000원" in out and "부가세 별도" in out and "11,000,000원" in out, "금액 — 공급가액·부가세 포함", out)
            check(f"deal={a}" in out, "건 열기 버튼", out)
            out = await ask(db, f"{CUST} 견적서에 무슨 품목 들어갔어?")
            check("TCV1 MASTER · Robot Side × 2 — 6,000,000원" in out and "물류비" in out, "품목 — 줄바꿈 이름 한 줄로", out)
            out = await ask(db, f"{CUST} 언제 출하했어?")
            check("출하 1회" in out and "2026-10-15" in out, "출하 날짜", out)
            out = await ask(db, f"{CUST} 입금 다 들어왔어?")
            check("아직 다 받지 못했습니다" in out and "선금 30% 3,000,000원 — 입금 확인 2026-10-09" in out
                  and "잔금 70% 7,000,000원 — 미입금 · 예정일 2026-10-20 (지남)" in out, "입금 회차·지난 예정일", out)

            out = await ask(db, "미입금 건 알려줘")
            check(CUST in out and "남은 7,000,000원" in out and "⏰" in out, "미입금 목록에 테스트 건 · 예정일 지남", out)
            out = await ask(db, "입금 예정일 지난 건 있어?")
            check(CUST in out and "잔금" in out, "예정일 지난 건만", out)
            out = await ask(db, f"{OWNER} 담당 건 뭐 있어?")
            check("1건" in out and CUST in out, "담당자별 건", out)
            out = await ask(db, "없는사람이름 담당 건 뭐 있어?")
            check("기록된 영업 건이 없습니다" in out, "모르는 담당자", out)
            out = await ask(db, "이번 분기 수주 금액 얼마야?")
            check("4분기 수주 금액은" in out and CUST in out, "이번 분기 수주 금액·건 목록", out)
            out = await ask(db, "이번 달 수주 금액 얼마야?")
            check("10월 수주 금액은" in out and CUST in out and "올해 수주 금액 합계" in out, "이번 달 수주 금액(올해 합계 아님)", out)
            out = await ask(db, "9월 수주 금액 얼마야?")
            check("9월 수주 금액은" in out and CUST not in out, "다른 달 — 테스트 건 빠짐", out)
            check(dq._month("3개월 대여 수주 금액 얼마야?", TODAY) is None, "'3개월'은 달로 보지 않음")
            out = await ask(db, "진행 중인 영업 건 몇 개야?")
            check("진행 중인 영업 건은" in out and "단계별" in out, "건 수", out)
            out = await ask(db, "가장 최근 견적서 발행번호 뭐야?")
            check("가장 최근 견적서 발행번호는" in out or "발행한 견적서가 아직 없습니다" in out, "최근 발행번호", out)
            prev = {"role": "assistant", "content": f"[건 열기](/proposals/recommend?tab=deals&deal={a})"}
            out = await ask(db, "", [{"role": "user", "content": "x"}, prev, {"role": "user", "content": "그 건 입금은 어떻게 됐어?"}])
            check(CUST in out and "선금 30%" in out, "앞 답의 건으로 입금", out)
            check(await dq.answer(db, {"kind": "deal", "facet": "pay", "names": ["예정일", "승인해"], "lead": False}, TODAY) is None,
                  "못 찾고 이름도 불확실 → 다음 흐름으로")
        finally:
            await cleanup(db)


def main() -> None:
    detect_cases()
    asyncio.run(db_cases())
    for p in PASS:
        print("PASS", p)
    for f, d in FAIL:
        print("FAIL", f, "\n   ", d.replace("\n", "\n    "))
    print(f"\n{len(PASS)} PASS / {len(FAIL)} FAIL")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
