# -*- coding: utf-8 -*-
"""제품 재고 관리(사용자 2026-11-19) — 구매 입고 기록 → 재고 줄(제품 한 대 = 한 줄) 자동 맞춤.

판매용·자체 개발용 줄 수, 처음 상태 '대기 중', 구분·상태·메모·영업팀 담당자 고치기와 이력, 입고 기록을 고쳐도 사용자가 정한 칸은 유지,
수량을 줄이거나 기록을 지우면 줄도 지움, 담당 바꾸면 자체 개발용 담당자 따라감(재고에서 고친 줄은 그대로), 잘못된 값 거절,
판매용엔 S/N 경고(알림) 없음, 구매 건 지우면 재고도 지움.
dev DB 전용, 만든 것은 끝에 지운다:
  docker compose run --rm -T --no-deps -e PYTHONPATH=/app -v ./backend/app:/app/app -v ./scripts:/scripts backend python /scripts/test_stock.py
"""
import asyncio
import os
import sys

assert "agent_ai_dev" in os.environ.get("DATABASE_URL", ""), "NOT DEV DB - abort"
from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402

SUP = "__QA_재고__"
res: list[bool] = []


def check(ok: bool, name: str, *more) -> None:
    res.append(bool(ok))
    print("PASS" if ok else "FAIL", name, *more)


async def expect_err(coro, name: str, part: str) -> None:
    try:
        await coro
        check(False, name, "오류가 나야 함")
    except Exception as e:  # noqa: BLE001
        check(part in str(e), name, str(e)[:70])


async def cleanup() -> None:
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM purchases WHERE supplier LIKE :s"), {"s": SUP + "%"})
        await db.execute(text("DELETE FROM doc_numbers WHERE issuer = 'QK'"))
        await db.commit()


def mine(data: dict, pid: int) -> list[dict]:
    return [r for r in data["rows"] if r["purchase_id"] == pid]


