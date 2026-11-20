# -*- coding: utf-8 -*-
"""매출 내역 엑셀(사용자 2026-11-20) — 잔금 세금계산서 발행일 기준, 월별·분기별·연도별.

건 = 한 덩어리(발행일·고객사·번호·건 합계 칸 세로 합침), 품목 여러 줄·수량·금액, 월 소계·총계, 요약 시트.
부가세 포함 견적은 품목 금액을 공급가로. 세금계산서 미완료·드랍 건은 빠짐.
dev DB 전용, 만든 것은 끝에 지운다:
  docker compose run --rm -T --no-deps -e PYTHONPATH=/app -v ./backend/app:/app/app -v ./scripts:/scripts backend python /scripts/test_revenue_export.py
"""
import asyncio
import datetime as dt
import io
import os
import sys

assert "agent_ai_dev" in os.environ.get("DATABASE_URL", ""), "NOT DEV DB - abort"
from openpyxl import load_workbook  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402

CUS = "__QA_매출엑셀"
UID = 1
res: list[bool] = []


def check(ok: bool, name: str, *more) -> None:
    res.append(bool(ok))
    print("PASS" if ok else "FAIL", name, *more)


async def cleanup() -> None:
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM sales_deals WHERE customer LIKE :c"), {"c": CUS + "%"})
        await db.commit()


async def make(db, name: str, items: list[dict], vat: bool, last: dt.date | None, dropped: bool = False) -> int:
    from app.sales_deals import service as svc
    did = await svc.create_deal(db, UID, {"customer": f"{CUS} {name}", "title": name, "owner": "Alex", "quote_date": svc.kst_today(),
                                          "vat_included": vat, "currency": "KRW", "items": items, "note": "", "pay_terms_text": None,
                                          "contact": None, "rental": None})
    await db.execute(text("UPDATE sales_deals SET won = TRUE, won_date = '2026-07-01', dropped = :dr WHERE id = :i"), {"i": did, "dr": dropped})
    await db.commit()
    await svc.set_tax_terms(db, did, UID, [{"label": "선금", "pct": 30}, {"label": "잔금", "pct": 70}])
    await svc.confirm_tax(db, did, UID, "QA", 0, dt.date(2026, 7, 5))
    if last:
        await svc.confirm_tax(db, did, UID, "QA", 1, last)
    return did


async def run() -> None:
    from app.sales_deals import revenue_xlsx as rx
    from app.sales_deals import service as svc

    async with SessionLocal() as db:
        await make(db, "A", [{"name": "MG10 그리퍼", "unit_price": 1_500_000, "qty": 2}, {"name": "케이블", "unit_price": 10_000, "qty": 5}],
                   False, dt.date(2026, 11, 5))
        await make(db, "B", [{"name": "TCV3 툴체인저", "unit_price": 1_100_000, "qty": 1}], True, dt.date(2026, 10, 20))   # 부가세 포함 견적
        await make(db, "C 미완료", [{"name": "로봇", "unit_price": 5_000_000, "qty": 1}], False, None)
        await make(db, "D 드랍", [{"name": "로봇", "unit_price": 5_000_000, "qty": 1}], False, dt.date(2026, 11, 7), dropped=True)
        rows = [r for r in (await svc.list_deals(db, svc.kst_today()))["rows"] if (r.get("customer") or "").startswith(CUS)]

    title, data = rx.build_xlsx(rows, 2026, "month", 11)
    ws = load_workbook(io.BytesIO(data))["매출 내역"]
    vals = [[c.value for c in r] for r in ws.iter_rows(min_row=4)]
    check(title == "2026년 11월" and vals[0][:6] == ["2026-11-05", f"{CUS} A", vals[0][2], "MG10 그리퍼", 2, 3_000_000]
          and vals[0][6] == 3_050_000 and vals[0][8] == 3_050_000 + vals[0][7], "11월 — A 건 첫 줄(발행일·고객사·품목·수량·금액·건 합계)", vals[0])
    check(vals[1][:6] == [None, None, None, "케이블", 5, 50_000], "같은 건 두 번째 품목 줄은 아래로(앞 칸 비움)", vals[1][:6])
    merged = {str(m) for m in ws.merged_cells.ranges}
    check("A4:A5" in merged and "B4:B5" in merged and "G4:G5" in merged, "건 하나 = 한 덩어리(발행일·고객사·건 합계 세로 합침)", sorted(merged)[:6])
    check(vals[2][0] == "2026년 11월 소계" and vals[2][1] == "1건" and vals[2][4] == 7 and vals[2][6] == 3_050_000,
          "월 소계 — 건수·수량 합계·공급가액(드랍·미완료 건 빠짐)", vals[2])
    _, data = rx.build_xlsx(rows, 2026, "quarter", 4)
    wb = load_workbook(io.BytesIO(data))
    v4 = [[c.value for c in r] for r in wb["매출 내역"].iter_rows(min_row=4)]
    b = next(r for r in v4 if r[1] == f"{CUS} B")
    check(b[0] == "2026-10-20" and b[3] == "TCV3 툴체인저" and b[5] == 1_000_000 and b[6] == 1_000_000 and b[7] == 100_000 and b[8] == 1_100_000,
          "부가세 포함 견적 — 품목 금액·건 공급가액은 공급가, 부가세·합계", b)
    tot = v4[-1]
    check(tot[0] == "2026년 4분기 총계" and tot[1] == "2건" and tot[6] == 4_050_000, "4분기 총계 — 10월·11월 합", tot)
    sm = [[c.value for c in r] for r in wb["요약"].iter_rows(min_row=4)]
    check([r[0] for r in sm] == ["10월", "11월", "12월", "2026년 4분기"] and sm[0][3] == 1_000_000 and sm[1][3] == 3_050_000 and sm[2][1] == 0,
          "요약 시트 — 월별 건수·금액", sm)
    _, data = rx.build_xlsx(rows, 2026, "year", None)
    sy = [[c.value for c in r] for r in load_workbook(io.BytesIO(data))["요약"].iter_rows(min_row=4)]
    check(len(sy) == 13 and sy[-1][1] == 2, "연도별 — 12개월 + 합계", len(sy))
    try:
        rx.build_xlsx(rows, 2026, "month", 13)
        check(False, "잘못된 월 거절")
    except rx.RevenueError as e:
        check("월" in str(e), "잘못된 월 거절", str(e))


async def main() -> None:
    await cleanup()
    try:
        await run()
    finally:
        await cleanup()
    print(f"PASS {sum(res)} / FAIL {len(res) - sum(res)}")
    sys.exit(0 if all(res) else 1)


asyncio.run(main())
