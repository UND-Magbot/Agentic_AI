# -*- coding: utf-8 -*-
"""새 건 등록(뼈대)·이름 고치기·번호 다시 쓰기(사용자 2026-11-18).

1) [+ 새 건 등록] — 품목 없이 고객사·프로젝트명만 → '견적 전', 건 번호 없음, 수주 확정 막힘
2) 상세의 [견적서 수기 작성](deal_id) → 그 건에 이어진 견적서 → 발행하면 첫 발행번호가 건 번호, 견적 단계, 고객사는 견적서 것
3) 이름 고치기 — 건 이름만, 재발행이 건 이름을 덮지 않고 견적서 고객사명도 그대로
4) 번호 다시 쓰기 — 거래명세서 발급 취소 / 발주서 발행 취소 / 구매 건 삭제 / 영업 건 삭제 → 다음 발행이 같은 번호(01)
dev DB 전용(agent_ai_dev), 만든 것은 끝에 지운다:
  docker compose run --rm -T --no-deps -e PYTHONPATH=/app -v ./backend/app:/app/app -v ./scripts:/scripts backend python /scripts/test_new_deal_skeleton.py
"""
import asyncio
import datetime as dt
import os
import sys

assert "agent_ai_dev" in os.environ.get("DATABASE_URL", ""), "NOT DEV DB - abort"
from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402

MARK = "__QA_SKEL__"
INI = "QK"
HEAD = {"initials": INI, "contact_name": "Alex", "contact_mobile": "010-0000-0000", "contact_email": "t@example.com"}
MG = {"name": "MG10", "qty": 1, "unit_price": 1_000_000}
res: list[tuple[bool, str]] = []


def check(ok: bool, name: str, *more) -> None:
    res.append((bool(ok), name))
    print("PASS" if ok else "FAIL", name, *more)


async def cleanup() -> None:
    async with SessionLocal() as db:
        c, n = MARK.lower(), len(MARK)
        dids = "SELECT id FROM sales_deals WHERE lower(left(customer, %d)) = :c" % n
        qids = "SELECT id FROM product_quotes WHERE lower(left(customer, %d)) = :c OR lower(left(state->'form'->>'customer', %d)) = :c" % (n, n)
        await db.execute(text(f"DELETE FROM doc_numbers WHERE upper(issuer) = :i OR quote_id IN ({qids}) OR deal_id IN ({dids})"), {"c": c, "i": INI})
        await db.execute(text(f"DELETE FROM product_quotes WHERE id IN ({qids}) OR deal_id IN ({dids})"), {"c": c})
        await db.execute(text("DELETE FROM sales_deal_trash WHERE lower(left(deal->>'customer', %d)) = :c" % n), {"c": c})
        await db.execute(text(f"DELETE FROM sales_deal_events WHERE deal_id IN ({dids})"), {"c": c})
        await db.execute(text(f"DELETE FROM sales_deals WHERE id IN ({dids})"), {"c": c})
        await db.execute(text("DELETE FROM purchase_events WHERE purchase_id IN (SELECT id FROM purchases WHERE supplier LIKE :m)"), {"m": MARK + "%"})
        await db.execute(text("DELETE FROM purchases WHERE supplier LIKE :m"), {"m": MARK + "%"})
        await db.commit()


