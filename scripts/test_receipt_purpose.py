# -*- coding: utf-8 -*-
"""입고 목적 나누기·S/N 두 줄(사용자 2026-11-18).

구매: 판매용 입고(품목마다 고객사·담당·메모, S/N 없음) / 보유 후 자체 개발용 입고(제조사·UND S/N, S/N 사진, S/W 담당자)를
섞인 품목에서 골라 따로 기록, 직접 적은(자유 형식) 품목, 예전 기록은 판매용, 입고 완료 판단.
영업 건 S/N: 제조사 S/N · UND S/N 두 줄(우리 제품은 같은 번호로 채움, 다른 제조사는 둘 다 필수), UND S/N 겹침 막기, S/N 사진.
dev DB 전용, 만든 것은 끝에 지운다:
  docker compose run --rm -T --no-deps -e PYTHONPATH=/app -v ./backend/app:/app/app -v ./scripts:/scripts backend python /scripts/test_receipt_purpose.py
"""
import asyncio
import json
import os
import sys

assert "agent_ai_dev" in os.environ.get("DATABASE_URL", ""), "NOT DEV DB - abort"
from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402

SUP = "__QA_입고목적__"
PH = "b" * 32 + ".jpg"
res: list[bool] = []


def check(ok: bool, name: str, *more) -> None:
    res.append(bool(ok))
    print("PASS" if ok else "FAIL", name, *more)


async def expect_err(coro, name: str, part: str) -> None:
    try:
        await coro
        check(False, name, "오류가 나야 함")
    except Exception as e:  # noqa: BLE001
        check(part in str(e), name, str(e)[:70])


async def cleanup() -> None:
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM purchase_events WHERE purchase_id IN (SELECT id FROM purchases WHERE supplier = :s)"), {"s": SUP})
        await db.execute(text("DELETE FROM purchases WHERE supplier = :s"), {"s": SUP})
        await db.execute(text("DELETE FROM doc_numbers WHERE issuer = 'QR'"))
        await db.commit()


