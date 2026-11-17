# -*- coding: utf-8 -*-
"""2026-11-17 수정 사항 손으로 확인하기 — 개발 DB(agent_ai_dev)에 항목별 시험 건을 만든다.

    MSYS_NO_PATHCONV=1 docker compose run --rm -T --no-deps -e PYTHONPATH=/app -e PYTHONIOENCODING=utf-8 \\
        -v ./backend/app:/app/app -v ./scripts:/scripts backend python /scripts/seed_manual_qa_1117.py          # 만들기(있으면 지우고 새로)
    ... python /scripts/seed_manual_qa_1117.py --clean                                                           # 지우기만

고객사·공급사 이름 앞에 '[수동검증]', 가상 S/N 은 MQ- 로 시작, 견적·발주 이니셜은 MQ. 가이드: test_cases/수정사항_수동_테스트_가이드_1117.html
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys

from sqlalchemy import text

assert os.environ.get("DATABASE_URL", "").rsplit("/", 1)[-1].startswith("agent_ai_dev"), "개발 DB(agent_ai_dev)가 아님 — 멈춤"

from app.company_knowledge import product_recommend as pr  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.purchases import service as ps  # noqa: E402
from app.sales_deals import service as svc  # noqa: E402
from app.sales_deals import units  # noqa: E402

MARK = "[수동검증] "
INI = "MQ"
SALES_USER = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--user=")), "alex")   # 만든 사람(--user=아이디)
TODAY = svc.kst_today()
HEAD = {"initials": INI, "contact_name": "Alex", "contact_mobile": "010-0000-0000", "contact_email": "test@example.com"}
BUYER = {"reg_no": "123-45-67890", "name": MARK + "공급받는자", "ceo": "홍길동", "address": "서울시 테스트구"}
MG = {"name": "MG10 그리퍼", "qty": 2, "unit_price": 1_000_000}
TC = {"name": "TCV3 툴체인저", "qty": 1, "unit_price": 500_000}
ROBOT = {"name": "방역 로봇", "qty": 1, "unit_price": 2_400_000}


async def cleanup() -> None:
    n = len(MARK)
    async with SessionLocal() as db:
        dids = f"SELECT id FROM sales_deals WHERE left(customer, {n}) = :c"
        qids = f"SELECT id FROM product_quotes WHERE left(customer, {n}) = :c OR left(state->'form'->>'customer', {n}) = :c"
        await db.execute(text(f"DELETE FROM as_cases WHERE serial ILIKE 'mq-%' OR deal_id IN ({dids})"), {"c": MARK})
        await db.execute(text(f"DELETE FROM product_units WHERE serial ILIKE 'mq-%' OR deal_id IN ({dids})"), {"c": MARK})
        await db.execute(text(f"DELETE FROM doc_numbers WHERE upper(issuer) = :i OR quote_id IN ({qids}) OR deal_id IN ({dids})"),
                         {"c": MARK, "i": INI})
        await db.execute(text(f"DELETE FROM product_quotes WHERE id IN ({qids})"), {"c": MARK})
        await db.execute(text(f"DELETE FROM sales_deal_trash WHERE left(deal->>'customer', {n}) = :c"), {"c": MARK})
        await db.execute(text(f"DELETE FROM sales_deal_events WHERE deal_id IN ({dids})"), {"c": MARK})
        await db.execute(text(f"DELETE FROM sales_deals WHERE id IN ({dids})"), {"c": MARK})
        await db.execute(text(f"DELETE FROM purchase_events WHERE purchase_id IN (SELECT id FROM purchases WHERE left(supplier, {n}) = :c)"),
                         {"c": MARK})
        await db.execute(text(f"DELETE FROM purchases WHERE left(supplier, {n}) = :c"), {"c": MARK})
        await db.commit()


async def quote(uid: int, cust: str, lines: list[dict], form: dict | None = None) -> tuple[dict, int]:
    q = await pr.quote_manual(user_id=uid, form=HEAD)
    await pr.quote_edit(q["id"], user_id=uid, payload={
        "form": {"customer": MARK + cust, "subject": "수동 검증", **(form or {})}, "delivery": {"case": 1},
        "lines": [{"id": None, "remark": "", **ln} for ln in lines]})
    q = await pr.quote_issue(q["id"], user_id=uid)
    async with SessionLocal() as db:
        did = (await db.execute(text("SELECT deal_id FROM product_quotes WHERE id = :i"), {"i": q["id"]})).scalar()
    return q, did


async def reissue(uid: int, qid: int, lines: list[dict]) -> None:
    await pr.quote_edit(qid, user_id=uid, payload={"lines": [{"id": None, "remark": "", **ln} for ln in lines]})
    await pr.quote_issue(qid, user_id=uid)


async def won_with_serials(db, uid: int, did: int, qid: int, tag: str, ship_day: dt.date | None = TODAY) -> list[int]:
    """수주 → 품목마다 S/N(MQ-태그-품목-번호) 등록 → (날짜가 있으면) 출하."""
    await svc.set_won(db, did, uid, True, None)
    iu = await units.issue_units(db, did, qid, 0)
    pay = [{"line_no": ln["line_no"], "units": [{"serial": f"MQ-{tag}-{ln['name'].split()[0]}-{k + 1}"} for k in range(int(ln["qty"]))]}
           for ln in iu["lines"]]
    iu = await units.save_units(db, did, uid, qid, 0, pay)
    ids = [u["id"] for ln in iu["lines"] for u in ln["units"]]
    if ship_day:
        await svc.add_shipment(db, did, uid, {"date": ship_day, "unit_ids": ids,
                                              "lines": [{"name": ln["name"], "qty": ln["qty"], "line_no": ln["line_no"], "photos": []}
                                                        for ln in iu["lines"]]})
    return ids


async def seed_deals(uid: int) -> list[str]:
    out = []
    # D1 — 출고까지 끝난 건: 견적서 고치기(수주한 견적서를 같은 번호로) → 금액·품목·S/N 따라가는지
    q, did = await quote(uid, "D1 출고 끝난 건 견적 고치기", [MG, TC])
    async with SessionLocal() as db:
        await won_with_serials(db, uid, did, q["id"], "D1")
    out.append(f"D1 {q['quote_no']}")
    # D2 — 선금 입금 확인한 건: 금액 바꾸는 견적 고치기 막힘
    q, did = await quote(uid, "D2 선금 입금 확인한 건", [MG, TC])
    async with SessionLocal() as db:
        await svc.set_won(db, did, uid, True, None)
        await svc.set_terms(db, did, uid, [{"label": "선금", "pct": 50}, {"label": "잔금", "pct": 50}])
        await svc.confirm_term(db, did, uid, 0, TODAY)
    out.append(f"D2 {q['quote_no']}")
    # D3 — 수주(1차) 뒤 2차를 새로 발행해 작성 화면이 2차인 건: 열면 수주한 1차를 자동으로 불러옴
    q, did = await quote(uid, "D3 수주 뒤 2차 발행한 건", [{**MG, "qty": 1}])
    async with SessionLocal() as db:
        await svc.set_won(db, did, uid, True, None)
    await reissue(uid, q["id"], [{**MG, "qty": 4}])
    out.append(f"D3 {q['quote_no']}")
    # D4 — 2차를 고치다 저장만 해 둔 건: 열면 [이어서 고치기]/[수주한 견적서 불러오기] 물어봄
    q, did = await quote(uid, "D4 고치다 만 견적", [{**MG, "qty": 1}])
    async with SessionLocal() as db:
        await svc.set_won(db, did, uid, True, None)
    await reissue(uid, q["id"], [{**MG, "qty": 4}])
    await pr.quote_edit(q["id"], user_id=uid, payload={"lines": [{"id": None, "remark": "고치다 만 줄", **MG, "qty": 7}]})
    out.append(f"D4 {q['quote_no']}")
    # D5 — 거래명세서까지 발급한 건: 견적 금액을 고치면 명세서가 새 금액으로 + 다시 발급 알림
    q, did = await quote(uid, "D5 명세서 발급한 건", [MG, TC])
    async with SessionLocal() as db:
        await svc.set_won(db, did, uid, True, None)
        dr = await svc.statement_draft(db, did, INI)
        await svc.set_statement(db, did, uid, {**dr, "buyer": BUYER, "date": TODAY, "initials": INI})
    out.append(f"D5 {q['quote_no']}")
    # D6 — 결제 조건 없이 입금만 남긴 건: 결제 조건 정하기 막힘·입금 기록 지우기
    q, did = await quote(uid, "D6 회차 없이 입금한 건", [MG, TC])
    async with SessionLocal() as db:
        await svc.set_won(db, did, uid, True, None)
        await svc.add_payment(db, did, uid, {"date": TODAY, "amount": 1_000_000, "full": False})
    out.append(f"D6 {q['quote_no']}")
    # D7 — 출하한 S/N 에 A/S 가 있는 건: 출하 기록 지우기 막힘
    q, did = await quote(uid, "D7 A/S 있는 출하 건", [{**MG, "qty": 1}])
    async with SessionLocal() as db:
        ids = await won_with_serials(db, uid, did, q["id"], "D7", TODAY - dt.timedelta(days=10))
        await units.create_case(db, uid, ids[0], {"received_on": TODAY - dt.timedelta(days=2), "symptom": "[수동검증] 그립 약함", "status": "접수"})
    out.append(f"D7 {q['quote_no']}")
    # R1·R2 — 대여: R1 에서 쓰고 회수한 로봇(MQ-R1, A/S 이력 있음)을 R2 에 다시 등록
    past = (TODAY - dt.timedelta(days=60)).isoformat()
    q1, r1 = await quote(uid, "R1 회수 끝난 대여", [ROBOT], {"purpose": "rental", "rental_months": 1, "rental_start": past})
    async with SessionLocal() as db:
        await svc.set_won(db, r1, uid, True, None)
        iu = await units.issue_units(db, r1, q1["id"], 0)
        iu = await units.save_units(db, r1, uid, q1["id"], 0, [{"line_no": iu["lines"][0]["line_no"],
                                                               "units": [{"serial": "MQ-R1", "maker": "UND", "purpose": "R"}]}])
        await svc.add_shipment(db, r1, uid, {"date": TODAY - dt.timedelta(days=60), "lines": [], "unit_ids": None})
        uid_r = iu["lines"][0]["units"][0]["id"]
        await units.create_case(db, uid, uid_r, {"received_on": TODAY - dt.timedelta(days=40), "symptom": "[수동검증] 대여 중 바퀴 소음",
                                                 "status": "완료"})
        await svc.set_rental_return(db, r1, uid, "done", TODAY - dt.timedelta(days=3))
    q2, r2 = await quote(uid, "R2 새 대여(S/N 아직)", [ROBOT], {"purpose": "rental", "rental_months": 3})
    async with SessionLocal() as db:
        await svc.set_won(db, r2, uid, True, None)
    out.append(f"R1 {q1['quote_no']} / R2 {q2['quote_no']}")
    # Q1 — 엑셀에 못 넣는 글자(복사해 붙인 제어 문자)가 든 견적서 초안: 미리보기·발행·내려받기가 되는지
    q = await pr.quote_manual(user_id=uid, form=HEAD)
    await pr.quote_edit(q["id"], user_id=uid, payload={
        "form": {"customer": MARK + "Q1 특수문자\x0b견적", "subject": "제어\x1f문자 시험"}, "delivery": {"case": 1},
        "lines": [{"id": None, "remark": "비고에\x08숨은 글자", **MG}]})
    q = await pr.quote_issue(q["id"], user_id=uid)          # 발행(영업 건과 이어져 영업 담당 누구나 열 수 있게)
    out.append(f"Q1 {q['quote_no']}")
    return out


async def seed_purchases(uid: int) -> list[str]:
    out = []
    hdr = {"supplier": {"name": MARK + "공급사"}, "issuer": {"name": "테스트"}}
    due = (TODAY + dt.timedelta(days=10)).isoformat()
    items = [{"commodity": "MG10 그리퍼", "qty": 2, "price": 700_000}, {"commodity": "TCV3 툴체인저", "qty": 1, "price": 300_000}]

    async def domestic(db, title: str) -> int:
        pid = await ps.create(db, uid, {"supplier": MARK + title, "title": title, "owner": "Alex"})
        await ps.skip_quote(db, pid, uid, "Alex", True)
        await ps.approve(db, pid, uid, "Alex", TODAY)
        await ps.issue_po(db, pid, uid, "Alex", {"initials": INI, "expected_date": due, "header": hdr, "items": items})
        return pid

    async with SessionLocal() as db:
        # P1 — 두 품목 다 입고한 건: 재발행 때 수량 줄이기·이름 바꾸기 막힘, 순서 바꾸기 OK, 품목 추가 → 알림 + 그 품목만 입고 대기
        pid = await domestic(db, "P1 입고 끝난 발주")
        await ps.add_receipt(db, pid, uid, "Alex", {"date": TODAY, "lines": [{"line_no": 0, "qty": 2}, {"line_no": 1, "qty": 1}]})
        out.append("P1")
        # P2 — 발주서만 발행한 건: 같은 번호 파일로 [교체]
        await domestic(db, "P2 같은 번호로 교체")
        out.append("P2")
        # P3 — 선금 송금 확인한 건: 확정 회차 바꾸기 막힘, [확인 취소] 뒤 바뀜
        pid = await domestic(db, "P3 선금 송금 확인")
        await ps.set_terms(db, pid, uid, [{"label": "선금", "pct": 50, "when": "발주 시"}, {"label": "잔금", "pct": 50}], admin=False)
        await ps.confirm_term(db, pid, uid, "Alex", 0, TODAY, None)
        out.append("P3")
        # P4 — 해외: 납기일 비우기
        oid = await ps.create(db, uid, {"supplier": MARK + "P4 해외 납기일", "title": "P4 해외 납기일", "region": "overseas", "owner": "Alex"})
        await ps.skip_quote(db, oid, uid, "Alex", True)
        await ps.approve(db, oid, uid, "Alex", TODAY)
        dr = await ps.po_draft(db, oid, {"initials": INI, "name_en": "Alex Test", "honorific": "Mr.", "email": "test@example.com",
                                         "mobile": "010-0000-0000"})
        await ps.issue_po(db, oid, uid, "Alex", {"initials": INI, "expected_date": (TODAY + dt.timedelta(days=40)).isoformat(),
                                                 "header": dr["header"], "items": [{"description": "AX8114", "qty": 2, "price": 4600.5}]})
        out.append("P4")
        # P5 — 일부만 입고한 건: 두 탭에서 같은 입고 기록을 고치기/지우기(낡은 화면 막힘)
        pid = await domestic(db, "P5 일부 입고")
        await ps.add_receipt(db, pid, uid, "Alex", {"date": TODAY, "lines": [{"line_no": 0, "qty": 1}]})
        await ps.add_receipt(db, pid, uid, "Alex", {"date": TODAY, "lines": [{"line_no": 1, "qty": 1}]})
        out.append("P5")
        # P6 — 구매 취소한 건: 되돌리기·지우기 막힘
        pid = await domestic(db, "P6 구매 취소한 건")
        await ps.add_receipt(db, pid, uid, "Alex", {"date": TODAY, "lines": [{"line_no": 0, "qty": 1}]})
        await ps.set_dropped(db, pid, uid, True, "수동 검증 — 취소한 건")
        out.append("P6")
        await db.commit()
    return out


async def main() -> None:
    await cleanup()
    if "--clean" in sys.argv:
        print("지웠습니다.")
        return
    async with SessionLocal() as db:
        uid = (await db.execute(text("SELECT id FROM users WHERE username = :u"), {"u": SALES_USER})).scalar_one()
    made = await seed_deals(uid) + await seed_purchases(uid)
    print("만든 시험 건:")
    for m in made:
        print("  ", m)


asyncio.run(main())
