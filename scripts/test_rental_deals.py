# -*- coding: utf-8 -*-
"""[대여 제품 목록] 건 단위·무상 대여 건 등록·S/N 불러오기(사용자 2026-11-10) — dev DB 전용, 시험 견적·건을 만들고 끝에 지운다.

    MSYS_NO_PATHCONV=1 docker compose run --rm -T --no-deps -e PYTHONPATH=/app -e PYTHONIOENCODING=utf-8 \\
        -v ./backend/app:/app/app -v ./scripts:/scripts backend python /scripts/test_rental_deals.py

확인: Remark S/N 읽기(범위 펼치기) / 대여 상태(견적·대여 예정·대여 중·회수 중·반납일 지남·회수 완료) /
무상 대여 견적서는 만들 때만(작성 중 유상↔무상 안 바뀜) / 무상 대여 건은 영업 건 관리에 안 나오고 대여 제품 목록에만 /
S/N 불러오기 — 빈 칸만 채움·수량보다 많으면 비움·다른 줄과 겹치면 비움·이미 등록된 S/N 비움·두 번 눌러도 그대로 /
시작일 정하면 대여 중·출하 없이 회수 완료(S/N 상태 회수) / 영업 건 목록 '확인 필요'·'미수' 수.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import sys

from sqlalchemy import text

from app.company_knowledge import product_recommend as pr
from app.company_knowledge import quote_session as qs
from app.config import settings
from app.database import SessionLocal
from app.sales_deals import deal_goto, flow
from app.sales_deals import rental_deals as rd
from app.sales_deals import service as svc

assert settings.database_url.rsplit("/", 1)[-1] == "agent_ai_dev", "dev DB 에서만 돌린다"
sys.stdout.reconfigure(encoding="utf-8")

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []
CUST = "__테스트_대여건목록__"
TODAY = svc.kst_today()


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


async def cleanup() -> None:
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM product_units WHERE serial LIKE 'ZZRD-%'"))
        await db.execute(text("DELETE FROM doc_numbers WHERE quote_id IN (SELECT id FROM product_quotes WHERE customer = :c "
                              "OR state->'form'->>'customer' = :c) OR deal_id IN (SELECT id FROM sales_deals WHERE customer = :c)"),
                         {"c": CUST})
        await db.execute(text("DELETE FROM product_quotes WHERE customer = :c OR state->'form'->>'customer' = :c"), {"c": CUST})
        await db.execute(text("DELETE FROM sales_deals WHERE customer = :c"), {"c": CUST})
        await db.commit()


def pure() -> None:
    check(rd.parse_serials("S/N: POM-BI3303-U4") == ["POM-BI3303-U4"], "Remark 'S/N: 번호' 읽기")
    rng = rd.parse_serials("S/N: CT-BI3321-U1 ~ U23")
    check(len(rng) == 23 and rng[0] == "CT-BI3321-U1" and rng[-1] == "CT-BI3321-U23", "범위 'U1 ~ U23' → 23개", str(rng[:3]))
    check(rd.parse_serials("S/N: PL-U01~PL-U03") == ["PL-U01", "PL-U02", "PL-U03"], "자릿수 있는 범위(U01~U03)")
    check(rd.parse_serials("SN : A1, B2 C3; A1") == ["A1", "B2", "C3"], "쉼표·빈칸·세미콜론 나눔, 같은 번호 한 번")
    check(rd.parse_serials("특주") == [] and rd.parse_serials("Pogo-Pin 2EA") == [], "'S/N' 표시 없는 메모는 S/N 아님")
    check(rd.parse_serials("S/N: 1 ~ 99999") == ["1", "~", "99999"] or len(rd.parse_serials("S/N: 1 ~ 99999")) < 10,
          "터무니없는 범위는 펼치지 않음")

    from app.sales_deals import units as un

    tl = un.target_lines([
        {"name": "3년 렌탈(월 납입금)", "commodity": "방역 로봇", "qty": 36, "unit": "개월"},
        {"name": "설치 및 시운전 (국내)\n- 교육 포함", "commodity": "설치", "qty": 1, "unit": "MD"},
        {"name": "배송비", "commodity": "배송", "qty": 1, "unit": "식"},
        {"name": "유지보수 3년 기준", "commodity": "유지보수", "qty": 36, "unit": "개월"},
        {"name": "MG10", "commodity": "Magnetic Gripper", "qty": 2}])
    check([x["name"] for x in tl] == ["3년 렌탈(월 납입금)", "MG10"], "설치·배송·유지보수 같은 서비스·비용 줄은 S/N 대상 아님",
          str([x["name"] for x in tl]))
    keep = [{"name": n, "commodity": c, "qty": 1} for n, c in (("교육용 키트", "Kit"), ("배송 로봇", "Robot"), ("설치 브라켓", "Bracket"),
                                                                 ("Training Kit", "Kit"), ("TCV1 MASTER", "교육용"))]
    drop = [{"name": n, "commodity": "", "qty": 1} for n in ("교육", "설치 및 시운전", "배송비", "현장 교육 1일", "Installation")]
    check(len(un.target_lines(keep)) == len(keep) and un.target_lines(drop) == [],
          "'교육용 키트'·'배송 로봇' 같은 제품은 S/N 대상, '교육'·'설치 및 시운전'은 아님",
          f"{[x['name'] for x in un.target_lines(keep)]} / {[x['name'] for x in un.target_lines(drop)]}")

    def ph(**kw) -> str:
        rent = {"months": 3, "start": kw.get("start"), "end": kw.get("end"), "return": kw.get("ret") or {},
                **({"free": True} if kw.get("free") else {})}
        return flow.rental_phase({"won": kw.get("won"), "quote": {"biz": "rental", "amount": kw.get("amount", 100), "rental": rent}}, TODAY)

    d = lambda n: (TODAY + dt.timedelta(days=n)).isoformat()  # noqa: E731
    check(ph(won=False) == "견적", "유상 수주 전 = 견적")
    check(ph(free=True) == "대여 예정", "무상은 견적 단계 없이 대여 예정(시작일 미정)")
    check(ph(won=True, start=d(3), end=d(90)) == "대여 예정", "시작일 전 = 대여 예정")
    check(ph(won=True, start=d(0), end=d(90)) == "대여 중", "시작일 당일부터 대여 중(자동)")
    check(ph(free=True, start=d(-10), end=d(80), ret={"started": d(-1)}) == "회수 중", "회수 시작 = 회수 중")
    check(ph(won=True, start=d(-100), end=d(-1)) == "반납일 지남", "반납일 지남(회수 완료 전)")
    check(ph(won=True, start=d(-100), end=d(-1), ret={"started": d(-3), "done": d(-2)}) == "회수 완료", "회수 완료")
    check(ph(amount=0) == "대여 예정", "예전 무상 대여 건(합계 0원)도 무상으로 봄")


async def flow_run(uid: int) -> None:
    head = {"initials": "AL", "contact_name": "Alex", "contact_mobile": "010-0000-0000", "contact_email": "t@example.com"}
    # 유상 대여 견적서는 화면 저장으로 무상이 되지 않는다
    p = await pr.quote_manual(user_id=uid, form={**head, "purpose": "rental"})
    p = await pr.quote_edit(p["id"], user_id=uid, payload={"form": {"customer": CUST, "rental_fee": "free", "purpose": "rental"}})
    check(not qs.free_rental(p["state"]), "유상 대여 견적서 — 저장으로 무상 안 됨")

    # [무상 대여 건 등록] — 만들 때 무상
    q = await pr.quote_manual(user_id=uid, form={**head, "purpose": "rental", "rental_fee": "free"})
    lines = [
        {"id": None, "name": "MG10", "commodity": "Magnetic Gripper", "qty": 2, "unit_price": None, "remark": "S/N: ZZRD-MG10-U1, ZZRD-MG10-U2"},
        {"id": None, "name": "Controller", "commodity": "Universal Controller", "qty": 3, "unit_price": None, "remark": "S/N: ZZRD-CT-U1 ~ U3"},
        {"id": None, "name": "PPM", "commodity": "PPM", "qty": 1, "unit_price": None, "remark": "S/N: ZZRD-P-U1, ZZRD-P-U2"},
        {"id": None, "name": "PPF", "commodity": "PPF", "qty": 2, "unit_price": None, "remark": "S/N: ZZRD-MG10-U1"},
        {"id": None, "name": "Cable 4M", "commodity": "Cable", "qty": 1, "unit_price": None, "remark": ""},
    ]
    q = await pr.quote_edit(q["id"], user_id=uid, payload={
        "form": {"customer": CUST, "subject": "무상 대여", "purpose": "rental", "rental_fee": "paid", "rental_months": 3},
        "delivery": {"case": 1}, "lines": lines})
    check(qs.free_rental(q["state"]), "무상 대여 건 등록 견적서 — 저장으로 유상 안 됨")
    q = await pr.quote_issue(q["id"], user_id=uid)
    async with SessionLocal() as db:
        did = (await db.execute(text("SELECT deal_id FROM product_quotes WHERE id = :i"), {"i": q["id"]})).scalar()
        if not did:
            did = (await db.execute(text("SELECT id FROM sales_deals WHERE customer = :c ORDER BY id DESC LIMIT 1"), {"c": CUST})).scalar()
        deal = await svc.get_deal(db, did, TODAY)
        check(deal["flow"]["free"] and (deal["quote"]["rental"] or {}).get("free"), "발행 → 무상 대여 건(표시 남김)", str(deal["quote"].get("rental")))
        sales = await svc.list_deals(db, TODAY)
        check(all(r["id"] != did for r in sales["rows"]), "무상 대여 건은 영업 건 관리 목록에 없음")
        check(any(r["id"] == did for r in (await svc.list_deals(db, TODAY, include_free=True))["rows"]), "채팅 건 찾기(include_free)에는 있음")
        check(deal_goto._deal_tab(deal) == "rentals", "채팅 '건 열기'는 대여 제품 목록으로")
        check("미수" in sales["summary"] and "확인 필요" in sales["summary"], "영업 건 목록 요약에 미수·확인 필요 수")
        lst = await rd.rental_list(db, TODAY)
        row = next((r for r in lst["rows"] if r["id"] == did), None)
        check(row is not None and row["free"] and row["phase"] == "대여 예정" and row["no_start"] and row["products"] == 5 and row["qty"] == 9,
              "대여 제품 목록 — 무상·대여 예정·시작일 미정·5종 9개", str(row))
        prods = [r for r in (await rd.rental_products(db, TODAY))["rows"] if r["deal_id"] == did]
        check([r["product"] for r in prods] == ["MG10", "Controller", "PPM", "PPF", "Cable 4M"]
              and all(r["customer"] == CUST and r["free"] and r["phase"] == "대여 예정" for r in prods) and prods[1]["qty"] == 3,
              "대여 제품 목록 — 품목 줄마다 한 줄 + 고객사·무상·상태", str([(r["product"], r["qty"]) for r in prods]))
        det = await rd.rental_detail(db, did, TODAY)
        hints ={ln["name"].split("\n")[0]: ln["hints"] for ln in det["issue"]["lines"]}
        check(det["issue"]["hint_total"] == 8 and len(hints.get("Controller", [])) == 3, "상세 — Remark S/N 8개(범위 3개 포함)", str(hints))

    # 다른 건에 이미 등록된 S/N 하나(ZZRD-CT-U3) — 불러오기에서 비워 둔다
    async with SessionLocal() as db:
        await db.execute(text("INSERT INTO product_units (deal_id, line_no, line_name, part_name, serial, purpose, status) "
                              "VALUES (NULL, 0, 'X', 'X', 'ZZRD-CT-U3', 'S', '등록')"))
        await db.commit()
        res = await rd.load_serials(db, did, uid, TODAY)
    why = {s["serial"]: s["why"] for s in res["skipped"]}
    check(res["filled"] == 4, "S/N 불러오기 — 4개 넣음(MG10 2 · Controller 2)", f"{res['filled']} {why}")
    check("수량" in why.get("ZZRD-P-U1", "") and "수량" in why.get("ZZRD-P-U2", ""), "수량보다 많이 적힌 줄은 비움", str(why))
    check("다른 줄" in why.get("ZZRD-MG10-U1", ""), "다른 줄과 겹치는 S/N 은 비움", str(why))
    check("이미 등록" in why.get("ZZRD-CT-U3", ""), "이미 등록된 S/N 은 비움", str(why))
    lines_now = {ln["name"].split("\n")[0]: [u["serial"] for u in ln["units"]] for ln in res["detail"]["issue"]["lines"]}
    check(lines_now["MG10"] == ["ZZRD-MG10-U1", "ZZRD-MG10-U2"] and lines_now["Controller"] == ["ZZRD-CT-U1", "ZZRD-CT-U2"]
          and lines_now["PPM"] == [] and lines_now["Cable 4M"] == [], "넣은 칸·빈 칸", str(lines_now))
    purposes = {u["purpose"] for ln in res["detail"]["issue"]["lines"] for u in ln["units"]}
    check(purposes == {"R"}, "불러온 S/N 상태 = R(대여)", str(purposes))
    async with SessionLocal() as db:
        again = await rd.load_serials(db, did, uid, TODAY)
    check(again["filled"] == 0, "두 번 눌러도 이미 넣은 칸은 그대로", str(again["filled"]))

    # 시작일 → 대여 중(출하 기록 없이), 회수 완료 → S/N 상태 회수
    async with SessionLocal() as db:
        await svc.set_rental_start(db, did, uid, TODAY)
        det = await rd.rental_detail(db, did, TODAY)
        check(det["phase"] == "대여 중" and det["started"], "시작일 = 오늘 → 대여 중(자동)", det["phase"])
        await svc.set_rental_return(db, did, uid, "done", None)
        det = await rd.rental_detail(db, did, TODAY)
        st = (await db.execute(text("SELECT DISTINCT status FROM product_units WHERE deal_id = :d"), {"d": did})).scalars().all()
        check(det["phase"] == "회수 완료" and st == ["회수"], "출하 기록 없이 회수 완료 · S/N 상태 회수", f"{det['phase']} {st}")


async def run() -> None:
    pure()
    await cleanup()
    async with SessionLocal() as db:
        uid = (await db.execute(text("SELECT id FROM users WHERE username = 'alex'"))).scalar()
    try:
        await flow_run(uid)
    finally:
        await cleanup()
    for label in PASS:
        print("PASS", label)
    for label, why in FAIL:
        print("FAIL", label, "—", why)
    print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")


asyncio.run(run())
sys.exit(1 if FAIL else 0)
