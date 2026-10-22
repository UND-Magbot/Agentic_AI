# -*- coding: utf-8 -*-
"""시리얼·A/S 화면 확인용 테스트 영업 건 두 개를 개발 DB(agent_ai_dev)에 만든다(사용자 2026-10-19).

    MSYS_NO_PATHCONV=1 docker compose run --rm -T --no-deps -e PYTHONPATH=/app -e PYTHONIOENCODING=utf-8 \\
        -v ./backend/app:/app/app -v ./scripts:/scripts backend python /scripts/seed_test_serial_deals.py

A. 견적서 최종 확정(수주) — 1차·2차 발행 뒤 2차로 수주, 시리얼 미등록 → [제품 시리얼 등록]·[AI 와 수주 진행] 확인용
B. 완료(출하·완납)·A/S 접수 전 — 가상 견적서·시리얼 등록, 선금 70%(8/13)·잔금 30%(8/22) 입금 확인 후 2026-08-25 출하
   (무상 12개월 안) → [제품·A/S] 탭 A/S 접수서 확인용. 대금을 다 받고 출하한 뒤 A/S 가 들어오는 실제 순서(사용자 2026-10-22)
고객사 이름 앞에 [테스트] 를 붙이고, 다시 돌리면 같은 이름의 건을 지우고 새로 만든다. 가상 시리얼은 TEST- 로 시작한다.
"""
from __future__ import annotations

import asyncio
import copy
import datetime as dt
import json
import os

from sqlalchemy import text

from app import migrations
from app.company_knowledge import product_prices as pp
from app.database import SessionLocal
from app.sales_deals import service as svc
from app.sales_deals import units

OWNER_UID = 1                     # 견적서 내려받기는 만든 사람만 — 관리자(Chris) 계정으로
CUST_A = "[테스트] 가상정밀(주)"
CUST_B = "[테스트] 가상물류센터(주)"

HEADER = {
    "cc": "", "date": "", "lang": "ko", "notes": [], "place": "고객사 지정장소 택배기준",
    "terms": {"vat": "VAT 별도", "validity": "견적으로부터 30일",
              "order_note": "※ 발주 시 서명 날인하여 담당자 이메일을 통해 발신해주시기 바랍니다.",
              "option_note": "* 견적 외 사양 및 추가 요청사항은 옵션 사항이며, 별도 협의 및 추가 견적 대상입니다"},
    "issuer": dict(pp.QUOTE_ISSUER),    # 회사 계좌·대표자·주소는 data/company_profile.json(공개 저장소에 두지 않음)
    "labels": {"by": "Quotation by", "no": "Quotation No.", "qty": "Quantity", "date": "Quotation Date", "note": "비    고",
               "spec": "Specifications", "goods": "Goods Description & Details", "order": "발주 확인", "place": "납품장소",
               "price": "Price", "total": "Total (부가세 별도)", "amount": "Supply amount", "remark": "Remark",
               "payment": "결제조건", "comments": "Comments", "customer": "Customer", "delivery": "납기 일자",
               "validity": "유효일자", "commodity": "Commodity"},
    "contact": {"name": "테스트", "email": "chris@example.com", "title": "", "mobile": "010-0000-0000"},
    "fx_rate": None, "payment": "선금 (발주 시) : 70% 잔금 (납품 전, 잔금 입금 확인 후 출하) : 30%",
    "comments": "www.magbot.kr\n맥봇 로봇 자동 툴 체인져\n(TCV1 Payload : 10kg)", "currency": "KRW",
    "customer": "", "delivery": "3~4주", "initials": "TS", "quote_no": "", "foot_notes": [], "total_note": "", "to": "",
}