async def run(uid: int) -> None:
    from app.purchases import service as ps
    from app.purchases import stock

    today = ps.kst_today()
    async with SessionLocal() as db:
        pid = await ps.create(db, uid, {"supplier": SUP, "title": "", "owner": "Mason", "note": "", "reason": "", "region": "overseas"})
        await ps.skip_quote(db, pid, uid, "QA", True)
        await ps.approve(db, pid, uid, "QA", today)
        dr = await ps.po_draft(db, pid, {})
        hd = {**dr["header"], "issuer": {**dr["header"]["issuer"], "name": "QA", "attn": "QA"}}
        await ps.issue_po(db, pid, uid, "QA", {"initials": "QK", "expected_date": "", "header": hd,
                                               "items": [{"description": "MG10", "qty": 3, "price": 10}, {"description": "Cable", "qty": 2, "price": 1}]})
        await ps.start_shipping(db, pid, uid, "QA", today, "")

        # 판매용 MG10 3대 — S/N 없이
        await ps.add_receipt(db, pid, uid, "QA", {"date": today, "purpose": "sale", "lines": [
            {"line_no": 0, "qty": 3, "customer": "LG CNS", "owner": "Lucas"}]})
        sale = mine(await stock.list_items(db, "sale"), pid)
        check(len(sale) == 3 and all(r["status"] == "대기 중" and r["customer"] == "LG CNS" and r["owner"] == "Lucas" for r in sale),
              "판매용 3대 = 3줄, 처음 '대기 중'", [(r["status"], r["customer"]) for r in sale])
        check(all(r["alerts"] == [] for r in sale), "판매용 S/N 없어도 알림 없음")
        check(all(r["maker"] == SUP and not r["maker_custom"] for r in sale), "제조사 기본 = 구매 공급사", sale[0]["maker"])
        await stock.update_items(db, [r["id"] for r in sale], uid, {"maker": "Deep Robotics"})
        rows = mine(await stock.list_items(db, "sale"), pid)
        check(all(r["maker"] == "Deep Robotics" and r["maker_custom"] for r in rows), "제조사 고치기", rows[0]["maker"])
        check((await stock.history(db, sale[0]["id"]))[0] == {**(await stock.history(db, sale[0]["id"]))[0],
              "label": "제조사", "detail": {"from": SUP, "to": "Deep Robotics"}}, "제조사 이력")
        await stock.update_items(db, [r["id"] for r in sale], uid, {"maker": SUP})
        check(not any(r["maker_custom"] for r in mine(await stock.list_items(db, "sale"), pid)), "공급사와 같게 적으면 '공급사 따름'으로")
        await stock.update_items(db, [r["id"] for r in sale], uid, {"maker": "Deep Robotics"})
        check(all(r["last"] and r["last"]["label"] == "입고" for r in sale), "최근 변경 = 입고", sale[0]["last"])

        # 자체 개발용 Cable 2개 — 두 줄 S/N, 1개만 적음
        await ps.add_receipt(db, pid, uid, "QA", {"date": today, "purpose": "dev", "lines": [
            {"line_no": 1, "qty": 2, "sw_owner": "Kim", "photos": ["c" * 32 + ".jpg"],
             "units": [{"maker_sn": "MK-1", "und_sn": "QK-UND-1", "photos": ["d" * 32 + ".jpg"]}]}]})
        dev = mine(await stock.list_items(db, "dev"), pid)
        check(dev[0]["line_photos"] == ["c" * 32 + ".jpg"] and dev[0]["photos"] == ["d" * 32 + ".jpg"] and dev[1]["photos"] == []
              and dev[1]["line_photos"] == ["c" * 32 + ".jpg"], "재고 줄에 입고 사진 — 전체 사진 + 그 대의 S/N 사진",
              [(r["line_photos"], r["photos"]) for r in dev])
        check(len(dev) == 2 and dev[0]["maker_sn"] == "MK-1" and dev[0]["und_sn"] == "QK-UND-1" and dev[1]["maker_sn"] == ""
              and all(r["sw_owner"] == "Kim" and r["owner"] == "Mason" for r in dev),
              "자체 개발용 2줄 — S/N 두 줄·S/W 담당, 영업팀 담당 = 구매 건 담당", [(r["maker_sn"], r["owner"]) for r in dev])

        # 재고에서 정하는 칸
        a = sale[0]["id"]
        await stock.update_item(db, a, uid, {"category": "6축"})
        await stock.update_item(db, a, uid, {"status": "수리 필요", "status_note": "모터 소음"})
        await expect_err(stock.update_item(db, a, uid, {"status": "없는 상태"}), "없는 상태 거절", "상태")
        await db.rollback()
        await expect_err(stock.update_item(db, a, uid, {"category": "로봇팔"}), "없는 구분 거절", "구분")
        await db.rollback()
        d0 = dev[0]["id"]
        await stock.update_item(db, d0, uid, {"owner": "Lucas"})
        # S/W 담당자도 재고에서 고친다(사용자 2026-11-19) — 자체 개발용만
        await stock.update_item(db, d0, uid, {"sw_owner": "Park"})
        row = next(r for r in mine(await stock.list_items(db, "dev"), pid) if r["id"] == d0)
        check(row["sw_owner"] == "Park" and row["last"]["label"] == "S/W 담당자", "재고에서 S/W 담당자 고치기", (row["sw_owner"], row["last"]))
        await expect_err(stock.update_item(db, a, uid, {"sw_owner": "Park"}), "판매용엔 S/W 담당자 없음", "자체 개발용")
        await db.rollback()
        await stock.update_item(db, d0, uid, {"sw_owner": "Kim"})
        row = next(r for r in mine(await stock.list_items(db, "sale"), pid) if r["id"] == a)
        check(row["category"] == "6축" and row["status"] == "수리 필요" and row["status_note"] == "모터 소음"
              and row["last"]["label"] == "상태", "구분·상태·메모 저장, 최근 변경 = 상태", row["last"])
        h = await stock.history(db, a)
        check([x["label"] for x in h] == ["상태", "구분", "제조사", "제조사", "제조사", "입고"] and h[0]["detail"]["to"] == "수리 필요", "변경 이력", [x["label"] for x in h])

        # 여러 줄 한 번에(판매용 묶음 줄 — '3개 중 2개를 사용 중으로', 사용자 2026-11-19)
        others = [r["id"] for r in sale if r["id"] != a]
        check(all(r["line_key"] == "po:0" for r in sale) and sorted(r["unit_index"] for r in sale) == [0, 1, 2], "묶음 열쇠(line_key·unit_index)")
        n = await stock.update_items(db, others, uid, {"status": "사용 중", "category": "부품"})
        rows = {r["id"]: r for r in mine(await stock.list_items(db, "sale"), pid)}
        check(n == 2 and all(rows[i]["status"] == "사용 중" and rows[i]["category"] == "부품" for i in others)
              and rows[a]["status"] == "수리 필요" and rows[a]["status_note"] == "모터 소음", "여러 줄 한 번에 — 고른 것만, 메모 안 보내면 그대로",
              [(rows[i]["status"], rows[i]["category"]) for i in rows])
        await expect_err(stock.update_items(db, others + [999999999], uid, {"status": "대기 중"}), "없는 줄이 섞이면 아무것도 안 바꿈", "찾을 수 없")
        await db.rollback()
        check(all(r["status"] == "사용 중" for r in mine(await stock.list_items(db, "sale"), pid) if r["id"] in others), "되돌림 확인")
        await stock.update_items(db, others, uid, {"category": ""})

        # 재고 표시 이름 — 발주서 품목명(name)은 그대로, 비우거나 품목명과 같으면 '' (사용자 2026-11-19)
        await stock.update_items(db, [r["id"] for r in sale], uid, {"display_name": "MG10 협동로봇"})
        rows = mine(await stock.list_items(db, "sale"), pid)
        check(all(r["display_name"] == "MG10 협동로봇" and r["name"] == "MG10" for r in rows), "표시 이름 저장 — 발주서 이름 그대로",
              [(r["name"], r["display_name"]) for r in rows])
        h = await stock.history(db, a)
        check(h[0]["label"] == "표시 이름" and h[0]["detail"] == {"from": "MG10", "to": "MG10 협동로봇"}, "표시 이름 이력", h[0])
        await stock.update_item(db, a, uid, {"display_name": "MG10"})
        check(next(r for r in mine(await stock.list_items(db, "sale"), pid) if r["id"] == a)["display_name"] == "",
              "품목명과 같은 이름 = 표시 이름 지움")

        # 재고에서 고객사·담당자·입고일 고치기(사용자 2026-11-19) — 그 입고 기록을 다시 저장하면 입고 기록 값으로 돌아감(아래 고침에서 확인)
        ids = [r["id"] for r in sale]
        await stock.update_items(db, ids, uid, {"customer": "재고고객", "owner": "재고담당", "received_on": "2026-01-02"})
        rows = mine(await stock.list_items(db, "sale"), pid)
        check(all(r["customer"] == "재고고객" and r["owner"] == "재고담당" and r["received_on"] == "2026-01-02" for r in rows),
              "재고에서 고객사·담당자·입고일 고치기", [(r["customer"], r["received_on"]) for r in rows])
        h = await stock.history(db, ids[0])
        check({"판매 고객사", "담당자", "입고일"} <= {x["label"] for x in h}, "고친 내용 이력", [x["label"] for x in h][:4])
        await expect_err(stock.update_items(db, ids, uid, {"received_on": "어제"}), "입고일은 날짜만", "달력")
        await db.rollback()

        # 입고 기록을 고쳐도(고객사 바꾸고 수량 3→2) 사용자가 정한 칸은 그대로, 줄어든 대는 지움
        recs = (await ps.get(db, pid, today))["receipt"]["records"]
        si = next(i for i, x in enumerate(recs) if x["purpose"] == "sale")
        await ps.update_receipt(db, pid, uid, "QA", si, {"date": today, "lines": [{"line_no": 0, "qty": 2, "customer": "Hyundai", "owner": "Lucas", "no_sn": True}],
                                                         "expect": {"at": recs[si]["at"]}})
        sale = mine(await stock.list_items(db, "sale"), pid)
        kept = next((r for r in sale if r["id"] == a), None)
        check(len(sale) == 2 and kept and kept["category"] == "6축" and kept["status"] == "수리 필요" and kept["customer"] == "Hyundai",
              "입고 기록 고침 — 고객사 따라감, 구분·상태 유지, 3→2대", [(r["customer"], r["status"]) for r in sale])
        check(any(r["display_name"] == "MG10 협동로봇" for r in sale), "입고 기록을 고쳐도 표시 이름 유지", [r["display_name"] for r in sale])
        check(all(r["owner"] == "Lucas" and r["received_on"] == today.isoformat() for r in sale), "입고 기록을 다시 저장하면 입고 기록 값으로",
              [(r["owner"], r["received_on"]) for r in sale])
        # 입고 기록에서 구분 고르기(사용자 2026-11-19) — 고르면 그 값, 목록에 없는 구분은 막음
        recs = (await ps.get(db, pid, today))["receipt"]["records"]
        await ps.update_receipt(db, pid, uid, "QA", si, {"date": today, "lines": [{"line_no": 0, "qty": 2, "customer": "Hyundai", "owner": "Lucas",
                                                                                     "no_sn": True, "category": "AMR"}],
                                                         "expect": {"at": recs[si]["at"]}})
        sale = mine(await stock.list_items(db, "sale"), pid)
        check(all(r["category"] == "AMR" for r in sale), "입고 기록에서 고른 구분이 재고에", [r["category"] for r in sale])
        recs = (await ps.get(db, pid, today))["receipt"]["records"]
        await expect_err(ps.update_receipt(db, pid, uid, "QA", si, {"date": today, "lines": [{"line_no": 0, "qty": 2, "category": "로봇팔"}],
                                                                    "expect": {"at": recs[si]["at"]}}), "입고 — 없는 구분 거절", "구분")
        await db.rollback()
        await stock.update_items(db, [r["id"] for r in sale], uid, {"category": "6축"})     # 아래 점검이 보는 원래 구분으로
        check(all(r["no_sn"] for r in sale), "판매용도 [S/N 없는 품목] 표시가 재고에 저장(묶어 보이기)", [r["no_sn"] for r in sale])
        check(kept and kept["last"]["label"] == "입고 기록 반영" and kept["last"]["detail"].get("고객사") == "Hyundai",
              "최근 변경 = 입고 기록 반영", kept and kept["last"])

        # 구매 건 담당 바꾸면 — 재고에서 고친 줄(d0)은 그대로, 나머지는 따라감
        await ps.set_info(db, pid, uid, {"supplier": SUP, "title": "", "owner": "Chris", "note": "", "region": "overseas"})
        dev = {r["id"]: r for r in mine(await stock.list_items(db, "dev"), pid)}
        other = next(r for i, r in dev.items() if i != d0)
        check(dev[d0]["owner"] == "Lucas" and other["owner"] == "Chris", "영업팀 담당 — 재고에서 고친 값 유지, 나머지는 구매 건 담당",
              (dev[d0]["owner"], other["owner"]))

        # S/N 나중에 채움 → 재고에도
        di = next(i for i, x in enumerate((await ps.get(db, pid, today))["receipt"]["records"]) if x["purpose"] == "dev")
        await ps.set_receipt_serials(db, pid, uid, di, [{"line_index": 0, "units": [
            {"maker_sn": "MK-1", "und_sn": "QK-UND-1"}, {"maker_sn": "MK-2", "und_sn": "QK-UND-2"}]}])
        dev = mine(await stock.list_items(db, "dev"), pid)
        check(sorted(r["und_sn"] for r in dev) == ["QK-UND-1", "QK-UND-2"], "S/N 나중에 적으면 재고에도", [r["und_sn"] for r in dev])

        # ── 제품 S/N 관리 — 자체 개발 제품도 S/N 표에 'SD · 개발'로(사용자 2026-11-19) ──
        from app.sales_deals import units
        dev = mine(await stock.list_items(db, "dev"), pid)
        us = (await db.execute(text("SELECT id, purpose, serial, und_serial, no_serial, deal_id, stock_item_id, maker FROM product_units "
                                    "WHERE stock_item_id = ANY(:i) ORDER BY und_serial"), {"i": [r["id"] for r in dev]})).mappings().all()
        check(len(us) == 2 and all(u["purpose"] == "SD" and u["deal_id"] is None and u["maker"] == SUP for u in us)
              and [u["und_serial"] for u in us] == ["QK-UND-1", "QK-UND-2"], "자체 개발 재고 → S/N 표 SD 줄", [dict(u) for u in us])
        # 백엔드를 다시 띄워도(마이그레이션의 옛 한글 상태 정리) SD 가 O 로 바뀌지 않는다 — 2026-11-19 발견한 버그
        from app import migrations
        for stmt in migrations._SALES_DEALS_STMTS + migrations._PURCHASES_STMTS:
            if "purpose" in str(stmt) and ("ELSE 'O'" in str(stmt) or "SET purpose = 'SD'" in str(stmt)):
                await db.execute(stmt)
        await db.commit()
        kinds = (await db.execute(text("SELECT DISTINCT purpose FROM product_units WHERE stock_item_id = ANY(:i)"),
                                  {"i": [r["id"] for r in dev]})).scalars().all()
        check(kinds == ["SD"], "재시작 마이그레이션 뒤에도 SD 그대로", kinds)
        # 제조사 — S/N 표(SD)도 재고 제조사를 따르고, 안 고친 줄은 구매 공급사 이름을 고치면 따라간다(사용자 2026-11-19)
        await stock.update_item(db, us[0]["stock_item_id"], uid, {"maker": "QK Maker"})
        mk = {r[0]: r[1] for r in (await db.execute(text("SELECT stock_item_id, maker FROM product_units WHERE stock_item_id = ANY(:i)"),
                                                     {"i": [r["id"] for r in dev]})).all()}
        check(mk[us[0]["stock_item_id"]] == "QK Maker" and mk[us[1]["stock_item_id"]] == SUP, "재고 제조사 → S/N 관리 제조사", mk)
        await ps.set_info(db, pid, uid, {"supplier": SUP + "2", "title": "", "owner": "Chris", "note": "", "region": "overseas"})
        mk = {r[0]: r[1] for r in (await db.execute(text("SELECT stock_item_id, maker FROM product_units WHERE stock_item_id = ANY(:i)"),
                                                     {"i": [r["id"] for r in dev]})).all()}
        srows = mine(await stock.list_items(db, "sale"), pid)
        check(mk[us[0]["stock_item_id"]] == "QK Maker" and mk[us[1]["stock_item_id"]] == SUP + "2"
              and all(r["maker"] == "Deep Robotics" for r in srows), "공급사 이름 바꾸면 안 고친 제조사만 따라감", (mk, srows[0]["maker"]))
        await ps.set_info(db, pid, uid, {"supplier": SUP, "title": "", "owner": "Chris", "note": "", "region": "overseas"})
        found = await units.search_units(db, "QK-UND-2", today)
        check(len(found) == 1 and found[0]["stock_supplier"] == SUP and found[0]["warranty_state"] == "자체 개발", "UND S/N 으로 찾기",
              found and found[0].get("stock_supplier"))
        check(any(x["stock_item_id"] for x in await units.search_units(db, "Cable", today)), "품목 이름으로 찾기")
        await stock.update_items(db, [r["id"] for r in dev], uid, {"display_name": "QK 개발용 케이블"})
        found = await units.search_units(db, "QK 개발용", today)
        check(len(found) == 2 and found[0]["stock_display_name"] == "QK 개발용 케이블", "S/N 관리에서 표시 이름으로도 찾기", len(found))
        u1 = next(u["id"] for u in us if u["und_serial"] == "QK-UND-1")
        det = await units.unit_detail(db, u1, today)
        stx = det.get("stock") or {}
        check(stx.get("supplier") == SUP and stx.get("sw_owner") == "Kim" and stx.get("status") == "대기 중" and stx.get("po_no"),
              "S/N 상세 — 구매 건·담당자·재고 상태", stx)
        case = await units.create_case(db, uid, u1, {"symptom": "통신 끊김", "status": "진행 중"})
        st1 = (await db.execute(text("SELECT purpose FROM product_units WHERE id = :i"), {"i": u1})).scalar()
        check(case["customer"] == "자체 개발" and st1 == "A", "A/S 접수 — 고객사 '자체 개발', 진행 중이면 A", (case["customer"], st1))
        await units.update_case(db, uid, case["id"], {"status": "완료"})
        st1 = (await db.execute(text("SELECT purpose FROM product_units WHERE id = :i"), {"i": u1})).scalar()
        check(st1 == "SD", "A/S 완료 → SD 로 돌아옴", st1)
        check(len((await units.unit_detail(db, u1, today))["cases"]) == 1, "A/S 이력 남음")

        # 영업 건 S/N 과 겹치면 입고 저장을 막는다
        recs = (await ps.get(db, pid, today))["receipt"]["records"]
        di = next(i for i, x in enumerate(recs) if x["purpose"] == "dev")
        tmp = (await db.execute(text("INSERT INTO product_units (part_name, serial, und_serial, maker, purpose, status) "
                                     "VALUES ('QA', 'QK-SALE-1', 'QK-SALE-1', 'UND', 'S', '등록') RETURNING id"))).scalar()
        await db.commit()
        try:
            await expect_err(ps.set_receipt_serials(db, pid, uid, di, [{"line_index": 0, "units": [
                {"maker_sn": "MK-1", "und_sn": "QK-UND-1"}, {"maker_sn": "MK-9", "und_sn": "QK-SALE-1"}]}]), "다른 S/N 과 겹치면 막기", "이미")
            await db.rollback()
        finally:
            await db.execute(text("DELETE FROM product_units WHERE id = :i"), {"i": tmp})
            await db.commit()

        # [S/N 없는 품목]으로 바꾸면 S/N 관리에서 빠짐(A/S 있는 u1 은 '입고 삭제'로 남음), 재고 줄 2개는 그대로 — 케이블 같은 소모품
        await ps.set_receipt_serials(db, pid, uid, di, [{"line_index": 0, "units": []}])
        rec = (await ps.get(db, pid, today))["receipt"]["records"][di]
        await ps.update_receipt(db, pid, uid, "QA", di, {"date": today, "lines": [{"line_no": 1, "qty": 2, "no_sn": True, "sw_owner": "Kim"}],
                                                         "expect": {"at": rec["at"]}})
        dev = mine(await stock.list_items(db, "dev"), pid)
        left = (await db.execute(text("SELECT id, status FROM product_units WHERE id = ANY(:i)"), {"i": [u["id"] for u in us]})).mappings().all()
        check(len(dev) == 2 and all(r["no_sn"] for r in dev) and [(x["id"], x["status"]) for x in left] == [(u1, "입고 삭제")],
              "S/N 없는 품목 — 재고엔 남고 S/N 관리에선 빠짐(A/S 있는 것만 '입고 삭제')", [dict(x) for x in left])
        await db.execute(text("DELETE FROM as_cases WHERE unit_id = :i"), {"i": u1})
        await db.execute(text("DELETE FROM product_units WHERE id = :i"), {"i": u1})
        await db.commit()
        # 체크를 풀고 S/N 을 적으면 다시 들어옴
        rec = (await ps.get(db, pid, today))["receipt"]["records"][di]
        await ps.update_receipt(db, pid, uid, "QA", di, {"date": today, "lines": [{"line_no": 1, "qty": 2, "sw_owner": "Kim", "units": [
            {"maker_sn": "MK-7", "und_sn": "QK-UND-7"}]}], "expect": {"at": rec["at"]}})
        us = (await db.execute(text("SELECT id, und_serial, no_serial FROM product_units WHERE stock_item_id = ANY(:i) ORDER BY und_serial DESC"),
                               {"i": [r["id"] for r in mine(await stock.list_items(db, "dev"), pid)]})).mappings().all()
        check([(u["und_serial"], u["no_serial"]) for u in us] == [("QK-UND-7", False), ("", True)],
              "체크 풀고 S/N 적으면 다시 S/N 관리로(적은 것 + 아직 미기재)", [dict(u) for u in us])
        u1 = -1

        # 기록 지우면 그 목적 줄 모두 지움 — A/S 기록이 있는 S/N 은 '입고 삭제'로 남고, 없는 것은 지움
        await ps.remove_receipt(db, pid, uid, di)
        check(mine(await stock.list_items(db, "dev"), pid) == [], "자체 개발용 기록 지움 → 재고 줄도 지움")
        left = (await db.execute(text("SELECT id FROM product_units WHERE id = ANY(:i)"), {"i": [u["id"] for u in us]})).mappings().all()
        check(left == [], "A/S 없는 S/N 줄은 지움", [dict(x) for x in left])

        # 구매 건 지우면 재고·이력 함께
        await ps.delete(db, [pid])
        n = (await db.execute(text("SELECT COUNT(*) FROM stock_items WHERE purchase_id = :p"), {"p": pid})).scalar()
        check(n == 0, "구매 건 지우면 재고도 지움", n)
        await expect_err(stock.list_items(db, "x"), "없는 목록 거절", "판매용")


