"""한 번만 — 예전 '확정 제안 내역'(product_proposals)을 제품 영업 건으로 옮긴다(사용자 2026-10-06: 확정 제안 내역 탭을 없애고 합침).

영업 건이 없는 확정 기록마다 '제품 추천 확정' 건을 만들고, 그 추천으로 이미 발행한 견적서가 있으면 마지막 판을 건의 견적으로 넣는다
(예전 번호 UND-… 로 발행한 견적서는 견적서 쪽 번호를 그대로 둔다). 여러 번 돌려도 이미 옮긴 기록은 건너뛴다.

    docker compose exec backend python /app/scripts_backfill.py   (또는 run --rm 으로 scripts 를 붙여 실행)
"""
from __future__ import annotations

import asyncio
import datetime as dt

from sqlalchemy import text

from app.database import SessionLocal
from app.sales_deals import service as deals


async def main() -> None:
    async with SessionLocal() as db:
        rows = (await db.execute(text(
            "SELECT p.id, p.recommendation_id, p.customer, p.model, p.created_at, COALESCE(u.alias, u.username) AS owner, p.user_id "
            "FROM product_proposals p LEFT JOIN users u ON u.id = p.user_id "
            "WHERE NOT EXISTS (SELECT 1 FROM sales_deals d WHERE d.proposal_id = p.id) ORDER BY p.id"))).mappings().all()
        for p in rows:
            deal_id, deal_no = await deals.recommend_deal(db, p["user_id"], p["id"], {
                "day": p["created_at"].date(), "customer": p["customer"], "owner": p["owner"], "title": p["model"]})
            q = (await db.execute(text(
                "SELECT state, history FROM product_quotes WHERE recommendation_id = :r AND status = 'issued' "
                "ORDER BY updated_at DESC LIMIT 1"), {"r": p["recommendation_id"]})).mappings().first()
            note = ""
            if q and q["history"]:
                h = q["history"][-1]
                f = (q["state"] or {}).get("form") or {}
                await deals.quote_deal(db, p["user_id"], p["id"], {
                    "quote_date": dt.date.fromisoformat(h["header"]["date"]), "customer": h["header"]["customer"],
                    "contact": h["header"].get("to") or None, "owner": f.get("contact_name") or p["owner"],
                    "vat_included": False, "currency": h["currency"], "title": "", "note": "",
                    "items": [{"name": ln["desc"], "unit_price": ln["unit_price"], "qty": ln["qty"]} for ln in h["lines"]],
                }, h["revision"] + 1)
                note = f" + 견적({h['header']['quote_no']})"
            print(f"확정 #{p['id']} → {deal_no}{note}")
        await db.commit()
        print(f"옮김 {len(rows)}건")


asyncio.run(main())