# (desc, commodity, qty, unit_price, kind) — 단가는 단가표 261013 기준, 로봇은 가상 재판매 단가
# A — 실제 견적처럼 ATC 1세트 + 툴 플레이트 2개(사용자 2026-10-22). CONTROLBOX·4m 케이블은 마스터 기본 포함이라 따로 안 적음.
# 1차 = 제품만, 2차 = 고객 요청으로 설치 및 교육(Service, 시리얼 없음)을 더해 그 판으로 수주
LINES_A1 = [("TCV1 MASTER T.C (Robot Side)", "Magbot ATC", 1, 2_300_000, "extra"),
            ("TCV1 Tool Plate (Tool Side)", "Magbot ATC", 2, 400_000, "extra")]
LINES_A2 = LINES_A1 + [("설치 및 교육", "Service", 1, 500_000, "extra")]
LINES_B = [("TCV1 MASTER T.C (Robot Side)", "ATC_본체", 1, 2_300_000, "extra"),
           ("TCV1 Tool Plate (Tool Side)", "ATC_본체", 3, 400_000, "extra"),
           ("TCV1 PPM (Pogo Pin Male)", "ATC_악세사리", 1, 250_000, "extra"),
           ("TCV1 PPF (Pogo Pin Female)", "ATC_악세사리", 3, 250_000, "extra"),
           ("Dobot CR5A 협동로봇 (재판매)", "Robot", 1, 15_000_000, "extra"),
           ("물류비", "Service", 1, 100_000, "freight")]


def issue_lines(spec: list[tuple]) -> list[dict]:
    return [{"id": f"L{i}", "key": kind, "qty": q, "desc": d, "kind": kind, "block": "B0", "amount": float(q * p),
             "option": False, "remark": "", "commodity": c, "unit_price": float(p), "unit_price_krw": float(p)}
            for i, (d, c, q, p, kind) in enumerate(spec, 1)]


def state_for(customer: str, contact: str, spec: list[tuple]) -> dict:
    """견적서 고치기로 이어 열 수 있게 편집 상태도 같이 넣는다(직접 작성한 견적서 모양)."""
    return {"form": {"cc": "", "to": contact, "bank": pp.QUOTE_ISSUER["bank"], "lang": "ko", "note": "", "place": HEADER["place"],
                     "fx_rate": None, "payment": HEADER["payment"], "subject": "", "comments": HEADER["comments"],
                     "customer": customer, "delivery": "3~4주", "initials": "TS", "account_no": pp.QUOTE_ISSUER["account_no"],
                     "foot_notes": "", "total_note": "", "contact_name": "테스트", "contact_email": "chris@example.com",
                     "contact_title": "", "account_holder": pp.QUOTE_ISSUER["account_holder"], "contact_mobile": "010-0000-0000",
                     "contact_name_en": "", "contact_honorific": ""},
            "work": {"days": None, "rate": None, "needed": False, "people": None}, "asked": ["extra"],
            "lines": [{"id": f"L{i}", "key": kind, "qty": q, "kind": kind, "name": d, "remark": "", "commodity": c,
                       "unit_price": float(p), "price_status": "set"} for i, (d, c, q, p, kind) in enumerate(spec, 1)],
            "model": "", "blocks": [{"id": "B0", "type": "items", "title": "", "subtotal": False}], "manual": True,
            "next_id": len(spec) + 1, "options": [], "delivery": {"case": 5, "freight": None}, "applicable": True,
            "payload_kg": None, "chat": []}


def issue(rev: int, no: str, day: str, customer: str, contact: str, spec: list[tuple]) -> dict:
    lines = issue_lines(spec)
    total = sum(ln["amount"] for ln in lines)
    header = {**copy.deepcopy(HEADER), "date": day, "customer": customer, "to": contact, "quote_no": no}
    return {"revision": rev, "quote_no": no, "header": header, "lines": lines, "total": total, "currency": "KRW",
            "issued_at": f"{day}T01:00:00+00:00",
            "file_name": f"(주)유엔디로보틱스_견적서_{customer}_{spec[0][0]}_{no}.xlsx"}


