# -*- coding: utf-8 -*-
"""출하·S/N 경계 상황 점검(2026-10-23 커밋 — 출하 기록 작성 때 품목별 S/N 등록·나눠 출하·출하 뒤 등록·출하 고치기).

    MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -e PYTHONPATH=/app -e PYTHONIOENCODING=utf-8 \\
        -v ./backend/app:/app/app -v ./scripts:/scripts backend python /scripts/test_ship_sn_edge.py

화면에서 한 번 눌러 봐서는 잘 안 드러나는 경우만 모았다 — 화면(deal-flow.tsx ShipForm·unit-register.tsx SnPanel)이
서버에 보내는 값(unit_ids 등)을 그대로 흉내 내서, 개발 DB(agent_ai_dev)에 테스트 건을 만들고 돌린 뒤 지운다.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import math
import os
import sys

from sqlalchemy import text

from app import migrations
from app.database import SessionLocal
from app.sales_deals import service as svc
from app.sales_deals import units

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []
CUST = "__테스트_출하SN경계__"
DAY1, DAY2 = dt.date(2026, 10, 1), dt.date(2026, 10, 20)
LINES = [{"name": "TCV3 툴체인저", "qty": 2, "unit_price": 5_000_000},
         {"name": "TCV3 Tool Plate", "qty": 1, "unit_price": 400_000}]


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


async def cleanup(db) -> None:
    ids = (await db.execute(text("SELECT id FROM sales_deals WHERE customer = :c"), {"c": CUST})).scalars().all()
    if ids:
        await db.execute(text("DELETE FROM as_cases WHERE deal_id = ANY(:i)"), {"i": ids})
        await db.execute(text("DELETE FROM product_units WHERE deal_id = ANY(:i)"), {"i": ids})
        await db.execute(text("DELETE FROM product_quotes WHERE deal_id = ANY(:i)"), {"i": ids})
        await db.execute(text("DELETE FROM sales_deals WHERE id = ANY(:i)"), {"i": ids})
    await db.commit()


async def new_deal(db, tag: str, shipments: list[dict] | None = None, lines: list[dict] = LINES) -> tuple[int, int]:
    no = f"QT-TE2026102{tag}"
    total = sum(ln["qty"] * ln["unit_price"] for ln in lines)
    quote = {"no": no, "date": "2026-10-01", "amount": total, "currency": "KRW", "items": svc.snapshot_items(lines), "revision": 1}
    deal_id = (await db.execute(text(
        "INSERT INTO sales_deals (deal_no, kind, customer, owner, title, base_date, quote, won, shipments) VALUES "
        "(:no, 'quote', :c, 'Test', 'TCV3', '2026-10-01', CAST(:q AS jsonb), TRUE, CAST(:s AS jsonb)) RETURNING id"),
        {"no": no, "c": CUST, "q": json.dumps(quote, ensure_ascii=False),
         "s": json.dumps(shipments or [], ensure_ascii=False)})).scalar_one()
    hist = [{"revision": 0, "quote_no": no, "lines": lines, "total": total, "currency": "KRW", "issued_at": "2026-10-01T01:00:00+00:00"}]
    qid = (await db.execute(text(
        "INSERT INTO product_quotes (deal_id, status, quote_no, customer, revision, history, total) "
        "VALUES (:d, 'issued', :no, :c, 0, CAST(:h AS jsonb), :t) RETURNING id"),
        {"d": deal_id, "no": no, "c": CUST, "h": json.dumps(hist, ensure_ascii=False), "t": total})).scalar_one()
    await db.commit()
    return deal_id, qid


async def register(db, deal_id: int, qid: int, line_no: int, serials: list[str], keep: list[dict] | None = None,
                   shipment_index: int | None = None) -> dict:
    """SnPanel 저장과 같은 요청(partial, 이 줄만)."""
    us = [*(keep or []), *({"serial": s, "warranty_months": 12} for s in serials)]
    return await units.save_units(db, deal_id, None, qid, 0, [{"line_no": line_no, "units": us}],
                                  partial=True, shipment_index=shipment_index)


def by_serial(v: dict) -> dict[str, dict]:
    return {u["serial"]: u for ln in v["lines"] for u in ln["units"]}


def screen_unit_ids(v: dict, ship_lines: list[dict], edit_index: int | None = None) -> list[int]:
    """deal-flow.tsx ShipForm 의 unitIds 계산 그대로 — 품목마다 (고치는 출하에 묶인 것 먼저 + 안 나간 것) 중 출하 수량만큼
    + 이번 품목 칸에 없는 줄에서 고치는 출하에 묶인 것."""
    need, _ = units.ship_need(ship_lines)
    out: list[int] = []
    for ln in v["lines"]:
        if ln["line_no"] in need:
            us = [u for u in ln["units"] if u["shipment_index"] is None or u["shipment_index"] == edit_index]
            mine = [u for u in us if edit_index is not None and u["shipment_index"] == edit_index]
            out += [u["id"] for u in (mine + [u for u in us if u not in mine])][:need[ln["line_no"]]]
        elif edit_index is not None:
            out += [u["id"] for u in ln["units"] if u["shipment_index"] == edit_index]
    return list(dict.fromkeys(out))


def ship(day: dt.date, lines: list[dict], unit_ids: list[int] | None, **kw) -> dict:
    return {"date": day, "carrier": "택배", "tracking": kw.get("tracking", ""), "items": "", "purpose": "판매",
            "lines": [{"name": LINES[ln["line_no"]]["name"] if ln.get("line_no") is not None else ln["name"], "photos": [], **ln}
                      for ln in lines],
            "unit_ids": unit_ids}


async def guarded(db, label: str, coro) -> None:
    """한 경우가 터져도 나머지는 돈다."""
    try:
        await coro
    except Exception as e:                                  # noqa: BLE001 — 예기치 않은 오류도 실패로 기록
        await db.rollback()
        check(False, label, f"{type(e).__name__}: {e}")


# ── E1) 나눠 출하 — 미리 적어 둔 S/N 이 출하 수량보다 많으면 ─────────────────
async def e1_overbind_new(db, tag: str) -> None:
    deal_id, qid = await new_deal(db, "1")
    v = await register(db, deal_id, qid, 0, [f"E1-{tag}-A", f"E1-{tag}-B"])      # 수주 수량 2개를 미리 다 적어 둠(10/23 이전 방식 데이터도 이 모양)
    lines = [{"line_no": 0, "qty": 1}]                                          # 이번엔 1개만 출하
    await svc.add_shipment(db, deal_id, None, ship(DAY1, lines, screen_unit_ids(v, lines)))
    v = await units.issue_units(db, deal_id, qid, 0)
    n0 = sum(1 for u in by_serial(v).values() if u["shipment_index"] == 0)
    check(n0 == 1, "E1-a 1개만 출하했는데 S/N 2개가 모두 첫 출하로 묶이지 않아야 함(무상 기간도 2개 다 시작됨)", f"첫 출하에 묶인 수 {n0}")
    try:
        await svc.add_shipment(db, deal_id, None, ship(DAY2, lines, screen_unit_ids(v, lines)))
        check(True, "E1-b 나머지 1개 두 번째 출하가 막히지 않아야 함")
    except svc.DealError as e:
        await db.rollback()
        check(False, "E1-b 나머지 1개 두 번째 출하가 막히지 않아야 함", str(e))


# ── E2) 이미 남긴 출하에 S/N 등록 — 그 줄의 안 묶인 S/N 이 전부 그 출하로 ────────
async def e2_overbind_existing(db, tag: str) -> None:
    sh = [{"date": DAY1.isoformat(), "items": "TCV3 툴체인저 × 1", "tracking": [], "photos": [],
           "lines": [{"name": "TCV3 툴체인저", "qty": 1, "photos": [], "line_no": 0}]}]
    deal_id, qid = await new_deal(db, "2", sh)
    # 두 번째 출하를 쓰면서 그 몫 S/N 을 먼저 적어 둠(출하 작성 칸의 [S/N 등록] 은 바로 저장된다)
    v = await register(db, deal_id, qid, 0, [f"E2-{tag}-NEXT"])
    nxt = by_serial(v)[f"E2-{tag}-NEXT"]
    # 그 사이 상세 탭의 첫 출하 카드에서 [S/N 등록] — 패널은 안 묶인 NEXT 를 이미 채운 칸으로 보여 주고, 사용자는 FIRST 를 더 적어 저장
    v = await register(db, deal_id, qid, 0, [f"E2-{tag}-FIRST"],
                       keep=[{"id": nxt["id"], "serial": nxt["serial"], "warranty_months": 12}], shipment_index=0)
    got = by_serial(v)
    check(got[f"E2-{tag}-NEXT"]["shipment_index"] is None,
          "E2 첫 출하(1개)에 S/N 등록 시 다음 출하 몫으로 적어 둔 S/N 까지 첫 출하로 묶이면 안 됨",
          str({k: u["shipment_index"] for k, u in got.items()}))


# ── E3) 남긴 출하 고치기(운송장만 바꿈) — 안 묶인 S/N 을 끌어옴 ────────────────
async def e3_edit_pulls_unbound(db, tag: str) -> None:
    deal_id, qid = await new_deal(db, "3")
    v = await register(db, deal_id, qid, 0, [f"E3-{tag}-A"])
    lines = [{"line_no": 0, "qty": 1}]
    await svc.add_shipment(db, deal_id, None, ship(DAY1, lines, screen_unit_ids(v, lines)))
    v = await register(db, deal_id, qid, 0, [f"E3-{tag}-B"],
                       keep=[{"id": by_serial(await units.issue_units(db, deal_id, qid, 0))[f"E3-{tag}-A"]["id"],
                              "serial": f"E3-{tag}-A", "warranty_months": 12}])   # 두 번째 출하 몫 B 를 미리 적음
    v = await units.issue_units(db, deal_id, qid, 0)
    p = ship(DAY1, lines, screen_unit_ids(v, lines, edit_index=0), tracking="123-456")
    await svc.update_shipment(db, deal_id, None, 0, p)
    v = await units.issue_units(db, deal_id, qid, 0)
    b = by_serial(v)[f"E3-{tag}-B"]
    check(b["shipment_index"] is None, "E3 첫 출하의 운송장만 고쳐도 아직 안 나간 S/N(B)이 첫 출하로 묶이면 안 됨",
          f"B shipment_index={b['shipment_index']} warranty_start={b['warranty_start']}")


# ── E4) 한 출하에 같은 품목 줄이 두 번(예전 기록 고치기·이름 짝) — 수량 합으로 확인하는지 ──
async def e4_dup_line(db, tag: str) -> None:
    deal_id, qid = await new_deal(db, "4")
    v = await register(db, deal_id, qid, 0, [f"E4-{tag}-A"])
    a = by_serial(v)[f"E4-{tag}-A"]["id"]
    lines = [{"line_no": 0, "qty": 1}, {"line_no": 0, "qty": 1}]
    try:
        await svc.add_shipment(db, deal_id, None, ship(DAY1, lines, [a]))
        check(False, "E4 같은 품목 줄 두 번(합 2개)인데 S/N 1개로 출하가 통과하면 안 됨", "통과함")
    except svc.DealError:
        await db.rollback()
        check(True, "E4 같은 품목 줄 두 번(합 2개)인데 S/N 1개로 출하가 통과하면 안 됨")


# ── E5) 서버 방어 — 화면을 거치지 않고(오래된 화면·직접 호출) 넘치게 보내도 막는지, 고치기로 수량을 줄이면 풀리는지 ──
async def e5_server_guards(db, tag: str) -> None:
    deal_id, qid = await new_deal(db, "5")
    v = await register(db, deal_id, qid, 0, [f"E5-{tag}-A", f"E5-{tag}-B"])
    v = await register(db, deal_id, qid, 1, [f"E5-{tag}-P"])
    ids = by_serial(v)
    try:
        await svc.add_shipment(db, deal_id, None, ship(DAY1, [{"line_no": 0, "qty": 1}], [ids[f"E5-{tag}-A"]["id"], ids[f"E5-{tag}-B"]["id"]]))
        check(False, "E5-a 출하 1개에 S/N 2개를 보내면 서버가 막음", "통과함")
    except svc.DealError as e:
        await db.rollback()
        check("많습니다" in str(e), "E5-a 출하 1개에 S/N 2개를 보내면 서버가 막음", str(e))
    try:
        await svc.add_shipment(db, deal_id, None, ship(DAY1, [{"line_no": 0, "qty": 1}], [ids[f"E5-{tag}-A"]["id"], ids[f"E5-{tag}-P"]["id"]]))
        check(False, "E5-b 이번 출하에 없는 품목의 S/N 을 보내면 서버가 막음", "통과함")
    except svc.DealError as e:
        await db.rollback()
        check("없는 품목" in str(e), "E5-b 이번 출하에 없는 품목의 S/N 을 보내면 서버가 막음", str(e))
    # 2개 출하 → 고치기로 1개로 줄이면 뒤 S/N 하나는 출하 전으로
    two = [{"line_no": 0, "qty": 2}]
    await svc.add_shipment(db, deal_id, None, ship(DAY1, two, screen_unit_ids(v, two)))
    v = await units.issue_units(db, deal_id, qid, 0)
    one = [{"line_no": 0, "qty": 1}]
    await svc.update_shipment(db, deal_id, None, 0, ship(DAY1, one, screen_unit_ids(v, one, edit_index=0)))
    got = by_serial(await units.issue_units(db, deal_id, qid, 0))
    check([got[f"E5-{tag}-{k}"]["shipment_index"] for k in "AB"] == [0, None],
          "E5-c 출하 수량을 2→1로 고치면 S/N 하나만 남고 하나는 출하 전으로", str({k: u["shipment_index"] for k, u in got.items()}))
    # 남긴 출하에 출하 수량보다 많이 새로 적으면 막음
    sh = [{"date": DAY1.isoformat(), "items": "", "tracking": [], "photos": [],
           "lines": [{"name": "TCV3 툴체인저", "qty": 1, "photos": [], "line_no": 0}]}]
    d2, q2 = await new_deal(db, "8", sh)
    try:
        await register(db, d2, q2, 0, [f"E5-{tag}-X", f"E5-{tag}-Y"], shipment_index=0)
        check(False, "E5-d 출하 1개짜리 기록에 S/N 2개를 새로 적으면 막음", "통과함")
    except svc.DealError as e:
        await db.rollback()
        check("더 적을 수 있습니다" in str(e), "E5-d 출하 1개짜리 기록에 S/N 2개를 새로 적으면 막음", str(e))
    # 줄 번호 없는 예전 세트 출하 — 수주 수량 − 다른 출하만큼 적을 수 있다
    sh = [{"date": DAY1.isoformat(), "items": "TCV3 1 SET", "tracking": [], "photos": [],
           "lines": [{"name": "TCV3 세트", "qty": None, "qty_text": "1 SET", "photos": []}]}]
    d3, q3 = await new_deal(db, "9", sh)
    v = await register(db, d3, q3, 0, [f"E5-{tag}-S1", f"E5-{tag}-S2"], shipment_index=0)
    check(all(u["shipment_index"] == 0 for u in by_serial(v).values()), "E5-e 예전 세트 출하에는 수주 수량(2개)까지 묶임",
          str({k: u["shipment_index"] for k, u in by_serial(v).items()}))


# ── E6) 가운데 출하 기록 삭제 — 뒤 출하의 S/N 번호가 당겨지는지 ──────────────
async def e6_remove_middle(db, tag: str) -> None:
    deal_id, qid = await new_deal(db, "6")
    v = await register(db, deal_id, qid, 0, [f"E6-{tag}-A"])
    l0 = [{"line_no": 0, "qty": 1}]
    await svc.add_shipment(db, deal_id, None, ship(DAY1, l0, screen_unit_ids(v, l0)))
    v = await register(db, deal_id, qid, 1, [f"E6-{tag}-P"])
    l1 = [{"line_no": 1, "qty": 1}]
    await svc.add_shipment(db, deal_id, None, ship(DAY2, l1, screen_unit_ids(v, l1)))
    await svc.remove_entry(db, deal_id, None, "shipments", 0)
    got = by_serial(await units.issue_units(db, deal_id, qid, 0))
    check(got[f"E6-{tag}-A"]["shipment_index"] is None and got[f"E6-{tag}-P"]["shipment_index"] == 0
          and got[f"E6-{tag}-P"]["warranty_start"] == DAY2.isoformat(),
          "E6 첫 출하 삭제 → 그 S/N 은 출하 전, 뒤 출하 S/N 은 0번으로 당겨지고 무상 시작일 유지",
          str({k: (u["shipment_index"], u["warranty_start"]) for k, u in got.items()}))


# ── E7) 거래명세서 전 결제 조건 → 입금 확인 ─────────────────────────────────
async def e7_terms_before_statement(db, tag: str) -> None:
    deal_id, _ = await new_deal(db, "7")
    await svc.set_terms(db, deal_id, None, [{"label": "선금", "pct": 30}, {"label": "잔금", "pct": 70}])
    await svc.confirm_term(db, deal_id, None, 0, DAY1)
    d = await svc.get_deal(db, deal_id, DAY2)
    t = d["flow"]["terms"]
    check([x["amount"] for x in t] == [3_120_000, 7_280_000] and t[0]["confirmed"],
          "E7 명세서 전 결제 조건 → 회차 금액 = 견적 공급가액 × %, 선금 입금 확인 가능", str(t))


# ── 운영 복사본 데이터 점검(읽기만) — 이미 쌓인 기록에 같은 문제가 있는지 ─────────
async def data_scan(db) -> list[str]:
    out = []
    rs = (await db.execute(text(
        "SELECT s.id, s.deal_no, s.customer, s.shipments, u.shipment_index, u.line_no, u.line_name "
        "FROM product_units u JOIN sales_deals s ON s.id = u.deal_id WHERE u.shipment_index IS NOT NULL AND s.customer <> :c"),
        {"c": CUST})).mappings().all()
    per: dict[tuple, int] = {}
    meta: dict[tuple, tuple] = {}
    for r in rs:
        k = (r["id"], r["shipment_index"], r["line_no"])
        per[k] = per.get(k, 0) + 1
        ships = r["shipments"] if not isinstance(r["shipments"], str) else json.loads(r["shipments"])
        meta[k] = (r["deal_no"], r["customer"], r["line_name"], ships)
    for k, n in per.items():
        no, cust, lname, ships = meta[k]
        if k[1] >= len(ships):
            out.append(f"{no} {cust}: S/N 이 없는 출하 번호({k[1]})에 묶여 있음")
            continue
        sl = [ln for ln in ships[k[1]].get("lines") or [] if ln.get("line_no") == k[2]]
        if sl:
            q = sum(math.ceil(ln.get("qty") or 0) for ln in sl)
            if n > q:
                out.append(f"{no} {cust}: {k[1] + 1}번째 출하 '{(lname or '').splitlines()[0][:30]}' 출하 {q}개인데 S/N {n}개 묶임")
    rs = (await db.execute(text("SELECT id, deal_no, customer, shipments FROM sales_deals WHERE jsonb_array_length(shipments) > 0"))).mappings().all()
    for r in rs:
        ships = r["shipments"] if not isinstance(r["shipments"], str) else json.loads(r["shipments"])
        for i, s in enumerate(ships):
            for ln in s.get("lines") or []:
                if (ln.get("qty") or 0) >= 12 and ln.get("line_no") is not None:
                    out.append(f"{r['deal_no']} {r['customer']}: {i + 1}번째 출하 '{ln['name'][:30]}' 수량 {ln['qty']} (기간 수량 의심)")
    return out


async def run() -> None:
    assert os.environ.get("DATABASE_URL", "").endswith("/agent_ai_dev"), "개발 DB(agent_ai_dev)에서만 돌린다"
    tag = dt.datetime.now().strftime("%H%M%S")
    async with SessionLocal() as db:
        await migrations.run_migrations(db)
        await cleanup(db)
        try:
            for name, fn in (("E1", e1_overbind_new), ("E2", e2_overbind_existing), ("E3", e3_edit_pulls_unbound),
                             ("E4", e4_dup_line), ("E5", e5_server_guards), ("E6", e6_remove_middle),
                             ("E7", e7_terms_before_statement)):
                await guarded(db, f"{name} 예기치 않은 오류", fn(db, tag))
            found = await data_scan(db)
            print(f"[데이터 점검] {len(found)}건")
            for x in found[:30]:
                print("  -", x)
        finally:
            await db.rollback()
            await cleanup(db)


asyncio.run(run())
for label in PASS:
    print("PASS", label)
for label, detail in FAIL:
    print(f"FAIL {label} :: {detail}")
print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
sys.exit(1 if FAIL else 0)
