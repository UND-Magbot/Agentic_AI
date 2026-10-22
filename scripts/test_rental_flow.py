# -*- coding: utf-8 -*-
"""제품 대여 관리 흐름(사용자 2026-10-22) — dev DB 전용, 테스트 건을 만들고 끝에 지운다.

    python scripts/test_rental_flow.py

- 대여 조건으로 만든 건 → [제품 대여 관리](biz=rental)에만, [제품 판매 관리](biz=sale)엔 없음
- 시리얼 화면 정보에 biz=rental(새 칸 기본 R)
- AI 대화 '10월 이후 대여 나간 제품 시리얼이랑 알려줘' → 질문 알아봄 · 기준일 · R 제품 표(시작일로 거름) · 시리얼 미등록 대여 건
- 판매 건의 R 아닌 제품은 나오지 않음, 영업부가 아니면 막음
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
TODAY = dt.date(2026, 10, 22)
CUST = "__테스트_대여관리__"


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


def deal(title: str, rental: dict | None) -> dict:
    return {"customer": CUST, "contact": None, "owner": "Alex", "title": title, "quote_date": TODAY, "vat_included": False,
            "currency": "KRW", "items": [{"name": title, "unit_price": 425_000, "qty": 36}], "pay_terms_text": None,
            "note": "자동 점검용", **({"rental": rental} if rental else {})}


async def cleanup(db) -> None:
    await db.execute(text("DELETE FROM product_units WHERE serial LIKE 'ZZTEST-RENT-%'"))
    await db.execute(text("DELETE FROM sales_deals WHERE customer = :c"), {"c": CUST})
    await db.commit()


async def unit(db, deal_id: int, serial: str, purpose: str, start: str | None) -> None:
    await db.execute(text(
        "INSERT INTO product_units (deal_id, line_no, line_name, part_name, serial, purpose, warranty_start, status) "
        "VALUES (:d, 1, '방역 로봇', '방역 로봇 본체', :s, :p, CAST(:w AS DATE), '출하')"),
        {"d": deal_id, "s": serial, "p": purpose, "w": dt.date.fromisoformat(start) if start else None})


async def run() -> None:
    # 질문 알아보기·기준일
    ask = [{"role": "user", "content": "현재 10월 이후로 대여나간 제품들 뭐가 있지 시리얼 번호랑 같이 어떤 프로젝트의 어떤 제품인지 나열해줘"}]
    check(rq.detect(ask), "질문 알아봄: '10월 이후 대여나간 제품 … 나열해줘'")
    check(rq.detect([{"role": "user", "content": "렌탈 중인 로봇 목록 보여줘"}]), "질문 알아봄: '렌탈 중인 로봇 목록'")
    check(not rq.detect([{"role": "user", "content": "대여 견적서 작성해줘"}]), "견적서 작성 요청은 이 질문이 아님")
    check(not rq.detect([{"role": "user", "content": "10월 매출 알려줘"}]), "대여 말이 없으면 아님")
    check(rq.since_date("10월 이후", TODAY) == dt.date(2026, 10, 1), "'10월 이후' → 2026-10-01")
    check(rq.since_date("11월부터", TODAY) == dt.date(2025, 11, 1), "아직 안 온 달은 작년(11월 → 2025-11-01)")
    check(rq.since_date("2026년 9월 15일 이후", TODAY) == dt.date(2026, 9, 15), "연·월·일")
    check(rq.since_date("10/5 이후", TODAY) == dt.date(2026, 10, 5), "'10/5' → 2026-10-05")
    check(rq.since_date("올해 대여", TODAY) == dt.date(2026, 1, 1) and rq.since_date("대여 목록", TODAY) is None, "올해 · 날짜 없음")

    async with SessionLocal() as db:
        await cleanup(db)
        uid = (await db.execute(text("SELECT id FROM users WHERE username = 'alex'"))).scalar()
        try:
            r_oct = await svc.create_deal(db, uid, deal("방역 로봇 3년 렌탈", {"months": 36, "start": "2026-10-05"}))
            r_sep = await svc.create_deal(db, uid, deal("안내 로봇 렌탈(9월)", {"months": 3, "start": "2026-09-20"}))
            r_nos = await svc.create_deal(db, uid, deal("행사 로봇 렌탈(시리얼 전)", {"months": 1, "start": None}))
            sale = await svc.create_deal(db, uid, deal("판매 건", None))
            # [제품 대여 관리]의 [+ 새 건 등록] — API 가 넘기는 시작일은 date(사용자 2026-10-22)
            r_new = await svc.create_deal(db, uid, deal("새 건 등록 대여", {"months": 12, "start": dt.date(2026, 10, 10)}))
            nd = await svc.get_deal(db, r_new, TODAY)
            check(nd["biz"] == "rental" and nd["rental"]["end"] == "2027-10-09" and nd["rental"]["monthly"] == 1_275_000,
                  "새 건 등록으로 만든 대여 건: 대여 관리 · 기간·월 납입금", str(nd["rental"]))
            from app.api_sales_deals import DealIn
            try:
                DealIn(**{**deal("x", None), "rental": {"months": 0}})
                check(False, "개월 수 0 은 거절")
            except Exception:
                check(True, "개월 수 0 은 거절")

            rent_ids = {r["id"] for r in (await svc.list_deals(db, TODAY, "rental"))["rows"] if r["customer"] == CUST}
            sale_ids = {r["id"] for r in (await svc.list_deals(db, TODAY, "sale"))["rows"] if r["customer"] == CUST}
            check(rent_ids == {r_oct, r_sep, r_nos, r_new} and sale_ids == {sale}, "대여 건은 대여 관리에만, 판매 건은 판매 관리에만",
                  f"{rent_ids} {sale_ids}")
            d = await svc.get_deal(db, r_oct, TODAY)
            check(d["biz"] == "rental" and d["rental"] == {"months": 36, "start": "2026-10-05", "end": "2029-10-04",
                                                           "monthly": 425_000},
                  "대여 건: 기간·월 납입금", str(d["rental"]))
            check((await svc.get_deal(db, sale, TODAY))["biz"] == "sale", "판매 건 biz=sale")

            await unit(db, r_oct, "ZZTEST-RENT-0001", "R", None)                # 시작일은 건의 대여 조건(10/5)
            await unit(db, r_oct, "ZZTEST-RENT-0002", "R", "2026-10-07")
            await unit(db, r_sep, "ZZTEST-RENT-0003", "R", None)                # 9/20 시작 — 10월 이후에서 빠짐
            await unit(db, sale, "ZZTEST-RENT-0004", "S", "2026-10-10")         # 판매 — 안 나옴
            await db.commit()

            data = await rq.rented_units(db, dt.date(2026, 10, 1))
            mine = [x for x in data["rows"] if x["customer"] == CUST]
            check([x["serial"] for x in mine] == ["ZZTEST-RENT-0001", "ZZTEST-RENT-0002"],
                  "10월 이후: 대여 시작 10/5 건의 R 제품 2개만(9월 건·판매 제품 빠짐)", str([x["serial"] for x in mine]))
            check(mine and mine[0]["start"] == "2026-10-05" and mine[0]["end"] == "2029-10-04" and "방역 로봇" in mine[0]["title"],
                  "시작일은 건의 대여 조건, 프로젝트·끝나는 날 함께", str(mine[:1]))
            allr = [x["serial"] for x in (await rq.rented_units(db, None))["rows"] if x["customer"] == CUST]
            check("ZZTEST-RENT-0003" in allr and "ZZTEST-RENT-0004" not in allr, "날짜 없이 물으면 R 제품 전부(판매 제품 제외)")
            check(any(u["title"] == "행사 로봇 렌탈(시리얼 전)" for u in data["unregistered"]), "시리얼 미등록 대여 건 따로 알림")
            text_ = rq.answer(data, dt.date(2026, 10, 1))
            check("1. ZZTEST-RENT-0001" in text_ and "프로젝트: " + CUST in text_ and "2026-10-01 이후" in text_
                  and "ZZTEST-RENT-0003" not in text_ and "|" not in text_ and "**" not in text_,
                  "답: 번호 목록에 시리얼·프로젝트·대여 기간, 기준일 표시(표·굵은 글씨 문법 없음)", text_[:300])

            from app import main
            out = "".join([c async for c in main._stream_rentals("10월 이후 대여 나간 제품 알려줘", uid)])
            check("ZZTEST-RENT-0001" in out, "AI 대화 경로(영업부 사용자)로 같은 답", out[:200])
            out = "".join([c async for c in main._stream_rentals("10월 이후 대여 나간 제품 알려줘", None)])
            check("영업부만" in out, "로그인 사용자를 모르면 막음")

            # ── 대여 단계: 수주 → 대여 중(출하) → 회수 중 → 회수 완료(사용자 2026-10-22) ──
            stage = lambda i: svc.get_deal(db, i, TODAY)  # noqa: E731
            check((await stage(r_oct))["stage"] == "견적", "대여 건 수주 전 = 견적")
            await svc.set_won(db, r_oct, uid, True, TODAY)
            check((await stage(r_oct))["stage"] == "수주", "수주 확정 → 수주")
            try:
                await svc.set_rental_return(db, r_oct, uid, "start", TODAY)
                check(False, "출하 전에는 회수 시작 못 함")
            except svc.DealError:
                await db.rollback()
                check(True, "출하 전에는 회수 시작 못 함")
            await db.execute(text("UPDATE product_units SET status = '출하' WHERE deal_id = :d"), {"d": r_oct})
            await svc.add_shipment(db, r_oct, uid, {"date": dt.date(2026, 10, 5), "carrier": "화물", "tracking": "", "receiver": "",
                                                    "address": "", "items": "", "purpose": "대여", "lines": []})
            d = await stage(r_oct)
            check(d["stage"] == "대여 중" and not any(a["code"] == "RENTAL_END" for a in d["alerts"]), "출하 → 대여 중(기간 안이면 알림 없음)",
                  str((d["stage"], d["alerts"])))
            late = await svc.get_deal(db, r_oct, dt.date(2029, 11, 1))
            check(any(a["code"] == "RENTAL_END" for a in late["alerts"]), "대여 기간 지나도 회수 안 했으면 '회수 필요' 알림",
                  str(late["alerts"]))
            await svc.set_rental_return(db, r_oct, uid, "start", dt.date(2026, 10, 20))
            d = await stage(r_oct)
            check(d["stage"] == "회수 중" and d["rental"]["return"] == {"started": "2026-10-20"}, "[회수 시작] → 회수 중",
                  str((d["stage"], d["rental"])))
            await svc.set_rental_return(db, r_oct, uid, "done", dt.date(2026, 10, 22))
            d = await stage(r_oct)
            sts = (await db.execute(text("SELECT DISTINCT status FROM product_units WHERE deal_id = :d"), {"d": r_oct})).scalars().all()
            check(d["stage"] == "회수 완료" and d["rental"]["return"]["done"] == "2026-10-22" and sts == ["회수"],
                  "[회수 완료] → 회수 완료, 시리얼 출하 상태 '회수'", str((d["stage"], sts)))
            data = await rq.rented_units(db, dt.date(2026, 10, 1))
            check(not any(x["customer"] == CUST and x["serial"].startswith("ZZTEST-RENT-000") and x["serial"] != "ZZTEST-RENT-0003"
                          for x in data["rows"]) and not any(u["title"] == "방역 로봇 3년 렌탈" for u in data["unregistered"]),
                  "회수한 제품은 AI '대여 나간 제품'에서 빠지고, 미등록 건에도 안 나옴")
            listed = {r["id"]: r["stage"] for r in (await svc.list_deals(db, TODAY, "rental"))["rows"]}
            summ = (await svc.list_deals(db, TODAY, "rental"))["summary"]
            check(listed.get(r_oct) == "회수 완료" and summ["회수 완료"] >= 1, "대여 관리 목록·단계 칩에 회수 완료", str(summ))
            try:
                await svc.set_rental_return(db, r_oct, uid, "cancel", None, admin=False)
                check(False, "회수 기록 취소는 관리자만")
            except svc.DealError:
                await db.rollback()
                check(True, "회수 기록 취소는 관리자만")
            await svc.set_rental_return(db, r_oct, uid, "cancel", None, admin=True)
            sts = (await db.execute(text("SELECT DISTINCT status FROM product_units WHERE deal_id = :d"), {"d": r_oct})).scalars().all()
            check((await stage(r_oct))["stage"] == "대여 중" and sts == ["출하"], "관리자 취소 → 대여 중, 시리얼 출하 상태 되돌림", str(sts))
            # 대여 시작일은 담당자가 정한다(사용자 2026-10-22) — 출하했는데 비어 있으면 확인 필요
            await svc.set_won(db, r_nos, uid, True, TODAY)
            await svc.add_shipment(db, r_nos, uid, {"date": dt.date(2026, 10, 20), "carrier": "택배", "tracking": "", "receiver": "",
                                                    "address": "", "items": "", "purpose": "대여", "lines": []})
            d = await stage(r_nos)
            check(d["stage"] == "대여 중" and any(a["code"] == "RENTAL_START" for a in d["alerts"]),
                  "출하했는데 대여 시작일이 비면 '정해 주세요' 확인 필요", str(d["alerts"]))
            await svc.set_rental_start(db, r_nos, uid, dt.date(2026, 10, 22))
            d = await stage(r_nos)
            check(d["rental"]["start"] == "2026-10-22" and d["rental"]["end"] == "2026-11-21"
                  and not any(a["code"] == "RENTAL_START" for a in d["alerts"]),
                  "대여 시작일 정하기 → 끝나는 날 다시 계산(1개월: 10-22 ~ 11-21)", str(d["rental"]))
            await svc.set_rental_start(db, r_nos, uid, dt.date(2026, 10, 25))
            check((await stage(r_nos))["rental"]["end"] == "2026-11-24", "시작일을 바꾸면 끝나는 날도 따라감")
            try:
                await svc.set_rental_start(db, sale, uid, TODAY)
                check(False, "판매 건에는 대여 시작일 없음")
            except svc.DealError:
                await db.rollback()
                check(True, "판매 건에는 대여 시작일 없음")
            try:
                await svc.set_rental_return(db, sale, uid, "start", TODAY)
                check(False, "판매 건에는 회수 없음")
            except svc.DealError:
                await db.rollback()
                check(True, "판매 건에는 회수 없음")

            # 대여 관리 탭 [삭제](관리자) — 판매와 같은 지우기: 보관함으로, 시리얼은 남음(사용자 2026-10-22)
            await svc.delete_deals(db, [r_oct], uid)
            left = (await db.execute(text("SELECT COUNT(*), COUNT(deal_id) FROM product_units WHERE serial LIKE 'ZZTEST-RENT-000%' "
                                          "AND serial IN ('ZZTEST-RENT-0001', 'ZZTEST-RENT-0002')"))).one()
            trash = (await db.execute(text("SELECT COUNT(*) FROM sales_deal_trash WHERE deal_id = :d"), {"d": r_oct})).scalar()
            rows = {r["id"] for r in (await svc.list_deals(db, TODAY, "rental"))["rows"]}
            check(r_oct not in rows and trash == 1 and tuple(left) == (2, 0),
                  "대여 건 삭제 → 목록에서 빠지고 보관함에, 시리얼 2개는 남음(건 연결만 끊김)", str((trash, tuple(left))))
            await db.execute(text("DELETE FROM sales_deal_trash WHERE deal_id = :d"), {"d": r_oct})
            await db.commit()
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
