# -*- coding: utf-8 -*-
"""[대여 제품 목록] 탭(사용자 2026-10-30) — dev DB 전용, 테스트 건을 만들고 끝에 지운다.

    python scripts/test_rental_units_list.py

대여 건의 제품 한 줄씩: 제품·S/N·상태 코드·고객사·담당자·대여 기간·반납일·상태. 금액은 없다.
급한 것이 위(대여 중·회수 중은 반납 예정일 가까운 순) → 출하 전 → S/N 미등록 → 회수 완료. 판매 건 제품은 안 나온다.
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
from app.sales_deals import rental_query as rq  # noqa: E402
from app.sales_deals import service as svc  # noqa: E402

assert settings.database_url.rsplit("/", 1)[-1] == "agent_ai_dev", "dev DB 에서만 돌린다"

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []
TODAY = dt.date(2026, 10, 30)
CUST = "__테스트_대여목록__"


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


def deal(title: str, rental: dict | None, price: int = 425_000, contact: str = "김철수 과장") -> dict:
    return {"customer": CUST, "contact": contact, "owner": "Alex", "title": title, "quote_date": TODAY, "vat_included": False,
            "currency": "KRW", "items": [{"name": f"{title} 본체", "unit_price": price, "qty": 1}], "pay_terms_text": None,
            "note": "자동 점검용", **({"rental": rental} if rental else {})}


async def cleanup(db) -> None:
    await db.execute(text("DELETE FROM product_units WHERE serial LIKE 'ZZTEST-RL-%'"))
    await db.execute(text("DELETE FROM sales_deals WHERE customer = :c"), {"c": CUST})
    await db.commit()


async def unit(db, deal_id: int, serial: str, purpose: str, status: str = "출하") -> None:
    await db.execute(text(
        "INSERT INTO product_units (deal_id, line_no, line_name, part_name, serial, purpose, warranty_start, status) "
        "VALUES (:d, 0, '방역 로봇\n- 본체', '방역 로봇 본체', :s, :p, CAST(:w AS DATE), :st)"),
        {"d": deal_id, "s": serial, "p": purpose, "w": dt.date(2026, 10, 1) if status != "등록" else None, "st": status})


async def run() -> None:
    async with SessionLocal() as db:
        await cleanup(db)
        uid = (await db.execute(text("SELECT id FROM users WHERE username = 'alex'"))).scalar()
        try:
            near = await svc.create_deal(db, uid, deal("곧 반납", {"months": 1, "start": "2026-10-10"}))        # 11/9 반납
            far = await svc.create_deal(db, uid, deal("오래 대여", {"months": 36, "start": "2026-10-01"}))       # 2029-09-30
            late = await svc.create_deal(db, uid, deal("반납 지남", {"months": 1, "start": "2026-09-01"}))      # 9/30 지남
            free = await svc.create_deal(db, uid, deal("무상 대여", {"months": 3, "start": "2026-10-20"}, price=0))
            nosn = await svc.create_deal(db, uid, deal("S/N 전", {"months": 6, "start": None}))
            done = await svc.create_deal(db, uid, deal("회수 끝", {"months": 1, "start": "2026-08-01"}))
            sale = await svc.create_deal(db, uid, deal("판매 건", None))
            await db.execute(text("UPDATE sales_deals SET won = TRUE, shipments = '[{\"date\": \"2026-10-01\"}]'::jsonb "
                                  "WHERE customer = :c"), {"c": CUST})
            await db.execute(text("UPDATE sales_deals SET shipments = '[]'::jsonb WHERE id = :i"), {"i": nosn})
            await unit(db, near, "ZZTEST-RL-0001", "R")
            await unit(db, far, "ZZTEST-RL-0002", "R")
            await unit(db, far, "ZZTEST-RL-0003", "A")                     # 대여 중 A/S
            await unit(db, late, "ZZTEST-RL-0004", "R")
            await unit(db, free, "ZZTEST-RL-0005", "R", status="등록")    # 출하 전
            await unit(db, done, "ZZTEST-RL-0006", "R")
            await unit(db, sale, "ZZTEST-RL-0007", "S")
            await db.commit()
            await svc.set_rental_return(db, done, None, "done", dt.date(2026, 9, 2))

            data = await rq.rental_units(db, TODAY)
            mine = [x for x in data["rows"] if x["customer"] == CUST]
            serials = [x["serial"] or f"({x['product']})" for x in mine]
            check("ZZTEST-RL-0007" not in serials, "판매 건 제품은 안 나옴", str(serials))
            check(serials == ["ZZTEST-RL-0004", "ZZTEST-RL-0001", "ZZTEST-RL-0002", "ZZTEST-RL-0003", "ZZTEST-RL-0005",
                              "(S/N 전 본체)", "ZZTEST-RL-0006"],
                  "급한 것이 위 — 반납 지남 → 곧 반납 → 오래 대여 → 출하 전 → S/N 미등록 → 회수 완료", str(serials))
            by = {x["serial"]: x for x in mine if x["serial"]}
            r1 = by["ZZTEST-RL-0001"]
            check(r1["product"] == "방역 로봇" and r1["part"] == "방역 로봇 본체" and r1["state"] == "R" and r1["phase"] == "대여 중",
                  "줄 = 대여 제품·부품·상태 코드·대여 중", str(r1))
            check(r1["contact"] == "김철수 과장" and r1["owner"] == "Alex" and r1["customer"] == CUST, "고객사·고객 담당자·영업 담당")
            check(r1["start"] == "2026-10-10" and r1["end"] == "2026-11-09" and r1["days_left"] == 10, "대여 기간·반납 예정일·남은 날",
                  str((r1["start"], r1["end"], r1["days_left"])))
            check(by["ZZTEST-RL-0004"]["days_left"] == -30, "반납 예정일 지난 것은 음수(30일 지남)", str(by["ZZTEST-RL-0004"]["days_left"]))
            check(by["ZZTEST-RL-0003"]["state"] == "A" and by["ZZTEST-RL-0003"]["phase"] == "대여 중", "대여 중 A/S — 상태 코드 A")
            check(by["ZZTEST-RL-0005"]["phase"] == "출하 전" and by["ZZTEST-RL-0005"]["days_left"] is None and by["ZZTEST-RL-0005"]["free"],
                  "출하 전 · 무상 대여 표시", str(by["ZZTEST-RL-0005"]))
            d6 = by["ZZTEST-RL-0006"]
            check(d6["phase"] == "회수 완료" and d6["returned"] == "2026-09-02" and d6["days_left"] is None, "회수 완료 · 반납일 = 회수 완료일",
                  str(d6))
            pend = next(x for x in mine if not x["serial"])
            check(pend["phase"] == "S/N 미등록" and pend["unit_id"] is None and pend["deal_id"] == nosn, "수주했는데 S/N 없는 건은 견적 품목 줄로")
            check(not any(k in x for x in mine for k in ("amount", "unit_price", "monthly")), "금액은 없음")
            await svc.set_rental_return(db, far, None, "start", TODAY)
            far_rows = [x for x in (await rq.rental_units(db, TODAY))["rows"] if x["deal_id"] == far]
            check(all(x["phase"] == "회수 중" and x["days_left"] is not None for x in far_rows), "회수 시작 → 회수 중(반납 예정일 계속 보임)")
            # 반납일 지났는데 반납 확인 전 → 영업 건 알림(목록 빨간 줄·팝업), 회수 중이어도, 회수 완료면 없음(사용자 2026-10-30)
            ld = await svc.get_deal(db, late, TODAY)
            check(any(a["code"] == "RENTAL_END" and "30일 지남" in a["text"] for a in ld["alerts"]), "반납일 지남 → 알림", str(ld["alerts"]))
            await svc.set_rental_return(db, late, None, "start", TODAY)
            ld = await svc.get_deal(db, late, TODAY)
            check(ld["stage"] == "회수 중" and any(a["code"] == "RENTAL_END" and "반납 확인" in a["text"] for a in ld["alerts"]),
                  "회수 중이어도 반납 확인 전이면 알림", str(ld["alerts"]))
            await svc.set_rental_return(db, late, None, "done", TODAY)
            ld = await svc.get_deal(db, late, TODAY)
            check(not any(a["code"] == "RENTAL_END" for a in ld["alerts"]), "회수 완료(반납 확인)하면 알림 없음")
            steps = [e["detail"].get("step") for e in ld["events"] if e["action"] == "rental_return"]
            check(steps == ["done", "start"], "영업 건 변경 이력에 회수 시작·회수 완료 둘 다(최신이 위)", str(steps))
            check(not any(a["code"] == "RENTAL_END" for a in (await svc.get_deal(db, near, TODAY))["alerts"]), "반납일 전이면 알림 없음")
        finally:
            await cleanup(db)


asyncio.run(run())
for label, detail in FAIL:
    print(f"FAIL {label} :: {detail}")
print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
sys.exit(1 if FAIL else 0)
