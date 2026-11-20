# -*- coding: utf-8 -*-
"""재고 직접 등록(사용자 2026-11-19) — 구매 없이 [제품 등록]으로 이미 있는 제품을 넣는 '직접 등록' 건.

빈 작성 건은 같은 사람이면 다시 씀, 판매용(줄 나누기)·자체 개발용(대마다 S/N·한 줄로 합치기) 저장 → 재고·S/N 관리,
구매 건 목록엔 안 나옴, 목적 바꾸기 거절, 고치기, 지우면 재고·S/N 정리.
dev DB 전용, 만든 것은 끝에 지운다:
  docker compose run --rm -T --no-deps -e PYTHONPATH=/app -v ./backend/app:/app/app -v ./scripts:/scripts backend python /scripts/test_stock_direct.py
"""
import asyncio
import datetime as dt
import os
import sys

assert "agent_ai_dev" in os.environ.get("DATABASE_URL", ""), "NOT DEV DB - abort"
from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402

SUP = "__QA_직접등록__"
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
        await db.execute(text("DELETE FROM purchases WHERE direct AND (supplier LIKE :s OR (created_by = 1 AND supplier = '직접 등록'))"),
                         {"s": SUP + "%"})
        await db.commit()


async def run() -> None:
    from app.purchases import service as ps
    from app.purchases import stock

    today = ps.kst_today()
    async with SessionLocal() as db:
        uid = 1
        a = await ps.direct_draft(db, uid, "QA")
        b = await ps.direct_draft(db, uid, "QA")
        check(a == b, "빈 작성 건은 다시 씀", (a, b))
        await expect_err(ps.save_direct(db, a, uid, "QA", {"purpose": "sale", "date": today, "lines": []}), "품목 없으면 거절", "품목")
        await db.rollback()
        await ps.save_direct(db, a, uid, "QA", {"purpose": "sale", "date": today, "supplier": SUP, "region": "overseas", "location": "본사 2층",
                                                "lines": [{"name": "M20", "qty": 3, "parts": [{"qty": 2, "customer": "A사"}, {"qty": 1, "customer": "B사"}]},
                                                          {"name": "케이블", "qty": 30, "no_sn": True}]})
        rows = [r for r in (await stock.list_items(db, "sale"))["rows"] if r["purchase_id"] == a]
        check(len(rows) == 33 and all(r["direct"] and r["region"] == "overseas" and r["maker"] == SUP for r in rows)
              and sorted({(r["name"], r["part"], r["customer"]) for r in rows}) == [("M20", 0, "A사"), ("M20", 1, "B사"), ("케이블", 0, "")],
              "판매용 직접 등록 → 재고(나눈 줄·국내/해외·제조사)", len(rows))
        listed = [x["id"] for x in (await ps.list_purchases(db, today))["rows"]]
        check(a not in listed, "구매 건 목록엔 안 나옴")
        c = await ps.direct_draft(db, uid, "QA")
        check(c != a, "저장한 건은 다시 안 씀 — 새 작성 건", (a, c))
        await expect_err(ps.save_direct(db, a, uid, "QA", {"purpose": "dev", "date": today, "lines": [{"name": "x", "qty": 1}]}), "목적 바꾸기 거절", "바꿀 수 없")
        await db.rollback()
        # 고치기 — 케이블 30 → 20, M20 그대로
        await ps.save_direct(db, a, uid, "QA", {"purpose": "sale", "date": today, "supplier": SUP, "region": "overseas",
                                                "lines": [{"name": "M20", "qty": 3, "parts": [{"qty": 2, "customer": "A사"}, {"qty": 1, "customer": "B사"}]},
                                                          {"name": "케이블", "qty": 20}]})
        rows = [r for r in (await stock.list_items(db, "sale"))["rows"] if r["purchase_id"] == a]
        check(len(rows) == 23, "고치기 — 수량 따라감", len(rows))
        # 자체 개발용 — 대마다 S/N, 한 줄로 합친 케이블
        await ps.save_direct(db, c, uid, "QA", {"purpose": "dev", "date": today, "supplier": SUP + "D", "lines": [
            {"name": "RB5", "qty": 2, "units": [{"maker_sn": "QDR-1", "und_sn": "QDR-U1", "sw_owner": "Kim"}, {"maker_sn": "QDR-2", "und_sn": "QDR-U2"}]},
            {"name": "케이블", "qty": 10, "no_sn": True}]})
        rows = [r for r in (await stock.list_items(db, "dev"))["rows"] if r["purchase_id"] == c]
        n = (await db.execute(text("SELECT COUNT(*) FROM product_units WHERE stock_item_id = ANY(:i)"), {"i": [r["id"] for r in rows]})).scalar()
        check(len(rows) == 12 and n == 2 and len({r["part"] for r in rows if r["name"] == "RB5"}) == 2,
              "자체 개발용 직접 등록 → 재고·S/N 관리(대마다)", (len(rows), n))
        # 지우기 — 재고·S/N 정리
        await ps.delete_direct(db, c)
        n = (await db.execute(text("SELECT COUNT(*) FROM product_units WHERE maker = :m"), {"m": SUP + "D"})).scalar()
        left = [r for r in (await stock.list_items(db, "dev"))["rows"] if r["purchase_id"] == c]
        check(n == 0 and left == [], "지우면 재고·S/N 정리", (n, len(left)))


async def main() -> None:
    await cleanup()
    try:
        await run()
    finally:
        await cleanup()
    print(f"PASS {sum(res)} / FAIL {len(res) - sum(res)}")
    sys.exit(0 if all(res) else 1)


asyncio.run(main())
