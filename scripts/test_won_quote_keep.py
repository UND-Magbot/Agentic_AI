# -*- coding: utf-8 -*-
"""수주한 건에 견적서를 또 발행해도 수주한 견적서가 그대로인지(사용자 2026-10-22).

    python scripts/test_won_quote_keep.py   (dev DB 전용 — 테스트 건을 만들고 끝에 지운다)

- 수주 전: 새 발행이 건의 견적(번호·금액)을 바꾼다(기존 동작 유지).
- 수주 후: 새 발행(첨부·AI 공통 quote_deal)은 이력에만 남고 건의 견적은 수주한 판 그대로.
- 파일 교체: 수주한 번호를 바꾸면 품목·금액을 따라가고, 다른 번호(가장 최근 판이어도)는 건 견적을 안 바꾼다.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.sales_deals import service as svc  # noqa: E402

assert settings.database_url.rsplit("/", 1)[-1] == "agent_ai_dev", "dev DB 에서만 돌린다"

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []
TODAY = dt.date.today()
TEST_CUSTOMER = "__테스트_수주견적유지__"


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


def issue(no: str, price: int) -> dict:
    return {"quote_date": TODAY, "customer": TEST_CUSTOMER, "contact": None, "owner": "Alex", "vat_included": False,
            "initials": "ZZ", "currency": "KRW", "title": "", "note": "", "doc_no": no, "file": "t.xlsx",
            "items": [{"name": "TCV1 본체", "unit_price": price, "qty": 1}]}


async def cleanup(db) -> None:
    await db.execute(text("DELETE FROM sales_deals WHERE customer = :c"), {"c": TEST_CUSTOMER})
    await db.commit()


async def run() -> None:
    async with SessionLocal() as db:
        await cleanup(db)
        uid = (await db.execute(text("SELECT id FROM users WHERE username = 'alex'"))).scalar()
        did = await svc.create_deal(db, uid, {
            "customer": TEST_CUSTOMER, "contact": None, "owner": "Alex", "title": None, "quote_date": TODAY,
            "vat_included": False, "currency": "KRW", "items": [{"name": "TCV1 본체", "unit_price": 1_000_000, "qty": 1}],
            "pay_terms_text": None, "note": "자동 점검용"})
        try:
            await svc.quote_deal(db, uid, None, issue("QT-ZZ-TEST-01", 1_000_000), 1, deal_id=did, action="quote_upload")
            await svc.quote_deal(db, uid, None, issue("QT-ZZ-TEST-02", 2_000_000), 2, deal_id=did, action="quote_upload")
            await db.commit()
            q = (await svc.get_deal(db, did, TODAY))["quote"]
            check(q["no"] == "QT-ZZ-TEST-02" and q["amount"] == 2_000_000, "수주 전: 새 발행이 건 견적을 바꾼다", str(q))

            later = await svc.create_deal(db, uid, {          # 나중에 만든 건 — 앞 건이 수주돼도 이 건이 위
                "customer": TEST_CUSTOMER, "contact": None, "owner": "Alex", "title": None, "quote_date": TODAY,
                "vat_included": False, "currency": "KRW", "items": [{"name": "Tool Plate", "unit_price": 1, "qty": 1}],
                "pay_terms_text": None, "note": "자동 점검용"})
            await svc.set_won(db, did, uid, True, TODAY)          # 02 로 수주
            mine = [r["id"] for r in (await svc.list_deals(db, TODAY))["rows"] if r["customer"] == TEST_CUSTOMER]
            check(mine == [later, did], "단계가 바뀌어도 리스트 줄은 그대로(새로 만든 건이 위)", str(mine))
            await svc.quote_deal(db, uid, None, issue("QT-ZZ-TEST-03", 3_000_000), 3, deal_id=did, action="quote_upload")
            await db.commit()
            d = await svc.get_deal(db, did, TODAY)
            q = d["quote"]
            check(q["no"] == "QT-ZZ-TEST-02" and q["amount"] == 2_000_000 and q["revision"] == 2,
                  "수주 후: 새 발행해도 수주한 견적서(번호·금액·판) 그대로", str(q))
            check(d["won"] is True and d["display_no"] == "QT-ZZ-TEST-02", "수주 상태·표시 번호 유지",
                  f"{d['won']} {d['display_no']}")
            ev = [e for e in d["events"] if e.get("action") == "quote_upload"]
            check(any((e.get("detail") or {}).get("no") == "QT-ZZ-TEST-03" for e in ev), "새 발행은 이력에 남는다",
                  str(ev[:3]))

            # 파일 교체 — 가장 최근 판(03)이어도 수주한 번호가 아니면 건 견적 그대로
            await svc.quote_replaced(db, did, uid, {"doc_no": "QT-ZZ-TEST-03", "revision": 3, "total": 3_500_000,
                                                    "currency": "KRW", "file": "t.xlsx",
                                                    "items": [{"name": "TCV1 본체", "unit_price": 3_500_000, "qty": 1}]},
                                     latest=True)
            await db.commit()
            q = (await svc.get_deal(db, did, TODAY))["quote"]
            check(q["no"] == "QT-ZZ-TEST-02" and q["amount"] == 2_000_000, "다른 번호 파일 교체는 수주 견적 안 바꿈", str(q))

            # 수주한 번호(02)를 교체 — 예전 판이어도 품목·금액은 그 파일로
            await svc.quote_replaced(db, did, uid, {"doc_no": "qt-zz-test-02", "revision": 2, "total": 2_200_000,
                                                    "currency": "KRW", "file": "t.xlsx",
                                                    "items": [{"name": "TCV1 본체", "unit_price": 2_200_000, "qty": 1}]},
                                     latest=False)
            await db.commit()
            q = (await svc.get_deal(db, did, TODAY))["quote"]
            check(q["no"] == "QT-ZZ-TEST-02" and q["amount"] == 2_200_000, "수주한 번호 파일 교체는 금액 따라감", str(q))
        finally:
            await db.rollback()
            await cleanup(db)


asyncio.run(run())
for label in PASS:
    print("PASS", label)
for label, detail in FAIL:
    print("FAIL", label, "—", detail)
print(f"\n{len(PASS)} PASS / {len(FAIL)} FAIL")
sys.exit(1 if FAIL else 0)
