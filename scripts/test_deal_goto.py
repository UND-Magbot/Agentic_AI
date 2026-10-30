# -*- coding: utf-8 -*-
"""채팅에서 영업 건 찾아 이동(사용자 2026-10-28) — 말 알아듣기·이름 뽑기·건 찾기·상태·버튼.

    MSYS_NO_PATHCONV=1 docker compose run --rm -T --no-deps -e PYTHONPATH=/app -e PYTHONIOENCODING=utf-8 \\
        -v ./backend/app:/app/app -v ./scripts:/scripts backend python /scripts/test_deal_goto.py

개발 DB(agent_ai_dev)에 테스트 건을 만들고 돌린 뒤 지운다.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import re
import sys

from sqlalchemy import text

from app.database import SessionLocal
from app.sales_deals import deal_goto as dg
from app.sales_deals import service as svc

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []
CUST = "__테스트_가나다__"
CUST2 = "__테스트_라마바__"
TODAY = dt.date(2026, 10, 28)
BTN = re.compile(r"\[([^\]]+)\]\((/proposals/recommend\?[^)\s]+)\)")


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


def say(t: str) -> list[dict]:
    return [{"role": "user", "content": t}]


def screen_cases() -> None:
    """영업 화면 자체로 가는 버튼(사용자 2026-10-28 — 'AI 제품 추천 페이지로 이동해줘'에 버튼이 없었다)."""
    from app.sales_deals import screen_goto as sg

    for t, href in (("AI 제품 추천 페이지로 이동해줘", "tab=recommend"), ("대여 관리 화면 띄워줘", "tab=rentals"),
                    ("수주 진행 탭으로 가줘", "tab=order"), ("견적서 수기 작성 열어줘", "tab=quote&new=manual"),
                    ("영업 건 목록 보여줘", "tab=deals"), ("승인 요청 화면으로 이동", "sub=approvals"), ("제안서 만들기로 가자", "/proposals/new")):
        hits = sg.detect(say(t))
        check(bool(hits) and href in hits[0][1], f"화면 이동: {t}", str(hits))
        if hits:
            check(BTN.search(sg.answer(hits)) is not None or "/proposals/new" in sg.answer(hits), f"버튼 표시: {t}", sg.answer(hits))
    for t in ("제품 추천은 어떻게 해?", "견적서 어디서 만들어?", "페이로드 10kg 로봇에 맞는 툴체인저 추천해줘", "미입금 건 알려줘"):
        check(sg.detect(say(t)) is None, f"화면 이동 아님: {t}")
    for t in ("견적서 수기 작성 열어줘", "영업 건 목록 보여줘"):
        d = dg.detect(say(t))
        check(d is None or not d["names"], f"건 이름으로 안 봄: {t}", str(d))
    prev = {"role": "assistant", "content": sg.answer(sg.detect(say("AI 제품 추천 페이지로 이동해줘")))}
    d = dg.detect([{"role": "user", "content": "x"}, prev, {"role": "user", "content": "응 이동해줘"}])
    check(bool(d) and d["kind"] == "repeat", "화면 버튼 뒤 '응 이동해줘' → 버튼 다시", str(d))


def detect_cases() -> None:
    d = dg.detect(say("채팅창에서 성균관대 견적서를 수정하려고 해. 어떻게 해야 할까?"))
    check(d == {"kind": "quote", "names": ["성균관대"], "lead": True}, "예시 문장 → 견적서 수정·성균관대", str(d))
    d = dg.detect(say("한화로보틱스 거래명세서 발급하고 싶어"))
    check(d and d["kind"] == "statement" and d["names"] == ["한화로보틱스"], "거래명세서", str(d))
    d = dg.detect(say("QT-LU20261012-01 출하 기록 고쳐줘"))
    check(d and d["kind"] == "ship" and d["names"] == ["QT-LU20261012-01"], "출하 + 발행번호", str(d))
    d = dg.detect(say("LG전자 영업 건 열어줘"))
    check(d and d["kind"] == "open" and d["names"] == ["LG전자"], "건 열기", str(d))
    d = dg.detect(say("현대모비스건 상태 보여줘"))
    check(d and d["kind"] == "open" and d["names"] == ["현대모비스"], "붙여 쓴 '건' 떼기", str(d))
    d = dg.detect(say("하나은행 견적서 고쳐줘"))
    check(d and d["names"] == ["하나은행"], "이름 속 '하나'는 남김", str(d))
    # 가로채지 않을 말
    for t in ("견적서 수정하는 방법 알려줘", "견적서 양식 보여줘", "거래명세서 어떻게 발급해?", "이 건 확인 부탁해",
              "10월 이후 대여 나간 제품 알려줘", "성균관대 견적서 내용 요약해줘", "출하 기록이 뭐야?"):
        check(dg.detect(say(t)) is None, f"가로채지 않음: {t}", str(dg.detect(say(t))))
    check(dg.detect([{"role": "assistant", "content": "성균관대 견적서 수정"}]) is None, "사용자 말이 아니면 안 잡음")
    # 채팅 실측(2026-10-28)에서 건 이름으로 잘못 찾던 질문 — 이름 조각이 없거나 확실한 이름이 아님
    for t in ("거래명세서 발급 절차가 어떻게 돼?", "출하 기록에 S/N은 언제 등록해?", "견적서 발행번호 규칙 알려줘",
              "가장 최근 견적서 발행번호 뭐야?"):
        d = dg.detect(say(t))
        check(d is None or not d["lead"], f"질문 말은 이름 아님: {t}", str(d))
    d = dg.detect(say("상주보건소 건 대여 시작일 바꾸고 싶어"))
    check(d and d["kind"] == "open" and d["names"] == ["상주보건소"], "대여 시작일 바꾸기 → 건 열기", str(d))
    prev = {"role": "assistant", "content": "이동할까요?\n[견적서 고치기로 이동](/proposals/recommend?tab=quote&quote=5)  [건 열기](/proposals/recommend?tab=deals&deal=9)"}
    d = dg.detect([{"role": "user", "content": "퍼씨엠 견적서 수정"}, prev, {"role": "user", "content": "응 이동해줘"}])
    check(d and d["kind"] == "repeat" and len(d["links"]) == 2, "'응 이동해줘' → 버튼 다시", str(d))
    d = dg.detect([{"role": "user", "content": "퍼씨엠 견적서 수정"}, prev, {"role": "user", "content": "그럼 거래명세서는?"}])
    check(d and d["kind"] == "statement" and d.get("deal_id") == 9, "'그럼 거래명세서는?' → 앞 답의 그 건", str(d))
    d = dg.detect([{"role": "user", "content": "퍼씨엠 견적서 수정"}, prev, {"role": "user", "content": "그 건 출하 기록도 고쳐야 해"}])
    check(d and d["kind"] == "ship" and d.get("deal_id") == 9, "'그 건 출하 기록도' → 앞 답의 그 건", str(d))


async def cleanup(db) -> None:
    ids = (await db.execute(text("SELECT id FROM sales_deals WHERE customer IN (:a, :b)"), {"a": CUST, "b": CUST2})).scalars().all()
    if ids:
        await db.execute(text("DELETE FROM product_quotes WHERE deal_id = ANY(:i)"), {"i": ids})
        await db.execute(text("DELETE FROM sales_deals WHERE id = ANY(:i)"), {"i": ids})
    await db.commit()


async def new_deal(db, cust: str, title: str, no: str, *, won: bool | None = None, statement: dict | None = None,
                   shipments: list | None = None, dropped: bool = False, biz: str = "sale", with_quote: bool = True,
                   pay_case: str | None = None) -> tuple[int, int | None]:
    quote = {"no": no, "date": "2026-10-01", "amount": 1000000, "currency": "KRW", "items": [], "revision": 1, "biz": biz}
    deal_id = (await db.execute(text(
        "INSERT INTO sales_deals (deal_no, kind, customer, owner, title, base_date, quote, won, statement, shipments, dropped, pay_case) "
        "VALUES (:no, 'quote', :c, 'Test', :t, '2026-10-01', CAST(:q AS jsonb), :w, CAST(:st AS jsonb), CAST(:s AS jsonb), :dr, :pc) RETURNING id"),
        {"no": no, "c": cust, "t": title, "q": json.dumps(quote, ensure_ascii=False), "w": won,
         "st": json.dumps(statement) if statement else None, "s": json.dumps(shipments or []), "dr": dropped, "pc": pay_case})).scalar_one()
    qid = None
    if with_quote:
        hist = [{"revision": 0, "quote_no": no, "lines": [], "total": 1000000, "currency": "KRW", "issued_at": "2026-10-01T01:00:00+00:00"},
                {"revision": 1, "quote_no": no, "lines": [], "total": 1000000, "currency": "KRW", "issued_at": "2026-10-02T01:00:00+00:00"}]
        qid = (await db.execute(text(
            "INSERT INTO product_quotes (deal_id, status, quote_no, customer, revision, history, total) "
            "VALUES (:d, 'issued', :no, :c, 1, CAST(:h AS jsonb), 1000000) RETURNING id"),
            {"d": deal_id, "no": no, "c": cust, "h": json.dumps(hist)})).scalar_one()
    await db.commit()
    return deal_id, qid


async def ask(db, t: str) -> str:
    intent = dg.detect(say(t))
    assert intent, t
    return await dg.answer(db, intent, TODAY)


async def db_cases() -> None:
    async with SessionLocal() as db:
        await cleanup(db)
        try:
            a, qa = await new_deal(db, CUST, "툴체인저 2세트", "QT-ZZ20261001-01")
            b, _ = await new_deal(db, CUST2, "그리퍼 교체", "QT-ZZ20261002-01", won=True,
                                  statement={"no": "TS-ZZ20261003-01", "date": "2026-10-03"}, pay_case="advance")
            c, _ = await new_deal(db, CUST2, "AMR 대여", "QT-ZZ20261004-01", won=True, biz="rental")
            d, _ = await new_deal(db, CUST2, "옛 건", "QT-ZZ20261005-01", dropped=True)

            out = await ask(db, f"채팅창에서 {CUST} 견적서를 수정하려고 해. 어떻게 해야 할까?")
            btns = BTN.findall(out)
            check("검색해 보니" in out and "이동할까요" in out, "1건 — 상태 안내 + 이동 물음", out)
            check(("견적서 고치기로 이동", f"/proposals/recommend?tab=quote&quote={qa}") in btns, "견적서 고치기 버튼 → 견적서 탭", out)
            check(("건 열기", f"/proposals/recommend?tab=deals&deal={a}") in btns, "건 열기 버튼", out)
            check("견적서 v2 발행" in out and "견적" in out, "상태 줄(단계·견적서 버전)", out)

            out = await ask(db, f"{CUST} 거래명세서 발급하고 싶어")
            check("수주 확정 전" in out and "tab=order" not in out, "수주 전 → 명세서 대신 건 열기", out)

            out = await ask(db, "QT-ZZ20261002-01 거래명세서 고쳐줘")
            check(f"tab=order&deal={b}" in out and "TS-ZZ20261003-01 발급" in out, "발행번호로 찾기 → 수주 진행 화면", out)

            out = await ask(db, "QT-ZZ20261002-01 출하 기록 작성할래")
            check(f"tab=order&deal={b}&focus=ship" in out and "출하 기록 작성으로 이동" in out, "출하 → 출하 칸 열기", out)

            out = await ask(db, f"{CUST2} 영업 건 열어줘")
            check("건을 찾았습니다" in out and out.count("[건 열기]") == 3, "여러 건 → 후보마다 버튼", out)
            check(f"tab=deals&deal={c}" in out, "대여 건도 제품 영업 건 관리 탭으로(사용자 2026-10-30)", out)
            check("드랍한 건" in out, "드랍 건 안내", out)

            out = await ask(db, f"{CUST2} 그리퍼 견적서 수정")
            check("검색해 보니" in out and "그리퍼 교체" in out, "이름 조각 2개 → 더 많이 맞는 1건", out)

            check(await dg.answer(db, {"kind": "statement", "names": ["절차"], "lead": False}, TODAY) is None,
                  "못 찾고 이름도 불확실 → AI 로 넘김")
            out = await dg.answer(db, {"kind": "statement", "names": [], "lead": True, "deal_id": b}, TODAY)
            check("검색해 보니" in out and f"tab=order&deal={b}" in out, "앞 답의 건(deal_id)으로 바로", out)
            out = await dg.answer(db, {"kind": "repeat", "links": [("건 열기", f"/proposals/recommend?tab=deals&deal={a}")]}, TODAY)
            check("버튼" in out and f"deal={a}" in out, "승낙 → 버튼 다시 안내", out)
            # 영업부끼리는 남이 발행한 견적서도 이어서 고친다(사용자 2026-10-28 — 담당 넘김·퇴사 뒤 이어받기)
            sales = (await db.execute(text("SELECT id FROM users WHERE CAST(domain AS text) = 'sales' ORDER BY id LIMIT 2"))).scalars().all()
            await db.execute(text("UPDATE product_quotes SET user_id = :u WHERE id = :q"), {"u": sales[0], "q": qa})
            await db.commit()
            intent = dg.detect(say(f"{CUST} 견적서 수정하고 싶어"))
            out = await dg.answer(db, intent, TODAY, sales[1])
            check(f"tab=quote&quote={qa}" in out and "작성 중" not in out, "동료가 발행한 견적서 → 고치기 버튼", out)
            out = await dg.answer(db, intent, TODAY, sales[0])
            check(f"tab=quote&quote={qa}" in out, "내 견적서 → 고치기 버튼", out)

            # 예전 '실주'(won=false, dropped=false)도 드랍으로 보이고, 되살리면 수주 결과 '미확인'으로(사용자 2026-10-28)
            e, _ = await new_deal(db, CUST2, "옛 실주", "QT-ZZ20261006-01", won=False)
            row = next(r for r in (await svc.list_deals(db, TODAY))["rows"] if r["id"] == e)
            check(row["stage"] == "드랍", "옛 실주 → 드랍 단계", row["stage"])
            await svc.set_dropped(db, e, None, False, None)
            row = next(r for r in (await svc.list_deals(db, TODAY))["rows"] if r["id"] == e)
            check(row["stage"] == "견적" and row["won"] is None and not row["dropped"], "옛 실주 되살리기 → 견적·미확인",
                  f"{row['stage']} won={row['won']} dropped={row['dropped']}")

            out = await ask(db, "없는회사이름 견적서 수정하고 싶어")
            check("찾은 영업 건이 없습니다" in out and "tab=quote&new=manual" in out, "못 찾음 → 수기 작성·최근 건", out)

            # 이동 버튼 주소는 모두 화면이 아는 주소만
            for t in (f"{CUST} 견적서 수정", f"{CUST2} 영업 건 열어줘", "QT-ZZ20261002-01 출하 기록 작성"):
                for _, href in BTN.findall(await ask(db, t)):
                    check(bool(re.fullmatch(r"/proposals/recommend\?tab=(quote|deals|order)&(quote|deal|new)=\w+(&focus=ship)?", href)),
                          f"버튼 주소 형식 {href}", href)
        finally:
            await cleanup(db)


def main() -> None:
    detect_cases()
    screen_cases()
    asyncio.run(db_cases())
    for p in PASS:
        print("PASS", p)
    for f, d in FAIL:
        print("FAIL", f, "\n   ", d.replace("\n", "\n    "))
    print(f"\n{len(PASS)} PASS / {len(FAIL)} FAIL")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
