# -*- coding: utf-8 -*-
"""영업 건 여러 건 삭제(sales_deals/service.delete_deals) 점검 — 실제 DB. 테스트 건·보관 기록은 끝에 스스로 지운다.

    python scripts/test_sales_deals_delete.py
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.sales_deals import service as svc  # noqa: E402

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []
CUST = "__테스트_삭제__"
TODAY = dt.date.today()


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


def new_deal(uid: int, name: str) -> dict:
    return {"customer": CUST, "contact": None, "owner": "Alex", "title": name, "quote_date": TODAY, "vat_included": False,
            "currency": "KRW", "items": [{"name": name, "unit_price": 100_000, "qty": 1}], "pay_terms_text": "선금 100%", "note": ""}


async def run() -> None:
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM sales_deals WHERE customer = :c"), {"c": CUST})
        await db.execute(text("DELETE FROM sales_deal_trash WHERE deal->>'customer' = :c"), {"c": CUST})
        await db.commit()
        uid = (await db.execute(text("SELECT id FROM users WHERE username = 'alex'"))).scalar()
        before = (await db.execute(text("SELECT COUNT(*) FROM sales_deals"))).scalar()

        a = await svc.create_deal(db, uid, new_deal(uid, "삭제 테스트 A"))
        b = await svc.create_deal(db, uid, new_deal(uid, "삭제 테스트 B"))
        keep = await svc.create_deal(db, uid, new_deal(uid, "남길 건"))
        await svc.set_won(db, a, uid, True, None)          # 이력이 있는 건도 통째로 보관되는지
        nos = {r["id"]: r["deal_no"] for r in (await svc.list_deals(db, TODAY))["rows"] if r["customer"] == CUST}

        # 여러 건 삭제 → 리스트에서 사라지고, 고르지 않은 건은 남음
        deleted = await svc.delete_deals(db, [a, b], uid)
        rows = (await svc.list_deals(db, TODAY))["rows"]
        ids = {r["id"] for r in rows}
        check(deleted == sorted([nos[a], nos[b]]), "지운 건번호를 돌려줌", str(deleted))
        check(a not in ids and b not in ids and keep in ids, "고른 2건만 리스트에서 사라짐")
        check(len(rows) == before + 1, "다른 건은 그대로", f"{before} → {len(rows)}")
        ev = (await db.execute(text("SELECT COUNT(*) FROM sales_deal_events WHERE deal_id = ANY(:i)"), {"i": [a, b]})).scalar()
        check(ev == 0, "지운 건의 변경 이력도 리스트 쪽에서는 정리됨")

        # 보관 — 건 전체와 이력이 그대로, 누가 지웠는지
        trash = (await db.execute(text("SELECT deal_id, deal_no, deal, events, deleted_by FROM sales_deal_trash "
                                       "WHERE deal_id = ANY(:i) ORDER BY deal_id"), {"i": [a, b]})).mappings().all()
        ta = next((t for t in trash if t["deal_id"] == a), None)
        deal_a = ta and (json.loads(ta["deal"]) if isinstance(ta["deal"], str) else ta["deal"])
        events_a = ta and (json.loads(ta["events"]) if isinstance(ta["events"], str) else ta["events"])
        check(len(trash) == 2 and ta and deal_a["title"] == "삭제 테스트 A" and deal_a["won"] is True
              and ta["deleted_by"] == uid, "지운 건은 보관 테이블에 통째로(내용·지운 사람)", str(trash)[:200])
        check(events_a and {e["action"] for e in events_a} >= {"create", "won"}, "보관에 변경 이력도 함께", str(events_a)[:200])

        # 지운 번호는 다시 쓰지 않음(이미 발행한 견적서 번호와 겹치지 않게)
        nxt = await svc.next_deal_no(db, TODAY)
        check(nxt > max(nos.values()), "다음 건번호는 지운 번호보다 뒤", f"{nxt} vs {max(nos.values())}")

        # 잘못된 요청
        for bad, label in (([a], "이미 지운 건"), ([], "빈 선택"), ([keep, 987654321], "없는 건이 섞임")):
            try:
                await svc.delete_deals(db, bad, uid)
                check(False, f"거부: {label}")
            except svc.DealError as e:
                await db.rollback()
                check(True, f"거부: {label}", str(e))
        check(keep in {r["id"] for r in (await svc.list_deals(db, TODAY))["rows"]}, "없는 건이 섞이면 아무것도 지우지 않음")

        await db.execute(text("DELETE FROM sales_deals WHERE customer = :c"), {"c": CUST})
        await db.execute(text("DELETE FROM sales_deal_trash WHERE deal->>'customer' = :c"), {"c": CUST})
        await db.commit()
        left = (await db.execute(text("SELECT COUNT(*) FROM sales_deals WHERE customer = :c"), {"c": CUST})).scalar()
        check(left == 0, "테스트 건 정리")


asyncio.run(run())
for label in PASS:
    print("PASS", label)
for label, detail in FAIL:
    print("FAIL", label, "—", detail)
print(f"\n{len(PASS)} PASS / {len(FAIL)} FAIL")
sys.exit(1 if FAIL else 0)