async def cleanup(db) -> None:
    ids = (await db.execute(text("SELECT id FROM sales_deals WHERE customer IN (:a, :b)"), {"a": CUST_A, "b": CUST_B})).scalars().all()
    if ids:
        for t in ("as_cases", "product_units", "product_quotes", "sales_deal_events", "doc_numbers"):
            await db.execute(text(f"DELETE FROM {t} WHERE deal_id = ANY(:i)"), {"i": ids})
        await db.execute(text("DELETE FROM sales_deals WHERE id = ANY(:i)"), {"i": ids})
    await db.commit()


async def make_deal(db, customer: str, contact: str, title: str, hist: list[dict], spec: list[tuple],
                    won_day: dt.date) -> tuple[int, int]:
    last = hist[-1]
    first_day = dt.date.fromisoformat(hist[0]["header"]["date"])
    quote = {**svc._quote_from_items(svc.snapshot_items(last["lines"]), dt.date.fromisoformat(last["header"]["date"]),
                                     False, "KRW", last["quote_no"]),
             "revision": len(hist), "sent_date": last["header"]["date"]}
    deal_id = (await db.execute(text(
        "INSERT INTO sales_deals (deal_no, kind, customer, contact, owner, title, base_date, quote, pay_terms, created_by) "
        "VALUES (:no, 'quote', :c, :ct, 'Chris', :t, :d, CAST(:q AS jsonb), CAST(:pt AS jsonb), :u) RETURNING id"),
        {"no": hist[0]["quote_no"], "c": customer, "ct": contact, "t": title, "d": first_day,
         "q": json.dumps(quote, ensure_ascii=False),
         "pt": json.dumps(svc.parse_pay_terms(HEADER["payment"]), ensure_ascii=False), "u": OWNER_UID})).scalar_one()
    await svc._log(db, deal_id, OWNER_UID, "create", {"deal_no": hist[0]["quote_no"], "amount": quote["amount"]})
    qid = (await db.execute(text(
        "INSERT INTO product_quotes (user_id, deal_id, status, quote_no, lang, customer, file_name, header, lines, total, "
        "state, revision, history) VALUES (:u, :d, 'issued', :no, 'ko', :c, :f, CAST(:h AS jsonb), CAST(:l AS jsonb), :tot, "
        "CAST(:s AS jsonb), :r, CAST(:hist AS jsonb)) RETURNING id"),
        {"u": OWNER_UID, "d": deal_id, "no": last["quote_no"], "c": customer, "f": last["file_name"],
         "h": json.dumps(last["header"], ensure_ascii=False), "l": json.dumps(last["lines"], ensure_ascii=False),
         "tot": last["total"], "s": json.dumps(state_for(customer, contact, spec), ensure_ascii=False),
         "r": last["revision"], "hist": json.dumps(hist, ensure_ascii=False)})).scalar_one()
    await db.commit()
    await svc.set_won(db, deal_id, OWNER_UID, True, won_day, {"quote_id": qid, "revision": last["revision"]})
    return deal_id, qid


