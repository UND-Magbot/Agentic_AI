"""이전 판 견적서로 수주(영업팀 요청 15번, 2026-10-13) — 1차 발행 → 2차 재발행 → 수주 때 1차 선택.
영업 건 견적(품목·금액·번호)이 1차로 바뀌고, 거래명세서 초안 품목도 1차. 개발 DB(agent_ai_dev)에 만들고 지운다.

    MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -e PYTHONPATH=/app -v ./backend/app:/app/app -v ./scripts:/scripts backend \\
        python /scripts/test_won_version.py
"""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

from app.company_knowledge import product_recommend as pr
from app.database import SessionLocal
from app.sales_deals import service as svc

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []
C = "__테스트_이전판수주__"


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


async def cleanup() -> None:
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM doc_numbers WHERE quote_id IN (SELECT id FROM product_quotes WHERE customer = :c "
                              "OR state->'form'->>'customer' = :c) OR deal_id IN (SELECT id FROM sales_deals WHERE customer = :c)"), {"c": C})
        await db.execute(text("DELETE FROM product_quotes WHERE customer = :c OR state->'form'->>'customer' = :c"), {"c": C})
        await db.execute(text("DELETE FROM sales_deals WHERE customer = :c"), {"c": C})
        await db.commit()


async def run() -> None:
    await cleanup()
    async with SessionLocal() as db:
        uid = (await db.execute(text("SELECT id FROM users WHERE username = 'alex'"))).scalar()
    head = {"initials": "AL", "contact_name": "Alex", "contact_mobile": "010-0000-0000", "contact_email": "t@example.com"}
    q = await pr.quote_manual(user_id=uid, form=head)
    q = await pr.quote_edit(q["id"], user_id=uid, payload={"form": {"customer": C}, "delivery": {"case": 5},
                                                          "lines": [{"id": None, "name": "MG10", "commodity": "Magbot Gripper", "qty": 2,
                                                                     "unit_price": 1_000_000, "remark": ""}],
                                                          "nego": {"mode": "amount", "value": 100_000, "label": ""}})
    q = await pr.quote_issue(q["id"], user_id=uid)
    first_no, deal_id = q["quote_no"], q["deal_id"]
    line_id = q["state"]["lines"][0]["id"]
    q = await pr.quote_edit(q["id"], user_id=uid, payload={"lines": [{"id": line_id, "name": "MG10", "commodity": "Magbot Gripper", "qty": 3,
                                                                     "unit_price": 900_000, "remark": ""}],
                                                          "nego": None})
    q = await pr.quote_issue(q["id"], user_id=uid)
    second_no = q["quote_no"]
    async with SessionLocal() as db:
        d = await svc.get_deal(db, deal_id, svc.kst_today())
    check(d["quote"]["no"] == second_no and d["quote"]["amount"] == 2_700_000, "재발행 후 영업 건 견적 = 2차(3 × 900,000)", str(d["quote"]))
    issues = d["quotes"][0]["issues"]
    check([x["issue_no"] for x in issues] == [first_no, second_no], "영업 건 상세에 1차·2차 판이 다 보임", str([x["issue_no"] for x in issues]))

    # 남의 견적서 번호로는 거절
    try:
        async with SessionLocal() as db:
            await svc.set_won(db, deal_id, uid, True, svc.kst_today(), {"quote_id": 0, "revision": 0})
        check(False, "이 건의 견적서가 아니면 거절")
    except svc.DealError as e:
        check("견적서가 아닙니다" in str(e), "이 건의 견적서가 아니면 거절", str(e))
    try:
        async with SessionLocal() as db:
            await svc.set_won(db, deal_id, uid, True, svc.kst_today(), {"quote_id": q["id"], "revision": 9})
        check(False, "없는 판이면 거절")
    except svc.DealError as e:
        check("판을 찾을 수 없습니다" in str(e), "없는 판이면 거절", str(e))

    # 1차로 수주
    async with SessionLocal() as db:
        await svc.set_won(db, deal_id, uid, True, svc.kst_today(), {"quote_id": q["id"], "revision": 0})
    async with SessionLocal() as db:
        d = await svc.get_deal(db, deal_id, svc.kst_today())
        sd = await svc.statement_draft(db, deal_id, "AL")
    check(d["won"] is True and d["quote"]["no"] == first_no and d["display_no"] == first_no and d["quote"]["amount"] == 1_900_000
          and d["quote"]["revision"] == 1,
          "1차로 수주 → 영업 건 견적이 1차(2 × 1,000,000 − NEGO 100,000 = 1,900,000)·번호 1차", str(d["quote"]))
    check([(x["qty"], x["unit_price"]) for x in sd["items"]] == [(2, 1_000_000)],
          "거래명세서 초안 품목도 1차 — 기본 품목의 제품 줄만(NEGO 는 빠짐)", str(sd["items"]))
    check(sd["items"][0]["name"] == "Magbot Gripper" and sd["items"][0]["spec"] == "MG10",
          "거래명세서 품명 = 견적서 품목(Magbot Gripper), 규격 = 견적서 규격(MG10)", str(sd["items"][0]))
    ev = next(e for e in d["events"] if e["action"] == "won")
    check(ev["detail"].get("no") == first_no, "변경 이력에 '수주 (1차 번호 견적서)'", str(ev["detail"]))
    async with SessionLocal() as db:
        await svc.set_won(db, deal_id, uid, None, None)
        await svc.set_won(db, deal_id, uid, True, svc.kst_today(), {"quote_id": q["id"], "revision": 1})
        d = await svc.get_deal(db, deal_id, svc.kst_today())
    check(d["quote"]["no"] == second_no and d["quote"]["amount"] == 2_700_000, "미확인으로 되돌린 뒤 2차로 다시 수주하면 2차로", str(d["quote"]))
    async with SessionLocal() as db:
        await svc.set_won(db, deal_id, uid, None, None)
        await svc.set_won(db, deal_id, uid, True, svc.kst_today(), None)
        d = await svc.get_deal(db, deal_id, svc.kst_today())
    check(d["quote"]["no"] == second_no, "판을 안 고르면(예전 방식) 지금 견적 그대로")
    await cleanup()


asyncio.run(run())
print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
for label, detail in FAIL:
    print(f"  ✗ {label}: {detail}")
sys.exit(1 if FAIL else 0)
