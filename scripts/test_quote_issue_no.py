# -*- coding: utf-8 -*-
"""[번호 정해서 발행](사용자 2026-11-18 — '다른 이름으로 저장'처럼).

- 번호를 적지 않으면 다음 자동 번호 · 적으면 그 번호(다른 규칙도) · 그 뒤 자동 번호는 남은 것 중 가장 큰 번호 + 1
- 이 견적서에서 낸 번호를 적으면 409 QUOTE_NO_EXISTS 로 묻고, overwrite 면 그 판을 덮어씀(번호·판 그대로)
- 다른 견적서에서 쓴 번호는 막힘
- 수주한 견적서 고치기 + 다른 번호 → 새 판을 내고 수주 판이 그 번호로(수주일 그대로)
dev DB 전용, 만든 것은 끝에 지운다:
  docker compose run --rm -T --no-deps -e PYTHONPATH=/app -v ./backend/app:/app/app -v ./scripts:/scripts backend python /scripts/test_quote_issue_no.py
"""
import asyncio
import datetime as dt
import os
import sys

assert "agent_ai_dev" in os.environ.get("DATABASE_URL", ""), "NOT DEV DB - abort"
from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402

MARK = "__QA_ISSUENO__"
INI = "QN"
HEAD = {"initials": INI, "contact_name": "Alex", "contact_mobile": "010-0000-0000", "contact_email": "t@example.com"}
MG = {"name": "MG10", "qty": 1, "unit_price": 1_000_000}
res: list[bool] = []


def check(ok: bool, name: str, *more) -> None:
    res.append(bool(ok))
    print("PASS" if ok else "FAIL", name, *more)


async def cleanup() -> None:
    async with SessionLocal() as db:
        c, n = MARK.lower(), len(MARK)
        dids = "SELECT id FROM sales_deals WHERE lower(left(customer, %d)) = :c" % n
        qids = "SELECT id FROM product_quotes WHERE lower(left(customer, %d)) = :c OR lower(left(state->'form'->>'customer', %d)) = :c" % (n, n)
        await db.execute(text(f"DELETE FROM doc_numbers WHERE upper(issuer) = :i OR quote_id IN ({qids}) OR deal_id IN ({dids}) OR doc_no LIKE 'QNX-%'"), {"c": c, "i": INI})
        await db.execute(text(f"DELETE FROM product_quotes WHERE id IN ({qids})"), {"c": c})
        await db.execute(text(f"DELETE FROM sales_deal_events WHERE deal_id IN ({dids})"), {"c": c})
        await db.execute(text(f"DELETE FROM sales_deals WHERE id IN ({dids})"), {"c": c})
        await db.commit()


