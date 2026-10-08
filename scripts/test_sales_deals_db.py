# -*- coding: utf-8 -*-
"""영업 건 관리 DB 흐름 점검(sales_deals/service.py + api_sales_deals.py).

    python scripts/test_sales_deals_db.py

실제 DB 에 테스트 건을 하나 만들어 수주 → 계산서 → 출하(입금 전 차단) → 입금 → 삭제 흐름을 돌리고, 끝에 지운다.
엑셀 이관 건은 옛 데이터 보관함(trash_bin)으로 옮겼으므로 리스트에 없는지만 본다.
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

from app.database import SessionLocal  # noqa: E402
from app.sales_deals import service as svc  # noqa: E402

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []
TODAY = dt.date.today()
TEST_CUSTOMER = "__테스트_영업건__"


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


async def run() -> None:
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM sales_deal_rollbacks WHERE deal_id IN (SELECT id FROM sales_deals WHERE customer = :c)"),
                         {"c": TEST_CUSTOMER})
        await db.execute(text("DELETE FROM sales_deals WHERE customer = :c"), {"c": TEST_CUSTOMER})
        await db.commit()
        uid = (await db.execute(text("SELECT id FROM users WHERE username = 'alex'"))).scalar()

        data = await svc.list_deals(db, TODAY)
        rows = data["rows"]
        # 예전 번호(S25-·S26-) 건은 옛 데이터 보관함(trash_bin)으로 옮겼다(사용자 2026-10-08) — 리스트에 없어야 함
        kept = (await db.execute(text("SELECT COUNT(*) FROM trash_bin WHERE source_table = 'sales_deals'"))).scalar()
        check(data["counts"]["imported"] == 0 and not any(str(r["deal_no"] or "").startswith(("S25-", "S26-")) for r in rows)
              and kept >= 190, "옛 번호 건은 리스트에 없고 보관함에", f"{data['counts']} 보관 {kept}")
        ups = [r["updated_at"] for r in rows]
        check(ups == sorted(ups, reverse=True), "최근에 바뀐 건이 위(사용자 2026-10-08)")

        # ── 새 건 → 맨 위 ──
        new_id = await svc.create_deal(db, uid, {
            "customer": TEST_CUSTOMER, "contact": "담당 테스트", "owner": "Alex", "title": None,
            "quote_date": TODAY, "vat_included": False, "currency": "KRW",
            "items": [{"name": "TCV1 본체", "unit_price": 4_000_000, "qty": 1},
                      {"name": "Tool Plate", "unit_price": 500_000, "qty": 2}],
            "pay_terms_text": "선금 : 발주 시 50% 잔금 : 납품 전 50% (잔금 입금 확인 후 출고)", "note": "자동 점검용"})
        d = await svc.get_deal(db, new_id, TODAY)
        top = (await svc.list_deals(db, TODAY))["rows"][0]
        check(top["id"] == new_id, "새로 등록한 건이 리스트 맨 위", f"{top['deal_no']} {top['customer']}")
        check(d["quote"]["amount"] == 5_000_000 and d["title"] == "TCV1 본체 외 1", "견적액·건명 자동 계산",
              f"{d['quote']['amount']} {d['title']}")
        check([t["pct"] for t in d["pay_terms"]] == [50, 50] and d["pay_terms"][1]["ship_gate"], "결제 회차 50/50 + 출고 조건")
        check(d["stage"] == "견적" and d["deal_no"].startswith(f"AL{TODAY:%Y%m%d}") and d["display_no"].endswith("-01"),
              "처음 단계는 견적 · 건 번호 = 담당 이니셜+날짜(AL…), 표시는 -01", f"{d['stage']} {d['display_no']}")

        # ── 수주 YES / (예전 실주 기록은 드랍으로 보임) / 되돌리기 ──
        await svc.set_won(db, new_id, uid, True, None)
        check((await svc.get_deal(db, new_id, TODAY))["stage"] == "수주", "수주 확정(YES) → 수주")
        await svc.set_won(db, new_id, uid, False, None)
        check((await svc.get_deal(db, new_id, TODAY))["stage"] == "드랍", "예전 실주 기록(won=False) → 드랍 단계(실주 없앰)")
        await svc.set_won(db, new_id, uid, None, None)
        check((await svc.get_deal(db, new_id, TODAY))["stage"] == "견적", "미확인으로 되돌리기 → 견적")

        # ── 선금 계산서 → 청구 중(일부), 수주 자동 ──
        await svc.add_invoice(db, new_id, uid, {"date": TODAY, "supply": 2_500_000, "vat": None, "issuer_note": None})
        d = await svc.get_deal(db, new_id, TODAY)
        check(d["stage"] == "수주" and d["won"] is True, "계산서 기록 → 수주 자동 확정(청구 중 단계 없음)", f"{d['stage']} {d['won']}")
        check(d["invoices"][0]["vat"] == 250_000 and d["invoice_status"]["partial"], "부가세 10% 자동 · 일부 청구 표시")

        # ── 출하는 입금 확인 없이도 바로 기록(사용자 2026-10-08: 막기·예외 체크 없앰) ──
        await svc.add_shipment(db, new_id, uid, {"date": TODAY, "carrier": "로젠택배", "tracking": "491-1, 491-2",
                                                 "items": "TCV1 1, T.P 2", "purpose": "판매"})
        d = await svc.get_deal(db, new_id, TODAY)
        check(d["stage"] == "출하 후 입금 대기" and d["shipments"][0]["tracking"] == ["491-1", "491-2"], "입금 확인 없이 출하 → 출하 후 입금 대기, 운송장 2개")
        check(not any(a["code"] == "SHIP_BEFORE_PAY" for a in d["alerts"]), "완납 전 출하도 그냥 기록(입금 확인 전 출하 알림 없음)")

        # ── 입금(완납) → 완료, 삭제하면 되돌아감 ──
        await svc.add_payment(db, new_id, uid, {"date": TODAY, "amount": 5_500_000, "full": True, "note": None})
        d = await svc.get_deal(db, new_id, TODAY)
        check(d["stage"] == "완료 (출하·완납)" and not d["alerts"], "완납 → 완료 (출하·완납), 알림 사라짐", f"{d['stage']} {d['alerts']}")
        await svc.remove_entry(db, new_id, uid, "payments", 0)
        d = await svc.get_deal(db, new_id, TODAY)
        check(d["stage"] == "출하 후 입금 대기" and not d["paid_full"], "입금 삭제 → 완납 해제")
        actions = [e["action"] for e in d["events"]]
        check({"create", "won", "invoice", "shipment", "payment", "remove_payment"} <= set(actions)
              and d["events"][0]["user_name"], "변경 이력과 입력한 사람 기록", str(actions[:8]))

        # ── 결제 조건(회차 %) → 회차별 입금 확인 → 출하 → 잔금 확인 → 완료 · 버전 · 드랍(사용자 2026-10-08) ──
        t_id = await svc.create_deal(db, uid, {
            "customer": TEST_CUSTOMER, "contact": None, "owner": "Victor", "title": None, "quote_date": TODAY,
            "vat_included": False, "currency": "KRW", "items": [{"name": "TCV3 본체", "unit_price": 3_000_000, "qty": 1}],
            "pay_terms_text": None, "note": None})
        d = await svc.get_deal(db, t_id, TODAY)
        check(d["deal_no"].startswith(f"VT{TODAY:%Y%m%d}") and d["deal_no"] != (await svc.get_deal(db, new_id, TODAY))["deal_no"],
              "담당별 이니셜(VT)로 번호", d["deal_no"])
        # 같은 담당·같은 날 다른 프로젝트도 같은 번호, 히스토리는 프로젝트마다 -01부터(사용자 2026-10-08: B·C 안 붙임)
        t2 = await svc.create_deal(db, uid, {
            "customer": TEST_CUSTOMER, "contact": None, "owner": "Victor", "title": "다른 프로젝트", "quote_date": TODAY,
            "vat_included": False, "currency": "KRW", "items": [{"name": "MG10", "unit_price": 1_000_000, "qty": 1}],
            "pay_terms_text": None, "note": None})
        d2 = await svc.get_deal(db, t2, TODAY)
        check(d2["deal_no"] == d["deal_no"] == f"VT{TODAY:%Y%m%d}" and d2["display_no"] == f"{d['deal_no']}-01",
              "같은 날 다른 프로젝트도 VT+날짜-01(B 안 붙음)", f"{d['deal_no']} / {d2['display_no']}")
        v0 = d["version"]
        await svc.set_won(db, t_id, uid, True, TODAY)
        try:
            await svc.set_terms(db, t_id, uid, [{"label": "선금", "pct": 30}, {"label": "잔금", "pct": 70}])
            check(False, "명세서 전 결제 조건 거절")
        except svc.DealError:
            await db.rollback()
            check(True, "명세서 전 결제 조건 거절")
        await svc.set_statement(db, t_id, uid, {"date": TODAY, "buyer": {"reg_no": "314-86-54321", "name": "테스트", "ceo": "홍길동",
                                                                         "address": "대구"},
                                                "items": [{"name": "TCV3 툴체인저(ATC)", "spec": "", "qty": 1, "unit": "SET",
                                                           "unit_price": 3_000_000}],
                                                "parts": [{"name": "TCV3", "spec": "본체", "qty": 1, "unit_price": 3_000_000}]})
        st = (await svc.get_deal(db, t_id, TODAY))["statement"]
        check(st["items"][0]["unit"] == "SET" and st["parts"][0]["name"] == "TCV3" and st["totals"]["supply"] == 3_000_000,
              "세트로 묶어 발급: 단위 SET + 구성 품목 보관", str(st["items"]))
        past = (TODAY - dt.timedelta(days=3)).isoformat()
        await svc.set_terms(db, t_id, uid, [{"label": "선금", "pct": 30, "when": "발주 시", "expected_date": past},
                                            {"label": "잔금", "pct": 70, "expected_date": None}])
        d = await svc.get_deal(db, t_id, TODAY)
        check([t["when"] for t in d["flow"]["terms"]] == ["발주 시", ""] and "선금 30%(발주 시)" in (d["flow"]["pay_case_label"] or ""),
              "시점(수기) 저장·상세 표시, 안 적은 회차는 빈칸", str([t["when"] for t in d["flow"]["terms"]]))
        check([t["amount"] for t in d["flow"]["terms"]] == [900_000, 2_100_000] and d["flow"]["total"] == 3_000_000,
              "회차 금액 = 공급가액 × %", str([t["amount"] for t in d["flow"]["terms"]]))
        check(d["flow"]["overdue"] and any(a["code"] == "OVERDUE" for a in d["alerts"]), "예정 입금일 지난 선금 → 알림")
        # 히스토리 번호(사용자 2026-10-08 오후) — 바뀔 때마다 +1: 등록 -01 → 수주 -02 → 명세서 -03 → 결제 조건 -04
        hist = [h["ver_no"] for h in d["history"]]
        check(v0 == 1 and d["display_no"] == f"{d['deal_no']}-04" and hist == [f"{d['deal_no']}-{n:02d}" for n in (4, 3, 2, 1)]
              and d["history"][0]["action"] == "pay_terms" and d["history"][2]["stage"] == "수주",
              "바뀔 때마다 히스토리 번호 +1(최신 먼저, 그때 단계)", f"{v0} → {d['display_no']} {hist}")
        later = TODAY + dt.timedelta(days=2)
        check(svc._ver_prefix(d["deal_no"], later) == f"VT{later:%Y%m%d}",
              "날짜가 바뀌면 그 날짜로(VT+바뀐 날)")
        v_before, no_before = d["version"], d["display_no"]
        await svc.confirm_term(db, t_id, uid, 0, TODAY)
        d = await svc.get_deal(db, t_id, TODAY)
        check(d["version"] == v_before + 1 and d["display_no"] == f"{d['deal_no']}-05", "입금 확인도 번호 +1(-04 → -05)",
              f"{no_before} → {d['display_no']}")
        # 히스토리 되돌리기(사용자 2026-10-08) — 맨 위(-05 입금 확인)를 지우고 -04 때로: 입금 취소·번호 -04, 다시 하면 -05 다시 씀
        try:
            await svc.rollback_history(db, t_id, d["history"][0]["id"], uid)
            check(False, "맨 위 줄을 돌아갈 곳으로 고르면 거절")
        except svc.DealError:
            await db.rollback()
            check(True, "맨 위 줄을 돌아갈 곳으로 고르면 거절")
        back_to = await svc.rollback_history(db, t_id, d["history"][1]["id"], uid)
        d = await svc.get_deal(db, t_id, TODAY)
        check(back_to == d["display_no"] == f"{d['deal_no']}-04" and d["payments"] == []
              and d["flow"]["overdue"] and len(d["history"]) == 4 and d["history"][0]["action"] == "pay_terms",
              "되돌리기: 입금 확인 전(-04) 내용·번호로", f"{back_to} {d['display_no']} {d['payments']}")
        await svc.confirm_term(db, t_id, uid, 0, TODAY)
        d = await svc.get_deal(db, t_id, TODAY)
        check(d["display_no"] == f"{d['deal_no']}-05" and len(d["payments"]) == 1, "되돌린 뒤 다음 변경은 -05부터 다시", d["display_no"])
        saved = (await db.execute(text("SELECT to_ver_no, jsonb_array_length(removed) FROM sales_deal_rollbacks WHERE deal_id = :d"),
                                  {"d": t_id})).all()
        check([tuple(x) for x in saved] == [(f"{d['deal_no']}-04", 1)], "지운 이력·되돌리기 전 내용 보관", str(saved))
        ship_step = next(x for x in d["flow"]["steps"] if x["key"] == "ship")
        check(ship_step["ready"] and not d["flow"]["overdue"] and d["payments"][0]["amount"] == 900_000,
              "선금 확인 → 지남 알림 사라짐(출하는 원래 언제든 가능)")
        try:
            await svc.set_terms(db, t_id, uid, [{"label": "선금", "pct": 40}, {"label": "잔금", "pct": 60}])
            check(False, "확인한 회차 % 변경 거절")
        except svc.DealError:
            await db.rollback()
            check(True, "확인한 회차 % 변경 거절")
        await svc.add_shipment(db, t_id, uid, {"date": TODAY, "carrier": "경동택배", "tracking": "1", "items": "TCV3 1", "purpose": "판매"})
        d = await svc.get_deal(db, t_id, TODAY)
        check(d["display_no"] == f"{d['deal_no']}-06", "출하 → -06", d["display_no"])
        await svc.confirm_term(db, t_id, uid, 1, TODAY)
        d = await svc.get_deal(db, t_id, TODAY)
        check(d["stage"] == "완료 (출하·완납)" and d["paid_full"], "잔금 확인 → 완료", d["stage"])
        await svc.unconfirm_term(db, t_id, uid, 1)
        d = await svc.get_deal(db, t_id, TODAY)
        check(d["stage"] == "출하 후 입금 대기" and not d["paid_full"], "잔금 확인 취소 → 출하 후 입금 대기")
        await svc.set_dropped(db, t_id, uid, True, "고객 일정 취소")
        data = await svc.list_deals(db, TODAY)
        row = next(r for r in data["rows"] if r["id"] == t_id)
        check(row["stage"] == "드랍" and row["drop_reason"] == "고객 일정 취소" and data["rows"][0]["id"] == t_id,
              "드랍 → 드랍 단계, 맨 위(최근 변경)")
        check(set(data["kpi"]) >= {"won_by_quarter", "won_total", "sales", "year"}, "리스트에 분기 수주·올해 매출")
        await svc.set_dropped(db, t_id, uid, False, None)
        check((await svc.get_deal(db, t_id, TODAY))["stage"] == "출하 후 입금 대기", "되살리기 → 원래 단계")

        # ── 이관은 두 번 들어가지 않음 ──
        try:
            await svc.import_preview_rows(db, [], {})
            check(False, "이관 중복 거부")
        except svc.DealError as e:
            check(e.status == 409, "이관 중복 거부(보관함으로 옮긴 이관 건도 셈)")

        await db.execute(text("DELETE FROM sales_deal_rollbacks WHERE deal_id IN (SELECT id FROM sales_deals WHERE customer = :c)"),
                         {"c": TEST_CUSTOMER})
        await db.execute(text("DELETE FROM sales_deals WHERE customer = :c"), {"c": TEST_CUSTOMER})
        await db.commit()
        left = (await db.execute(text("SELECT COUNT(*) FROM sales_deals WHERE customer = :c"), {"c": TEST_CUSTOMER})).scalar()
        check(left == 0, "테스트 건 정리")


asyncio.run(run())
for label in PASS:
    print("PASS", label)
for label, detail in FAIL:
    print("FAIL", label, "—", detail)
print(f"\n{len(PASS)} PASS / {len(FAIL)} FAIL")
sys.exit(1 if FAIL else 0)