async def purchase_part(uid: int) -> None:
    from app.purchases import service as ps

    today = ps.kst_today()
    async with SessionLocal() as db:
        pid = await ps.create(db, uid, {"supplier": SUP, "title": "", "owner": "", "note": "", "reason": "", "region": "overseas"})
        await ps.skip_quote(db, pid, uid, "QA", True)
        await ps.approve(db, pid, uid, "QA", today)
        dr = await ps.po_draft(db, pid, {})
        hd = {**dr["header"], "issuer": {**dr["header"]["issuer"], "name": "QA", "attn": "QA"}}
        await ps.issue_po(db, pid, uid, "QA", {"initials": "QR", "expected_date": "", "header": hd,
                                               "items": [{"description": "Robot A", "qty": 3, "price": 10}, {"description": "Gripper B", "qty": 2, "price": 5}]})
        await ps.start_shipping(db, pid, uid, "QA", today, "")
        # 판매용 — 0번 품목 2대, 고객사·담당·메모, S/N 은 보내도 저장 안 함
        await ps.add_receipt(db, pid, uid, "QA", {"date": today, "purpose": "sale", "lines": [
            {"line_no": 0, "qty": 2, "customer": "LG CNS", "owner": "Lucas", "note": "11월 납품", "units": [{"maker_sn": "X"}]}]})
        p = await ps.get(db, pid, today)
        r = p["receipt"]["records"]
        check(len(r) == 1 and r[0]["purpose"] == "sale" and r[0]["lines"][0]["customer"] == "LG CNS" and "units" not in r[0]["lines"][0],
              "판매용 입고 — 고객사·담당·메모, S/N 없음", r[0]["lines"][0])
        await expect_err(ps.add_receipt(db, pid, uid, "QA", {"date": today, "purpose": "dev", "lines": [
            {"line_no": 1, "qty": 2, "units": [{"maker_sn": "M1", "und_sn": "D"}, {"maker_sn": "M2", "und_sn": "d"}]}]}), "같은 UND S/N 두 번 거절", "두 번")
        await db.rollback()
        # 자체 개발용 — 남은 0번 1대 + 1번 2대, 두 줄 S/N·사진·S/W 담당
        await ps.add_receipt(db, pid, uid, "QA", {"date": today, "purpose": "dev", "lines": [
            {"line_no": 0, "qty": 1, "sw_owner": "Kim", "units": [{"maker_sn": "MK-1", "und_sn": "UND-1", "photos": [PH, "../bad"]}]},
            {"line_no": 1, "qty": 2, "units": [{"maker_sn": "MK-2", "und_sn": "UND-2"}, {"maker_sn": "", "und_sn": ""}]}]})
        p = await ps.get(db, pid, today)
        recs = {x["purpose"]: x for x in p["receipt"]["records"]}
        dev = recs.get("dev") or {}
        l0 = dev["lines"][0] if dev else {}
        check(set(recs) == {"sale", "dev"} and l0.get("sw_owner") == "Kim" and l0["units"][0] == {"maker_sn": "MK-1", "und_sn": "UND-1", "photos": [PH], "sw_owner": "", "item_photos": []},
              "자체 개발용 입고 — 두 줄 S/N·S/N 사진(잘못된 사진 이름 버림)·S/W 담당", l0)
        check(len(dev["lines"][1]["units"]) == 1, "뒤쪽 빈 S/N 칸은 버림", dev["lines"][1]["units"])
        check(p["receipt"]["done"] and next(x for x in p["steps"] if x["key"] == "receipt")["done"], "두 목적 합쳐 발주 수량을 다 받으면 입고 완료")
        # 발주 수량 넘김 막기(두 목적 합쳐서)
        di = next(i for i, x in enumerate(p["receipt"]["records"]) if x["purpose"] == "dev")
        await expect_err(ps.update_receipt(db, pid, uid, "QA", di, {"date": today, "lines": [{"line_no": 0, "qty": 2}, {"line_no": 1, "qty": 2}],
                                                                   "expect": {"at": p["receipt"]["records"][di]["at"]}}),
                         "두 목적 합쳐 발주 수량 넘기면 거절", "넘습니다")
        await db.rollback()
        # S/N 만 따로 고치기(기록 안 순번)
        await ps.set_receipt_serials(db, pid, uid, di, [{"line_index": 1, "units": [{"maker_sn": "MK-2", "und_sn": "UND-2"}, {"maker_sn": "MK-3", "und_sn": "UND-3"}],
                                                         "sw_owner": "Park"}], {"at": p["receipt"]["records"][di]["at"]})
        p = await ps.get(db, pid, today)
        dev = next(x for x in p["receipt"]["records"] if x["purpose"] == "dev")
        check(len(dev["lines"][1]["units"]) == 2 and dev["lines"][1]["sw_owner"] == "Park", "S/N·S/W 담당자 나중에 고치기", dev["lines"][1])

        # S/N 없는 품목(소모품) — 낱개 칸 없이 전체 사진만(사용자 2026-11-19)
        await ps.update_receipt(db, pid, uid, "QA", di, {"date": today, "lines": [
            {"line_no": 0, "qty": 1, "units": [{"maker_sn": "MK-1", "und_sn": "UND-1"}]},
            {"line_no": 1, "qty": 2, "no_sn": True, "photos": [PH], "units": [{"maker_sn": "버려짐", "und_sn": "x"}]}],
            "expect": {"at": dev["at"]}})
        p = await ps.get(db, pid, today)
        g = next(x for x in p["receipt"]["records"] if x["purpose"] == "dev")["lines"][1]
        check(g["no_sn"] is True and g["units"] == [] and g["photos"] == [PH], "S/N 없는 품목 — 낱개 S/N 없이 전체 사진만", g)

        # 자유 형식(품목 모두 비우기) — 발주 수량과 비교하지 않고 입고 완료는 체크로
        pid2 = await ps.create(db, uid, {"supplier": SUP, "title": "", "owner": "", "note": "", "reason": "", "region": "overseas"})
        await ps.skip_quote(db, pid2, uid, "QA", True)
        await ps.approve(db, pid2, uid, "QA", today)
        await ps.issue_po(db, pid2, uid, "QA", {"initials": "QR", "expected_date": "", "header": hd, "items": [{"description": "Robot A", "qty": 1, "price": 1}]})
        await expect_err(ps.add_receipt(db, pid2, uid, "QA", {"date": today, "purpose": "sale", "lines": [{"line_no": None, "name": "", "qty": 1}]}),
                         "직접 적은 품목은 이름 필수", "이름")
        await db.rollback()
        await ps.add_receipt(db, pid2, uid, "QA", {"date": today, "purpose": "sale", "lines": [{"line_no": None, "name": "로봇 본체(박스 2개)", "qty": 2}]})
        p2 = await ps.get(db, pid2, today)
        check(not p2["receipt"]["done"] and p2["receipt"]["records"][0]["lines"][0]["line_no"] is None,
              "직접 적은 품목만 — 발주 수량과 비교 안 해서 아직 입고 대기", p2["stage"])
        idx = 0
        await ps.update_receipt(db, pid2, uid, "QA", idx, {"date": today, "final": True, "reclose": True,
                                                           "lines": [{"line_no": None, "name": "로봇 본체(박스 2개)", "qty": 2}],
                                                           "expect": {"at": p2["receipt"]["records"][0]["at"]}})
        p2 = await ps.get(db, pid2, today)
        check(p2["receipt"]["done"], "직접 적은 품목 — '입고 완료' 체크로 끝", p2["stage"])

        # 예전 기록(목적 없음)은 판매용으로
        await db.execute(text("UPDATE purchases SET receipt = jsonb_set(receipt, '{records,0}', (receipt->'records'->0) - 'purpose') WHERE id = :i"), {"i": pid2})
        await db.commit()
        p2 = await ps.get(db, pid2, today)
        check(p2["receipt"]["records"][0]["purpose"] == "sale", "예전 입고 기록(구분 없음)은 판매용으로 보임")


