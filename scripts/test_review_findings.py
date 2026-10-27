# -*- coding: utf-8 -*-
"""2026-10-26 코드 검토에서 나온 지적이 실제로 재현되는지 확인(대여 관리·A/S·S/N 검색·영업 건 관리).
FAIL = 문제가 재현됨(고칠 대상). 고친 뒤 다시 돌리면 PASS 가 되도록 '바른 동작'을 기대값으로 적었다.

    MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -e PYTHONPATH=/app -e PYTHONIOENCODING=utf-8 \\
        -v ./backend/app:/app/app -v ./scripts:/scripts backend python /scripts/test_review_findings.py
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import sys

from sqlalchemy import text

from app import migrations
from app.company_knowledge import quote_session as qs
from app.database import SessionLocal
from app.sales_deals import flow, order_agent, rental_query
from app.sales_deals import service as svc
from app.sales_deals import units

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []
CUST = "__테스트_검토재현__"
TODAY = dt.date(2026, 10, 26)


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


def msg(t: str) -> list[dict]:
    return [{"role": "user", "content": t}]


# ── 함수만(DB 없음) ──
for t in ("첨부한 거래내역에서 임대료 현황 알려줘", "대여 견적서 양식 알려줘", "렌탈 위약금 계산 방법 알려줘"):
    check(not rental_query.detect(msg(t)), f"대여-1 대여 목록 질문이 아닌 말을 가로채지 않음: '{t}'", "가로챔")
for t in ("렌탈한 제품 시리얼", "대여한 로봇 S/N 좀", "지금 빌려준 제품 뭐야"):
    check(rental_query.detect(msg(t)), f"대여-1 대여 목록 질문을 알아들음: '{t}'", "놓침")
R36 = {"purpose": "rental", "rental_months": 36}
check(qs.month_factor(R36, {"commodity": "NEGO", "name": "NEGO", "unit": ""}) == 1,
      "대여-2 올린 파일 경로(kind·row 없음)의 NEGO 줄은 × 개월 하지 않음", str(qs.month_factor(R36, {"commodity": "NEGO", "name": "NEGO", "unit": ""})))
for t in ("현재 10월 이후로 대여나간 제품들 뭐가 있지 시리얼 번호랑 같이 어떤 프로젝트의 어떤 제품인지 나열해줘",
          "렌탈 중인 로봇 목록 보여줘", "대여 제품 몇 대 나가 있어?", "대여 나간 S/N이랑 고객사 알려줘"):
    check(rental_query.detect(msg(t)), f"대여-1 원래 잡던 대여 목록 질문은 그대로: '{t}'", "놓침")
for t in ("대여 견적서 작성해줘", "대여 로봇 추천해줘", "렌탈료 얼마야", "10월 매출 알려줘"):
    check(not rental_query.detect(msg(t)), f"대여-1 목록 질문이 아님: '{t}'", "가로챔")
check(qs.month_factor(R36, {"name": "설치 인건비"}) == 1 and qs.month_factor(R36, {"name": "설치비"}) == 1
      and qs.month_factor(R36, {"name": "방역 로봇", "commodity": "방역 로봇"}) == 36,
      "대여-2 이름만 있는 줄: 설치비·인건비는 한 번, 제품은 × 개월")
from app.sales_deals import quote_upload as qu                 # noqa: E402
up = qu.clean_items([{"name": "방역 로봇", "qty": 1, "unit_price": 1_000_000}, {"name": "NEGO", "qty": 1, "unit_price": -1_000_000}],
                    check_total=False)
amt = sum(x["qty"] * x["unit_price"] * qs.month_factor(R36, {"name": x["name"]}) for x in up)
check(amt == 35_000_000, "대여-2 월 100만 × 36개월 + NEGO −100만(총 할인액) = 3,500만 (0원 아님)", f"{amt:,}")
s = rental_query.since_date("10월 이전 대여 나간 제품", TODAY)
check(s is None or s < dt.date(2026, 10, 1), "대여-8 '10월 이전'을 10/1 이후로 거르지 않음", str(s))
for t in ("지난달 대여 나간 제품", "최근 3개월 대여 나간 제품"):
    check(rental_query.since_date(t, TODAY) is not None, f"대여-8 기간 조건을 무시하지 않음: '{t}'", "None(전체 목록)")
D = dt.date
for t, want in (("10월 이전 대여 나간 제품", (None, D(2026, 9, 30))),
                ("10월 이후 대여 나간 제품", (D(2026, 10, 1), None)),
                ("9월부터 10월까지 대여 나간 제품", (D(2026, 9, 1), D(2026, 10, 31))),
                ("10월까지 대여 나간 제품", (None, D(2026, 10, 31))),
                ("10월에 대여 나간 제품", (D(2026, 10, 1), D(2026, 10, 31))),
                ("지난달 대여 나간 제품", (D(2026, 9, 1), D(2026, 9, 30))),
                ("이번 달 대여 나간 제품", (D(2026, 10, 1), None)),
                ("최근 3개월 대여 나간 제품", (D(2026, 7, 26), None)),
                ("최근 2주 대여 나간 제품", (D(2026, 10, 12), None)),
                ("작년 대여 나간 제품", (D(2025, 1, 1), D(2025, 12, 31))),
                ("10.1 이후 대여 나간 제품", (D(2026, 10, 1), None)),
                ("2026년 이후 대여 나간 제품", (D(2026, 1, 1), None)),
                ("10/5 이전 대여 나간 제품", (None, D(2026, 10, 4))),
                ("대여 나간 제품 목록", (None, None))):
    p = rental_query.period(t, TODAY)
    check((p["since"], p["until"]) == want and not p["unclear"], f"대여-8 기간: '{t}'", str(p))
check(rental_query.period("이번 주 대여 나간 제품", TODAY)["unclear"], "대여-8 못 알아든 기간('이번 주')은 전체 목록 안내",
      str(rental_query.period("이번 주 대여 나간 제품", TODAY)))
ans = rental_query.answer({"rows": [], "undated": [], "unregistered": []}, None, None, True)
check(ans.startswith("말씀하신 기간을 알아듣지 못해"), "대여-8 안내 문구가 답 맨 앞", ans[:40])
check(units.warranty_state({"warranty_start": "2026-10-01", "warranty_months": 0, "warranty_until": "2026-09-30"}, TODAY) == "무상 없음"
      and units.warranty_until(dt.date(2026, 10, 1), None) is None
      and units.warranty_state({"warranty_start": "2026-10-01", "warranty_months": None, "warranty_until": None}, TODAY) == "무상 기간 미정",
      "A/S-2 0개월 → '무상 없음', 개월 비움 → 그대로 '무상 기간 미정'")
check(units.warranty_until(dt.date(2026, 10, 1), 0) == dt.date(2026, 9, 30),
      "A/S-2 무상 0개월 = 무상 없음(미정이 아님)", str(units.warranty_until(dt.date(2026, 10, 1), 0)))
check(units.suggest_charge({"warranty_until": units.warranty_until(dt.date(2026, 10, 1), 0)}, dt.date(2026, 10, 2), "제품 결함")[1] == "유상",
      "A/S-2 무상 0개월 제품의 A/S 제안 = 유상", str(units.suggest_charge({"warranty_until": None}, dt.date(2026, 10, 2), "제품 결함")))
deal0 = {"id": 0, "deal_no": "X", "customer": "C", "quote": {}, "statement": None, "flow": {"terms": []}}
st0 = {"draft": {"buyer": {}, "items": [{"name": n, "qty": 1, "unit_price": 1} for n in "ABCD"]}}
try:
    st1, *_ = order_agent.apply_ops(deal0, st0, [{"op": "remove_item", "index": 1}, {"op": "remove_item", "index": 2}], "1번 2번 빼줘")
    left = [it["name"] for it in st1["draft"]["items"]]
    check(left == ["A", "D"], "건관리-9 AI 수주 대화에서 1·2번 품목을 한 번에 빼면 B·C 가 빠짐", str(left))
    st2, *_ = order_agent.apply_ops(deal0, st0, [{"op": "remove_item", "index": 0}, {"op": "item", "index": 3, "qty": 5}], "0번 빼고 3번 수량 5개로")
    got = [(it["name"], it["qty"]) for it in st2["draft"]["items"]]
    check(got == [("B", 1), ("C", 1), ("D", 5)], "건관리-9 같은 차례에서 빼기 뒤 수정도 처음 번호(D) 기준", str(got))
    st3, *_ = order_agent.apply_ops(deal0, st0, [{"op": "remove_item", "index": 1}, {"op": "remove_item", "index": 1}], "1번 빼줘")
    check([it["name"] for it in st3["draft"]["items"]] == ["A", "C", "D"], "건관리-9 같은 번호를 두 번 빼라고 해도 하나만 빠짐",
          str([it["name"] for it in st3["draft"]["items"]]))
except Exception as e:                                          # noqa: BLE001
    check(False, "건관리-5 AI 수주 대화 품목 빼기 실행", f"{type(e).__name__}: {e}")
usd = {"pay_case": "x", "quote": {"amount": 5000, "currency": "USD"}}
check(not (4001 >= flow.expected_total(usd) - flow.pay_tolerance(usd)), "건관리-2 $5,000 건에 $4,001 입금은 완납이 아님", "완납 처리됨")
krw = {"pay_case": "x", "quote": {"amount": 5_000_000}}
check(4_999_500 >= flow.expected_total(krw) - flow.pay_tolerance(krw), "건관리-2 원화 건은 1,000원 차이까지 완납", "완납 안 됨")


# ── DB ──
async def cleanup(db) -> None:
    ids = (await db.execute(text("SELECT id FROM sales_deals WHERE customer = :c"), {"c": CUST})).scalars().all()
    if ids:
        await db.execute(text("DELETE FROM as_cases WHERE deal_id = ANY(:i)"), {"i": ids})
        await db.execute(text("DELETE FROM product_quotes WHERE deal_id = ANY(:i)"), {"i": ids})
        await db.execute(text("DELETE FROM sales_deals WHERE id = ANY(:i)"), {"i": ids})
    await db.execute(text("DELETE FROM as_cases WHERE serial LIKE 'RV-%'"))
    await db.execute(text("DELETE FROM product_units WHERE serial LIKE 'RV-%'"))
    await db.execute(text("DELETE FROM sales_deal_trash WHERE deal->>'customer' = :c"), {"c": CUST})
    await db.commit()


async def new_deal(db, no: str, amount: int = 10_000_000) -> tuple[int, int]:
    lines = [{"name": "TCV3 툴체인저", "qty": 1, "unit_price": amount}]
    quote = {"no": no, "date": "2026-10-01", "amount": amount, "currency": "KRW", "items": svc.snapshot_items(lines), "revision": 1}
    deal_id = (await db.execute(text(
        "INSERT INTO sales_deals (deal_no, kind, customer, owner, title, base_date, quote, won) VALUES "
        "(:no, 'quote', :c, 'Test', 'TCV3', '2026-10-01', CAST(:q AS jsonb), TRUE) RETURNING id"),
        {"no": no, "c": CUST, "q": json.dumps(quote, ensure_ascii=False)})).scalar_one()
    hist = [{"revision": 0, "quote_no": no, "lines": lines, "total": amount, "currency": "KRW", "issued_at": "2026-10-01T01:00:00+00:00"}]
    qid = (await db.execute(text(
        "INSERT INTO product_quotes (deal_id, status, quote_no, customer, revision, history, total) "
        "VALUES (:d, 'issued', :no, :c, 0, CAST(:h AS jsonb), :t) RETURNING id"),
        {"d": deal_id, "no": no, "c": CUST, "h": json.dumps(hist, ensure_ascii=False), "t": amount})).scalar_one()
    await db.commit()
    return deal_id, qid


async def guarded(db, label: str, coro) -> None:
    try:
        await coro
    except Exception as e:                                      # noqa: BLE001
        await db.rollback()
        check(False, label, f"{type(e).__name__}: {e}")


async def as_progress(db, tag: str) -> None:
    deal_id, qid = await new_deal(db, "QT-RV20261026-01")
    v = await units.save_units(db, deal_id, None, qid, 0, [{"line_no": 0, "units": [{"serial": f"RV-{tag}-1", "warranty_months": 12}]}])
    uid = v["lines"][0]["units"][0]["id"]
    await db.execute(text("UPDATE product_units SET purpose = 'D' WHERE id = :i"), {"i": uid})     # 데모 제품
    await db.commit()
    case = await units.create_case(db, None, uid, {"status": "진행 중", "symptom": "테스트"})
    p = (await db.execute(text("SELECT purpose FROM product_units WHERE id = :i"), {"i": uid})).scalar()
    check(p == "A", "A/S-1 접수 때 '진행 중'으로 고르면 제품 상태가 A(A/S 중)", f"purpose={p}")
    await units.update_case(db, None, case["id"], {"status": "완료"})
    p = (await db.execute(text("SELECT purpose FROM product_units WHERE id = :i"), {"i": uid})).scalar()
    check(p == "D", "A/S-1 그 A/S 를 완료하면 원래 상태(D 데모)로 돌아감", f"purpose={p}")
    c2 = await units.create_case(db, None, uid, {"symptom": "접수만"})
    p = (await db.execute(text("SELECT purpose FROM product_units WHERE id = :i"), {"i": uid})).scalar()
    check(p == "D" and c2["status"] == "접수", "A/S-1 '접수'로만 받으면 제품 상태는 그대로", f"purpose={p}")
    await units.update_case(db, None, c2["id"], {"status": "완료"})       # 같은 S/N 중복 접수는 막으므로(사용자 2026-10-27) 먼저 끝낸다
    await units.create_cases(db, None, [uid], {"status": "진행 중", "symptom": "일괄"})
    p = (await db.execute(text("SELECT purpose FROM product_units WHERE id = :i"), {"i": uid})).scalar()
    check(p == "A", "A/S-1 여러 S/N 한 번에 접수(진행 중)도 A", f"purpose={p}")


async def deleted_deal_serial(db, tag: str) -> None:
    deal_id, qid = await new_deal(db, "QT-RV20261026-02")
    await units.save_units(db, deal_id, None, qid, 0, [{"line_no": 0, "units": [{"serial": f"RV-{tag}-2", "warranty_months": 12}]}])
    await svc.delete_deals(db, [deal_id], None)
    left = (await db.execute(text("SELECT deal_id FROM product_units WHERE serial = :s"), {"s": f"RV-{tag}-2"})).all()
    # 사용자 2026-10-26: 지운 건의 S/N 도 기록으로 남기고(어느 건·고객사였는지), S/N 은 겹치지 않으므로 다시 등록은 막는다
    u = (await db.execute(text("SELECT deal_id, status, note FROM product_units WHERE serial = :s"), {"s": f"RV-{tag}-2"})).mappings().one()
    check(u["deal_id"] is None and u["status"] == units.DELETED and "QT-RV20261026-02" in u["note"] and CUST in u["note"],
          "S/N-5 건을 지워도 S/N 은 남고, 상태 '건 삭제' + 메모에 건번호·고객사", str(dict(u)))
    found = await units.search_units(db, CUST, TODAY)
    check(any(x["serial"] == f"RV-{tag}-2" for x in found), "S/N-5 지운 건의 고객사 이름으로 찾으면 나옴", str([x["serial"] for x in found]))
    d2, q2 = await new_deal(db, "QT-RV20261026-03")
    try:
        await units.save_units(db, d2, None, q2, 0, [{"line_no": 0, "units": [{"serial": f"RV-{tag}-2", "warranty_months": 12}]}])
        check(False, "S/N-5 지운 건의 S/N 을 다시 등록하면 막고 지운 건이라고 알려 줌", "등록됨")
    except svc.DealError as e:
        await db.rollback()
        check("지운 영업 건" in str(e) and "QT-RV20261026-02" in str(e), "S/N-5 지운 건의 S/N 을 다시 등록하면 막고 지운 건이라고 알려 줌", str(e))


async def replaced_after_confirm(db, tag: str) -> None:
    """사용자 2026-10-26: 입금 확인으로 완납이 되면 그 뒤로는 견적(품목·금액)을 못 바꾼다."""
    deal_id, qid = await new_deal(db, "QT-RV20261026-04")
    await svc.set_terms(db, deal_id, None, [{"label": "선금", "pct": 30}, {"label": "잔금", "pct": 70}])
    await svc.confirm_term(db, deal_id, None, 0, TODAY)
    await svc.confirm_term(db, deal_id, None, 1, TODAY)
    d = await svc.get_deal(db, deal_id, TODAY)
    check(d["paid_full"], "건관리-7 회차를 모두 확인하면 완납", str(d["paid_full"]))
    from app.company_knowledge import product_recommend as pr      # noqa: E402
    qv = pr._quote_view(await pr._quote_row(db, qid, None))
    check(qv["deal_paid_full"] is True, "건관리-7 완납 건의 견적서 화면에 '완납' 표시가 와서 발행 전 확인 창이 뜸", str(qv.get("deal_paid_full")))
    rep = {"doc_no": "QT-RV20261026-04", "revision": 1, "currency": "KRW", "file": "x.xlsx",
           "items": [{"name": "TCV3 툴체인저", "qty": 1, "unit_price": 12_000_000}], "total": 12_000_000}
    try:
        await svc.quote_replaced(db, deal_id, None, rep, latest=True)
        check(False, "건관리-7 완납 뒤 같은 번호 견적서 파일 교체는 막음", "교체됨")
    except svc.DealError as e:
        await db.rollback()
        check("완납" in str(e), "건관리-7 완납 뒤 같은 번호 견적서 파일 교체는 막음", str(e))
    d = await svc.get_deal(db, deal_id, TODAY)
    check(d["quote"]["amount"] == 10_000_000, "건관리-7 완납 건 견적 금액은 그대로", str(d["quote"]["amount"]))
    # 다른 판으로 수주 바꾸기도 막음 — 2차 판(1,200만)을 만들어 그 판으로 수주 시도
    hist = [{"revision": 0, "quote_no": "QT-RV20261026-04", "lines": [{"name": "TCV3 툴체인저", "qty": 1, "unit_price": 10_000_000}],
             "total": 10_000_000, "currency": "KRW", "issued_at": "2026-10-01T01:00:00+00:00"},
            {"revision": 1, "quote_no": "QT-RV20261026-05", "lines": [{"name": "TCV3 툴체인저", "qty": 1, "unit_price": 12_000_000}],
             "total": 12_000_000, "currency": "KRW", "issued_at": "2026-10-02T01:00:00+00:00"}]
    await db.execute(text("UPDATE product_quotes SET history = CAST(:h AS jsonb), revision = 1 WHERE id = :i"),
                     {"h": json.dumps(hist, ensure_ascii=False), "i": qid})
    await db.commit()
    try:
        await svc.set_won(db, deal_id, None, True, None, {"quote_id": qid, "revision": 1})
        check(False, "건관리-7 완납 뒤 다른 판으로 수주 바꾸기는 막음", "바뀜")
    except svc.DealError as e:
        await db.rollback()
        check("완납" in str(e), "건관리-7 완납 뒤 다른 판으로 수주 바꾸기는 막음", str(e))
    # 완납 전(선금만 확인)에는 지금처럼 교체 가능
    d2, _ = await new_deal(db, "QT-RV20261026-06")
    await svc.set_terms(db, d2, None, [{"label": "선금", "pct": 30}, {"label": "잔금", "pct": 70}])
    await svc.confirm_term(db, d2, None, 0, TODAY)
    await svc.quote_replaced(db, d2, None, {**rep, "doc_no": "QT-RV20261026-06"}, latest=True)
    await db.commit()
    check((await svc.get_deal(db, d2, TODAY))["quote"]["amount"] == 12_000_000, "건관리-7 완납 전에는 견적 교체 가능(지금과 같음)")


async def payment_unsets_full(db, tag: str) -> None:
    deal_id, _ = await new_deal(db, "QT-RV20261026-05")
    await svc.set_paid_full(db, deal_id, None, True)
    await svc.add_payment(db, deal_id, None, {"date": TODAY, "amount": 1000, "full": False})
    d = await svc.get_deal(db, deal_id, TODAY)
    check(d["paid_full"], "건관리-8 완납 건에 입금 한 줄을 더해도(완납 체크 없이) 완납이 풀리지 않음", "완납 풀림")


async def pick_rental(db, tag: str) -> None:
    """사용자 2026-10-26: 판을 골라 수주하면 대여 개월 수·월 납입금·끝나는 날도 그 판 기준(판 기록에 남긴 값)."""
    from app.company_knowledge import product_recommend as pr      # noqa: E402
    check(pr._snap_biz({"purpose": "rental", "rental_months": 24, "rental_start": "2026-11-01"})
          == {"biz": "rental", "rental": {"months": 24, "start": "2026-11-01"}} and pr._snap_biz({"purpose": "sale"}) == {"biz": "sale"},
          "대여-판 발행 기록에 판매·대여 구분과 개월 수·시작일을 남김")
    L = [{"name": "방역 로봇", "qty": 1, "unit_price": 1_000_000, "months": 24}]
    L36 = [{"name": "방역 로봇", "qty": 1, "unit_price": 1_000_000, "months": 36}]
    hist = [{"revision": 0, "quote_no": "QT-RV20261026-11", "lines": L, "total": 24_000_000, "currency": "KRW",
             "issued_at": "2026-10-01T01:00:00+00:00", "biz": "rental", "rental": {"months": 24, "start": None}},
            {"revision": 1, "quote_no": "QT-RV20261026-12", "lines": L36, "total": 36_000_000, "currency": "KRW",
             "issued_at": "2026-10-02T01:00:00+00:00", "biz": "rental", "rental": {"months": 36, "start": None}},
            {"revision": 2, "quote_no": "QT-RV20261026-13", "lines": [{"name": "방역 로봇", "qty": 1, "unit_price": 30_000_000}],
             "total": 30_000_000, "currency": "KRW", "issued_at": "2026-10-03T01:00:00+00:00", "biz": "sale"},
            {"revision": 3, "quote_no": "QT-RV20261026-14", "lines": L, "total": 24_000_000, "currency": "KRW",
             "issued_at": "2026-10-04T01:00:00+00:00"}]
    deal_id, qid = await new_deal(db, "QT-RV20261026-12", amount=36_000_000)
    await db.execute(text("UPDATE sales_deals SET won = FALSE, quote = quote || CAST(:q AS jsonb) WHERE id = :d"),
                     {"q": json.dumps({"biz": "rental", "rental": {"months": 36, "start": "2026-11-01", "end": "2029-10-31",
                                                                   "monthly": 1_000_000}}), "d": deal_id})
    await db.execute(text("UPDATE product_quotes SET history = CAST(:h AS jsonb), revision = 3 WHERE id = :i"),
                     {"h": json.dumps(hist, ensure_ascii=False), "i": qid})
    await db.commit()
    await svc.set_won(db, deal_id, None, True, TODAY, {"quote_id": qid, "revision": 0})
    q = (await svc.get_deal(db, deal_id, TODAY))["quote"]
    r = q.get("rental") or {}
    check(q.get("amount") == 24_000_000 and r.get("months") == 24 and r.get("monthly") == 1_000_000
          and r.get("start") == "2026-11-01" and r.get("end") == "2028-10-31",
          "대여-판 24개월 판으로 수주 → 24개월·월 100만·끝나는 날 2028-10-31(시작일은 정해 둔 값)", str(q))
    await db.execute(text("UPDATE sales_deals SET won = FALSE WHERE id = :d"), {"d": deal_id})
    await db.commit()
    await svc.set_won(db, deal_id, None, True, TODAY, {"quote_id": qid, "revision": 2})
    q = (await svc.get_deal(db, deal_id, TODAY))["quote"]
    check(q.get("biz") is None and not q.get("rental"), "대여-판 판매 판으로 수주 → 판매 건(대여 조건 없음)", str(q))
    await db.execute(text("UPDATE sales_deals SET won = FALSE, quote = quote || CAST(:q AS jsonb) WHERE id = :d"),
                     {"q": json.dumps({"biz": "rental", "rental": {"months": 36, "start": None, "end": None, "monthly": 1_000_000}}),
                      "d": deal_id})
    await db.commit()
    await svc.set_won(db, deal_id, None, True, TODAY, {"quote_id": qid, "revision": 3})
    d = await svc.get_deal(db, deal_id, TODAY)
    check(d["quote"]["rental"]["months"] == 36 and any(a["code"] == "RENTAL_UNVERIFIED" for a in d["alerts"]),
          "대여-판 기록 없는 예전 판 → 지금 값 유지 + 확인 알림", str(d["alerts"]))


def _xlsx(months: int | None, *, keep_formula: bool = True) -> bytes:
    """대여(months) 또는 판매(None) 견적서 엑셀 — 엑셀에서 고친 파일 흉내(keep_formula=False 면 월 납입금 식을 값으로 덮음)."""
    import io

    import openpyxl

    from app.company_knowledge import quote_xlsx as qx      # noqa: E402
    st = qs.draft_state({"applicable": True, "items": []}, model="", payload_kg=None, form={
        "customer": CUST, "to": "담당자", "contact_name": "홍길동", "initials": "RV",
        "contact_mobile": "010-0000-0000", "contact_email": "name@example.com",
        **({"purpose": "rental", "rental_months": months} if months else {})})
    st.update(manual=True, asked=["extra"], delivery={"case": 1, "freight": None}, next_id=2)
    st["lines"] = [{"id": "L1", "kind": "extra", "key": "extra", "name": "방역 로봇", "commodity": "Robot", "qty": 1,
                    "unit_price": 1_000_000, "remark": "", "price_status": "set"}]
    data = qx.build_xlsx(qx.quote_header(st["form"], lang="ko", quote_no="QT-RV20261027-01", day=TODAY, pogo=False),
                         qs.render_lines(st, "ko", None))
    if keep_formula:
        return data
    wb = openpyxl.load_workbook(io.BytesIO(data))
    ws = wb.worksheets[0]
    for row in ws.iter_rows():
        for c in row:
            if isinstance(c.value, str) and c.value.upper().startswith("=ROUNDUP("):
                c.value = 1_000_000
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


async def replace_rental(db, tag: str) -> None:
    """사용자 2026-10-27: 대여 견적서 파일을 바꿔 올리면 개월 수는 파일 기준, 시작일은 그대로, 끝나는 날·월 납입금 다시 계산.
    판매/대여가 다른 파일은 막는다."""
    from app.company_knowledge import product_recommend as pr      # noqa: E402
    from app.company_knowledge import quote_readback as rb         # noqa: E402
    pf = rb.parse(_xlsx(24))["form"]
    check(pf.get("purpose") == "rental" and pf.get("rental_months") == 24, "대여-교체 대여 견적서 파일에서 개월 수 24 를 읽음", str(pf))
    pf = rb.parse(_xlsx(None))["form"]
    check(pf.get("purpose") == "sale", "대여-교체 판매 견적서 파일은 판매로 읽음", str(pf.get("purpose")))
    pf = rb.parse(_xlsx(24, keep_formula=False))["form"]
    check(pf.get("purpose") == "rental" and not pf.get("rental_months"),
          "대여-교체 월 납입금 식을 지운 대여 파일도 대여로 읽음(개월 수는 모름)", str(pf))
    form = {"purpose": "rental", "rental_months": 36, "rental_start": "2026-11-01"}
    check(pr._upload_biz(form, rb.parse(_xlsx(24)))["rental_months"] == 24, "대여-교체 올린 파일의 개월 수(24)로 곱함")
    check(pr._upload_biz(form, rb.parse(_xlsx(24, keep_formula=False)))["rental_months"] == 36,
          "대여-교체 파일에서 개월 수를 못 읽으면 작성 중인 값(36)")
    check(pr._upload_biz(form, None) is form, "대여-교체 회사 양식이 아닌 파일은 작성 중인 값")
    for f_, parsed, label in ((form, rb.parse(_xlsx(None)), "대여 견적에 판매 파일"),
                              ({"purpose": "sale"}, rb.parse(_xlsx(24)), "판매 견적에 대여 파일")):
        try:
            pr._upload_biz(f_, parsed)
            check(False, f"대여-교체 {label}을 올리면 막음", "통과됨")
        except pr.RecommendError as e:
            check("판매/대여" in str(e), f"대여-교체 {label}을 올리면 막음", str(e))

    deal_id, _ = await new_deal(db, "QT-RV20261027-01", amount=36_000_000)
    await db.execute(text("UPDATE sales_deals SET won = FALSE, quote = quote || CAST(:q AS jsonb) WHERE id = :d"),
                     {"q": json.dumps({"biz": "rental", "rental": {"months": 36, "start": "2026-11-01", "end": "2029-10-31",
                                                                   "monthly": 1_000_000}}), "d": deal_id})
    await db.commit()
    rep = {"doc_no": "QT-RV20261027-01", "revision": 1, "currency": "KRW", "file": "x.xlsx", "total": 24_000_000,
           "items": [{"name": "방역 로봇", "qty": 1, "unit_price": 24_000_000}]}
    await svc.quote_replaced(db, deal_id, None, {**rep, "rental": {"months": 24, "start": "2026-12-01"}}, latest=True)
    await db.commit()
    q = (await svc.get_deal(db, deal_id, TODAY))["quote"]
    r = q.get("rental") or {}
    check(q.get("amount") == 24_000_000 and r.get("months") == 24 and r.get("monthly") == 1_000_000
          and r.get("start") == "2026-11-01" and r.get("end") == "2028-10-31",
          "대여-교체 36개월 → 24개월 파일로 교체: 24개월·월 100만·시작일 그대로·끝나는 날 2028-10-31", str(q))
    await svc.quote_replaced(db, deal_id, None, {**rep, "total": 30_000_000, "rental": {"months": 24, "start": None},
                                                 "items": [{"name": "방역 로봇", "qty": 1, "unit_price": 30_000_000}]}, latest=True)
    await db.commit()
    r = (await svc.get_deal(db, deal_id, TODAY))["quote"]["rental"]
    check(r["monthly"] == 1_250_000 and r["months"] == 24, "대여-교체 금액만 바꾼 파일: 월 납입금 다시 계산(3,000만 ÷ 24 = 125만)", str(r))
    try:
        await svc.quote_replaced(db, deal_id, None, rep, latest=True)
        check(False, "대여-교체 대여 건에 판매 파일 교체는 막음", "교체됨")
    except svc.DealError as e:
        await db.rollback()
        check("판매/대여" in str(e), "대여-교체 대여 건에 판매 파일 교체는 막음", str(e))


async def reissue_start(db, tag: str) -> None:
    """사용자 2026-10-27: 견적서를 다시 발행해도 건에서 정한 대여 시작일은 그대로, 끝나는 날은 새 개월 수로."""
    deal_id, _ = await new_deal(db, "QT-RV20261027-02", amount=36_000_000)
    await db.execute(text("UPDATE sales_deals SET won = FALSE, quote = quote || CAST(:q AS jsonb) WHERE id = :d"),
                     {"q": json.dumps({"biz": "rental", "rental": {"months": 36, "start": "2026-11-01", "end": "2029-10-31",
                                                                   "monthly": 1_000_000}}), "d": deal_id})
    await db.commit()
    p = {"quote_date": TODAY, "customer": CUST, "vat_included": False, "currency": "KRW", "doc_no": "QT-RV20261027-03",
         "items": [{"name": "방역 로봇", "qty": 1, "unit_price": 24_000_000}], "rental": {"months": 24, "start": None}}
    await svc.quote_deal(db, None, None, p, 2, deal_id=deal_id)
    await db.commit()
    r = (await svc.get_deal(db, deal_id, TODAY))["quote"].get("rental") or {}
    check(r.get("start") == "2026-11-01" and r.get("end") == "2028-10-31" and r.get("months") == 24 and r.get("monthly") == 1_000_000,
          "대여-재발행 시작일 없는 견적서로 재발행해도 건의 시작일 유지, 끝나는 날은 24개월로", str(r))
    await svc.quote_deal(db, None, None, {**p, "rental": {"months": 24, "start": "2026-10-01"}}, 3, deal_id=deal_id)
    await db.commit()
    r = (await svc.get_deal(db, deal_id, TODAY))["quote"].get("rental") or {}
    check(r.get("start") == "2026-11-01", "대여-재발행 견적서에 옛 시작일(10/1)이 남아 있어도 건에서 정한 11/1 유지", str(r))
    await db.execute(text("UPDATE sales_deals SET quote = jsonb_set(quote, '{rental,start}', 'null') WHERE id = :d"), {"d": deal_id})
    await db.commit()
    await svc.quote_deal(db, None, None, {**p, "rental": {"months": 24, "start": "2026-12-01"}}, 4, deal_id=deal_id)
    await db.commit()
    r = (await svc.get_deal(db, deal_id, TODAY))["quote"].get("rental") or {}
    check(r.get("start") == "2026-12-01" and r.get("end") == "2028-11-30", "대여-재발행 건에 시작일이 없으면 견적서의 시작일을 씀", str(r))
    await svc.quote_deal(db, None, None, {k: v for k, v in p.items() if k != "rental"}, 5, deal_id=deal_id)
    await db.commit()
    q = (await svc.get_deal(db, deal_id, TODAY))["quote"]
    check(q.get("biz") is None and not q.get("rental"), "대여-재발행 판매 견적서로 재발행하면 판매 건(대여 조건 없음)", str(q))


async def upload_start_step(db, tag: str) -> None:
    """사용자 2026-10-27: 대여 견적서 파일을 올려 발행할 때는 대여 시작일을 한 번 더 확인해야 발행된다."""
    import io

    from app.company_knowledge import product_recommend as pr      # noqa: E402
    from app.company_knowledge import quote_readback as rb         # noqa: E402
    from app.sales_deals import quote_upload as qu                 # noqa: E402
    from app.storage import put_object, remove_object               # noqa: E402
    uid = (await db.execute(text("SELECT id FROM users WHERE username = 'alex'"))).scalar()
    q = await pr.quote_manual(user_id=uid, form={"initials": "RV", "contact_name": "Alex", "contact_mobile": "010-0000-0000",
                                                  "contact_email": "t@example.com"})
    q = await pr.quote_edit(q["id"], user_id=uid, payload={
        "form": {"customer": CUST, "subject": "대여", "purpose": "rental", "rental_months": 36}, "delivery": {"case": 1},
        "lines": [{"id": None, "name": "방역 로봇", "qty": 1, "unit_price": 1_000_000, "remark": ""}]})
    q = await pr.quote_issue(q["id"], user_id=uid)
    deal_id = q["deal_id"]
    await svc.set_rental_start(db, deal_id, uid, dt.date(2026, 11, 1))
    data = _xlsx(24)
    key = f"product-quotes/{q['id']}/rv-{tag}.xlsx"
    put_object(key=key, data=io.BytesIO(data), length=len(data), mime=qu.TYPES[".xlsx"])
    up = {"key": key, "filename": "대여견적.xlsx", "mime": qu.TYPES[".xlsx"], "size": len(data)}
    items = [{"name": "방역 로봇", "spec": "", "qty": 1, "unit_price": 1_000_000}]
    try:
        sp = await pr.upload_sync_preview(qid=q["id"], deal_id=None, user_id=uid, owner_check=True,
                                          read={"items": items, "_parsed": rb.parse(data)})
        check(sp.get("rental") == {"months": 24, "start": "2026-11-01"} and not sp.get("blocked"),
              "대여-확인 올리기 확인 창에 대여(파일 기준 24개월)·건의 시작일 11/1 을 보여 줌", str(sp.get("rental")))
        try:
            await pr.quote_issue_upload(qid=q["id"], deal_id=None, user_id=uid, owner_check=True, upload=up, items=items,
                                        initials="RV", doc_no="")
            check(False, "대여-확인 시작일 확인 없이는 발행 안 됨", "발행됨")
        except pr.RecommendError as e:
            check("대여 시작일" in str(e), "대여-확인 시작일 확인 없이는 발행 안 됨", str(e))
        res = await pr.quote_issue_upload(qid=q["id"], deal_id=None, user_id=uid, owner_check=True, upload=up, items=items,
                                          initials="RV", doc_no="", rental_start=dt.date(2026, 12, 1), rental_checked=True)
        r = (await svc.get_deal(db, deal_id, TODAY))["quote"]["rental"]
        check(r["start"] == "2026-12-01" and r["end"] == "2028-11-30" and r["months"] == 24 and r["monthly"] == 1_000_000,
              "대여-확인 확인 창에서 고른 12/1 이 건의 시작일 — 24개월, 끝나는 날 2028-11-30, 월 100만", str(r))
        snap = (await db.execute(text("SELECT history->-1->'rental' FROM product_quotes WHERE id = :i"), {"i": q["id"]})).scalar()
        check(snap == {"months": 24, "start": "2026-12-01"}, "대여-확인 발행 기록(판)에도 24개월·12/1", str(snap))
        await pr.quote_issue_upload(qid=q["id"], deal_id=None, user_id=uid, owner_check=True, upload=up, items=items,
                                    initials="RV", doc_no=res["quote_no"], overwrite=True, rental_start=None, rental_checked=True)
        r = (await svc.get_deal(db, deal_id, TODAY))["quote"]["rental"]
        check(r["start"] is None and r["end"] is None, "대여-확인 같은 번호 교체에서 '미정으로 둠'을 고르면 시작일 미정", str(r))
        sale = _xlsx(None)
        sp = await pr.upload_sync_preview(qid=q["id"], deal_id=None, user_id=uid, owner_check=True,
                                          read={"items": items, "_parsed": rb.parse(sale)})
        check(bool(sp.get("blocked")) and "rental" not in sp, "대여-확인 대여 견적에 판매 파일을 올리면 확인 창에서 막음 안내", str(sp.get("blocked")))
    finally:
        remove_object(key)
        await db.rollback()
        await db.execute(text("DELETE FROM doc_numbers WHERE deal_id = :d OR quote_id = :q"), {"d": deal_id, "q": q["id"]})
        await db.commit()


async def rental_phase(db, tag: str) -> None:
    """사용자 2026-10-27: AI '대여 나간 제품' = 대여 중(출하)만. 대여 건의 A/S 중 제품은 포함, 출하 전 R 은 따로, 회수 뒤엔 다 빠짐."""
    deal_id, _ = await new_deal(db, "QT-RV20261027-21", amount=36_000_000)
    sale_id, _ = await new_deal(db, "QT-RV20261027-22")
    await db.execute(text("UPDATE sales_deals SET shipments = CAST(:s AS jsonb), quote = quote || CAST(:q AS jsonb) WHERE id = :d"),
                     {"s": json.dumps([{"date": "2026-10-05"}]), "d": deal_id,
                      "q": json.dumps({"biz": "rental", "rental": {"months": 36, "start": "2026-10-05", "end": "2029-10-04", "monthly": 1_000_000}})})
    for did, sn, purpose, st in ((deal_id, "11", "R", "출하"), (deal_id, "12", "R", "등록"), (deal_id, "13", "A", "출하"),
                                 (sale_id, "14", "A", "출하")):
        await db.execute(text("INSERT INTO product_units (deal_id, line_no, line_name, part_name, serial, purpose, status) "
                              "VALUES (:d, 0, '방역 로봇', '방역 로봇', :s, :p, :st)"),
                         {"d": did, "s": f"RV-{tag}-{sn}", "p": purpose, "st": st})
    await db.commit()
    data = await rental_query.rented_units(db, None)
    mine = lambda xs: sorted(x["serial"].rsplit("-", 1)[1] for x in xs if x["customer"] == CUST)   # noqa: E731
    check(mine(data["rows"]) == ["11", "13"], "대여-단계 '대여 나간 제품' = 대여 중(출하)인 R + 대여 건의 A/S 중 제품(판매 건 A/S 는 빠짐)",
          str(mine(data["rows"])))
    check(mine(data["waiting"]) == ["12"], "대여-단계 출하 전 R 은 '대여 전'으로 따로", str(mine(data["waiting"])))
    text_ = rental_query.answer(data, None)
    check(f"RV-{tag}-13" in text_ and "대여 중 · A/S 중" in text_ and "출하 전인 R 제품(대여 전)" in text_
          and text_.index(f"RV-{tag}-12") > text_.index("출하 전인 R 제품"),
          "대여-단계 답: A/S 중 표시, 출하 전 R 은 아래에 따로", text_[:400])
    await svc.set_rental_return(db, deal_id, None, "done", TODAY)
    data = await rental_query.rented_units(db, None)
    check(mine(data["rows"]) == [] and mine(data["waiting"]) == [], "대여-단계 회수를 마치면 출하 전이던 S/N 도 목록에서 빠짐",
          str((mine(data["rows"]), mine(data["waiting"]))))


async def ship_edit_keep(db, tag: str) -> None:
    """검증 2026-10-26: 남긴 출하 기록을 고쳐도 화면에 없는 정보(구분·담당·사진·출처·예외 출하 표시)는 그대로,
    회수를 마친 대여 건의 S/N 은 '회수' 그대로. (엑셀 이관 기록은 이관을 꺼 두어 다루지 않음 — 사용자 2026-10-27)"""
    deal_id, q31 = await new_deal(db, "QT-RV20261027-31")
    await units.save_units(db, deal_id, None, q31, 0, [{"line_no": 0, "units": [{"serial": f"RV-{tag}-30", "warranty_months": 12}]}])
    edit = {"date": dt.date(2026, 10, 1), "carrier": "화물", "tracking": "", "items": None, "qty_text": None, "receiver": "",
            "address": "", "purpose": "판매", "force": False, "lines": [{"name": "TCV3 툴체인저", "qty": 1, "photos": []}],
            "unit_ids": None}
    await svc.add_shipment(db, deal_id, None, edit)
    await db.execute(text("UPDATE sales_deals SET shipments = jsonb_set(jsonb_set(jsonb_set(shipments, '{0,purpose}', '\"샘플\"'), "
                          "'{0,owner}', '\"Kim\"'), '{0,forced}', 'true') WHERE id = :d"), {"d": deal_id})
    await db.commit()
    await svc.update_shipment(db, deal_id, None, 0, {**edit, "date": dt.date(2026, 10, 3), "carrier": "퀵", "tracking": "123 456"})
    s0 = (await svc.get_deal(db, deal_id, TODAY))["shipments"][0]
    check(s0["date"] == "2026-10-03" and s0["carrier"] == "퀵" and s0["tracking"] == ["123", "456"]
          and s0["purpose"] == "샘플" and s0["owner"] == "Kim" and s0["forced"] is True and s0["items"] == "TCV3 툴체인저 × 1",
          "출하수정 고친 칸(날짜·택배·운송장)만 바뀌고 구분·담당·예외 출하 표시는 그대로", str(s0))

    # 회수를 마친 대여 건
    r_id, qid = await new_deal(db, "QT-RV20261027-32")
    await db.execute(text("UPDATE sales_deals SET quote = quote || CAST(:q AS jsonb) WHERE id = :d"),
                     {"q": json.dumps({"biz": "rental", "rental": {"months": 12, "start": "2026-10-01", "end": "2027-09-30",
                                                                   "monthly": 1_000_000}}), "d": r_id})
    await db.commit()
    await units.save_units(db, r_id, None, qid, 0, [{"line_no": 0, "units": [{"serial": f"RV-{tag}-31", "warranty_months": 12,
                                                                             "purpose": "R"}]}])
    await svc.add_shipment(db, r_id, None, {**edit, "date": dt.date(2026, 10, 1), "lines": [{"name": "TCV3 툴체인저", "qty": 1, "photos": []}]})
    await svc.set_rental_return(db, r_id, None, "done", TODAY)
    await svc.update_shipment(db, r_id, None, 0, {**edit, "date": dt.date(2026, 10, 2), "carrier": "퀵"})
    st = (await db.execute(text("SELECT status FROM product_units WHERE serial = :s"), {"s": f"RV-{tag}-31"})).scalar()
    check(st == "회수", "출하수정 회수를 마친 대여 건의 출하 기록을 고쳐도 S/N 은 '회수' 그대로", str(st))


async def stale_index(db, tag: str) -> None:
    """검증 2026-10-26: 줄 번호로 고치는 요청은 그 자리의 기록이 화면에서 본 것과 같을 때만(여러 명이 같이 쓸 때)."""
    from types import SimpleNamespace

    from fastapi import HTTPException

    from app import api_sales_deals as api      # noqa: E402
    me = SimpleNamespace(id=None, role="admin", is_superuser=True)
    deal_id, qid = await new_deal(db, "QT-RV20261027-41")
    await svc.add_payment(db, deal_id, None, {"date": dt.date(2026, 10, 1), "amount": 1000, "full": False})
    await svc.add_payment(db, deal_id, None, {"date": dt.date(2026, 10, 2), "amount": 2000, "full": False})
    await svc.set_terms(db, deal_id, None, [{"label": "선금", "pct": 30}, {"label": "잔금", "pct": 70}])
    seen = await svc.get_deal(db, deal_id, TODAY)              # A 가 본 화면
    p0, p1 = seen["payments"]
    t1 = next(t for t in seen["flow"]["terms"] if t["index"] == 1)

    async def status_of(coro) -> int:
        try:
            await coro
            return 200
        except HTTPException as e:
            await db.rollback()
            return e.status_code

    # 화면에서 받은 그대로 보내면 통과(화면용 칸이 덧붙어 있어도)
    await svc.check_entry(db, deal_id, "payments", 1, p1)
    await svc.check_entry(db, deal_id, "pay_terms", 1, {"label": t1["label"], "pct": t1["pct"]})
    await db.rollback()
    # B 가 첫 입금을 지움 → A 가 보던 2번째 줄(index 1)을 지우려 하면 막힘(예전엔 없는 줄 오류 또는 엉뚱한 줄)
    await api.remove_entry(deal_id, api.RemoveIn(kind="payments", index=0, expect=p0), me, db)
    code = await status_of(api.remove_entry(deal_id, api.RemoveIn(kind="payments", index=1, expect=p1), me, db))
    left = [x["amount"] for x in (await svc.get_deal(db, deal_id, TODAY))["payments"]]
    check(code in (404, 409) and left == [2000], "동시사용 앞 줄이 지워진 뒤 예전 번호로 지우기 → 막고 다른 줄은 그대로", f"{code} {left}")
    code = await status_of(api.remove_entry(deal_id, api.RemoveIn(kind="payments", index=0, expect=p0), me, db))
    left = [x["amount"] for x in (await svc.get_deal(db, deal_id, TODAY))["payments"]]
    check(code == 409 and left == [2000], "동시사용 이미 지워진 줄을 다시 지우려 하면 그 자리의 다른 줄을 지우지 않고 막음", f"{code} {left}")
    # 결제 조건을 B 가 바꾼 뒤 A 가 예전 회차로 입금 확인 → 막힘
    await svc.set_terms(db, deal_id, None, [{"label": "선금", "pct": 50}, {"label": "잔금", "pct": 50}])
    code = await status_of(api.confirm_term(deal_id, api.TermConfirmIn(index=1, expect={"label": t1["label"], "pct": t1["pct"]}), me, db))
    d = await svc.get_deal(db, deal_id, TODAY)
    check(code == 409 and not any(t["confirmed"] for t in d["flow"]["terms"]), "동시사용 결제 조건이 바뀐 뒤 예전 회차로 입금 확인 → 막음", str(code))
    t1n = next(t for t in d["flow"]["terms"] if t["index"] == 1)
    code = await status_of(api.confirm_term(deal_id, api.TermConfirmIn(index=1, expect={"label": t1n["label"], "pct": t1n["pct"]}), me, db))
    check(code == 200, "동시사용 새 화면의 회차로는 입금 확인 됨", str(code))
    code = await status_of(api.confirm_term(deal_id, api.TermConfirmIn(index=0), me, db))
    check(code == 200, "동시사용 expect 없이 온 요청(예전 화면)은 그대로 처리", str(code))

    # 출하: B 가 첫 출하를 지운 뒤 A 가 두 번째 출하를 고치거나 S/N 을 묶으려 하면 막힘
    await units.save_units(db, deal_id, None, qid, 0, [{"line_no": 0, "units": [{"serial": f"RV-{tag}-41", "warranty_months": 12}]}])
    base = {"carrier": "화물", "tracking": "", "items": None, "qty_text": None, "receiver": "", "address": "", "purpose": "판매",
            "force": False, "unit_ids": None}
    await svc.add_shipment(db, deal_id, None, {**base, "date": dt.date(2026, 10, 1), "lines": [{"name": "TCV3 툴체인저", "qty": 1, "photos": []}]})
    await svc.add_shipment(db, deal_id, None, {**base, "date": dt.date(2026, 10, 5), "unit_ids": [],
                                               "lines": [{"name": "설치 키트", "qty": 1, "photos": []}]})
    s0, s1 = (await svc.get_deal(db, deal_id, TODAY))["shipments"]
    await api.remove_entry(deal_id, api.RemoveIn(kind="shipments", index=0, expect=s0), me, db)
    body = api.ShipmentEditIn(index=1, expect=s1, date=dt.date(2026, 10, 6), carrier="퀵", lines=[{"name": "설치 키트", "qty": 1, "photos": []}])
    code = await status_of(api.edit_shipment(deal_id, body, me, db))
    check(code == 409, "동시사용 앞 출하가 지워진 뒤 예전 번호로 출하 고치기 → 막음", str(code))
    ub = api.UnitsIn(quote_id=qid, revision=0, partial=True, shipment_index=1, shipment_expect=s1,
                     lines=[{"line_no": 0, "units": [{"serial": f"RV-{tag}-41", "warranty_months": 12}]}])
    code = await status_of(api.save_units(deal_id, ub, me, db))
    check(code == 409, "동시사용 예전 출하 번호로 S/N 묶기 → 막음", str(code))
    s_now = (await svc.get_deal(db, deal_id, TODAY))["shipments"]
    check(len(s_now) == 1 and s_now[0]["carrier"] == "화물", "동시사용 남은 출하 기록은 그대로", str(s_now))


async def past_due(db, tag: str) -> None:
    """검증 2026-10-26: 아직 입금 확인 안 한 회차에 지난 예정일을 새로 넣으면 막는다. 이미 있던 지난 날짜는 그대로 두고 다른 회차는 고칠 수 있다."""
    deal_id, _ = await new_deal(db, "QT-RV20261027-51")
    today = svc.kst_today()
    try:
        await svc.set_terms(db, deal_id, None, [{"label": "선금", "pct": 30, "expected_date": "2026-03-30"},
                                                {"label": "잔금", "pct": 70}], admin=False)
        check(False, "지난날짜 AI·직원이 지난 예정일(연도 착오)을 넣으면 막음", "저장됨")
    except svc.DealError as e:
        await db.rollback()
        check("2026-03-30" in str(e) and "연도" in str(e), "지난날짜 AI·직원이 지난 예정일(연도 착오)을 넣으면 막음", str(e))
    try:
        await svc.set_terms(db, deal_id, None, [{"label": "선금", "pct": 30, "expected_date": "2026-03-30"},
                                                {"label": "잔금", "pct": 70}], admin=True)
        check(False, "지난날짜 관리자도 지난 예정일은 새로 못 넣음(연장은 [기한 연장])", "저장됨")
    except svc.DealError:
        await db.rollback()
        check(True, "지난날짜 관리자도 지난 예정일은 새로 못 넣음(연장은 [기한 연장])")
    nxt = (today + dt.timedelta(days=10)).isoformat()
    await svc.set_terms(db, deal_id, None, [{"label": "선금", "pct": 30, "expected_date": nxt}, {"label": "잔금", "pct": 70}])
    check((await svc.get_deal(db, deal_id, today))["flow"]["terms"][0]["expected_date"] == nxt, "지난날짜 오늘 이후 날짜는 저장됨")
    old = (today - dt.timedelta(days=5)).isoformat()      # 날이 지나 이미 지난 예정일이 된 회차
    await db.execute(text("UPDATE sales_deals SET pay_terms = jsonb_set(pay_terms, '{0,expected_date}', to_jsonb(CAST(:p AS text))) "
                          "WHERE id = :i"), {"p": old, "i": deal_id})
    await db.commit()
    await svc.set_terms(db, deal_id, None, [{"label": "선금", "pct": 30, "expected_date": old},
                                            {"label": "잔금", "pct": 70, "expected_date": nxt}])
    ts = (await svc.get_deal(db, deal_id, today))["flow"]["terms"]
    check(ts[0]["expected_date"] == old and ts[1]["expected_date"] == nxt, "지난날짜 이미 지난 예정일은 그대로 두고 다른 회차는 고칠 수 있음", str(ts))
    from app.sales_deals import order_agent as oa      # noqa: E402
    msgs = oa.build_messages(await svc.get_deal(db, deal_id, today), oa.initial_state(await svc.get_deal(db, deal_id, today), None),
                             "선금 30 3월 30일")
    check(any(f"[오늘] {today.isoformat()}" in m["content"] for m in msgs), "지난날짜 AI 에게 오늘 날짜를 알려 줌(연도 착오 줄이기)")


async def search_literal(db, tag: str) -> None:
    """검증 2026-10-26: S/N 찾기에서 %·_ 는 글자 그대로, 목록이 잘리면 more 로 알린다."""
    from types import SimpleNamespace

    from app import api_sales_deals as api      # noqa: E402
    deal_id, qid = await new_deal(db, "QT-RV20261027-61")
    await units.save_units(db, deal_id, None, qid, 0, [{"line_no": 0, "units": [
        {"serial": f"RV-{tag}-A_1", "warranty_months": 12}, {"serial": f"RV-{tag}-AB1", "warranty_months": 12},
        {"serial": f"RV-{tag}-50%", "warranty_months": 12}]}])
    await db.execute(text("UPDATE sales_deals SET quote = jsonb_set(quote, '{items,0,qty}', '3') WHERE id = :d"), {"d": deal_id})
    await db.commit()
    got = sorted(x["serial"] for x in await units.search_units(db, f"RV-{tag}-A_", TODAY))
    check(got == [f"RV-{tag}-A_1"], "S/N찾기 '_' 는 글자 그대로(AB1 은 안 나옴)", str(got))
    got = sorted(x["serial"] for x in await units.search_units(db, "50%", TODAY))
    check(f"RV-{tag}-50%" in got and f"RV-{tag}-A_1" not in got, "S/N찾기 '%' 도 글자 그대로", str(got))
    me = SimpleNamespace(id=None, role="admin", is_superuser=True)
    r = await api.search_units(q="", _=me, db=db)
    total = (await db.execute(text("SELECT COUNT(*) FROM product_units"))).scalar()
    check(len(r["items"]) <= api.UNIT_LIST_MAX and r["more"] == (total > api.UNIT_LIST_MAX),
          "S/N찾기 다 못 보이면 more 로 알림", f"{len(r['items'])} / {total} more={r['more']}")


async def as_dup_drop(db, tag: str) -> None:
    """사용자 2026-10-27: 같은 S/N 에 끝나지 않은 A/S 가 있으면 새 접수를 막는다. 출하·입금 기록이 있는 건의 드랍은 관리자만."""
    deal_id, qid = await new_deal(db, "QT-RV20261027-71")
    v = await units.save_units(db, deal_id, None, qid, 0, [{"line_no": 0, "units": [{"serial": f"RV-{tag}-71", "warranty_months": 12}]}])
    uid = v["lines"][0]["units"][0]["id"]
    c1 = await units.create_case(db, None, uid, {"symptom": "전원 안 들어옴"})
    for label, coro in (("한 건 접수", lambda: units.create_case(db, None, uid, {"symptom": "또"})),
                        ("여러 S/N 한 번에 접수", lambda: units.create_cases(db, None, [uid], {"symptom": "또"}))):
        try:
            await coro()
            check(False, f"A/S중복 {label}도 막음", "접수됨")
        except svc.DealError as e:
            await db.rollback()
            check("이미 A/S 가 진행 중" in str(e) and "전원 안 들어옴" in str(e), f"A/S중복 {label}도 막고 진행 중인 A/S 를 알려 줌", str(e))
    done = await units.create_case(db, None, uid, {"symptom": "지난 수리 기록", "status": "완료"})
    check(done["status"] == "완료", "A/S중복 이미 끝난 A/S 를 기록으로 남기는 건 됨")
    await units.update_case(db, None, c1["id"], {"status": "완료"})
    c3 = await units.create_case(db, None, uid, {"symptom": "다른 고장"})
    check(c3["status"] == "접수", "A/S중복 앞 A/S 를 끝내면 새로 접수 됨")
    try:
        await units.update_case(db, None, c1["id"], {"status": "진행 중"})
        check(False, "A/S중복 끝난 A/S 를 다시 열 때도 진행 중인 게 있으면 막음", "열림")
    except svc.DealError:
        await db.rollback()
        check(True, "A/S중복 끝난 A/S 를 다시 열 때도 진행 중인 게 있으면 막음")

    await svc.add_payment(db, deal_id, None, {"date": TODAY, "amount": 1000, "full": False})
    try:
        await svc.set_dropped(db, deal_id, None, True, "테스트", admin=False)
        check(False, "드랍권한 입금 기록이 있는 건은 일반 직원이 드랍 못 함", "드랍됨")
    except svc.DealError as e:
        await db.rollback()
        check(e.status == 403, "드랍권한 입금 기록이 있는 건은 일반 직원이 드랍 못 함", str(e))
    await svc.set_dropped(db, deal_id, None, True, "테스트", admin=True)
    check((await svc.get_deal(db, deal_id, TODAY))["dropped"], "드랍권한 관리자는 드랍 됨")
    fresh, _ = await new_deal(db, "QT-RV20261027-72")
    await svc.set_dropped(db, fresh, None, True, "고객 취소", admin=False)
    check((await svc.get_deal(db, fresh, TODAY))["dropped"], "드랍권한 출하·입금 없는 건은 일반 직원도 드랍 됨")


async def month_end(db, tag: str) -> None:
    """사용자 2026-10-27: 월 단위 끝나는 날은 민법(통상) — 같은 날의 전날, 그 달에 같은 날이 없으면 말일."""
    D = dt.date
    cases = [((D(2026, 1, 15), 1), D(2026, 2, 14)), ((D(2026, 1, 31), 1), D(2026, 2, 28)), ((D(2026, 1, 30), 1), D(2026, 2, 28)),
             ((D(2028, 1, 31), 1), D(2028, 2, 29)), ((D(2026, 3, 31), 1), D(2026, 4, 30)), ((D(2026, 1, 31), 36), D(2029, 1, 30)),
             ((D(2026, 10, 31), 12), D(2027, 10, 30)), ((D(2026, 2, 28), 1), D(2026, 3, 27)), ((D(2026, 10, 19), 0), D(2026, 10, 18))]
    bad = [(a, svc.term_end(*a), want) for a, want in cases if svc.term_end(*a) != want]
    check(not bad, "월말 끝나는 날 = 민법 기준(1/31 + 1개월 → 2/28, 윤년 2/29, 3/31 → 4/30, 보통은 전날)", str(bad))
    check(units.warranty_until(D(2026, 1, 31), 1) == D(2026, 2, 28) and units.warranty_until(D(2026, 10, 19), 12) == D(2027, 10, 18),
          "월말 무상 종료일도 같은 규칙")
    deal_id, _ = await new_deal(db, "QT-RV20261027-81")
    await db.execute(text("UPDATE sales_deals SET quote = quote || CAST(:q AS jsonb) WHERE id = :d"),
                     {"q": json.dumps({"biz": "rental", "rental": {"months": 1, "start": None, "end": None, "monthly": 1}}), "d": deal_id})
    await db.commit()
    await svc.set_rental_start(db, deal_id, None, D(2027, 1, 31))
    check((await svc.get_deal(db, deal_id, TODAY))["quote"]["rental"]["end"] == "2027-02-28", "월말 대여 끝나는 날도 같은 규칙(1/31 → 2/28)")


async def run() -> None:
    assert os.environ.get("DATABASE_URL", "").endswith("/agent_ai_dev"), "개발 DB(agent_ai_dev)에서만 돌린다"
    tag = dt.datetime.now().strftime("%H%M%S")
    async with SessionLocal() as db:
        await migrations.run_migrations(db)
        await cleanup(db)
        try:
            for name, fn in (("A/S-1", as_progress), ("S/N-5", deleted_deal_serial), ("건관리-7", replaced_after_confirm),
                             ("건관리-8", payment_unsets_full), ("대여-판", pick_rental),
                             ("대여-교체", replace_rental), ("대여-재발행", reissue_start),
                             ("대여-확인", upload_start_step),
                             ("대여-단계", rental_phase),
                             ("출하수정", ship_edit_keep),
                             ("동시사용", stale_index),
                             ("지난날짜", past_due),
                             ("S/N찾기", search_literal),
                             ("A/S중복", as_dup_drop),
                             ("월말", month_end)):
                await guarded(db, f"{name} 실행 오류", fn(db, tag))
        finally:
            await db.rollback()
            await cleanup(db)


asyncio.run(run())
for label in PASS:
    print("PASS", label)
for label, detail in FAIL:
    print(f"FAIL {label} :: {detail}")
print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
sys.exit(1 if FAIL else 0)
