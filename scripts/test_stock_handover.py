# -*- coding: utf-8 -*-
"""재고 제품 → 영업 건 판매·대여 넘기기(사용자 2026-11-20).

- 판매용 입고도 대마다 S/N — 입고하면 제품 S/N 관리에 'SP · 판매 예정'(구매·고객사·담당자)
- 출하 S/N 이 판매용 입고 S/N 과 같으면 STOCK_MATCH('판매하시겠습니까?') → take_stock 이면 그 S/N 줄이 건으로 넘어가고
  재고는 '판매 완료'(목록에서 빠짐). 건에서 S/N 을 지우면 재고로 돌아옴('대기 중', 다시 SP)
- 대여 S/N 이 자체 개발용 S/N 과 같으면 '대여하시겠습니까?' → 재고 '대여 중'(목록에 남음) → 회수하면 '대기 중', 다시 SD
- '대여 중'은 손으로 넣고 빼지 않는다, [S/N 없는 품목]은 S/N 표에 안 올림
dev DB 전용, 만든 것은 끝에 지운다:
  docker compose run --rm -T --no-deps -e PYTHONPATH=/app -v ./backend/app:/app/app -v ./scripts:/scripts backend python /scripts/test_stock_handover.py
"""
import asyncio
import os
import sys

assert "agent_ai_dev" in os.environ.get("DATABASE_URL", ""), "NOT DEV DB - abort"
from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402

SUP = "__QA_재고넘김__"
UID = 1
res: list[bool] = []


def check(ok: bool, name: str, *more) -> None:
    res.append(bool(ok))
    print("PASS" if ok else "FAIL", name, *more)


async def expect_err(coro, name: str, part: str) -> str:
    try:
        await coro
        check(False, name, "오류가 나야 함")
        return ""
    except Exception as e:  # noqa: BLE001
        check(part in str(e), name, str(e)[:90])
        return str(e)


async def cleanup() -> None:
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM product_units WHERE serial LIKE 'QH-%' OR und_serial LIKE 'QH-%'"))
        await db.execute(text("DELETE FROM purchases WHERE supplier = :s"), {"s": SUP})
        dids = (await db.execute(text("SELECT id FROM sales_deals WHERE customer = :c"), {"c": SUP + "건"})).scalars().all()
        await db.execute(text("DELETE FROM product_quotes WHERE deal_id = ANY(:d)"), {"d": list(dids)})
        await db.execute(text("DELETE FROM sales_deal_trash WHERE deal->>'customer' = :c"), {"c": SUP + "건"})
        await db.execute(text("DELETE FROM sales_deals WHERE customer = :c"), {"c": SUP + "건"})
        await db.execute(text("DELETE FROM doc_numbers WHERE issuer = 'QH'"))
        await db.commit()


async def register(purpose: str, lines: list[dict]) -> int:
    from app.purchases import service as ps

    async with SessionLocal() as db:
        pid = await ps.direct_draft(db, UID, "QA")
        await ps.save_direct(db, pid, UID, "QA", {"purpose": purpose, "date": ps.kst_today(), "supplier": SUP, "region": "domestic",
                                                  "lines": lines})
        return pid


async def unit_of(db, und: str) -> dict | None:
    r = (await db.execute(text("SELECT u.id, u.deal_id, u.purpose, u.status, u.stock_item_id, s.status AS stock_status FROM product_units u "
                               "LEFT JOIN stock_items s ON s.id = u.stock_item_id WHERE u.und_serial = :u ORDER BY u.id DESC LIMIT 1"),
                          {"u": und})).mappings().first()
    return dict(r) if r else None