async def units_part(uid: int) -> None:
    from app.sales_deals import service as svc
    from app.sales_deals import units

    today = svc.kst_today()
    async with SessionLocal() as db:
        did = await svc.create_deal(db, uid, {"customer": SUP + "건", "title": "t", "owner": "Alex", "quote_date": today, "vat_included": False,
                                              "currency": "KRW", "items": [{"name": "MG10 그리퍼", "unit_price": 1, "qty": 3}], "note": "",
                                              "pay_terms_text": None, "contact": None, "rental": None})
        d = await svc.get_deal(db, did, today)
    # 견적서 판이 없는 직접 등록 건은 S/N 판이 없어 — 출하 S/N 은 견적서 판 기준이므로 견적서로 만든 건을 쓴다
    from app.company_knowledge import product_recommend as pr

    q = await pr.quote_manual(user_id=uid, form={"initials": "QR", "contact_name": "Alex", "contact_mobile": "010", "contact_email": "a@b.c"})
    await pr.quote_edit(q["id"], user_id=uid, payload={"form": {"customer": SUP + "건", "subject": "MG"}, "delivery": {"case": 1},
                                                       "lines": [{"id": None, "remark": "", "name": "MG10", "qty": 3, "unit_price": 1}]})
    q = await pr.quote_issue(q["id"], user_id=uid)
    async with SessionLocal() as db:
        did = (await db.execute(text("SELECT deal_id FROM product_quotes WHERE id = :i"), {"i": q["id"]})).scalar()
        line = 0
        await units.save_units(db, did, uid, q["id"], 0, [{"line_no": line, "units": [{"serial": "QR-UND-1", "maker": "UND"}]}], partial=True)
        u = (await db.execute(text("SELECT serial, und_serial FROM product_units WHERE deal_id = :d"), {"d": did})).one()
        check(tuple(u) == ("QR-UND-1", "QR-UND-1"), "우리 제품(UND) — 제조사 S/N 만 적으면 UND S/N 도 같은 번호", tuple(u))
        await expect_err(units.save_units(db, did, uid, q["id"], 0, [{"line_no": line, "units": [
            {"serial": "QR-UND-1", "maker": "UND"}, {"serial": "SCH-1", "maker": "Schunk"}]}], partial=True),
            "다른 제조사는 UND S/N 도 적어야 함", "UND S/N")
        await db.rollback()
        await units.save_units(db, did, uid, q["id"], 0, [{"line_no": line, "units": [
            {"serial": "QR-UND-1", "maker": "UND"}, {"serial": "SCH-1", "und_serial": "QR-UND-2", "maker": "Schunk", "photos": [PH, "x"]}]}], partial=True)
        rs = (await db.execute(text("SELECT serial, und_serial, photos FROM product_units WHERE deal_id = :d ORDER BY id"), {"d": did})).all()
        ph = rs[1][2] if not isinstance(rs[1][2], str) else json.loads(rs[1][2])
        check(rs[1][0] == "SCH-1" and rs[1][1] == "QR-UND-2" and ph == [PH], "다른 제조사 — 두 줄 S/N·S/N 사진 저장", [tuple(r) for r in rs])
        await expect_err(units.save_units(db, did, uid, q["id"], 0, [{"line_no": line, "units": [
            {"serial": "QR-UND-1", "maker": "UND"}, {"serial": "SCH-1", "und_serial": "QR-UND-2", "maker": "Schunk"},
            {"serial": "SCH-2", "und_serial": "qr-und-2", "maker": "Schunk"}]}], partial=True),
            "UND S/N 겹치면 거절", "UND S/N")
        await db.rollback()
        found = await units.search_units(db, "QR-UND-2", today)
        check(any(x["serial"] == "SCH-1" for x in found), "UND S/N 으로 찾기", [x["serial"] for x in found])
        await svc.delete_deals(db, [did], uid)
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM product_quotes WHERE id = :i"), {"i": q["id"]})
        await db.execute(text("DELETE FROM sales_deal_trash WHERE deal->>'customer' = :c"), {"c": SUP + "건"})
        await db.execute(text("DELETE FROM sales_deals WHERE customer = :c"), {"c": SUP + "건"})
        await db.execute(text("DELETE FROM doc_numbers WHERE issuer = 'QR'"))
        await db.commit()


async def main() -> None:
    await cleanup()
    async with SessionLocal() as db:
        uid = (await db.execute(text("SELECT id FROM users WHERE username = 'alex'"))).scalar()
    try:
        await purchase_part(uid)
        await units_part(uid)
    finally:
        await cleanup()
    print(f"PASS {sum(res)} / FAIL {len(res) - sum(res)}")
    sys.exit(0 if all(res) else 1)


asyncio.run(main())