async def buyers_part(uid: int) -> None:
    """판매용 [같은 품목 줄 나누기](사용자 2026-11-19) — 3대를 2대(A사)·1대(B사)로 나눠 받으면 재고 목록에 나눈 줄 그대로,
    남은 수량을 다른 고객사로 더 받아 기록이 합쳐져도 잃지 않음. 나눈 수량 합이 다르면 거절."""
    from app.purchases import service as ps
    from app.purchases import stock

    today = ps.kst_today()
    async with SessionLocal() as db:
        pid = await ps.create(db, uid, {"supplier": SUP + "B", "title": "", "owner": "", "note": "", "reason": "", "region": "overseas"})
        await ps.skip_quote(db, pid, uid, "QA", True)
        await ps.approve(db, pid, uid, "QA", today)
        dr = await ps.po_draft(db, pid, {})
        hd = {**dr["header"], "issuer": {**dr["header"]["issuer"], "name": "QA", "attn": "QA"}}
        await ps.issue_po(db, pid, uid, "QA", {"initials": "QK", "expected_date": "", "header": hd,
                                               "items": [{"description": "L250 AMR", "qty": 4, "price": 10}]})
        await ps.start_shipping(db, pid, uid, "QA", today, "")
        await expect_err(ps.add_receipt(db, pid, uid, "QA", {"date": today, "purpose": "sale", "lines": [
            {"line_no": 0, "qty": 3, "parts": [{"qty": 2, "customer": "A"}, {"qty": 2, "customer": "B"}]}]}), "나눈 수량 합이 다르면 거절", "합")
        await db.rollback()
        await ps.add_receipt(db, pid, uid, "QA", {"date": today, "purpose": "sale", "lines": [
            {"line_no": 0, "qty": 3, "parts": [{"qty": 2, "customer": "현대위치드", "owner": "Lucas", "photos": ["e" * 32 + ".jpg"]},
                                               {"qty": 1, "customer": "LG CNS", "owner": "Chris", "note": "데모"}]}]})
        rows = mine(await stock.list_items(db, "sale"), pid)
        check([(r["customer"], r["owner"], r["part"]) for r in rows] == [("현대위치드", "Lucas", 0), ("현대위치드", "Lucas", 0), ("LG CNS", "Chris", 1)],
              "나눈 줄 → 재고 줄마다 고객사·나눈 줄 번호", [(r["customer"], r["part"]) for r in rows])
        check(rows[0]["line_photos"] == ["e" * 32 + ".jpg"] and rows[1]["line_photos"] == ["e" * 32 + ".jpg"] and rows[2]["line_photos"] == [],
              "나눈 줄 사진 → 그 줄의 대마다 품목 사진으로", [r["line_photos"] for r in rows])
        # 남은 1대를 또 다른 고객사로 — 기록은 한 건으로 합쳐져도 나눈 줄은 그대로 이어짐
        await ps.add_receipt(db, pid, uid, "QA", {"date": today, "purpose": "sale", "lines": [{"line_no": 0, "qty": 1, "customer": "SK", "owner": "Mason"}]})
        rec = next(x for x in (await ps.get(db, pid, today))["receipt"]["records"] if x["purpose"] == "sale")
        rows = mine(await stock.list_items(db, "sale"), pid)
        check(len(rec["lines"]) == 1 and [(x["qty"], x["customer"]) for x in rec["lines"][0]["parts"]] == [(2, "현대위치드"), (1, "LG CNS"), (1, "SK")]
              and [(r["customer"], r["part"]) for r in rows] == [("현대위치드", 0), ("현대위치드", 0), ("LG CNS", 1), ("SK", 2)],
              "합쳐도 나눈 줄 유지", [(r["customer"], r["part"]) for r in rows])
        # 자체 개발용도 같게 — 3대를 2대(S/W Kim)·1대(S/W Lee)로 나누면 재고 목록 두 줄, S/N 은 대마다 그대로
        pid3 = await ps.create(db, uid, {"supplier": SUP + "D", "title": "", "owner": "", "note": "", "reason": "", "region": "overseas"})
        await ps.skip_quote(db, pid3, uid, "QA", True)
        await ps.approve(db, pid3, uid, "QA", today)
        await ps.issue_po(db, pid3, uid, "QA", {"initials": "QK", "expected_date": "", "header": hd,
                                                "items": [{"description": "RB5", "qty": 3, "price": 10}]})
        await ps.start_shipping(db, pid3, uid, "QA", today, "")
        await ps.add_receipt(db, pid3, uid, "QA", {"date": today, "purpose": "dev", "lines": [
            {"line_no": 0, "qty": 3, "parts": [{"qty": 2, "sw_owner": "Kim", "photos": ["f" * 32 + ".jpg"]}, {"qty": 1, "sw_owner": "Lee"}],
             "units": [{"maker_sn": "QKD-1", "und_sn": "QKD-U1"}, {"maker_sn": "QKD-2", "und_sn": "QKD-U2"}, {"maker_sn": "QKD-3", "und_sn": "QKD-U3"}]}]})
        rows = mine(await stock.list_items(db, "dev"), pid3)
        check([(r["sw_owner"], r["part"], r["und_sn"]) for r in rows] == [("Kim", 0, "QKD-U1"), ("Kim", 0, "QKD-U2"), ("Lee", 1, "QKD-U3")]
              and rows[0]["line_photos"] == ["f" * 32 + ".jpg"] and rows[2]["line_photos"] == [],
              "자체 개발용 나눈 줄 → 목록 줄·S/W 담당·S/N·사진", [(r["sw_owner"], r["part"], r["und_sn"]) for r in rows])
        n = (await db.execute(text("SELECT COUNT(*) FROM product_units WHERE stock_item_id = ANY(:i)"), {"i": [r["id"] for r in rows]})).scalar()
        check(n == 3, "나눠도 S/N 관리엔 대마다", n)

        # 자체 개발용 기본 = 대마다 한 줄(사용자 2026-11-19) — 대마다 S/W 담당자·낱개 품목 사진, [한 줄로 합치기](no_sn)면 한 줄
        pid4 = await ps.create(db, uid, {"supplier": SUP + "E", "title": "", "owner": "", "note": "", "reason": "", "region": "overseas"})
        await ps.skip_quote(db, pid4, uid, "QA", True)
        await ps.approve(db, pid4, uid, "QA", today)
        await ps.issue_po(db, pid4, uid, "QA", {"initials": "QK", "expected_date": "", "header": hd,
                                                "items": [{"description": "RB5", "qty": 2, "price": 10}, {"description": "케이블", "qty": 20, "price": 1}]})
        await ps.start_shipping(db, pid4, uid, "QA", today, "")
        await ps.add_receipt(db, pid4, uid, "QA", {"date": today, "purpose": "dev", "lines": [
            {"line_no": 0, "qty": 2, "units": [{"maker_sn": "QKE-1", "und_sn": "QKE-U1", "sw_owner": "Kim", "item_photos": ["a" * 32 + ".jpg"]},
                                               {"maker_sn": "QKE-2", "und_sn": "QKE-U2"}]},
            {"line_no": 1, "qty": 20, "no_sn": True, "sw_owner": "Park"}]})
        rows = mine(await stock.list_items(db, "dev"), pid4)
        rb = [r for r in rows if r["name"] == "RB5"]
        cb = [r for r in rows if r["name"] == "케이블"]
        check(len({r["part"] for r in rb}) == 2 and [r["sw_owner"] for r in rb] == ["Kim", ""] and rb[0]["line_photos"] == ["a" * 32 + ".jpg"],
              "자체 개발용 기본 — 대마다 목록 줄, 대마다 S/W 담당·낱개 품목 사진", [(r["part"], r["sw_owner"]) for r in rb])
        check(len(cb) == 20 and len({r["part"] for r in cb}) == 1 and all(r["sw_owner"] == "Park" for r in cb), "한 줄로 합침 → 목록 한 줄", len({r["part"] for r in cb}))
        # [수정] 모드 사진(사용자 2026-11-19) — 재고 줄에서 넣고 빼면 입고 기록에 저장
        P1, P2, P3 = "1" * 32 + ".jpg", "2" * 32 + ".jpg", "3" * 32 + ".jpg"
        await stock.edit_photos(db, rb[1]["id"], uid, {"add": [P1], "scope": "unit", "kind": "sn"})
        await stock.edit_photos(db, rb[1]["id"], uid, {"add": [P2], "scope": "unit", "kind": "item"})
        await stock.edit_photos(db, cb[0]["id"], uid, {"add": [P3], "scope": "line"})
        rows = mine(await stock.list_items(db, "dev"), pid4)
        rb2 = [r for r in rows if r["name"] == "RB5"][1]
        cb2 = [r for r in rows if r["name"] == "케이블"]
        check(rb2["photos"] == [P1] and P2 in rb2["line_photos"] and all(P3 in r["line_photos"] for r in cb2),
              "재고에서 사진 넣기 — 그 대 S/N·낱개 품목, 묶음 줄 품목 사진", (rb2["photos"], rb2["line_photos"]))
        rec = next(x for x in (await ps.get(db, pid4, today))["receipt"]["records"] if x["purpose"] == "dev")
        check(rec["lines"][0]["units"][1]["photos"] == [P1] and P3 in rec["lines"][1]["photos"], "입고 기록에도 저장")
        await stock.edit_photos(db, rb2["id"], uid, {"remove": [P1, P2]})
        rb3 = [r for r in mine(await stock.list_items(db, "dev"), pid4) if r["name"] == "RB5"][1]
        check(rb3["photos"] == [] and P2 not in rb3["line_photos"], "재고에서 사진 빼기", rb3["line_photos"])

        # [품목 모두 비우기] 후 같은 이름 품목을 고객사만 달리 두 줄로 — 둘 다 재고에, 서로 다른 목록 줄로
        pid2 = await ps.create(db, uid, {"supplier": SUP + "C", "title": "", "owner": "", "note": "", "reason": "", "region": "overseas"})
        await ps.skip_quote(db, pid2, uid, "QA", True)
        await ps.approve(db, pid2, uid, "QA", today)
        await ps.issue_po(db, pid2, uid, "QA", {"initials": "QK", "expected_date": "", "header": hd,
                                                "items": [{"description": "L250 AMR", "qty": 2, "price": 10}]})
        await ps.start_shipping(db, pid2, uid, "QA", today, "")
        await ps.add_receipt(db, pid2, uid, "QA", {"date": today, "purpose": "sale", "final": True, "lines": [
            {"line_no": None, "name": "L250 AMR", "qty": 1, "customer": "X사"}, {"line_no": None, "name": "L250 AMR", "qty": 1, "customer": "Y사"}]})
        rows = mine(await stock.list_items(db, "sale"), pid2)
        check(sorted((r["customer"], r["part"]) for r in rows) == [("X사", 0), ("Y사", 1)], "직접 적은 같은 이름 두 줄 → 재고 2줄·목록 2줄",
              [(r["customer"], r["part"]) for r in rows])


async def main() -> None:
    await cleanup()
    async with SessionLocal() as db:
        uid = (await db.execute(text("SELECT id FROM users WHERE username = 'alex'"))).scalar()
    try:
        await run(uid)
        await buyers_part(uid)
    finally:
        await cleanup()
    print(f"PASS {sum(res)} / FAIL {len(res) - sum(res)}")
    sys.exit(0 if all(res) else 1)


asyncio.run(main())