async def main() -> None:
    from app.company_knowledge import product_recommend as pr
    from app.purchases import service as ps
    from app.sales_deals import service as svc

    await cleanup()
    async with SessionLocal() as db:
        uid = (await db.execute(text("SELECT id FROM users WHERE username = 'alex'"))).scalar()
    today = svc.kst_today()
    ymd = f"{today:%Y%m%d}"

    # ── 1) 뼈대 등록 ──
    async with SessionLocal() as db:
        did = await svc.create_skeleton(db, uid, {"customer": MARK + "고객사(건)", "title": "RC1 부자재", "owner": "Lucas"})
        d = await svc.get_deal(db, did, today)
    check(d["stage"] == "견적 전" and not d.get("deal_no") and not d.get("quote"), "새 건 = 견적 전 · 번호 없음 · 견적 없음", d["stage"], d.get("deal_no"))
    async with SessionLocal() as db:
        try:
            await svc.set_won(db, did, uid, True, today)
            check(False, "견적서 없이 수주 확정 막힘")
        except svc.DealError as e:
            await db.rollback()
            check("견적서를 먼저" in str(e), "견적서 없이 수주 확정 막힘", str(e)[:40])
    async with SessionLocal() as db:
        try:
            await svc.create_skeleton(db, uid, {"customer": MARK + "x", "title": ""})
            check(False, "프로젝트명 없으면 등록 막힘")
        except svc.DealError:
            await db.rollback()
            check(True, "프로젝트명 없으면 등록 막힘")

    # ── 2) 상세의 [견적서 수기 작성] → 발행 ──
    q = await pr.quote_manual(user_id=uid, form={**HEAD, "purpose": "sale"}, deal_id=did)
    check(q["state"]["form"].get("customer") == MARK + "고객사(건)" and q["deal_id"] == did, "견적서 수기 작성 창에 건의 고객사가 채워지고 건에 이어짐", q["state"]["form"].get("customer"))
    again = await pr.quote_manual(user_id=uid, form=HEAD, deal_id=did)
    check(again["id"] == q["id"], "같은 건에서 다시 누르면 작성 중인 견적서를 다시 연다")
    await pr.quote_edit(q["id"], user_id=uid, payload={"form": {"customer": MARK + "고객사(견적서)", "subject": "MG"}, "delivery": {"case": 1},
                                                       "lines": [{"id": None, "remark": "", **MG}]})
    qi = await pr.quote_issue(q["id"], user_id=uid)
    async with SessionLocal() as db:
        d = await svc.get_deal(db, did, today)
    no1 = qi.get("quote_no") or (qi.get("issued") or {}).get("no")
    check(d["stage"] == "견적" and d["deal_no"] == no1 and d["deal_no"].startswith(f"QT-{INI}"), "첫 발행 → 견적 단계 · 발행번호가 건 번호", d["stage"], d["deal_no"])
    check(d["customer"] == MARK + "고객사(견적서)" and d["title"] == "RC1 부자재" and d["owner"] == "Lucas",
          "첫 발행: 고객사는 견적서 것, 프로젝트명·영업 담당은 등록한 것 유지", d["customer"], d["title"], d["owner"])
    async with SessionLocal() as db:
        try:
            await pr.quote_manual(user_id=uid, form=HEAD, deal_id=did)
            check(False, "견적서를 낸 건에서는 수기 작성 다시 못 함")
        except pr.RecommendError:
            check(True, "견적서를 낸 건에서는 수기 작성 다시 못 함")

    # ── 3) 이름 고치기 — 서로 연동 없음 ──
    async with SessionLocal() as db:
        await svc.set_info(db, did, uid, {"customer": MARK + "새이름", "title": "새 프로젝트명"})
    await pr.quote_edit(q["id"], user_id=uid, payload={"form": {"customer": MARK + "고객사(견적서2)"}, "lines": [{"id": None, "remark": "", **MG, "qty": 2}]})
    await pr.quote_issue(q["id"], user_id=uid)
    async with SessionLocal() as db:
        d = await svc.get_deal(db, did, today)
        qc = (await db.execute(text("SELECT customer FROM product_quotes WHERE id = :i"), {"i": q["id"]})).scalar()
    check(d["customer"] == MARK + "새이름" and d["title"] == "새 프로젝트명", "재발행해도 고친 건 이름 그대로", d["customer"], d["title"])
    check(qc == MARK + "고객사(견적서2)", "건 이름을 고쳐도 견적서 고객사명은 견적서 것", qc)
    check(d["quote"]["amount"] == 2_000_000, "재발행 금액은 건에 반영", d["quote"]["amount"])

    # ── 4) 번호 다시 쓰기 — 거래명세서 ──
    st_body = {"initials": INI, "date": today, "buyer": {"reg_no": "1", "name": "x", "address": "y"},
               "items": [{"name": "MG10", "qty": 2, "unit_price": 1_000_000}]}
    async with SessionLocal() as db:
        await svc.set_won(db, did, uid, True, today)
        await svc.set_statement(db, did, uid, st_body)
        st1 = (await svc.get_deal(db, did, today))["statement"]["no"]
        await svc.cancel_statement(db, did, uid)
        await svc.set_statement(db, did, uid, st_body)
        st2 = (await svc.get_deal(db, did, today))["statement"]["no"]
    check(st1 == st2 == f"TS-{INI}{ymd}-01", "명세서 발급 취소 뒤 다시 발급 → 같은 번호 01", st1, st2)
    async with SessionLocal() as db:
        await svc.set_statement(db, did, uid, st_body)          # 02 로 다시 발급 → 취소하면 01 발급본으로 돌아감
        s02 = (await svc.get_deal(db, did, today))["statement"]
        await svc.cancel_statement(db, did, uid)
        back = (await svc.get_deal(db, did, today))["statement"]
    check(s02["no"].endswith("-02") and back["no"].endswith("-01") and not back.get("history"),
          "재발급(02)을 취소하면 01 발급본으로 돌아감", s02["no"], back["no"])
    async with SessionLocal() as db:
        await svc.delete_deals(db, [did], uid)
        did2 = await svc.create_skeleton(db, uid, {"customer": MARK + "고객사2", "title": "t"})
        await db.execute(text("UPDATE sales_deals SET kind = 'quote', won = TRUE, quote = CAST(:q AS jsonb) WHERE id = :i"),
                         {"i": did2, "q": '{"items": [{"name": "MG10", "qty": 2, "unit_price": 1000000}], "amount": 2000000, "currency": "KRW"}'})
        await db.commit()
        await svc.set_statement(db, did2, uid, st_body)
        st3 = (await svc.get_deal(db, did2, today))["statement"]["no"]
    check(st3 == f"TS-{INI}{ymd}-01", "영업 건 삭제 → 그 건의 명세서 번호 다시 씀(01)", st3)

    # ── 4) 번호 다시 쓰기 — 발주서 ──
    async def new_purchase() -> int:
        async with SessionLocal() as db:
            pid = await ps.create(db, uid, {"supplier": MARK + "공급사", "title": "", "owner": "", "note": "", "reason": "", "region": "overseas"})
            await ps.skip_quote(db, pid, uid, "Alex", True)
            await ps.approve(db, pid, uid, "Alex", today)
            return pid

    async def issue(pid: int) -> str:
        async with SessionLocal() as db:
            dr = await ps.po_draft(db, pid, {})
            hd = {**dr["header"], "issuer": {**dr["header"]["issuer"], "name": "QA", "attn": "QA"}}
            po = await ps.issue_po(db, pid, uid, "Alex", {"initials": INI, "expected_date": "", "header": hd,
                                                           "items": dr.get("items") or [{"description": "X", "qty": 1, "price": 10}]})
            return po["no"]

    pid = await new_purchase()
    p1 = await issue(pid)
    async with SessionLocal() as db:
        await ps.cancel_po(db, pid, uid, "Alex")
        p = await ps.get(db, pid, today)
    check(not p["po"]["no"] and not p["po"]["history"] and p["po"]["has_draft"], "발주서 발행 취소 → 발주 전 · 이력 비움 · 초안으로")
    p2 = await issue(pid)
    async with SessionLocal() as db:
        p = await ps.get(db, pid, today)
    check(p1 == p2 == f"PO-{INI}{ymd}-01" and p["po"]["revision"] == 1, "발행 취소 뒤 다시 발행 → 같은 번호 01 · v1(첫 이력)", p1, p2, p["po"]["revision"])
    p3 = await issue(pid)                                           # 재발행 02
    async with SessionLocal() as db:
        await ps.cancel_po(db, pid, uid, "Alex")
        p = await ps.get(db, pid, today)
    check(p3.endswith("-02") and p["po"]["no"] == p1 and p["po"]["revision"] == 1, "재발행(02)을 취소하면 01 판으로 돌아감", p3, p["po"]["no"])
    async with SessionLocal() as db:
        await ps.delete(db, [pid])
    pid2 = await new_purchase()
    p4 = await issue(pid2)
    check(p4 == f"PO-{INI}{ymd}-01", "구매 건 삭제 → 그 발주서 번호 다시 씀(01)", p4)

    # ── 5) [AI 제품 추천으로 시작] — 확정하면 새 건 대신 이 건에 붙는다 ──
    async def proposal() -> int:
        async with SessionLocal() as db:
            pid = (await db.execute(text("INSERT INTO product_proposals (user_id, kind, customer, title, model) "
                                         "VALUES (:u, 'atc', '', '', 'TCV3') RETURNING id"), {"u": uid})).scalar_one()
            await db.commit()
            return pid

    async with SessionLocal() as db:
        sid = await svc.create_skeleton(db, uid, {"customer": MARK + "추천건", "title": "추천 프로젝트"})
        n0 = (await db.execute(text("SELECT count(*) FROM sales_deals"))).scalar()
    pid1 = await proposal()
    async with SessionLocal() as db:
        got, _ = await svc.recommend_deal(db, uid, pid1, {"day": today, "customer": "", "owner": "Alex", "title": "TCV3"}, attach=sid)
        await db.commit()
        d = await svc.get_deal(db, sid, today)
        n1 = (await db.execute(text("SELECT count(*) FROM sales_deals"))).scalar()
    check(got == sid and n1 == n0 and d["stage"] == "제품 추천 확정", "추천 확정 → 새 건 없이 이 건에 붙음 · 제품 추천 확정", d["stage"], n1 - n0)
    check(d["customer"] == MARK + "추천건" and d["title"] == "추천 프로젝트", "붙여도 건의 고객사·프로젝트명 그대로", d["customer"], d["title"])
    async with SessionLocal() as db:
        await svc.recommend_deal(db, uid, pid1, {"day": today, "customer": "다른고객", "owner": "Alex", "title": "TCV4"})
        await db.commit()
        d = await svc.get_deal(db, sid, today)
    check(d["customer"] == MARK + "추천건" and d["title"] == "추천 프로젝트", "같은 추천을 다시 확정해도 새 건 등록 건의 이름은 덮지 않음", d["customer"])
    pid2 = await proposal()
    async with SessionLocal() as db:
        try:
            await svc.recommend_deal(db, uid, pid2, {"day": today, "customer": "", "owner": "", "title": "x"}, attach=sid)
            check(False, "다른 추천이 붙은 건에는 또 못 붙임")
        except svc.DealError:
            await db.rollback()
            check(True, "다른 추천이 붙은 건에는 또 못 붙임")
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM product_proposals WHERE id = ANY(:i)"), {"i": [pid1, pid2]})
        await db.commit()

    await cleanup()
    ok = sum(1 for r in res if r[0])
    print(f"PASS {ok} / FAIL {len(res) - ok}")
    sys.exit(0 if ok == len(res) else 1)


asyncio.run(main())