async def run() -> None:
    # 운영(agent_ai)은 A/S 테스트 가이드용으로만(사용자 2026-10-22) — SEED_TARGET=agent_ai 를 직접 줘야 돈다. [테스트] 건만 지우고 만든다
    db_name = os.environ.get("DATABASE_URL", "").rsplit("/", 1)[-1]
    assert db_name == "agent_ai_dev" or (db_name == "agent_ai" and os.environ.get("SEED_TARGET") == "agent_ai"),         "개발 DB(agent_ai_dev)에서만 돌린다(운영은 SEED_TARGET=agent_ai)"
    async with SessionLocal() as db:
        await migrations.run_migrations(db)
        await cleanup(db)

        # A. 견적서 최종 확정(2차로 수주), 시리얼 미등록
        ct_a = "홍길동 과장님"
        hist_a = [issue(0, "QT-TS20261016-01", "2026-10-16", CUST_A, ct_a, LINES_A1),
                  issue(1, "QT-TS20261019-01", "2026-10-19", CUST_A, ct_a, LINES_A2)]
        a_id, _ = await make_deal(db, CUST_A, ct_a, f"TCV1 MASTER T.C 외 {len(LINES_A2) - 1}", hist_a, LINES_A2,
                                  dt.date(2026, 10, 19))

        # B. 견적 → 수주 → 시리얼 등록 → 선금·잔금 입금 확인 → 출하(2026-08-25) = 완료(출하·완납), A/S 접수 전
        ct_b = "김영희 대리님"
        hist_b = [issue(0, "QT-TS20260810-01", "2026-08-10", CUST_B, ct_b, LINES_B)]
        b_id, b_qid = await make_deal(db, CUST_B, ct_b, "TCV1 MASTER T.C 외 5", hist_b, LINES_B, dt.date(2026, 8, 12))
        payload = [
            {"line_no": 0, "units": [{"serial": "TEST-TCV1M-2608-0001", "warranty_months": 12}]},
            {"line_no": 1, "units": [{"serial": f"TEST-TCV1T-2608-000{i}", "warranty_months": 12} for i in (1, 2, 3)]},
            {"line_no": 2, "units": [{"serial": "TEST-PPM-2608-0001", "warranty_months": 12}]},
            {"line_no": 3, "units": [{"serial": f"TEST-PPF-2608-000{i}", "warranty_months": 12} for i in (1, 2, 3)]},
            {"line_no": 4, "units": [{"serial": "TEST-DTCR5A-2608-0417", "maker": "Dobot", "part_name": "Dobot CR5A 본체",
                                      "warranty_months": 12}]},
        ]
        await units.save_units(db, b_id, OWNER_UID, b_qid, 0, payload)
        await db.commit()
        # 실제 순서: 거래명세서 발급 → 결제 조건(선금 70% / 잔금 30%) → 회차별 입금 확인 → 출하
        st = await svc.statement_draft(db, b_id, "TS")
        await svc.set_statement(db, b_id, OWNER_UID, {
            **st, "initials": "TS", "date": dt.date(2026, 8, 12),
            "buyer": {"reg_no": "000-00-00000", "name": CUST_B, "ceo": "테스트", "address": "대구광역시 가상구 테스트로 1"}})
        await svc.set_terms(db, b_id, OWNER_UID, [
            {"label": "선금", "pct": 70, "when": "발주 시", "expected_date": dt.date(2026, 8, 14)},
            {"label": "잔금", "pct": 30, "when": "납품 전", "expected_date": dt.date(2026, 8, 24)}])
        await svc.confirm_term(db, b_id, OWNER_UID, 0, dt.date(2026, 8, 13))     # 선금 70%
        await svc.confirm_term(db, b_id, OWNER_UID, 1, dt.date(2026, 8, 22))     # 잔금 30% → 완납, 출하 가능
        await svc.add_shipment(db, b_id, OWNER_UID, {
            "date": dt.date(2026, 8, 25), "carrier": "택배", "tracking": "TEST-1234-5678", "receiver": ct_b,
            "address": "대구광역시 가상구 테스트로 1", "items": "", "purpose": "판매",
            "lines": [{"name": d, "qty": q} for d, _, q, _, k in LINES_B if k == "extra"]})

        today = svc.kst_today()
        for did in (a_id, b_id):
            d = await svc.get_deal(db, did, today)
            print(f"{d['deal_no']} | {d['customer']} | 단계 {d['stage']} | 시리얼 {d['units']} | A/S {d['as_open']}/{d['as_total']}")
        for u in await units.deal_units(db, b_id):
            print(f"  {u['serial']:<24} {u['part_name']:<32} {u['maker']:<6} 출하 {u['warranty_start']} ~ 무상 {u['warranty_until']}")


asyncio.run(run())
