# -*- coding: utf-8 -*-
"""발주서 VAT 포함/별도(사용자 2026-11-20).

국내 발주서에서 고른 값이 머리(header.vat_included)에 남고, 엑셀 품목 표 위 글자('VAT 포함'/'VAT 별도')로 찍힌다.
VAT 포함이면 공급가액 = 합계 ÷ 1.1, 송금 회차의 부가세 포함 금액은 발주서 합계를 그대로 나눔. 해외는 늘 별도.
송금 확인한 뒤에는 VAT 포함/별도도 못 바꾼다. 고친 파일의 'VAT 포함' 글자를 읽는다.
dev DB 전용, 만든 것은 끝에 지운다:
  docker compose run --rm -T --no-deps -e PYTHONPATH=/app -v ./backend/app:/app/app -v ./scripts:/scripts backend python /scripts/test_po_vat.py
"""
import asyncio
import io
import os
import sys

assert "agent_ai_dev" in os.environ.get("DATABASE_URL", ""), "NOT DEV DB - abort"
from openpyxl import load_workbook  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402

SUP = "__QA_발주VAT__"
UID = 1
res: list[bool] = []


def check(ok: bool, name: str, *more) -> None:
    res.append(bool(ok))
    print("PASS" if ok else "FAIL", name, *more)


async def expect_err(coro, name: str, part: str) -> None:
    try:
        await coro
        check(False, name, "오류가 나야 함")
    except Exception as e:  # noqa: BLE001
        check(part in str(e), name, str(e)[:80])


async def cleanup() -> None:
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM purchases WHERE supplier = :s"), {"s": SUP})
        await db.execute(text("DELETE FROM doc_numbers WHERE issuer = 'QV'"))
        await db.commit()


async def make(db, region: str) -> int:
    from app.purchases import service as ps
    t = ps.kst_today()
    pid = await ps.create(db, UID, {"supplier": SUP, "title": "", "owner": "", "note": "", "reason": "", "region": region})
    await ps.skip_quote(db, pid, UID, "QA", True)
    await ps.approve(db, pid, UID, "QA", t)
    return pid


async def issue(db, pid: int, vat: bool, price: float = 110_000) -> None:
    from app.purchases import service as ps
    dr = await ps.po_draft(db, pid, {})
    hd = {**dr["header"], "vat_included": vat, "issuer": {**dr["header"]["issuer"], "name": "QA", "attn": "QA"}}
    await ps.issue_po(db, pid, UID, "QA", {"initials": "QV", "expected_date": ps.kst_today().isoformat(), "header": hd,
                                           "items": [{"description": "QV 로봇", "qty": 3, "price": price}]})


async def run() -> None:
    from app.purchases import flow
    from app.purchases import po_read
    from app.purchases import po_xlsx as px
    from app.purchases import service as ps

    from app import money
    check((money.vat_of(123_457), money.with_vat(12_345), money.supply_of(13_579), money.supply_of(110_000), money.won(-12.7))
          == (12_345, 13_579, 12_345, 100_000, -12), "원 미만 절삭 — 부가세·부가세 포함·공급가액(되돌리면 같은 값)")
    check(all(money.supply_of(money.with_vat(x)) == x for x in range(0, 200_000)), "공급가액 → 부가세 포함 → 공급가액 0~200,000원 모두 같음")
    today = ps.kst_today()
    async with SessionLocal() as db:
        pid = await make(db, "domestic")
        await issue(db, pid, True)                                       # 110,000 × 3 = 330,000 (VAT 포함)
        p = await ps.get(db, pid, today)
        d = await ps._lock(db, pid)
        await db.rollback()
        check(p["po"]["supply"] == 300_000, "발주 금액은 늘 공급가액(VAT 별도) — 화면 값", p["po"]["supply"])
        got = await ps.list_purchases(db, today)
        row = next(r for r in (got.get("rows") or got.get("items") or []) if r["id"] == pid)
        check(row["total"] == 300_000, "구매 건 목록 금액도 공급가액", row["total"])
        stt = __import__("app.sales_deals.statement_xlsx", fromlist=["x"]).totals([{"qty": 1, "unit_price": 123_457}])
        check(stt == {"supply": 123_457, "vat": 12_345, "total": 135_802}, "거래명세서 부가세도 원 미만 절삭", stt)
        check(p["po"]["vat_included"] and p["po"]["total"] == 330_000 and flow.po_total(d) == 300_000,
              "VAT 포함 발주서 — 찍힌 합계 330,000, 공급가액 300,000", (p["po"]["vat_included"], p["po"]["total"], flow.po_total(d)))
        await ps.set_terms(db, pid, UID, [{"label": "선금", "pct": 30, "when": "발주 시"}, {"label": "잔금", "pct": 70}], admin=False)
        p = await ps.get(db, pid, today)
        t = p["terms"]
        check([x["amount"] for x in t] == [90_000, 210_000] and [x["amount_vat"] for x in t] == [99_000, 231_000]
              and sum(x["amount_vat"] for x in t) == 330_000, "회차 — 공급가액·부가세 포함 금액이 발주서 합계와 맞음",
              [(x["amount"], x["amount_vat"]) for x in t])
        wb = load_workbook(io.BytesIO(px.build_xlsx(d["po"]["header"], d["po"]["items"])))
        check(wb.active["G19"].value == "VAT 포함", "엑셀 품목 표 위 'VAT 포함'", wb.active["G19"].value)
        read = po_read._header(wb.active, 20)
        check(read.get("vat_included") is True, "고친 파일에서 'VAT 포함' 읽음", read.get("vat_included"))
        # 송금 확인 뒤엔 VAT 포함/별도 못 바꿈
        await ps.confirm_term(db, pid, UID, "QA", 0, today, None)
        await expect_err(issue(db, pid, False), "송금 확인한 건은 VAT 포함/별도도 못 바꿈", "확인 취소")
        await db.rollback()
        await ps.unconfirm_term(db, pid, UID, 0)
        await issue(db, pid, False)                                      # 별도로 다시 발행 → 공급가액 = 합계
        d = await ps._lock(db, pid)
        await db.rollback()
        wb = load_workbook(io.BytesIO(px.build_xlsx(d["po"]["header"], d["po"]["items"])))
        check(not flow.vat_included(d) and flow.po_total(d) == 330_000 and wb.active["G19"].value == "VAT 별도",
              "VAT 별도로 다시 발행 — 공급가액 = 합계, 'VAT 별도'", (flow.po_total(d), wb.active["G19"].value))
        # 해외는 늘 별도
        pid2 = await make(db, "overseas")
        await issue(db, pid2, True, 100)
        d2 = await ps._lock(db, pid2)
        await db.rollback()
        check(not flow.vat_included(d2) and flow.po_total(d2) == 300, "해외 발주서는 VAT 포함을 보내도 별도", flow.po_total(d2))


async def main() -> None:
    await cleanup()
    try:
        await run()
    finally:
        await cleanup()
    print(f"PASS {sum(res)} / FAIL {len(res) - sum(res)}")
    sys.exit(0 if all(res) else 1)


asyncio.run(main())
