# -*- coding: utf-8 -*-
"""제품 시리얼·무상 기간·A/S(sales_deals/units.py, 사용자 2026-10-19 영업팀 요청 20번) 점검.

    MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -e PYTHONPATH=/app -e PYTHONIOENCODING=utf-8 \\
        -v ./backend/app:/app/app -v ./scripts:/scripts backend python /scripts/test_product_units.py

순수 규칙(대상 품목 줄·부품명·시리얼 없음·무상 종료일·무상/유상 제안)을 보고, 개발 DB(agent_ai_dev)에 테스트 건을 하나 만들어
등록 → 출하 차단 → 겹침 검사 → 출하(무상 기간 시작) → 지우기 차단 → A/S → 출하 삭제 흐름을 돌린 뒤 지운다.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import sys

from sqlalchemy import text

from app import migrations
from app.database import SessionLocal
from app.sales_deals import service as svc
from app.sales_deals import units

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []
CUST = "__테스트_시리얼__"
NO = "QT-TS20261019-01"


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


async def expect_error(coro, label: str, needle: str = "") -> None:
    try:
        await coro
        check(False, label, "오류가 나지 않음")
    except svc.DealError as e:
        check(needle in str(e), label, str(e))


# ── 1) 순수 규칙 ──
LINES = [
    {"name": "ATC_CORE\n- TCV1 MASTER T.C (Robot Side)", "qty": 1, "unit_price": 2300000},
    {"name": "ATC_CORE\n- TCV1 Tool Plate (Tool Side)", "qty": 2, "unit_price": 400000},
    {"desc": "Dobot MG400", "qty": 1, "unit_price": 3000000, "commodity": "Robot"},
    {"name": "설치 인건비", "qty": 2, "unit_price": 500000, "kind": "work"},
    {"name": "물류비", "qty": 1, "unit_price": 100000, "commodity": "Service"},
    {"name": "NEGO", "qty": None, "unit_price": -100000, "row": "nego"},
    {"name": "Tool Stand", "qty": 0, "unit_price": 0},
]
tl = units.target_lines(LINES)
check([(x["line_no"], x["qty"]) for x in tl] == [(0, 1), (1, 2), (2, 1)],
      "대상 품목 줄 = 수량 있는 제품 줄만(설치비·Service·NEGO·수량 0 제외)", str(tl))
check(units.default_part(LINES[0]["name"]) == "TCV1 MASTER T.C (Robot Side)" and units.default_part("Dobot MG400") == "Dobot MG400",
      "부품명 기본값 = 묶음 이름 아래 '- ' 줄")
check(units.is_no_serial("특주") and units.is_no_serial("실주") and not units.is_no_serial("DT1802-4619-0048"),
      "숫자 없는 글(특주·실주) = 시리얼 없음")
check(units.warranty_until(dt.date(2026, 10, 19), 12) == dt.date(2027, 10, 18)
      and units.warranty_until(dt.date(2026, 1, 31), 1) == dt.date(2026, 2, 28)
      and units.warranty_until(dt.date(2026, 10, 19), None) is None, "무상 종료일 = 시작 + 개월 − 하루(그 달에 같은 날이 없으면 말일 — 민법, 사용자 2026-10-27), 개월 없으면 미정")
u_in = {"warranty_until": "2027-10-18"}
check(units.suggest_charge(u_in, dt.date(2027, 1, 5), "제품 결함") == (True, "무상")
      and units.suggest_charge(u_in, dt.date(2027, 1, 5), "고객 과실") == (True, "유상")
      and units.suggest_charge(u_in, dt.date(2028, 1, 5), "제품 결함") == (False, "유상")
      and units.suggest_charge({"warranty_until": None}, dt.date(2027, 1, 5), "확인 중") == (None, "미정"),
      "무상/유상 제안 = 기간 안·고객 과실 아님 → 무상, 기간 지남·고객 과실 → 유상, 기간 미정 → 미정")
pol = [{"keyword": "TCV", "months": 12}, {"keyword": "Tool Plate", "months": 6}, {"keyword": "Dobot", "months": None}]
check(units.match_policy(pol, "TCV1 Tool Plate (Tool Side)")["months"] == 6 and units.match_policy(pol, "TCV1 MASTER T.C")["months"] == 12
      and units.match_policy(pol, "Dobot MG400")["months"] is None and units.match_policy(pol, "MG10") is None,
      "무상 기간 기본값 = 맞는 것 중 가장 긴 제품 이름")
check([units.state_code(x) for x in ("S", "r", " D ", "A", "E", "O", "판매", "대여", "A/S 교체", "", None, "Z")]
      == ["S", "R", "D", "A", "E", "O", "S", "R", "E", "S", "S", "S"],
      "상태 이니셜 = 코드(소문자 허용)·예전 한글 값 → 코드, 모르는 값은 S")


# ── 2) DB 흐름 ──
async def cleanup(db) -> None:
    ids = (await db.execute(text("SELECT id FROM sales_deals WHERE customer = :c"), {"c": CUST})).scalars().all()
    if ids:
        await db.execute(text("DELETE FROM as_cases WHERE deal_id = ANY(:i)"), {"i": ids})
        await db.execute(text("DELETE FROM product_units WHERE deal_id = ANY(:i)"), {"i": ids})
        await db.execute(text("DELETE FROM product_quotes WHERE deal_id = ANY(:i)"), {"i": ids})
        await db.execute(text("DELETE FROM sales_deals WHERE id = ANY(:i)"), {"i": ids})
    await db.commit()


async def run() -> None:
    assert os.environ.get("DATABASE_URL", "").endswith("/agent_ai_dev"), "개발 DB(agent_ai_dev)에서만 돌린다"
    async with SessionLocal() as db:
        await migrations.run_migrations(db)
        await cleanup(db)
        items = svc.snapshot_items(LINES)
        quote = {"no": NO, "date": "2026-10-19", "amount": 6_100_000, "vat_included": False, "currency": "KRW", "items": items,
                 "revision": 1, "sent_date": None}
        deal_id = (await db.execute(text(
            "INSERT INTO sales_deals (deal_no, kind, customer, owner, title, base_date, quote) "
            "VALUES (:no, 'quote', :c, 'Test', 'TCV1 외', '2026-10-19', CAST(:q AS jsonb)) RETURNING id"),
            {"no": NO, "c": CUST, "q": json.dumps(quote, ensure_ascii=False)})).scalar_one()
        hist = [{"revision": 0, "quote_no": NO, "lines": LINES, "total": 6_100_000, "currency": "KRW",
                 "issued_at": "2026-10-19T01:00:00+00:00"}]
        qid = (await db.execute(text(
            "INSERT INTO product_quotes (deal_id, status, quote_no, customer, revision, history, total) "
            "VALUES (:d, 'issued', :no, :c, 0, CAST(:h AS jsonb), 6100000) RETURNING id"),
            {"d": deal_id, "no": NO, "c": CUST, "h": json.dumps(hist, ensure_ascii=False)})).scalar_one()
        await db.commit()
        try:
            await flow(db, deal_id, qid)
        finally:
            await db.rollback()
            await cleanup(db)
        try:
            await bind_flow(db)
        finally:
            await db.rollback()
            await cleanup(db)


async def bind_flow(db) -> None:
    """이미 남긴 출하 기록에 나중에 S/N 등록(사용자 2026-10-23) — 그 출하로 바로 묶이고 그 출하일부터 무상 기간."""
    today = dt.date(2026, 10, 23)
    no = "QT-TS20261023-09"
    lines = [{"name": "TCV3 툴체인저", "qty": 2, "unit_price": 5_000_000}]
    quote = {"no": no, "date": "2026-10-01", "amount": 10_000_000, "currency": "KRW", "items": svc.snapshot_items(lines), "revision": 1}
    shipments = [{"date": "2026-10-01", "items": "TCV3 툴체인저 × 1", "lines": [{"name": "TCV3 툴체인저", "qty": 1, "photos": []}],
                  "tracking": [], "photos": []}]
    deal_id = (await db.execute(text(
        "INSERT INTO sales_deals (deal_no, kind, customer, owner, title, base_date, quote, won, shipments) VALUES "
        "(:no, 'quote', :c, 'Test', 'TCV3', '2026-10-01', CAST(:q AS jsonb), TRUE, CAST(:s AS jsonb)) RETURNING id"),
        {"no": no, "c": CUST, "q": json.dumps(quote, ensure_ascii=False), "s": json.dumps(shipments, ensure_ascii=False)})).scalar_one()
    hist = [{"revision": 0, "quote_no": no, "lines": lines, "total": 10_000_000, "currency": "KRW", "issued_at": "2026-10-01T01:00:00+00:00"}]
    qid = (await db.execute(text(
        "INSERT INTO product_quotes (deal_id, status, quote_no, customer, revision, history, total) "
        "VALUES (:d, 'issued', :no, :c, 0, CAST(:h AS jsonb), 10000000) RETURNING id"),
        {"d": deal_id, "no": no, "c": CUST, "h": json.dumps(hist, ensure_ascii=False)})).scalar_one()
    await db.commit()
    d = await svc.get_deal(db, deal_id, today)
    check(d["units"] and d["units"]["missing"] == 2, "S/N 없이 출하된 건 — 현황 2개 남음", str(d["units"]))
    tag = dt.datetime.now().strftime("%H%M%S")
    v = await units.save_units(db, deal_id, None, qid, 0, [{"line_no": 0, "units": [{"serial": f"TST-{tag}-B1", "warranty_months": 12}]}],
                               partial=True, shipment_index=0)
    u = v["lines"][0]["units"][0]
    check(u["shipment_index"] == 0 and u["warranty_start"] == "2026-10-01" and u["warranty_until"] == "2027-09-30",
          "출하 뒤 S/N 등록 → 그 출하로 묶이고 그 출하일부터 무상 기간", str({k: u[k] for k in ("shipment_index", "warranty_start", "warranty_until")}))
    await expect_error(units.save_units(db, deal_id, None, qid, 0, [{"line_no": 0, "units": [{"id": u["id"], "serial": u["serial"]}]}],
                                        partial=True, shipment_index=5), "없는 출하 번호면 막음", "출하 기록이 없습니다")
    await db.rollback()
    ship = {"date": today, "carrier": "택배", "tracking": "", "items": "", "purpose": "판매",
            "lines": [{"name": "TCV3 툴체인저", "qty": 1, "line_no": 0}], "unit_ids": []}
    await expect_error(svc.add_shipment(db, deal_id, None, dict(ship)), "두 번째 출하도 S/N 빈칸이면 막음", "S/N을 등록")
    await db.rollback()


async def flow(db, deal_id: int, qid: int) -> None:
    today = dt.date(2026, 10, 19)
    d = await svc.get_deal(db, deal_id, today)
    check(d["units"] and (d["units"]["total"], d["units"]["done"], d["units"]["issue_no"]) == (4, 0, NO),
          "영업 건 상세 = 수주 판 시리얼 현황(4개 중 0개)", str(d["units"]))
    ship = {"date": today, "carrier": "택배", "tracking": "", "items": "", "lines": [], "purpose": "판매"}
    await expect_error(svc.add_shipment(db, deal_id, None, dict(ship)), "S/N 미등록이면 출하 막음", "S/N을 등록")
    await db.rollback()

    v = await units.issue_units(db, deal_id, qid, 0)
    check([ln["qty"] for ln in v["lines"]] == [1, 2, 1] and v["is_won_issue"] and v["summary"]["missing"] == 4,
          "등록 화면 = 품목 줄·수량, 수주 판 표시", str(v["summary"]))
    tag = dt.datetime.now().strftime("%H%M%S")
    s1, s2, s3 = f"TST-{tag}-M1", f"TST-{tag}-T1", f"TST-{tag}-T2"
    # 일부만(빈 칸은 저장 안 함)
    v = await units.save_units(db, deal_id, None, qid, 0, [
        {"line_no": 0, "units": [{"serial": s1, "warranty_months": 12}]},
        {"line_no": 1, "units": [{"serial": s2, "warranty_months": 6}, {"serial": "  "}]}])
    check(v["summary"]["done"] == 2 and v["summary"]["missing"] == 2, "빈 칸 = 미등록(저장 안 함)", str(v["summary"]))
    await expect_error(svc.add_shipment(db, deal_id, None, dict(ship)), "일부만 적어도 출하 막음", "4개 중 2개")
    await db.rollback()
    # 출하 기록의 품목 줄마다 [S/N 등록](사용자 2026-10-23) — 보낸 줄만 저장, 다른 줄 S/N 은 그대로
    v = await units.save_units(db, deal_id, None, qid, 0, [{"line_no": 2, "units": [{"serial": "특주"}]}], partial=True)
    have = {ln["line_no"]: [u["serial"] for u in ln["units"]] for ln in v["lines"]}
    check(have == {0: [s1], 1: [s2], 2: ["특주"]}, "줄 하나만 저장(partial) — 다른 줄은 그대로", str(have))
    await expect_error(units.save_units(db, deal_id, None, qid, 0, [{"line_no": 2, "units": [{"serial": s1.lower()}]}], partial=True),
                       "줄 하나만 저장해도 같은 판 다른 줄의 S/N 과 겹치면 막음", "이미 등록된")
    await db.rollback()
    ids = {u["serial"]: u["id"] for ln in v["lines"] for u in ln["units"]}
    d0 = await svc.get_deal(db, deal_id, today)
    got = await units.check_before_ship(db, d0, {"lines": [{"name": "MASTER", "qty": 1, "line_no": 0}], "unit_ids": [ids[s1]]})
    check(got == (qid, 0), "나눠 출하 — 이 출하 품목의 S/N 만 다 적었으면 통과(다른 줄 미등록이어도)", str(got))
    await expect_error(svc.add_shipment(db, deal_id, None, {**ship, "lines": [{"name": "TOOL", "qty": 2, "line_no": 1}],
                                                            "unit_ids": [ids[s2]]}),
                       "출하 수량보다 S/N 이 적으면 그 품목 이름과 함께 막음", "S/N을 등록")
    await db.rollback()
    keep = [{"id": u["id"], "serial": u["serial"], "warranty_months": u["warranty_months"]} for ln in v["lines"] for u in ln["units"]]
    await expect_error(units.save_units(db, deal_id, None, qid, 0, [
        {"line_no": 1, "units": [{"serial": s2}, {"serial": s2.lower()}]}]), "같은 시리얼 두 번 → 막음", "두 번")
    await db.rollback()
    # 시리얼 없는 품목은 '특주' — 두 줄에 같은 글이어도 겹침 아님
    v = await units.save_units(db, deal_id, None, qid, 0, [
        {"line_no": 0, "units": [keep[0]]},
        {"line_no": 1, "units": [keep[1], {"serial": s3, "warranty_months": 6, "purpose": "R"}]},
        {"line_no": 2, "units": [{"serial": "특주", "maker": "Dobot", "part_name": "MG400 본체", "purpose": "A/S 교체"}]}])
    st = {u["serial"]: u["purpose"] for ln in v["lines"] for u in ln["units"]}
    check((st[s1], st[s3], st["특주"]) == ("S", "R", "E"), "상태 이니셜 저장(기본 S, 예전 한글 값은 코드로)", str(st))
    check(v["summary"]["missing"] == 0 and sum(u["no_serial"] for ln in v["lines"] for u in ln["units"]) == 1,
          "'특주' = 등록한 것(시리얼 없음 표시)", str(v["summary"]))
    # 다른 건·다른 판에 같은 시리얼이면 막음 — 같은 판을 다시 저장하는 건 괜찮음
    other = (await db.execute(text("INSERT INTO product_units (deal_id, part_name, serial, maker) VALUES (NULL, 'x', :s, 'UND') "
                                   "RETURNING id"), {"s": f"TST-{tag}-X"})).scalar_one()
    await db.commit()
    await expect_error(units.save_units(db, deal_id, None, qid, 0, [{"line_no": 0, "units": [{"serial": f"tst-{tag}-x"}]}]),
                       "다른 곳에 이미 있는 시리얼 → 막음", "이미 등록된")
    await db.rollback()
    await db.execute(text("DELETE FROM product_units WHERE id = :i"), {"i": other})
    await db.commit()

    d = await svc.get_deal(db, deal_id, today)
    check(d["units"]["missing"] == 0, "다 적으면 현황 0개 남음")
    await svc.add_shipment(db, deal_id, None, dict(ship))
    rs = {r["serial"]: r for r in await units.deal_units(db, deal_id)}
    check(rs[s1]["shipment_index"] == 0 and rs[s1]["warranty_start"] == "2026-10-19" and rs[s1]["warranty_until"] == "2027-10-18"
          and rs[s2]["warranty_until"] == "2027-04-18" and rs["특주"]["warranty_until"] is None and rs[s1]["status"] == "출하",
          "출하 = 무상 기간 시작·종료일 확정(개월 없으면 미정)", str({k: (r["warranty_start"], r["warranty_until"]) for k, r in rs.items()}))
    # 출하한 시리얼은 지울 수 없음, 무상 기간을 고치면 종료일도 다시
    await expect_error(units.save_units(db, deal_id, None, qid, 0, [{"line_no": 0, "units": [{"id": rs[s1]["id"], "serial": s1}]}]),
                       "출하한 시리얼 지우기 → 막음", "지울 수 없습니다")
    await db.rollback()
    cur = await units.issue_units(db, deal_id, qid, 0)
    pay = [{"line_no": ln["line_no"], "units": [{**u, "warranty_months": 24 if u["serial"] == s1 else u["warranty_months"]}
                                                for u in ln["units"]]} for ln in cur["lines"]]
    await units.save_units(db, deal_id, None, qid, 0, pay)
    rs = {r["serial"]: r for r in await units.deal_units(db, deal_id)}
    check(rs[s1]["warranty_until"] == "2028-10-18", "출하 뒤 무상 기간 고치면 종료일 다시(시작일 그대로)", rs[s1]["warranty_until"])

    # A/S — 기간 안 제품 결함 = 무상 제안, 고객 과실 = 유상, 완료하면 완료일
    c1 = await units.create_case(db, None, rs[s1]["id"], {"received_on": dt.date(2027, 3, 2), "symptom": "결합 불량", "cause": "제품 결함"})
    check(c1["in_warranty"] is True and c1["charge"] == "무상" and c1["customer"] == CUST and c1["status"] == "접수",
          "A/S 접수 — 무상 기간 안이면 무상 제안", str(c1))
    pid = "0" * 32 + ".jpg"
    c2 = await units.create_case(db, None, rs[s2]["id"], {"received_on": dt.date(2027, 3, 2), "cause": "고객 과실",
                                                          "customer": "(주)바뀐고객", "contact": "김철수 과장 010-1234-5678",
                                                          "photos": [pid, pid]})
    check(c2["charge"] == "유상", "고객 과실이면 유상 제안")
    check(c2["customer"] == "(주)바뀐고객" and c2["contact"].startswith("김철수") and c2["photos"] == [pid] and "received_by" in c2,
          "A/S 접수서 = 고객사(고칠 수 있음)·담당자·사진(겹침 하나로)·접수자", str(c2)[:300])
    # 같은 S/N 에 진행 중 A/S(c2)가 있으면 새 접수는 막으므로(사용자 2026-10-27) 끝난 기록으로 남겨 확인한다
    c3 = await units.create_case(db, None, rs[s2]["id"], {"received_on": dt.date(2027, 3, 2), "charge": "무상", "status": "완료"})
    check(c3["charge"] == "무상" and c3["in_warranty"] is True, "무상·유상 직접 고르면 그 값(제안은 출하일 기준 무상 기간)")
    await db.execute(text("DELETE FROM as_cases WHERE id = :i"), {"i": c3["id"]})
    await db.commit()
    await expect_error(units.create_case(db, None, rs[s2]["id"], {"photos": ["../../etc/passwd"]}), "사진 id 가 아니면 막음", "사진")
    await db.rollback()
    c2 = await units.update_case(db, None, c2["id"], {"photos": [], "contact": "이영희 대리"})
    check(c2["photos"] == [] and c2["contact"] == "이영희 대리", "접수서 고치기(담당자·사진)", str(c2)[:200])
    d = await svc.get_deal(db, deal_id, today)
    lst = next(r for r in (await svc.list_deals(db, today))["rows"] if r["id"] == deal_id)
    check((d["as_open"], d["as_total"], lst["as_open"]) == (2, 2, 2), "영업 건에 A/S 진행 중 건수(상세·리스트)",
          str((d["as_open"], d["as_total"], lst["as_open"])))
    c1 = await units.update_case(db, None, c1["id"], {"status": "완료", "action": "마스터 교체"})
    check(c1["status"] == "완료" and c1["closed_on"] == dt.date.today().isoformat() and len(c1["history"]) == 2,
          "완료로 바꾸면 완료일·이력", str(c1))
    lst = next(r for r in (await svc.list_deals(db, today))["rows"] if r["id"] == deal_id)
    check((lst["as_open"], lst["as_total"]) == (1, 2), "완료한 A/S 는 진행 중에서 빠짐", str((lst["as_open"], lst["as_total"])))
    # [A/S 들어감 확인](사용자 2026-10-22) — 접수만 한 건은 'A/S 진행 중'이 아니고, 버튼(상태만 보냄) 뒤에 진행 중
    data = await svc.list_deals(db, today)
    lst = next(r for r in data["rows"] if r["id"] == deal_id)
    check(lst["as_active"] == 0, "접수만 한 A/S 는 진행 중 아님(줄 연하게 안 함)", str(lst["as_active"]))
    c2 = await units.update_case(db, None, c2["id"], {"status": "진행 중"})
    data = await svc.list_deals(db, today)
    lst = next(r for r in data["rows"] if r["id"] == deal_id)
    check(c2["status"] == "진행 중" and c2["contact"] == "이영희 대리" and lst["as_active"] == 1 and data["summary"]["A/S 진행 중"] >= 1,
          "[A/S 들어감 확인] → 진행 중, 다른 칸은 그대로, 리스트 as_active·칩", str((c2["status"], lst["as_active"])))
    state = lambda: db.execute(text("SELECT purpose FROM product_units WHERE id = :i"), {"i": c2["unit_id"]})  # noqa: E731
    check((await state()).scalar() == "A" and c2["history"][-1].get("unit_state") == ["S", "A"],
          "A/S 진행 중이면 제품 상태 A(A/S 중), 이력에 전 상태", str(c2["history"][-1]))
    hit = next(x for x in await units.search_units(db, c2["serial"], today) if x["id"] == c2["unit_id"])
    check(hit["deal_id"] == deal_id and hit["deal_biz"] == "sale" and hit["deal_title"] and hit["purpose"] == "A",
          "시리얼 찾기 결과에 프로젝트 건(id·판매/대여·건명)", str({k: hit.get(k) for k in ("deal_id", "deal_biz", "deal_title")}))
    c2 = await units.update_case(db, None, c2["id"], {"status": "완료"})
    check((await state()).scalar() == "S", "A/S 완료면 제품 상태가 들어가기 전(S)으로")
    lst = next(r for r in (await svc.list_deals(db, today))["rows"] if r["id"] == deal_id)
    check(lst["as_active"] == 0 and lst["as_total"] == 2, "[A/S 완료] → 진행 중에서 빠지고 기록은 남음", str((lst["as_active"], lst["as_total"])))
    # 관리자 A/S 기록 삭제(사용자 2026-10-22) — 진행 중이던 건이면 제품 상태 되돌림, 보관함에 남김, 시리얼은 그대로
    c3 = await units.create_case(db, None, c2["unit_id"], {"symptom": "삭제 확인용"})
    c3 = await units.update_case(db, None, c3["id"], {"status": "진행 중"})
    check((await state()).scalar() == "A", "삭제 전: 진행 중이라 제품 상태 A")
    n = await units.delete_cases(db, [c3["id"]], None)
    gone = (await db.execute(text("SELECT COUNT(*) FROM as_cases WHERE id = :i"), {"i": c3["id"]})).scalar()
    kept = (await db.execute(text("SELECT COUNT(*) FROM trash_bin WHERE source_table = 'as_cases' AND source_id = :i"), {"i": c3["id"]})).scalar()
    unit_left = (await db.execute(text("SELECT COUNT(*) FROM product_units WHERE id = :i"), {"i": c3["unit_id"]})).scalar()
    check(n == 1 and gone == 0 and kept == 1 and unit_left == 1 and (await state()).scalar() == "S",
          "A/S 기록 삭제 → 보관함으로, 시리얼은 남고 제품 상태 S 로 되돌림", str((n, gone, kept, unit_left)))
    await db.execute(text("DELETE FROM trash_bin WHERE source_table = 'as_cases' AND source_id = :i"), {"i": c3["id"]})
    await db.commit()
    try:
        await units.delete_cases(db, [c3["id"]], None)
        check(False, "이미 지운 기록은 다시 못 지움")
    except svc.DealError:
        await db.rollback()
        check(True, "이미 지운 기록은 다시 못 지움")
    det = await units.unit_detail(db, rs[s1]["id"], today)
    check(det["customer"] == CUST and len(det["cases"]) == 1 and det["shipment"]["date"] == "2026-10-19"
          and det["warranty_state"].startswith("무상 기간 (D-"), "시리얼 한 개 = 고객사·출하·무상 상태·A/S 이력", str(det)[:300])
    found = await units.search_units(db, s1, today)
    check([x["serial"] for x in found] == [s1] and found[0]["as_count"] == 1, "시리얼로 찾기 + A/S 건수")
    await expect_error(units.save_units(db, deal_id, None, qid, 0, []), "출하·A/S 기록이 있으면 통째 지우기 막음", "지울 수 없습니다")
    await db.rollback()

    # 출하 기록을 지우면 묶인 시리얼은 출하 전으로
    await svc.remove_entry(db, deal_id, None, "shipments", 0)
    rs = {r["serial"]: r for r in await units.deal_units(db, deal_id)}
    check(all(r["shipment_index"] is None and r["warranty_start"] is None and r["status"] == "등록" for r in rs.values()),
          "출하 삭제 → 시리얼은 출하 전으로")


asyncio.run(run())
for label, detail in FAIL:
    print(f"FAIL {label} :: {detail}")
print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
sys.exit(1 if FAIL else 0)