async def run() -> None:
    from app.company_knowledge import product_recommend as pr
    from app.purchases import stock
    from app.purchases import service as ps
    from app.sales_deals import units

    today = ps.kst_today()
    from app.api_purchases import ReceiptLineIn
    ln = ReceiptLineIn(**{"line_no": 0, "qty": 1, "units": [{"und_sn": "X", "customer": "A사", "owner": "Lucas"}]}).model_dump()
    check(ln["units"][0]["customer"] == "A사" and ln["units"][0]["owner"] == "Lucas", "화면 요청 — 대마다 고객사·담당자가 서버까지 전달", ln["units"][0])
    from fastapi import HTTPException
    from app.api_purchases import require_category
    try:
        require_category([ReceiptLineIn(line_no=0, qty=2), ReceiptLineIn(line_no=1, qty=1, category="AMR")])
        check(False, "구분 없는 품목은 저장 막음")
    except HTTPException as e:
        check("구분" in str(e.detail), "구분 없는 품목은 저장 막음(필수, 2026-11-20)", e.detail)
    require_category([ReceiptLineIn(line_no=0, qty=0), ReceiptLineIn(line_no=1, qty=1, category="AMR")])
    check(True, "수량 0 줄은 구분 없어도 됨")
    # 대마다 S/N 전(2026-11-20 전)에 남긴 판매용 줄(units 칸 없음) — 예전처럼 한 줄로 묶이고 S/N 표에 안 올라감
    w = stock.wanted({"records": [{"purpose": "sale", "date": "2026-11-19", "lines": [{"line_no": None, "name": "옛 케이블", "qty": 3, "customer": "DD"}]}]})
    check({v["part"] for v in w.values()} == {0} and all(v["no_sn"] for v in w.values()) and len(w) == 3,
          "예전 판매용 기록은 묶음 줄(S/N 없는 품목처럼)", [(v["part"], v["no_sn"]) for v in w.values()])
    # 판매용 입고 — 대마다 S/N·고객사·담당자, 케이블은 [한 줄로 합치기]
    pid = await register("sale", [
        {"name": "QH 로봇", "qty": 2, "units": [{"maker_sn": "QH-M1", "und_sn": "QH-U1", "customer": "A사", "owner": "Lucas"},
                                                 {"maker_sn": "", "und_sn": "QH-U2", "customer": "B사", "owner": "Mason"}]},
        {"name": "QH 케이블", "qty": 5, "no_sn": True}])
    async with SessionLocal() as db:
        u1 = await unit_of(db, "QH-U1")
        check(u1 and u1["purpose"] == "SP" and u1["deal_id"] is None and u1["stock_item_id"], "판매용 입고 → S/N 관리에 SP · 판매 예정", u1)
        found = await units.search_units(db, "QH-U1", today)
        check(found and found[0]["warranty_state"] == "판매 전", "판매 예정 S/N 찾기 — 무상 상태 '판매 전'", found[:1] and found[0]["warranty_state"])
        det = await units.unit_detail(db, u1["id"], today)
        check(det["stock"] and det["stock"]["customer"] == "A사" and det["stock"]["owner"] == "Lucas" and det["stock"]["purpose"] == "sale",
              "상세 — 판매 고객사·우리 담당자", det["stock"] and {k: det["stock"][k] for k in ("customer", "owner", "purpose")})
        n_cable = (await db.execute(text("SELECT COUNT(*) FROM product_units u JOIN stock_items s ON s.id = u.stock_item_id "
                                         "WHERE s.purchase_id = :p AND s.name = 'QH 케이블'"), {"p": pid})).scalar()
        check(n_cable == 0, "S/N 없는 품목(케이블)은 S/N 표에 안 올림", n_cable)
        rows = [r for r in (await stock.list_items(db, "sale"))["rows"] if r["purchase_id"] == pid]
        check(len([r for r in rows if r["name"] == "QH 로봇"]) == 2 and {r["customer"] for r in rows if r["name"] == "QH 로봇"} == {"A사", "B사"},
              "재고 — 대마다 한 줄, 대마다 고객사", [(r["name"], r["customer"]) for r in rows])

    # 견적서로 만든 영업 건(S/N 판이 있음)
    q = await pr.quote_manual(user_id=UID, form={"initials": "QH", "contact_name": "Alex", "contact_mobile": "010", "contact_email": "a@b.c"})
    await pr.quote_edit(q["id"], user_id=UID, payload={"form": {"customer": SUP + "건", "subject": "QH"}, "delivery": {"case": 1},
                                                       "lines": [{"id": None, "remark": "", "name": "QH 로봇", "qty": 3, "unit_price": 1}]})
    q = await pr.quote_issue(q["id"], user_id=UID)
    async with SessionLocal() as db:
        did = (await db.execute(text("SELECT deal_id FROM product_quotes WHERE id = :i"), {"i": q["id"]})).scalar()
        sale_payload = [{"line_no": 0, "units": [{"serial": "QH-M1", "und_serial": "QH-U1", "maker": "Deep", "purpose": "S"}]}]
        msg = await expect_err(units.save_units(db, did, UID, q["id"], 0, sale_payload, partial=True),
                               "출하 S/N 이 판매용 입고 S/N 과 같으면 묻기", "판매용 입고 제품과 겹치는 S/N이 있습니다. 판매하시겠습니까?")
        check("QH-U1" in msg, "겹치는 S/N 번호를 알려 줌", msg[:120])
        await db.rollback()
        await units.save_units(db, did, UID, q["id"], 0, sale_payload, partial=True, take_stock=True)
        u = await unit_of(db, "QH-U1")
        check(u["id"] == u1["id"] and u["deal_id"] == did and u["purpose"] == "S" and u["stock_status"] == stock.SOLD,
              "확인하면 그 S/N 줄이 건으로(같은 줄·구매 연결 유지), 재고 '판매 완료'", u)
        rows = [r for r in (await stock.list_items(db, "sale"))["rows"] if r["purchase_id"] == pid and r["name"] == "QH 로봇"]
        check(len(rows) == 1 and rows[0]["und_sn"] == "QH-U2", "판매 완료는 재고 목록에서 빠짐", [r["und_sn"] for r in rows])
        det = await units.unit_detail(db, u["id"], today)
        check(det["stock"] and det["stock"]["purchase_id"] == pid, "판매 건 S/N 상세에도 구매 정보", det["stock"] and det["stock"]["purchase_id"])
        titles = [x["title"] for x in det["timeline"]]
        check(titles[:3] == ["판매용 입고 → 판매 예정", "재고에서 판매로", "판매 건에 S/N 등록"]
              and det["timeline"][0]["link"]["kind"] == "purchase" and det["timeline"][2]["link"]["id"] == did,
              "S/N 이력 — 입고 → 판매로 → 판매 건 등록(구매 건·영업 건 열기)", titles)
        c = await units.create_case(db, UID, u["id"], {"received_on": today, "symptom": "바퀴 소음", "cause": "제품 결함"})
        await units.update_case(db, UID, c["id"], {"status": "진행 중"})
        tl = (await units.unit_detail(db, u["id"], today))["timeline"]
        tail = [x["title"] for x in tl][-2:]
        check(tail == ["A/S 접수", "A/S 진행 중"] and tl[-2].get("as_case") == c["id"] and "제품 결함" in tl[-2]["detail"],
              "S/N 이력에 A/S 접수·진행 중도(짧게)", [(x["title"], x["detail"]) for x in tl[-2:]])
        await db.execute(text("DELETE FROM as_cases WHERE id = :i"), {"i": c["id"]})
        await db.execute(text("UPDATE product_units SET purpose = 'S' WHERE id = :i"), {"i": u["id"]})
        await db.commit()
        # 입고 기록을 다시 저장해도 넘어간 S/N 줄은 그대로
        await stock.sync(db, pid)
        await db.commit()
        u = await unit_of(db, "QH-U1")
        check(u["deal_id"] == did and u["purpose"] == "S", "입고 기록이 다시 맞춰져도 건으로 넘어간 S/N 줄은 그대로", u)
        # 건에서 S/N 을 지우면 재고로 돌아온다
        await units.save_units(db, did, UID, q["id"], 0, [{"line_no": 0, "units": []}], partial=True)
        rows = [r for r in (await stock.list_items(db, "sale"))["rows"] if r["purchase_id"] == pid and r["name"] == "QH 로봇"]
        u = await unit_of(db, "QH-U1")
        check(len(rows) == 2 and u and u["deal_id"] is None and u["purpose"] == "SP" and u["stock_status"] == "대기 중",
              "건에서 S/N 을 지우면 재고로 돌아옴(대기 중, 다시 SP)", u)

        # 자체 개발용 → 대여 → 회수
        pid2 = await register("dev", [{"name": "QH 개발 로봇", "qty": 1, "units": [{"maker_sn": "QH-M9", "und_sn": "QH-U9"}]}])
        rent_payload = [{"line_no": 0, "units": [{"serial": "QH-M9", "und_serial": "QH-U9", "maker": "Deep", "purpose": "R"}]}]
        await expect_err(units.save_units(db, did, UID, q["id"], 0, rent_payload, partial=True),
                         "대여 S/N 이 자체 개발용 입고 S/N 과 같으면 묻기", "자체 개발용 입고 제품과 겹치는 S/N이 있습니다. 대여하시겠습니까?")
        await db.rollback()
        await units.save_units(db, did, UID, q["id"], 0, rent_payload, partial=True, take_stock=True)
        rows = [r for r in (await stock.list_items(db, "dev"))["rows"] if r["purchase_id"] == pid2]
        check(len(rows) == 1 and rows[0]["status"] == stock.RENTED, "대여로 넘어가면 재고 '대여 중'(목록에 남음)", [r["status"] for r in rows])
        sid = rows[0]["id"]
        await expect_err(stock.update_item(db, sid, UID, {"status": "대기 중"}), "'대여 중'은 손으로 빼지 않음", "자동으로")
        await db.rollback()
        await expect_err(stock.delete_items(db, [sid], UID), "대여 중인 제품은 지울 수 없음(관리자 지우기)", "회수한 뒤")
        await db.rollback()
        rid = (await unit_of(db, "QH-U9"))["id"]
        await db.execute(text("UPDATE product_units SET status = '회수' WHERE id = :i"), {"i": rid})   # 회수 완료
        await db.commit()
        rows = [r for r in (await stock.list_items(db, "dev"))["rows"] if r["purchase_id"] == pid2]
        back = (await db.execute(text("SELECT purpose, deal_id FROM product_units WHERE stock_item_id = :s"), {"s": sid})).mappings().first()
        old = (await db.execute(text("SELECT status, deal_id, stock_item_id FROM product_units WHERE id = :i"), {"i": rid})).mappings().first()
        check(rows[0]["status"] == "대기 중" and back and back["purpose"] == "SD" and back["deal_id"] is None,
              "회수하면 재고 '대기 중', S/N 관리에 다시 SD", (rows[0]["status"], dict(back or {})))
        check(old["status"] == "회수" and old["deal_id"] == did and old["stock_item_id"] is None, "회수한 대여 줄은 그 건의 이력으로 남음", dict(old))
        sd = (await db.execute(text("SELECT id FROM product_units WHERE stock_item_id = :s"), {"s": sid})).scalar()
        tl = [x["title"] for x in (await units.unit_detail(db, sd, today))["timeline"]]
        check(tl == ["보유 후 자체 개발용 입고 → 자체 개발", "재고에서 대여로", "대여 건에 S/N 등록", "대여 회수", "재고로 돌아옴"],
              "S/N 이력 — 자체 개발 입고 → 대여 → 회수 → 재고로(재고 줄이 다시 생겨도 이어짐)", tl)
        await expect_err(stock.update_item(db, sid, UID, {"status": stock.RENTED}), "'대여 중'은 손으로 넣지 않음", "자동으로")
        await db.rollback()

        # 관리자 지우기(사용자 2026-11-20) — 재고 목록·S/N 표에서 빠지고, 입고 기록을 다시 맞춰도 안 되살아남
        u2 = await unit_of(db, "QH-U2")
        s2 = u2["stock_item_id"]
        n = await stock.delete_items(db, [s2], UID)
        rows = [r for r in (await stock.list_items(db, "sale"))["rows"] if r["purchase_id"] == pid]
        check(n == 1 and s2 not in {r["id"] for r in rows} and await unit_of(db, "QH-U2") is None,
              "관리자 지우기 — 재고 목록·S/N 표(SP)에서 빠짐", (n, [r["id"] for r in rows]))
        await stock.sync(db, pid)
        await db.commit()
        rows = [r for r in (await stock.list_items(db, "sale"))["rows"] if r["purchase_id"] == pid]
        check(s2 not in {r["id"] for r in rows} and await unit_of(db, "QH-U2") is None,
              "입고 기록을 다시 맞춰도 지운 줄은 안 되살아남", [r["id"] for r in rows])
        h = await stock.history(db, s2)
        check(any(x.get("action") == "delete" or x.get("label") == "삭제(관리자)" for x in h), "이력에 '삭제(관리자)'", h[:1])


async def main() -> None:
    await cleanup()
    try:
        await run()
    finally:
        await cleanup()
    print(f"PASS {sum(res)} / FAIL {len(res) - sum(res)}")
    sys.exit(0 if all(res) else 1)


asyncio.run(main())
