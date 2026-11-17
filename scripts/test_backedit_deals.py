# -*- coding: utf-8 -*-
"""앞 단계를 나중에 고칠 때 — 명세서·대여 시작일·결제 조건 전 입금(QA 2026-11-16 영업 4·7·8).

4: 명세서 발급(또는 초안 저장) 뒤 견적 금액이 바뀌면 알림(STATEMENT_STALE), 다시 발급 창은 지금 견적 품목으로(공급받는자 그대로).
7: 수주한 대여 건에 다른 번호 견적서를 올려도 건의 대여 시작일은 그대로(건 견적이 바뀔 때만 확인 창 값 반영).
8: 결제 조건 전에 남긴 입금이 있으면 결제 조건을 못 정함(이중 집계 방지) — 그 입금을 지우면 됨.
dev DB 전용(agent_ai_dev), 만든 것은 끝에 지운다:
  docker compose run --rm -T --no-deps -e PYTHONPATH=/app -v ./backend/app:/app/app -v ./scripts:/scripts backend python /scripts/test_backedit_deals.py
"""
import asyncio
import os, sys, json, datetime as dt
assert "agent_ai_dev" in os.environ.get("DATABASE_URL", ""), "NOT DEV DB - abort"
from sqlalchemy import text
from app.database import SessionLocal
MARK = "__QA_BACK__"
INI = "QB"

async def uid_of(name="alex"):
    async with SessionLocal() as db:
        return (await db.execute(text("SELECT id FROM users WHERE username = :u"), {"u": name})).scalar()

async def deal(db, i):
    from app.sales_deals import service as svc
    return await svc.get_deal(db, i, svc.kst_today())

async def cleanup(verbose=False):
    async with SessionLocal() as db:
        c = MARK.lower(); n = len(MARK)
        dids = "SELECT id FROM sales_deals WHERE lower(left(customer, %d)) = :c" % n
        qids = ("SELECT id FROM product_quotes WHERE lower(left(customer, %d)) = :c OR lower(left(state->'form'->>'customer', %d)) = :c" % (n, n))
        await db.execute(text(f"DELETE FROM as_cases WHERE lower(serial) LIKE 'qb-%' OR deal_id IN ({dids}) OR unit_id IN (SELECT id FROM product_units WHERE lower(serial) LIKE 'qb-%')"), {"c": c})
        await db.execute(text(f"DELETE FROM product_units WHERE lower(serial) LIKE 'qb-%' OR deal_id IN ({dids})"), {"c": c})
        await db.execute(text(f"DELETE FROM doc_numbers WHERE upper(issuer) = :i OR quote_id IN ({qids}) OR deal_id IN ({dids})"), {"c": c, "i": INI})
        await db.execute(text(f"DELETE FROM product_quotes WHERE id IN ({qids})"), {"c": c})
        await db.execute(text("DELETE FROM sales_deal_trash WHERE lower(left(deal->>'customer', %d)) = :c" % n), {"c": c})
        await db.execute(text(f"DELETE FROM sales_deal_events WHERE deal_id IN ({dids})"), {"c": c})
        await db.execute(text(f"DELETE FROM sales_deals WHERE id IN ({dids})"), {"c": c})
        await db.commit()
        if verbose:
            left = {}
            for t, w in [("sales_deals", f"id IN ({dids})"), ("product_quotes", f"id IN ({qids})"),
                         ("product_units", "lower(serial) LIKE 'qb-%'"), ("as_cases", "lower(serial) LIKE 'qb-%'"),
                         ("doc_numbers", "upper(issuer) = :i")]:
                left[t] = (await db.execute(text(f"SELECT count(*) FROM {t} WHERE {w}"), {"c": c, "i": INI})).scalar()
            print("LEFT:", left)

HEAD = {"initials": INI, "contact_name": "Alex", "contact_mobile": "010-0000-0000", "contact_email": "t@example.com"}

async def make_quote(uid, cust, lines, form_extra=None):
    from app.company_knowledge import product_recommend as pr
    q = await pr.quote_manual(user_id=uid, form=HEAD)
    q = await pr.quote_edit(q["id"], user_id=uid, payload={"form": {"customer": MARK + cust, "subject": "MG", **(form_extra or {})},
        "delivery": {"case": 1}, "lines": [{"id": None, "remark": "", **l} for l in lines]})
    q = await pr.quote_issue(q["id"], user_id=uid)
    async with SessionLocal() as db:
        did = (await db.execute(text("SELECT deal_id FROM product_quotes WHERE id=:i"), {"i": q["id"]})).scalar()
    return q, did

async def reissue(uid, qid, lines, form_extra=None):
    from app.company_knowledge import product_recommend as pr
    q = await pr.quote_edit(qid, user_id=uid, payload={"lines": [{"id": None, "remark": "", **l} for l in lines], **({"form": form_extra} if form_extra else {})})
    return await pr.quote_issue(qid, user_id=uid)

def upref(qid, name="edited.pdf"):
    return {"key": f"product-quotes/{qid}/{name}", "filename": name, "mime": "application/pdf", "size": 10}

OK = []


def check(c, label, extra=""):
    OK.append(bool(c))
    print(("PASS " if c else "FAIL ") + label + ("" if c else f" :: {extra}"))


BUYER = {"reg_no": "123-45-67890", "name": MARK + "공급받는자", "ceo": "QA", "address": "Seoul"}


