# -*- coding: utf-8 -*-
"""출하 뒤 견적서를 고쳐 같은 번호로 교체·다른 판으로 수주(QA 2026-11-16 영업 1·2·3).

S/N 은 견적서 판의 '몇 번째 줄'에 붙는다 — 같은 번호 교체·다른 판으로 수주 바꾸기 때 품목 이름으로 새 줄에 다시 짝짓는다
(순서가 바뀌어도 그 품목을 따라감, 수량이 줄면 출하한 S/N 먼저). 막지 않는다(사용자 결정): 짝이 없는 S/N 중 출하·A/S 기록이 있는 것은
지우지 않고 이전 기록으로 남기고, 출하 전 S/N 은 지우며, 그 안내(sn_note)를 돌려준다.
dev DB 전용(agent_ai_dev), 만든 건·견적·S/N·번호는 끝에 지운다:
  docker compose run --rm -T --no-deps -e PYTHONPATH=/app -v ./backend/app:/app/app -v ./scripts:/scripts backend python /scripts/test_reissue_after_ship.py
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


async def shipped_deal(uid, cust, lines):
    """수주 → S/N 등록(줄마다 수량만큼) → 출하까지 마친 건."""
    from app.sales_deals import service as svc, units
    q, did = await make_quote(uid, cust, lines)
    async with SessionLocal() as db:
        await svc.set_won(db, did, uid, True, None)
        iu = await units.issue_units(db, did, q["id"], 0)
        pay = [{"line_no": l["line_no"], "units": [{"serial": f"QB-{cust}-{l['name'].split()[0]}-{k}"} for k in range(l["qty"])]} for l in iu["lines"]]
        iu = await units.save_units(db, did, uid, q["id"], 0, pay)
        ids = [u["id"] for l in iu["lines"] for u in l["units"]]
        await svc.add_shipment(db, did, uid, {"date": svc.kst_today(), "unit_ids": ids,
                                              "lines": [{"name": l["name"], "qty": l["qty"], "line_no": l["line_no"], "photos": []} for l in iu["lines"]]})
    return q, did


async def main():
    from app.company_knowledge import product_recommend as pr
    from app.sales_deals import service as svc, units
    await cleanup()
    uid = await uid_of()
    MG = {"name": "MG10 그리퍼", "qty": 1, "unit_price": 1_000_000}
    TC = {"name": "TCV3 툴체인저", "qty": 1, "unit_price": 500_000}

    async def view(qid, did, rev=0):
        async with SessionLocal() as db:
            iu = await units.issue_units(db, did, qid, rev)
        return [(l["name"].splitlines()[0], [u["serial"] for u in l["units"]]) for l in iu["lines"]]

    async def unit(serial):
        async with SessionLocal() as db:
            return (await db.execute(text("SELECT quote_revision, line_no, status FROM product_units WHERE serial = :s"), {"s": serial})).first()

    async def replace(q, items):
        return await pr.quote_issue_upload(qid=q["id"], deal_id=None, user_id=uid, owner_check=True, upload=upref(q["id"]),
                                           items=[{"spec": "", **x} for x in items], initials=INI, doc_no=q["quote_no"], overwrite=True)

    # ① 순서 바꾸고 수량·단가 바꿔 교체 — S/N 은 자기 품목을 따라감, 금액은 바뀜, 안내 없음
    q, did = await shipped_deal(uid, "a", [MG, TC])
    r = await replace(q, [{**TC, "qty": 2}, {**MG, "unit_price": 1_100_000}])
    v = await view(q["id"], did)
    check(v == [("TCV3 툴체인저", ["QB-a-TCV3-0"]), ("MG10 그리퍼", ["QB-a-MG10-0"])] and not r["sn_note"],
          "출하 뒤 순서 바꿔 교체 — S/N 이 자기 품목을 따라감(뒤바뀌지 않음)", str((v, r["sn_note"])))
    async with SessionLocal() as db:
        d = await deal(db, did)
    check(d["flow"]["total"] == 2_100_000, "단가·수량 변경은 그대로 반영(금액 210만)", d["flow"]["total"])
    # ② 출하한 S/N 이 붙은 품목(MG10)을 뺀 교체 — 막지 않고, 그 S/N 은 품목 줄 없이 기록으로 남김 + 안내
    r = await replace(q, [{**TC, "qty": 2}])
    u = await unit("QB-a-MG10-0")
    check("QB-a-MG10-0" in r["sn_note"] and "이전 기록으로 남겼습니다" in r["sn_note"] and u and u[1] is None and u[2] == "출하",
          "출하한 S/N 붙은 품목을 뺀 교체 — 됨, S/N 은 출하 기록 그대로 남기고 안내", str((r["sn_note"], u)))
    async with SessionLocal() as db:
        d = await deal(db, did)
    check(any(x["serial"] == "QB-a-MG10-0" and x["shipment_index"] == 0 for x in d["unit_list"]), "남긴 S/N 은 출하 카드(unit_list)에 계속 보임")
    check(await view(q["id"], did) == [("TCV3 툴체인저", ["QB-a-TCV3-0"])], "S/N 등록 화면엔 새 품목만", str(await view(q["id"], did)))
    # ③ 수량을 S/N 수보다 줄인 교체 — 출하한 S/N 하나는 품목에 남고 나머지는 기록으로
    q2, did2 = await shipped_deal(uid, "b", [{**TC, "qty": 2}, MG])
    r = await replace(q2, [{**TC, "qty": 1}, MG])
    v2 = await view(q2["id"], did2)
    check(v2 == [("TCV3 툴체인저", ["QB-b-TCV3-0"]), ("MG10 그리퍼", ["QB-b-MG10-0"])] and "QB-b-TCV3-1" in r["sn_note"],
          "S/N 2개 품목을 수량 1로 — 하나는 품목에, 하나는 기록으로 + 안내", str((v2, r["sn_note"])))
    # ④ 이름 대소문자·띄어쓰기만 다른 파일 — 같은 품목으로 짝지음
    await replace(q2, [{**TC, "name": "tcv3  툴체인저", "qty": 1}, {**MG, "name": " mg10 그리퍼 "}])
    v2 = await view(q2["id"], did2)
    check([s for _, s in v2] == [["QB-b-TCV3-0"], ["QB-b-MG10-0"]], "대소문자·띄어쓰기 차이는 같은 품목", str(v2))
    # ⑤ 출하 전 S/N(번호만 적음) 붙은 품목을 뺀 교체 — 그 S/N 은 지우고 안내
    q3, did3 = await make_quote(uid, "c", [MG, TC])
    async with SessionLocal() as db:
        await svc.set_won(db, did3, uid, True, None)
        iu = await units.issue_units(db, did3, q3["id"], 0)
        await units.save_units(db, did3, uid, q3["id"], 0, [{"line_no": l["line_no"], "units": [{"serial": f"QB-c-{k}"}]} for k, l in enumerate(iu["lines"])])
    r = await replace(q3, [MG])
    check("출하 전 S/N 1개(QB-c-1)" in r["sn_note"] and await unit("QB-c-1") is None and await view(q3["id"], did3) == [("MG10 그리퍼", ["QB-c-0"])],
          "출하 전 S/N 붙은 품목을 뺀 교체 — 그 S/N 은 지우고 안내", str(r["sn_note"]))
    # ⑥ 출하 뒤 견적서 탭에서 고친 새 판(TCV3 빼고 MG10 3대)으로 수주 바꾸기 — 막지 않고 S/N 을 새 판으로 옮김
    await reissue(uid, q2["id"], [{**MG, "qty": 3}])
    async with SessionLocal() as db:
        rev2 = max(int(h["revision"]) for h in (await svc.deal_quotes(db, None, did2))[0]["issues"])
        note = await svc.set_won(db, did2, uid, True, None, {"quote_id": q2["id"], "revision": rev2})
        d = await deal(db, did2)
    mg, tc = await unit("QB-b-MG10-0"), await unit("QB-b-TCV3-0")
    check(d["quote"]["no"] != q2["quote_no"] and mg[0] == rev2 and tc[0] == 0 and "QB-b-TCV3-0" in note,
          "출하 뒤 새 판으로 수주 바꾸기 — MG10 S/N 은 새 판으로, 빠진 TCV3 S/N 은 이전 판 기록 + 안내", str((d["quote"]["no"], mg, tc, note)))
    check(d["units"]["done"] == 1 and d["units"]["total"] == 3, "새 판 S/N 현황 1/3(옮긴 MG10 1개)", str(d["units"]))


async def run():
    try:
        await main()
    finally:
        await cleanup(True)
    print(f"\nPASS {sum(OK)} / FAIL {len(OK) - sum(OK)}")

asyncio.run(run())
