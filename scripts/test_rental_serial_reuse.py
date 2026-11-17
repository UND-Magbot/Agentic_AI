# -*- coding: utf-8 -*-
"""회수한 대여 제품 S/N 을 다음 건에 다시 등록·출하(사용자 2026-11-16 — QA '대여 1').

같은 S/N 은 동시에 한 건에서만 쓰인다(ux_product_units_active_serial: '회수' 아닌 줄끼리만 겹치지 않음).
A 건 기록은 '회수'로 남고 B 건에 새 줄, A/S 이력은 같은 S/N 전체로 이어 보기, 다른 건에 다시 나간 뒤 A 회수 취소는 막음.
dev DB 전용(agent_ai_dev), 만든 건·S/N 은 끝에 지운다:
  docker compose run --rm -T --no-deps -e PYTHONPATH=/app -v ./backend/app:/app/app -v ./scripts:/scripts backend python /scripts/test_rental_serial_reuse.py
"""
import asyncio, datetime as dt, json, os, traceback
from sqlalchemy import text
assert "agent_ai_dev" in os.environ["DATABASE_URL"], "DEV only"
from app.database import SessionLocal
from app.sales_deals import service as svc, units, flow, rental_query, rental_deals as rd

CUST = "__QA_RENTAL__"
TODAY = svc.kst_today()