async def main():
    from app.company_knowledge import product_recommend as pr
    from app.sales_deals import service as svc
    await cleanup()
    uid = await uid_of()
    MG = {"name": "MG10 그리퍼", "qty": 1, "unit_price": 1_000_000}
    TC = {"name": "TCV3 툴체인저", "qty": 1, "unit_price": 500_000}

    # ── 4. 명세서 발급 뒤 견적 금액이 바뀜 ──
    q, did = await make_quote(uid, "st", [MG, TC])
    async with SessionLocal() as db:
        await svc.set_won(db, did, uid, True, None)
        dr = await svc.statement_draft(db, did, INI)
        await svc.set_statement(db, did, uid, {**dr, "buyer": BUYER, "date": svc.kst_today(), "initials": INI})
        d = await deal(db, did)
    check(not any(a["code"] == "STATEMENT_STALE" for a in d["alerts"]), "명세서와 견적이 같으면 알림 없음", str(d["alerts"]))
    await pr.quote_issue_upload(qid=q["id"], deal_id=None, user_id=uid, owner_check=True, upload=upref(q["id"]),
                                items=[{"spec": "", **MG, "qty": 2}, {"spec": "", **TC}], initials=INI, doc_no=q["quote_no"], overwrite=True)
    async with SessionLocal() as db:
        d = await deal(db, did)
        dr = await svc.statement_draft(db, did, INI)
    al = [a for a in d["alerts"] if a["code"] == "STATEMENT_STALE"]
    check(al and "1,500,000" in al[0]["text"] and "2,500,000" in al[0]["text"], "견적이 바뀌면 '명세서 금액이 지금 견적과 다름' 알림", str(d["alerts"]))
    check([(i["qty"], i["unit_price"]) for i in dr["items"]] == [(2, 1_000_000), (1, 500_000)] and dr["buyer"]["name"] == BUYER["name"]
          and dr.get("refreshed"), "다시 발급 창 — 품목은 지금 견적, 공급받는자는 그대로", str((dr["items"], dr["buyer"])))
    async with SessionLocal() as db:
        await svc.set_statement(db, did, uid, {**dr, "date": svc.kst_today(), "initials": INI})
        d = await deal(db, did)
    check(not any(a["code"] == "STATEMENT_STALE" for a in d["alerts"]), "다시 발급하면 알림 사라짐", str(d["alerts"]))
    # 수주 진행 탭에 저장한 초안도 — 견적이 바뀌면 품목은 지금 견적으로
    q2, did2 = await make_quote(uid, "dr", [MG])
    async with SessionLocal() as db:
        await svc.set_won(db, did2, uid, True, None)
        dr2 = await svc.statement_draft(db, did2, INI)
        await svc.save_order_state(db, did2, {"draft": {**dr2, "buyer": BUYER, "initials": INI}})
        await db.commit()
    await pr.quote_issue_upload(qid=q2["id"], deal_id=None, user_id=uid, owner_check=True, upload=upref(q2["id"]),
                                items=[{"spec": "", **MG, "qty": 3}], initials=INI, doc_no=q2["quote_no"], overwrite=True)
    async with SessionLocal() as db:
        dr2 = await svc.statement_draft(db, did2, INI)
    check([i["qty"] for i in dr2["items"]] == [3] and dr2["buyer"]["name"] == BUYER["name"], "저장한 초안도 견적이 바뀌면 품목은 지금 견적으로",
          str((dr2["items"], dr2["buyer"])))

    # ── 7. 수주한 대여 건에 다른 번호 견적서 — 시작일 그대로 ──
    q3, did3 = await make_quote(uid, "rt", [{**MG, "unit_price": 100_000}], {"purpose": "rental", "rental_months": 6})
    async with SessionLocal() as db:
        await svc.set_won(db, did3, uid, True, None)
        await svc.set_rental_start(db, did3, uid, dt.date(2026, 11, 1))
    await pr.quote_issue_upload(qid=q3["id"], deal_id=None, user_id=uid, owner_check=True, upload=upref(q3["id"]),
                                items=[{"spec": "", **MG, "unit_price": 100_000}], initials=INI, doc_no="", rental_start=None, rental_checked=True)
    async with SessionLocal() as db:
        d = await deal(db, did3)
    r = d["quote"].get("rental") or {}
    check(r.get("start") == "2026-11-01" and r.get("end") and d["quote"]["no"] == q3["quote_no"],
          "수주한 대여 건에 새 번호 견적서(시작일 미정) — 건의 시작일·끝나는 날 그대로", str((r, d["quote"]["no"])))

    # ── 8. 결제 조건 전에 남긴 입금 ──
    q4, did4 = await make_quote(uid, "pay", [MG])
    async with SessionLocal() as db:
        await svc.set_won(db, did4, uid, True, None)
        await svc.add_payment(db, did4, uid, {"date": svc.kst_today(), "amount": 400_000, "full": False})
        try:
            await svc.set_terms(db, did4, uid, [{"label": "선금", "pct": 50}, {"label": "잔금", "pct": 50}])
            check(False, "조건 전 입금이 있으면 결제 조건 저장 거절")
        except svc.DealError as e:
            await db.rollback()
            check("400,000" in str(e) and "지운 뒤" in str(e), "조건 전 입금이 있으면 결제 조건 저장 거절(이중 집계 방지)", str(e))
        await svc.remove_entry(db, did4, uid, "payments", 0)
        await svc.set_terms(db, did4, uid, [{"label": "선금", "pct": 50}, {"label": "잔금", "pct": 50}])
        await svc.confirm_term(db, did4, uid, 0, svc.kst_today())
        await svc.confirm_term(db, did4, uid, 1, svc.kst_today())
        d = await deal(db, did4)
    check(d["paid_full"] and d["flow"]["paid"] == 1_000_000, "입금 기록을 지우고 회차로 확인하면 정확히 100만·완납", str((d["paid_full"], d["flow"]["paid"])))


async def run():
    try:
        await main()
    finally:
        await cleanup(True)
    print(f"\nPASS {sum(OK)} / FAIL {len(OK) - sum(OK)}")

asyncio.run(run())
