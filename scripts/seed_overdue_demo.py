"""입금기한 연장·승인 요청 직접 테스트용 영업 건(개발 DB agent_ai_dev 전용) — 선금 예정일이 5일 지난 수주 건 하나.

    만들기:  MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -e PYTHONPATH=/app -v ./backend/app:/app/app -v ./scripts:/scripts backend \\
                 python /scripts/seed_overdue_demo.py
    지우기:  ... python /scripts/seed_overdue_demo.py --clean      (요청 기록도 같이 지워짐)
"""
from __future__ import annotations

import asyncio
import datetime as dt
import sys

from sqlalchemy import text

from app.database import SessionLocal
from app.sales_deals import service as svc
from app.sales_deals import statement_xlsx as sx

CUSTOMER = "[테스트] 기한연장 데모"


async def main(clean: bool) -> None:
    async with SessionLocal() as db:
        name = (await db.execute(text("SELECT current_database()"))).scalar()
        if name != "agent_ai_dev":
            sys.exit(f"개발 DB(agent_ai_dev)에서만 씁니다 — 지금 연결: {name}")
        n = (await db.execute(text("DELETE FROM sales_deals WHERE customer = :c RETURNING id"), {"c": CUSTOMER})).all()
        await db.commit()
        if clean:
            print(f"지움: 테스트 건 {len(n)}개")
            return
        today = svc.kst_today()
        uid = (await db.execute(text("SELECT id FROM users WHERE username = 'lucas'"))).scalar()
        deal_id = await svc.create_deal(db, uid, {"customer": CUSTOMER, "contact": "홍길동 과장", "owner": "Lucas", "title": "기한 연장 테스트",
                                                  "quote_date": today - dt.timedelta(days=40), "vat_included": False, "currency": "KRW",
                                                  "items": [{"name": "TCV3 툴체인저", "unit_price": 10_000_000, "qty": 1}],
                                                  "pay_terms_text": None, "note": "직접 테스트용 — 끝나면 --clean 으로 지우기"})
        items = [{"name": "TCV3 툴체인저", "spec": "", "qty": 1, "unit_price": 10_000_000, "note": ""}]
        statement = {"no": "TS-TEST20261001-01", "date": (today - dt.timedelta(days=12)).isoformat(), "initials": "TEST",
                     "buyer": {"reg_no": "000-00-00000", "name": CUSTOMER, "ceo": "홍길동", "address": "테스트 주소"},
                     "items": items, "totals": sx.totals(items)}
        await svc._save(db, deal_id, won=True, won_date=today - dt.timedelta(days=30), statement=statement, pay_case="terms",
                        pay_terms=[{"label": "선금", "pct": 50, "when": "발주 시", "expected_date": (today - dt.timedelta(days=5)).isoformat()},
                                   {"label": "잔금", "pct": 50, "when": "납품 후", "expected_date": (today + dt.timedelta(days=20)).isoformat()}])
        await db.commit()
        d = await svc.get_deal(db, deal_id, today)
        print(f"만듦: 영업 건 #{deal_id} '{CUSTOMER}' — 선금 예정일 {d['pay_terms'][0]['expected_date']}"
              f"({len(d['flow']['overdue'])}개 회차 지남)")


asyncio.run(main("--clean" in sys.argv))
