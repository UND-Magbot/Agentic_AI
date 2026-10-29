# -*- coding: utf-8 -*-
"""영업부끼리 견적서·확정 제안 이어받기(사용자 2026-10-28) — 담당 넘김·퇴사 뒤 다른 직원이 이어서 고친다.

- 같이 쓰는 견적서(발행 · 영업 건 연결 · 최종 제안으로 확정한 추천의 견적서) → 영업부(전체 권한 포함) 누구나 열고 고친다.
- 발행 전이고 어디에도 안 이어진 작성 중 견적서 · 확정 전 추천 대화 → 쓴 사람만. 견적서 탭 목록도 각자 자기 것만.
- 영업부가 아니면 남의 견적서는 못 연다.

    MSYS_NO_PATHCONV=1 docker compose run --rm -T --no-deps -e PYTHONPATH=/app -e PYTHONIOENCODING=utf-8 \\
        -v ./backend/app:/app/app -v ./scripts:/scripts backend python /scripts/test_quote_sharing.py

개발 DB(agent_ai_dev)에 테스트 견적서·추천을 만들고 돌린 뒤 지운다.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import sys

from sqlalchemy import text

from app.company_knowledge import product_recommend as pr
from app.database import SessionLocal
from app.sales_deals import deal_goto as dg

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []
MARK = "__테스트_이어받기__"


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


async def users(db) -> tuple[int, int, int]:
    """(원래 담당 영업, 이어받는 영업, 영업부 아닌 사용자)."""
    rs = (await db.execute(text("SELECT id, CAST(role AS text) AS role, CAST(domain AS text) AS domain FROM users "
                                "WHERE is_active ORDER BY id"))).mappings().all()
    sales = [r["id"] for r in rs if r["domain"] == "sales" and r["role"] != "superadmin"]
    other = next(r["id"] for r in rs if r["domain"] not in ("sales", "all") and r["role"] != "superadmin")
    return sales[0], sales[1], other


async def opens(qid: int, uid: int) -> bool:
    """견적서 작업 화면(보기·대화·고치기·발행·미리보기·파일)이 모두 거치는 접근 확인."""
    async with SessionLocal() as db:
        try:
            await pr._quote_row(db, qid, uid)
            return True
        except pr.RecommendError:
            return False


async def cleanup(db) -> None:
    await db.execute(text("DELETE FROM product_quotes WHERE customer = :m"), {"m": MARK})
    rids = (await db.execute(text("SELECT id FROM product_recommendations WHERE request = :m"), {"m": MARK})).scalars().all()
    if rids:
        await db.execute(text("DELETE FROM product_proposals WHERE recommendation_id = ANY(:r)"), {"r": rids})
        await db.execute(text("DELETE FROM product_recommendations WHERE id = ANY(:r)"), {"r": rids})
    await db.execute(text("DELETE FROM sales_deals WHERE customer = :m"), {"m": MARK})
    await db.commit()


async def quote(db, owner: int, *, status: str = "draft", deal_id: int | None = None, rid: int | None = None) -> int:
    qid = (await db.execute(text(
        "INSERT INTO product_quotes (user_id, status, customer, deal_id, recommendation_id, state) "
        "VALUES (:u, :s, :c, :d, :r, CAST(:st AS jsonb)) RETURNING id"),
        {"u": owner, "s": status, "c": MARK, "d": deal_id, "r": rid,
         "st": json.dumps({"form": {"customer": MARK}, "lines": [{"name": "x"}]})})).scalar_one()
    await db.commit()
    return qid


async def cases() -> None:
    async with SessionLocal() as db:
        await cleanup(db)
        alex, lucas, finance = await users(db)
        try:
            deal = (await db.execute(text(
                "INSERT INTO sales_deals (deal_no, kind, customer, owner, title, base_date, quote) "
                "VALUES ('ZZ20261028', 'quote', :c, 'Alex', '이어받기', '2026-10-28', CAST(:q AS jsonb)) RETURNING id"),
                {"c": MARK, "q": json.dumps({"no": "QT-ZZ20261028-01", "amount": 1000})})).scalar_one()
            await db.commit()
            issued = await quote(db, alex, status="issued", deal_id=deal)
            linked = await quote(db, alex, deal_id=deal)
            private = await quote(db, alex)

            check(await opens(issued, lucas), "남이 발행한 견적서 → 영업부 동료가 열기")
            check(await opens(linked, lucas), "영업 건에 이어진 작성 중 견적서 → 동료가 열기")
            check(not await opens(private, lucas), "발행 전·건 연결 없는 작성 중 견적서 → 동료는 못 염")
            check(await opens(private, alex), "작성 중 견적서 → 쓴 사람은 염")
            check(not await opens(issued, finance), "영업부가 아니면 남의 발행 견적서도 못 염")

            mine = {q["id"] for q in await pr.quote_list(user_id=lucas)}
            check(not ({issued, linked, private} & mine), "견적서 탭 목록은 각자 자기 것만", str(mine & {issued, linked, private}))

            # 확정 제안(추천) — 확정한 것만 동료가 이어받는다
            rid_done = (await db.execute(text("INSERT INTO product_recommendations (user_id, request, result) "
                                              "VALUES (:u, :m, '{}') RETURNING id"), {"u": alex, "m": MARK})).scalar_one()
            rid_open = (await db.execute(text("INSERT INTO product_recommendations (user_id, request, result) "
                                              "VALUES (:u, :m, '{}') RETURNING id"), {"u": alex, "m": MARK})).scalar_one()
            await db.execute(text("INSERT INTO product_proposals (recommendation_id, user_id) VALUES (:r, :u)"),
                             {"r": rid_done, "u": alex})
            await db.commit()
            check(await pr.get_recommendation(rid_done, lucas) is not None, "확정한 추천 → 동료가 이어받기")
            check(await pr.get_recommendation(rid_open, lucas) is None, "확정 전 추천 대화 → 동료는 못 봄")
            check(await pr.get_recommendation(rid_done, finance) is None, "영업부 아니면 확정 추천도 못 봄")
            rec_q = await quote(db, alex, rid=rid_done)
            check(await opens(rec_q, lucas), "확정 제안으로 만든 견적서 → 동료가 열기")

            # 채팅 '견적서 수정' — 남이 발행한 견적서도 고치기 버튼
            intent = {"kind": "quote", "names": ["ZZ20261028"], "lead": True}
            out = await dg.answer(db, intent, dt.date(2026, 10, 28), lucas)
            check(f"tab=quote&quote={issued}" in out or f"tab=quote&quote={linked}" in out, "채팅 이동 버튼 — 동료도 견적서 고치기", out)
        finally:
            await cleanup(db)


def main() -> None:
    asyncio.run(cases())
    for p in PASS:
        print("PASS", p)
    for f, d in FAIL:
        print("FAIL", f, "\n   ", d.replace("\n", "\n    "))
    print(f"\n{len(PASS)} PASS / {len(FAIL)} FAIL")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