async def new_deal(db, no, amount, rental, won=True):
    lines = [{"name": "방역 로봇", "qty": 2, "unit_price": amount // 2}]
    quote = {"no": no, "date": "2026-10-01", "amount": amount, "currency": "KRW", "items": svc.snapshot_items(lines), "revision": 1,
             "biz": "rental", "rental": rental}
    did = (await db.execute(text(
        "INSERT INTO sales_deals (deal_no, kind, customer, owner, title, base_date, quote, won) VALUES "
        "(:no, 'quote', :c, 'Test', 'QA', '2026-10-01', CAST(:q AS jsonb), :w) RETURNING id"),
        {"no": no, "c": CUST, "q": json.dumps(quote, ensure_ascii=False), "w": won})).scalar_one()
    hist = [{"revision": 0, "quote_no": no, "lines": lines, "total": amount, "currency": "KRW",
             "issued_at": "2026-10-01T01:00:00+00:00", "biz": "rental", "rental": {"months": rental["months"], "start": rental.get("start")}}]
    qid = (await db.execute(text(
        "INSERT INTO product_quotes (deal_id, status, quote_no, customer, revision, history, total) "
        "VALUES (:d, 'issued', :no, :c, 0, CAST(:h AS jsonb), :t) RETURNING id"),
        {"d": did, "no": no, "c": CUST, "h": json.dumps(hist, ensure_ascii=False), "t": amount})).scalar_one()
    await db.commit()
    return did, qid


async def cleanup(db):
    ids = (await db.execute(text("SELECT id FROM sales_deals WHERE customer = :c"), {"c": CUST})).scalars().all()
    if ids:
        await db.execute(text("DELETE FROM as_cases WHERE deal_id = ANY(:i)"), {"i": ids})
        await db.execute(text("DELETE FROM sales_deal_events WHERE deal_id = ANY(:i)"), {"i": ids})
        await db.execute(text("DELETE FROM doc_numbers WHERE deal_id = ANY(:i)"), {"i": ids})
        await db.execute(text("DELETE FROM product_quotes WHERE deal_id = ANY(:i)"), {"i": ids})
        await db.execute(text("DELETE FROM sales_deals WHERE id = ANY(:i)"), {"i": ids})
    await db.execute(text("DELETE FROM as_cases WHERE serial LIKE 'QA-%'"))
    await db.execute(text("DELETE FROM product_units WHERE serial LIKE 'QA-%'"))
    await db.commit()



OK = []


def check(c, label, extra=""):
    OK.append(bool(c))
    print(("PASS " if c else "FAIL ") + label + ("" if c else f" :: {extra}"))


async def blocked(db, coro, label, needle):
    try:
        await coro
        check(False, label, "통과됨")
    except svc.DealError as e:
        await db.rollback()
        check(needle in str(e), label, str(e))


async def main():
    async with SessionLocal() as db:
        await cleanup(db)
        try:
            past = (TODAY - dt.timedelta(days=40)).isoformat()
            a, qa = await new_deal(db, "QA-RENT-A", 2_400_000, {"months": 12, "start": past, "end": None, "monthly": 100_000})
            await units.save_units(db, a, None, qa, 0, [{"line_no": 0, "units": [
                {"serial": "QA-R1", "maker": "UND", "purpose": "R"}, {"serial": "QA-R2", "maker": "UND", "purpose": "R"}]}])
            await svc.add_shipment(db, a, None, {"date": TODAY - dt.timedelta(days=40), "lines": [], "unit_ids": None})
            # A 건에서 쓰던 중 A/S(끝남) — 같은 로봇의 고장 이력
            ua1 = (await db.execute(text("SELECT id FROM product_units WHERE deal_id=:d AND serial='QA-R1'"), {"d": a})).scalar()
            await units.create_case(db, None, ua1, {"received_on": TODAY - dt.timedelta(days=20), "symptom": "QA 모터 소음", "status": "완료"})
            b, qb = await new_deal(db, "QA-RENT-B", 2_400_000, {"months": 12, "start": None, "end": None, "monthly": 100_000})
            # 회수 전에는 B 에 못 넣음
            await blocked(db, units.save_units(db, b, None, qb, 0, [{"line_no": 0, "units": [{"serial": "QA-R1", "maker": "UND", "purpose": "R"}]}],
                                               partial=True), "A 에서 대여 중인 S/N 은 B 에 등록 거절", "이미 등록된 S/N")
            await svc.set_rental_return(db, a, None, "done", TODAY)
            # 회수 뒤에는 B 에 등록·출하
            await units.save_units(db, b, None, qb, 0, [{"line_no": 0, "units": [
                {"serial": "qa-r1 ", "maker": "UND", "purpose": "R"}, {"serial": "QA-R2", "maker": "UND", "purpose": "R"}]}])
            await svc.add_shipment(db, b, None, {"date": TODAY, "lines": [], "unit_ids": None})
            rows = (await db.execute(text("SELECT deal_id, serial, status FROM product_units WHERE lower(serial) IN ('qa-r1','qa-r2') ORDER BY id"))).all()
            check([(r[0] == a, r[2]) for r in rows] == [(True, "회수"), (True, "회수"), (False, "출하"), (False, "출하")],
                  "회수한 S/N 을 B 에 다시 등록·출하 — A 기록은 '회수'로 남고 B 는 출하", str(rows))
            # 다른 건(C)에는 B 가 쓰는 동안 못 넣음
            c, qc = await new_deal(db, "QA-RENT-C", 2_400_000, {"months": 12, "start": None, "end": None, "monthly": 100_000})
            await blocked(db, units.save_units(db, c, None, qc, 0, [{"line_no": 0, "units": [{"serial": "QA-R1", "maker": "UND", "purpose": "R"}]}],
                                               partial=True), "B 에서 대여 중인 S/N 은 C 에 등록 거절", "QA-RENT-B")
            # A/S 이력 이어 보기 — B 의 QA-R1 상세에 A 때 A/S 가 보임
            ub1 = (await db.execute(text("SELECT id FROM product_units WHERE deal_id=:d AND lower(serial)='qa-r1'"), {"d": b})).scalar()
            det = await units.unit_detail(db, ub1, TODAY)
            check(any(x.get("symptom") == "QA 모터 소음" for x in det["cases"]), "B 의 S/N 상세에 A 건 때 A/S 기록도 보임",
                  str([x.get("symptom") for x in det["cases"]]))
            # 검색 — 같은 S/N 이 건마다 한 줄씩
            found = await units.search_units(db, "QA-R1", TODAY)
            check(sorted(u["status"] for u in found) == ["출하", "회수"], "검색 — 건마다 한 줄(A 회수, B 출하)", str([(u["status"], u.get("deal_no")) for u in found]))
            # 관리자 회수 취소 — 이미 B 에 다시 나갔으면 막음
            await blocked(db, svc.set_rental_return(db, a, None, "cancel", TODAY, admin=True), "다른 건에 다시 나간 뒤 A 회수 취소 거절", "다시 등록")
            # B 도 회수하면 그 S/N 은 다시 쓸 수 있음(세 번째 대여)
            await svc.set_rental_return(db, b, None, "done", TODAY)
            await units.save_units(db, c, None, qc, 0, [{"line_no": 0, "units": [{"serial": "QA-R1", "maker": "UND", "purpose": "R"}]}], partial=True)
            check(True, "B 도 회수하면 C 에 세 번째로 등록됨")
            # A/S 기록이 있는 S/N 이 묶인 출하(A 의 QA-R1)는 지울 수 없음 — 무상 기간 기준이 사라지므로(QA 2026-11-16 영업 6)
            await blocked(db, svc.remove_entry(db, a, None, "shipments", 0), "A/S 있는 S/N 이 묶인 출하 기록 삭제 거절", "A/S 기록")
            await blocked(db, svc.reset_to_quote(db, a, None), "A/S 있는 S/N 이 묶인 건 견적 상태로 되돌리기 거절", "A/S 기록")
            # 회수한 S/N 을 다른 건에 다시 쓴 뒤 앞 건의 출하 기록 삭제·견적 상태로 되돌리기 — 오류(500) 없이, '회수' 그대로(영업 5)
            past2 = (TODAY - dt.timedelta(days=30)).isoformat()
            dd, qd = await new_deal(db, "QA-RENT-D", 2_400_000, {"months": 12, "start": past2, "end": None, "monthly": 100_000})
            await units.save_units(db, dd, None, qd, 0, [{"line_no": 0, "units": [{"serial": "QA-R3", "maker": "UND", "purpose": "R"},
                                                                                {"serial": "QA-R4", "maker": "UND", "purpose": "R"}]}])
            await svc.add_shipment(db, dd, None, {"date": TODAY - dt.timedelta(days=30), "lines": [], "unit_ids": None})
            await svc.set_rental_return(db, dd, None, "done", TODAY)
            e, qe = await new_deal(db, "QA-RENT-E", 2_400_000, {"months": 12, "start": None, "end": None, "monthly": 100_000})
            await units.save_units(db, e, None, qe, 0, [{"line_no": 0, "units": [{"serial": "QA-R3", "maker": "UND", "purpose": "R"}]}], partial=True)
            await svc.remove_entry(db, dd, None, "shipments", 0)
            st = (await db.execute(text("SELECT array_agg(DISTINCT status) FROM product_units WHERE deal_id = :d"), {"d": dd})).scalar()
            check(st == ["회수"], "다른 건에 다시 쓴 뒤 앞 건 출하 기록 삭제 — 오류 없이 '회수' 그대로", str(st))
            await svc.reset_to_quote(db, dd, None)
            st = (await db.execute(text("SELECT array_agg(DISTINCT status) FROM product_units WHERE deal_id = :d"), {"d": dd})).scalar()
            check(st == ["회수"], "앞 건 견적 상태로 되돌리기도 오류 없이 '회수' 그대로", str(st))
        except Exception:
            traceback.print_exc()
            await db.rollback()
            OK.append(False)
        finally:
            await cleanup(db)
            await db.execute(text("DELETE FROM as_cases WHERE serial ILIKE 'qa-%'"))
            await db.execute(text("DELETE FROM product_units WHERE serial ILIKE 'qa-%'"))     # 소문자로 적은 시험 S/N 까지
            await db.commit()
            left = (await db.execute(text("SELECT count(*) FROM sales_deals WHERE customer=:c"), {"c": CUST})).scalar()
            left2 = (await db.execute(text("SELECT count(*) FROM product_units WHERE serial ILIKE 'QA-%'"))).scalar()
            print("cleanup left:", left, left2)
    print(f"\nPASS {sum(OK)} / FAIL {len(OK) - sum(OK)}")

asyncio.run(main())
