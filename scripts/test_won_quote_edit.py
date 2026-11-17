# -*- coding: utf-8 -*-
"""수주한 건의 [견적서 고치기](사용자 2026-11-16).

열 때: 작성 상태가 수주한 판이 아니면 수주한 판을 띄움(auto), 고치다 만 내용이 있으면 물어봄(ask), 수주한 판(또는 그 위에서 고치는 중)이면 그대로(none).
발행할 때: 수주한 견적서 고치기(같은 번호 교체 — 영업 건 금액·품목 반영, S/N 품목 따라감, 입금 확인 있으면 금액 변경 막힘) / 새 견적서.
dev DB 전용(agent_ai_dev), 만든 것은 끝에 지운다:
  docker compose run --rm -T --no-deps -e PYTHONPATH=/app -v ./backend/app:/app/app -v ./scripts:/scripts backend python /scripts/test_won_quote_edit.py
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


async def main():
    from app.company_knowledge import product_recommend as pr
    from app.sales_deals import service as svc, units
    await cleanup()
    uid = await uid_of()
    MG = {"name": "MG10 그리퍼", "qty": 1, "unit_price": 1_000_000}
    TC = {"name": "TCV3 툴체인저", "qty": 1, "unit_price": 500_000}
    q, did = await make_quote(uid, "won", [MG, TC])
    won_no = q["quote_no"]
    async with SessionLocal() as db:
        await svc.set_won(db, did, uid, True, None)
        iu = await units.issue_units(db, did, q["id"], 0)
        await units.save_units(db, did, uid, q["id"], 0, [{"line_no": l["line_no"], "units": [{"serial": f"QB-W-{k}"}]} for k, l in enumerate(iu["lines"])])

    def qty(s):
        return [(l.get("name") or l.get("desc") or "").splitlines()[0][:8] + f"x{l['qty']}" for l in s["state"]["lines"] if (l.get("qty") or 0) > 0]

    g = await pr.quote_get(q["id"], user_id=uid)
    check({k: g["won_issue"][k] for k in ("revision", "quote_no", "open")} == {"revision": 0, "quote_no": won_no, "open": "none"}
          and g["won_issue"].get("statement_no") is None, "수주한 판 그대로면 열 때 그대로(none), 명세서 없음", str(g["won_issue"]))
    # 수주한 뒤 새 판(v2, MG10 2대) — 영업 건은 그대로, 다시 열면 수주한 판을 불러옴(auto)
    await reissue(uid, q["id"], [{**MG, "qty": 2}, TC])
    g = await pr.quote_get(q["id"], user_id=uid)
    check(g["won_issue"]["open"] == "auto", "수주 뒤 새 판을 낸 상태로 열면 수주한 판을 불러옴(auto)", str(g["won_issue"]))
    w = await pr.quote_load_won(q["id"], user_id=uid)
    check(any(x.startswith("MG10") and x.endswith("x1") for x in qty(w)) and w["won_issue"]["open"] == "none",
          "[수주한 견적서 불러오기] — 작성 상태가 수주한 판(MG10 1대)으로", str(qty(w)))
    # 수주한 판 위에서 고치는 중(저장만) — 그대로(none)
    await pr.quote_edit(q["id"], user_id=uid, payload={"lines": [{"id": None, "remark": "", **MG, "qty": 3}, {"id": None, "remark": "", **TC}]})
    g = await pr.quote_get(q["id"], user_id=uid)
    check(g["won_issue"]["open"] == "none", "수주한 판 위에서 고치는 중이면 그대로(none)", str(g["won_issue"]))
    # [수주한 견적서 고치기] — 같은 번호, 영업 건 금액 반영, S/N 그대로
    r = await pr.quote_issue(q["id"], user_id=uid, replace_won=True)
    async with SessionLocal() as db:
        d = await deal(db, did)
        iu = await units.issue_units(db, did, q["id"], 0)
    check(r.get("replaced_won") and d["quote"]["no"] == won_no and d["flow"]["total"] == 3_500_000,
          "수주한 견적서 고치기 — 같은 번호로 교체, 영업 건 금액 350만", str((d["quote"]["no"], d["flow"]["total"])))
    check([[u["serial"] for u in l["units"]] for l in iu["lines"]] == [["QB-W-0"], ["QB-W-1"]], "S/N 은 자기 품목 그대로", str(iu["lines"]))
    hist = r["history"]
    check(len(hist) == 2 and [h["quote_no"] for h in hist][0] == won_no, "판 수는 그대로(v1 교체, v2 는 목록에)", str([(h["revision"], h["quote_no"]) for h in hist]))
    # 다른 판(v2)에서 고치다 만 상태 — 열 때 물어봄(ask)
    await reissue(uid, q["id"], [{**MG, "qty": 5}])
    await pr.quote_edit(q["id"], user_id=uid, payload={"lines": [{"id": None, "remark": "", **MG, "qty": 6}]})
    g = await pr.quote_get(q["id"], user_id=uid)
    check(g["won_issue"]["open"] == "ask", "다른 판에서 고치다 만 내용이 있으면 물어봄(ask)", str(g["won_issue"]))
    # 입금 확인이 있으면 금액 바뀌는 수주한 견적서 고치기는 막힘
    await pr.quote_load_won(q["id"], user_id=uid)
    async with SessionLocal() as db:
        await svc.set_terms(db, did, uid, [{"label": "선금", "pct": 50}, {"label": "잔금", "pct": 50}])
        await svc.confirm_term(db, did, uid, 0, svc.kst_today())
    await pr.quote_edit(q["id"], user_id=uid, payload={"lines": [{"id": None, "remark": "", **MG, "qty": 4}, {"id": None, "remark": "", **TC}]})
    try:
        await pr.quote_issue(q["id"], user_id=uid, replace_won=True)
        check(False, "입금 확인 뒤 금액 바뀌는 수주한 견적서 고치기 거절")
    except pr.RecommendError as e:
        check("확인 취소" in str(e), "입금 확인 뒤 금액 바뀌는 수주한 견적서 고치기 거절", str(e))
        # 화면이 팝업으로 알리고 그 건의 입금 확인 칸으로 데려가도록 코드·건 번호를 함께 준다(사용자 2026-11-17)
        check(e.code == "PAY_LOCKED" and e.deal_id == did, "거절 오류에 PAY_LOCKED 코드·영업 건 번호", (e.code, e.deal_id, did))
    async with SessionLocal() as db:
        d = await deal(db, did)
    check(d["flow"]["total"] == 3_500_000, "거절되면 영업 건 금액 그대로", d["flow"]["total"])

    # 출하까지 한 건에서 순서를 바꾸고 MG10 이름을 다르게 적어 수주한 견적서 고치기(수동 검증 2026-11-17 D1)
    #  → S/N 과 출하 기록의 품목 줄 번호가 같이 새 줄로(출하 카드에서 TCV3 S/N 이 MG10 밑에 보이던 문제)
    q2, did2 = await make_quote(uid, "ship", [{**MG, "qty": 2}, TC])
    async with SessionLocal() as db:
        await svc.set_won(db, did2, uid, True, None)
        iu = await units.issue_units(db, did2, q2["id"], 0)
        iu = await units.save_units(db, did2, uid, q2["id"], 0, [
            {"line_no": l["line_no"], "units": [{"serial": f"QB-S-{l['line_no']}-{k}"} for k in range(int(l["qty"]))]} for l in iu["lines"]])
        await svc.add_shipment(db, did2, uid, {"date": svc.kst_today(), "unit_ids": [u["id"] for l in iu["lines"] for u in l["units"]],
                                                "lines": [{"name": l["name"], "qty": l["qty"], "line_no": l["line_no"], "photos": []} for l in iu["lines"]]})
    await pr.quote_edit(q2["id"], user_id=uid, payload={"lines": [{"id": None, "remark": "", **TC}, {"id": None, "remark": "", "name": "MG10", "qty": 1, "unit_price": 1_100_000}]})
    r = await pr.quote_issue(q2["id"], user_id=uid, replace_won=True)
    async with SessionLocal() as db:
        d = await deal(db, did2)
        rows = (await db.execute(text("SELECT serial, line_no FROM product_units WHERE deal_id = :d ORDER BY serial"), {"d": did2})).all()
    ship = {l["name"].splitlines()[0]: l.get("line_no") for l in d["shipments"][0]["lines"]}
    check(ship == {"TCV3 툴체인저": 0, "MG10 그리퍼": None}, "출하 기록 줄 번호도 새 줄로(TCV3→0, 이름이 다른 MG10 그리퍼는 비움)", str(ship))
    check(dict(rows) == {"QB-S-0-0": None, "QB-S-0-1": None, "QB-S-1-0": 0} and "MQ" not in r["sn_note"] and "QB-S-0-0" in r["sn_note"],
          "S/N — TCV3 는 새 0번 줄로, 이름이 다른 MG10 S/N 은 기록으로 남기고 안내", str(rows))

    # 거래명세서를 발급한 건 — 수주한 견적서를 고칠 때 명세서도 같은 번호로 교체 / 새 번호 / 그대로(사용자 2026-11-17)
    q3, did3 = await make_quote(uid, "stmt", [{**MG, "qty": 2}, TC])
    buyer = {"reg_no": "123-45-67890", "name": MARK + "공급받는자", "ceo": "홍길동", "address": "서울"}
    async with SessionLocal() as db:
        await svc.set_won(db, did3, uid, True, None)
        dr = await svc.statement_draft(db, did3, INI)
        await svc.set_statement(db, did3, uid, {**dr, "buyer": buyer, "date": svc.kst_today(), "initials": INI})
        st0 = (await deal(db, did3))["statement"]
    g = await pr.quote_get(q3["id"], user_id=uid)
    check(g["won_issue"].get("statement_no") == st0["no"], "발급한 명세서 번호를 견적서 화면에 알려 줌", str(g["won_issue"]))
    # ① 같은 번호로 교체 — MG10 1,200,000
    await pr.quote_edit(q3["id"], user_id=uid, payload={"lines": [{"id": None, "remark": "", **MG, "qty": 2, "unit_price": 1_200_000}, {"id": None, "remark": "", **TC}]})
    r = await pr.quote_issue(q3["id"], user_id=uid, replace_won=True, statement="same")
    async with SessionLocal() as db:
        d = await deal(db, did3)
    st = d["statement"]
    check(r["statement_reissued"] == {"no": st0["no"], "same_no": True} and st["no"] == st0["no"] and st["totals"]["supply"] == 2_900_000
          and st["date"] == st0["date"] and st["buyer"]["name"] == buyer["name"] and len(st.get("history") or []) == 1
          and st["history"][0]["kind"] == "replaced" and st["history"][0]["totals"]["supply"] == 2_500_000,
          "명세서 같은 번호로 교체 — 번호·날짜·공급받는자 그대로, 공급가 290만, 교체 전 250만 보관", str((st["no"], st["totals"], st["date"])))
    check(not any(a["code"] == "STATEMENT_STALE" for a in d["alerts"]), "교체 뒤 '명세서 다시 발급' 알림 없음", str(d["alerts"]))
    # ② 새 번호로 — MG10 1,100,000
    await pr.quote_edit(q3["id"], user_id=uid, payload={"lines": [{"id": None, "remark": "", **MG, "qty": 2, "unit_price": 1_100_000}, {"id": None, "remark": "", **TC}]})
    r = await pr.quote_issue(q3["id"], user_id=uid, replace_won=True, statement="new")
    async with SessionLocal() as db:
        d = await deal(db, did3)
    st = d["statement"]
    check(r["statement_reissued"]["no"] == st["no"] != st0["no"] and st["totals"]["supply"] == 2_700_000 and st["buyer"]["name"] == buyer["name"],
          "명세서 새 번호로 — 새 번호, 공급가 270만, 공급받는자 그대로", str((st0["no"], st["no"], st["totals"])))
    h = st.get("history") or []
    check([(x["no"], x["kind"], x["totals"]["supply"]) for x in h] == [(st0["no"], "replaced", 2_500_000), (st0["no"], "reissued", 2_900_000)],
          "이전 발급본 기록 — 교체 전 250만, 새 번호 전 290만(번호·종류)", str([(x["no"], x["kind"]) for x in h]))
    async with SessionLocal() as db:
        _, x0 = await svc.statement_file(db, did3, 0)
        _, x1 = await svc.statement_file(db, did3, 1)
    check(len(x0) > 1000 and len(x1) > 1000 and x0 != x1, "이전 발급본 파일도 받기(순번별로 다른 내용)", (len(x0), len(x1)))
    # ③ 견적서만 — 명세서는 그대로, 다시 발급 알림
    await pr.quote_edit(q3["id"], user_id=uid, payload={"lines": [{"id": None, "remark": "", **MG, "qty": 2, "unit_price": 1_000_000}, {"id": None, "remark": "", **TC}]})
    r = await pr.quote_issue(q3["id"], user_id=uid, replace_won=True)
    async with SessionLocal() as db:
        d = await deal(db, did3)
    check(not r.get("statement_reissued") and d["statement"]["totals"]["supply"] == 2_700_000 and d["flow"]["total"] == 2_500_000
          and any(a["code"] == "STATEMENT_STALE" for a in d["alerts"]),
          "견적서만 고치면 명세서 그대로 + '다시 발급' 알림", str((d["statement"]["totals"], d["flow"]["total"])))
    # 수주 진행 화면에서 다시 발급해도 예전 발급본이 남는다
    async with SessionLocal() as db:
        before = d["statement"]["no"]
        dr = await svc.statement_draft(db, did3, INI)
        await svc.set_statement(db, did3, uid, {**dr, "date": svc.kst_today(), "initials": INI})
        d = await deal(db, did3)
    h = d["statement"].get("history") or []
    check(len(h) == 3 and h[-1]["no"] == before and h[-1]["kind"] == "reissued" and d["statement"]["totals"]["supply"] == 2_500_000,
          "수주 진행 화면에서 다시 발급 — 예전 발급본 3개째로 남고 새 명세서는 250만", str([(x["no"], x["kind"]) for x in h]))


async def run():
    try:
        await main()
    finally:
        await cleanup(True)
    print()
    print("PASS %d / FAIL %d" % (sum(OK), len(OK) - sum(OK)))

asyncio.run(run())