async def main() -> None:
    from app.company_knowledge import product_recommend as pr
    from app.sales_deals import service as svc

    await cleanup()
    async with SessionLocal() as db:
        uid = (await db.execute(text("SELECT id FROM users WHERE username = 'alex'"))).scalar()
    ymd = f"{svc.kst_today():%Y%m%d}"
    no = lambda n: f"QT-{INI}{ymd}-{n:02d}"  # noqa: E731

    async def edit(qid, qty):
        await pr.quote_edit(qid, user_id=uid, payload={"form": {"customer": MARK + "고객", "subject": "MG"}, "delivery": {"case": 1},
                                                       "lines": [{"id": None, "remark": "", **MG, "qty": qty}]})

    async def deal_of(qid):
        async with SessionLocal() as db:
            did = (await db.execute(text("SELECT deal_id FROM product_quotes WHERE id = :i"), {"i": qid})).scalar()
            return did, await svc.get_deal(db, did, svc.kst_today())

    q = await pr.quote_manual(user_id=uid, form=HEAD)
    qid = q["id"]
    await edit(qid, 1)
    nx = await pr.quote_next_no(qid, user_id=uid)
    check(nx["next"] == no(1), "다음 자동 번호 미리 보기(잡지 않음)", nx["next"])
    v = await pr.quote_issue(qid, user_id=uid)
    check(v["quote_no"] == no(1), "번호 없이 발행 → 자동 01", v["quote_no"])

    await edit(qid, 2)
    v = await pr.quote_issue(qid, user_id=uid, doc_no=no(5))
    did, d = await deal_of(qid)
    check(v["quote_no"] == no(5) and d["quote"]["no"] == no(5) and d["quote"]["amount"] == 2_000_000, "번호 적어 발행 → 05 · 영업 건도 05·200만", v["quote_no"])
    nx = await pr.quote_next_no(qid, user_id=uid)
    check(nx["next"] == no(6), "그다음 자동 번호는 남은 것 중 가장 큰 번호 + 1(06)", nx["next"])

    await edit(qid, 3)
    v = await pr.quote_issue(qid, user_id=uid, doc_no="QNX-2026-A1")
    check(v["quote_no"] == "QNX-2026-A1", "다른 규칙 번호로 발행", v["quote_no"])

    await edit(qid, 4)
    try:
        await pr.quote_issue(qid, user_id=uid, doc_no=no(1).lower())
        check(False, "이 견적서에서 낸 번호 → 덮어쓸지 묻기(409)")
    except pr.RecommendError as e:
        check(e.code == "QUOTE_NO_EXISTS" and e.extra.get("revision") == 0, "이 견적서에서 낸 번호 → 덮어쓸지 묻기(409)", str(e)[:50])
    v = await pr.quote_issue(qid, user_id=uid, doc_no=no(1), overwrite=True)
    h0 = next(h for h in v["history"] if h["revision"] == 0)
    did, d = await deal_of(qid)
    check(v.get("replaced_rev") == 0 and h0["total"] == 4_000_000 and len(v["history"]) == 3,
          "덮어쓰기 → 01 판(v1) 내용이 바뀌고 판은 늘지 않음", h0["total"], len(v["history"]))
    check(d["quote"]["no"] == "QNX-2026-A1" and d["quote"]["amount"] == 3_000_000, "예전 판을 덮어써도 영업 건은 최신 판 그대로", d["quote"]["no"])

    q2 = await pr.quote_manual(user_id=uid, form=HEAD)
    await edit(q2["id"], 1)
    try:
        await pr.quote_issue(q2["id"], user_id=uid, doc_no=no(5))
        check(False, "다른 견적서에서 쓴 번호 → 막힘")
    except pr.RecommendError as e:
        check(e.code is None and "다른 견적서" in str(e), "다른 견적서에서 쓴 번호 → 막힘(덮어쓰기 아님)", str(e)[:40])

    # 수주한 견적서를 다른 번호로 고치기
    async with SessionLocal() as db:
        await svc.set_won(db, did, uid, True, dt.date(2026, 10, 2))
    await edit(qid, 5)
    v = await pr.quote_issue(qid, user_id=uid, replace_won=True, doc_no="QNX-2026-B1")
    did, d = await deal_of(qid)
    check(v.get("replaced_won") and d["quote"]["no"] == "QNX-2026-B1" and d["quote"]["amount"] == 5_000_000 and d["won_date"] == "2026-10-02",
          "수주한 견적서를 다른 번호로 고치기 → 수주 판이 새 번호 · 금액 반영 · 수주일 그대로", d["quote"]["no"], d["won_date"])
    check((v.get("won_issue") or {}).get("quote_no") == "QNX-2026-B1", "견적서 화면의 수주 판 표시도 새 번호", (v.get("won_issue") or {}).get("quote_no"))
    await edit(qid, 6)
    v = await pr.quote_issue(qid, user_id=uid, replace_won=True)
    did, d = await deal_of(qid)
    check(v["replaced_won"] and d["quote"]["no"] == "QNX-2026-B1" and d["quote"]["amount"] == 6_000_000, "수주한 견적서 같은 번호로 고치기는 그대로", d["quote"]["no"])

    await cleanup()
    print(f"PASS {sum(res)} / FAIL {len(res) - sum(res)}")
    sys.exit(0 if all(res) else 1)


asyncio.run(main())
