# -*- coding: utf-8 -*-
"""영업 건 세금계산서 회차 · 매출일(사용자 2026-11-20).

- 처음엔 결제 조건 % 를 그대로 보여 줌(저장 전 초안), 첫 [확인]에서 저장
- 선금·중도금1·중도금2·잔금 % (합 100) 로 공급가액 나눔 — 원 미만 절삭, 마지막 회차가 끝전
- [확인]은 발행일 필수, 확인한 회차가 있으면 회차를 못 바꿈, 확인 취소
- 마지막 회차까지 확인하면 그 발행일 = 매출일 → 분기별 매출액(수주일과 상관없이)
dev DB 전용, 만든 것은 끝에 지운다:
  docker compose run --rm -T --no-deps -e PYTHONPATH=/app -v ./backend/app:/app/app -v ./scripts:/scripts backend python /scripts/test_tax_invoices.py
"""
import asyncio
import datetime as dt
import os
import sys

assert "agent_ai_dev" in os.environ.get("DATABASE_URL", ""), "NOT DEV DB - abort"
from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402

CUS = "__QA_세금계산서__"
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
        await db.execute(text("DELETE FROM sales_deals WHERE customer = :c"), {"c": CUS})
        await db.commit()


async def run() -> None:
    from app.sales_deals import flow
    from app.sales_deals import service as svc

    today = svc.kst_today()
    y = today.year
    async with SessionLocal() as db:
        did = await svc.create_deal(db, UID, {"customer": CUS, "title": "t", "owner": "Alex", "quote_date": today, "vat_included": False,
                                              "currency": "KRW", "items": [{"name": "로봇", "unit_price": 1_000_001, "qty": 1}], "note": "",
                                              "pay_terms_text": None, "contact": None, "rental": None})
        await db.execute(text("UPDATE sales_deals SET won = TRUE, won_date = :w WHERE id = :i"), {"w": dt.date(y, 2, 10), "i": did})
        await db.commit()
        await svc.set_terms(db, did, UID, [{"label": "선금", "pct": 30}, {"label": "잔금", "pct": 70}])
        d = await svc.get_deal(db, did, today)
        tx = d["flow"]["tax"]
        check(not tx["saved"] and [(r["label"], r["pct"]) for r in tx["rows"]] == [("선금", 30), ("잔금", 70)],
              "처음엔 결제 조건 % 그대로(저장 전)", [(r["label"], r["pct"]) for r in tx["rows"]])
        check([r["amount"] for r in tx["rows"]] == [300_000, 700_001] and tx["rows"][0]["vat"] == 30_000,
              "공급가액 원 미만 절삭, 마지막 회차가 끝전 · 부가세", [(r["amount"], r["vat"]) for r in tx["rows"]])
        await svc.confirm_tax(db, did, UID, "QA", 0, dt.date(y, 3, 2))
        d = await svc.get_deal(db, did, today)
        check(d["flow"]["tax"]["saved"] and d["flow"]["tax"]["rows"][0]["confirmed"] and d["flow"]["tax"]["rows"][0]["date"] == f"{y}-03-02"
              and not d["flow"]["tax"]["done"], "첫 [확인] — 초안 저장 + 발행일·확인", d["flow"]["tax"]["rows"][0])
        await expect_err(svc.set_tax_terms(db, did, UID, [{"label": "선금", "pct": 50}, {"label": "잔금", "pct": 50}]),
                         "확인한 회차가 있으면 회차 못 바꿈", "확인 취소")
        await db.rollback()
        await expect_err(svc.confirm_tax(db, did, UID, "QA", 0, dt.date(y, 3, 3)), "같은 회차 두 번 확인 거절", "이미")
        await db.rollback()
        await svc.unconfirm_tax(db, did, UID, 0)
        await expect_err(svc.set_tax_terms(db, did, UID, [{"label": "선금", "pct": 50}, {"label": "잔금", "pct": 40}]),
                         "합이 100 이 아니면 거절", "100")
        await db.rollback()
        terms = [{"label": "선금", "pct": 20}, {"label": "중도금1", "pct": 30}, {"label": "중도금2", "pct": 20}, {"label": "잔금", "pct": 30}]
        await svc.set_tax_terms(db, did, UID, terms)
        d = await svc.get_deal(db, did, today)
        rows = d["flow"]["tax"]["rows"]
        check([r["label"] for r in rows] == ["선금", "중도금1", "중도금2", "잔금"] and [r["amount"] for r in rows] == [200_000, 300_000, 200_000, 300_001]
              and rows[0]["date"] == f"{y}-03-02", "중도금 나눠 4회차 — 확인 취소한 발행일은 남음", [(r["label"], r["amount"], r["date"]) for r in rows])
        k = svc.kpi([d], today)
        check(k["sales"] == 0 and sum(k["sales_by_quarter"]) == 0 and k["won_by_quarter"][0] == 1_000_001,
              "잔금까지 확인 전엔 매출 아님(수주금액은 1분기)", (k["sales_by_quarter"], k["won_by_quarter"]))
        for i, day in enumerate([dt.date(y, 3, 2), dt.date(y, 6, 1), dt.date(y, 8, 1), dt.date(y, 11, 5)]):
            await svc.confirm_tax(db, did, UID, "QA", i, day)
        d = await svc.get_deal(db, did, today)
        tx = d["flow"]["tax"]
        check(tx["done"] and tx["revenue_date"] == f"{y}-11-05" and flow.revenue_date(d) == f"{y}-11-05",
              "모두 확인 → 매출일 = 잔금 발행일", tx["revenue_date"])
        k = svc.kpi([d], today)
        check(k["sales_by_quarter"] == [0, 0, 0, 1_000_001] and k["sales"] == 1_000_001,
              "분기별 매출 — 2월 수주라도 11월 잔금 계산서면 4분기", k["sales_by_quarter"])
        await svc.unconfirm_tax(db, did, UID, 3)
        d = await svc.get_deal(db, did, today)
        check(d["flow"]["tax"]["revenue_date"] is None and svc.kpi([d], today)["sales"] == 0, "잔금 확인 취소 → 매출일 풀림")
        ev = (await db.execute(text("SELECT action FROM sales_deal_events WHERE deal_id = :i ORDER BY id"), {"i": did})).scalars().all() \
            if (await db.execute(text("SELECT to_regclass('sales_deal_events')"))).scalar() else []
        check(not ev or {"tax_terms", "tax_confirm", "tax_unconfirm"} <= set(ev), "변경 이력에 남음", sorted(set(ev))[:8])


async def main() -> None:
    await cleanup()
    try:
        await run()
    finally:
        await cleanup()
    print(f"PASS {sum(res)} / FAIL {len(res) - sum(res)}")
    sys.exit(0 if all(res) else 1)


asyncio.run(main())
