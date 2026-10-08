# -*- coding: utf-8 -*-
"""견적서 수기 작성 → 영업 건 흐름(사용자 2026-10-08) — 실제 DB 에 테스트 견적·건을 만들고 끝에 지운다.

    docker compose run --rm --no-deps -e PYTHONPATH=/app -v ./backend/app:/app/app -v ./scripts:/scripts backend \\
        python /scripts/test_manual_quote_db.py

확인: 빈 수기 견적 → 품목·머리 입력 → 첫 발행 때 영업 건(견적 단계)과 건 번호 → 다시 발행해도 같은 건(Rev) →
영업 건 상세의 견적서 목록 → 미팅 정보 직접 입력. AI 추천 쪽: [최종 제안 확정]은 번호 없이 등록, 첫 견적 발행 때 번호.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import sys

from sqlalchemy import text

from app.company_knowledge import product_recommend as pr
from app.database import SessionLocal
from app.sales_deals import service as svc

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []
TODAY = dt.date.today()
CUSTOMER = "__테스트_수기견적__"
FAKE_PROPOSAL = 9_100_000_001


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


async def cleanup(db) -> None:
    await db.execute(text("DELETE FROM product_quotes WHERE customer = :c OR state->'form'->>'customer' = :c"), {"c": CUSTOMER})
    await db.execute(text("DELETE FROM sales_deals WHERE customer = :c OR proposal_id = :p"), {"c": CUSTOMER, "p": FAKE_PROPOSAL})
    await db.execute(text("DELETE FROM sales_deal_trash WHERE deal->>'customer' = :c"), {"c": CUSTOMER})
    await db.commit()


async def run() -> None:
    async with SessionLocal() as db:
        await cleanup(db)
        uid = (await db.execute(text("SELECT id FROM users WHERE username = 'alex'"))).scalar()

    # ── 수기 견적 ──
    head = {"initials": "AL", "contact_name": "Alex", "contact_title": "팀장", "contact_mobile": "010-0000-0000",
            "contact_email": "test@example.com"}
    q = await pr.quote_manual(user_id=uid, form=head)
    again = await pr.quote_manual(user_id=uid, form=head)
    check(again["id"] == q["id"], "손대지 않은 빈 수기 초안은 다시 열기(누를 때마다 쌓이지 않음)", f"{q['id']} / {again['id']}")
    listed = [x["id"] for x in await pr.quote_list(user_id=uid)]
    check(q["id"] not in listed, "손대지 않은 빈 수기 초안은 견적서 목록에 안 보임", str(listed[:5]))
    check(q["manual"] and q["state"]["lines"] == [] and q["recommendation_id"] is None and q["question"]["id"] == "customer"
          and "공급 건" in q["state"]["form"]["comments"], "수기 견적: 빈 품목·일반 문구로 시작, 첫 질문은 고객사", str(q["question"]))
    q = await pr.quote_edit(q["id"], user_id=uid, payload={"form": {"customer": CUSTOMER}})
    check(q["id"] in [x["id"] for x in await pr.quote_list(user_id=uid)], "고객사를 넣으면 목록에 보임")
    check(q["question"]["id"] == "items" and "품목이 없습니다" in q["problems"], "고객사 다음은 품목을 묻고, 품목 없으면 발행 막음", str(q["question"]))
    q = await pr.quote_edit(q["id"], user_id=uid, payload={
        "form": {"subject": "MG 그리퍼"}, "delivery": {"case": 1},
        "lines": [{"id": None, "name": "MG10 그리퍼", "qty": 2, "unit_price": 1_500_000, "remark": ""}]})
    check(not q["problems"] and q["subtotal"] == 3_000_000 and not any("툴체인저" in p for p in q["problems"]),
          "품목·납품 방식 채우면 발행 가능(툴체인저 구성 검사는 수기 견적에 적용 안 함)", str(q["problems"]))
    q = await pr.quote_issue(q["id"], user_id=uid)
    no = q["quote_no"]
    check(q["status"] == "issued" and no and no.startswith(f"AL{TODAY:%Y%m%d}") and q["deal_id"],
          "첫 발행 → 건 번호(AL+날짜)가 견적번호, 영업 건 연결", f"{no} {q['deal_id']}")
    async with SessionLocal() as db:
        d = await svc.get_deal(db, q["deal_id"], TODAY)
    check(d["deal_no"] == no and d["kind"] == "quote" and d["stage"] == "견적" and d["quote"]["amount"] == 3_000_000
          and d["proposal_id"] is None and [x["id"] for x in d["quotes"]] == [q["id"]] and d["owner"] == "Alex",
          "영업 건: 견적 단계·견적액·담당·상세의 견적서 목록에 이 견적", str({k: d[k] for k in ("deal_no", "kind", "stage", "owner")}))
    check(d["version"] == 1 and d["display_no"] == f"{no}-01" and [(h["ver_no"], h["stage"], h["quote_rev"]) for h in d["history"]] == [(f"{no}-01", "견적", 1)],
          "첫 견적서 = 히스토리 -01(견적 V1)", f"{d['display_no']} {d['history']}")
    q2 = await pr.quote_edit(q["id"], user_id=uid, payload={"lines": [{"id": "L1", "name": "MG10 그리퍼", "qty": 3, "unit_price": 1_500_000, "remark": ""}]})
    q2 = await pr.quote_issue(q2["id"], user_id=uid)
    async with SessionLocal() as db:
        d2 = await svc.get_deal(db, q["deal_id"], TODAY)
        n = (await db.execute(text("SELECT COUNT(*) FROM sales_deals WHERE customer = :c"), {"c": CUSTOMER})).scalar()
    check(q2["quote_no"] == no and q2["revision"] == 1 and q2["deal_id"] == q["deal_id"] and n == 1
          and d2["quote"]["amount"] == 4_500_000 and d2["quote"]["revision"] == 2,
          "다시 발행 → 같은 번호 Rev.1, 같은 건(새 건 안 생김), 건 견적 갱신", f"{q2['quote_no']} rev{q2['revision']} 건 {n}개")
    # 히스토리 번호는 사내용(사용자 2026-10-08) — 다시 발행하면 -02(견적 V2), 옛 V1 줄은 '버전 업데이트됨', 견적서·파일명에는 번호만
    async with SessionLocal() as db:
        rows = (await svc.list_deals(db, TODAY))["rows"]
    row = next(r for r in rows if r["id"] == q["deal_id"])
    got = [(h["ver_no"], h["quote_rev"], h["amount"], h["superseded"]) for h in row["history"]]
    check(d2["display_no"] == f"{no}-02" and got == [(f"{no}-02", 2, 4_500_000, False), (f"{no}-01", 1, 3_000_000, True)],
          "고쳐 다시 발행 → -02(견적 V2), 옛 -01(V1)은 버전 업데이트됨", f"{d2['display_no']} {got}")
    fname, data = await pr.quote_file(q["id"], user_id=uid)
    import io as _io

    import openpyxl as _ox
    e2 = _ox.load_workbook(_io.BytesIO(data)).active["E2"].value
    check(len(data) > 1000 and e2 == no and "Rev" not in fname
          and fname == f"(주)유엔디로보틱스_견적서_{CUSTOMER}_MG 그리퍼_{no}.xlsx",
          "수정본 견적서: 번호에 (Rev.n) 없음, 파일명에 이니셜·날짜 중복 없음", f"{e2} / {fname}")

    # 미팅 정보 직접 입력(AI 추천 기록 없는 건)
    async with SessionLocal() as db:
        check(not (d2.get("meeting") or {}).get("customer"), "수기 견적 건은 처음엔 미팅 정보 없음")
        await svc.set_meeting(db, q["deal_id"], uid, {"customer": CUSTOMER, "contact": "홍길동 과장", "meeting_date": TODAY.isoformat(),
                                                    "writer": "Alex", "category": "그리퍼", "robot": "두산 M1013",
                                                    "requirements": "박스 이송, 10kg", "notes": None})
        d3 = await svc.get_deal(db, q["deal_id"], TODAY)
    m = d3["meeting"]
    check(m["category"] == "그리퍼" and m["robot"] == "두산 M1013" and m["notes"] is None and m["updated_at"]
          and any(e["action"] == "meeting" for e in d3["events"]), "미팅 정보 직접 입력 저장·이력", str(m))

    # ── AI 추천: 확정은 번호 없이, 첫 견적 발행 때 번호 ──
    async with SessionLocal() as db:
        rid, rno = await svc.recommend_deal(db, uid, FAKE_PROPOSAL, {"day": TODAY, "customer": CUSTOMER, "owner": "Victor",
                                                                     "title": "TCC1"})
        await db.commit()
        r = await svc.get_deal(db, rid, TODAY)
        check(rno is None and r["deal_no"] is None and r["display_no"] is None and r["stage"] == "제품 추천 확정",
              "[최종 제안 확정] → 번호 없이 '제품 추천 확정' 등록", str((rno, r["display_no"], r["stage"])))
        data = await svc.list_deals(db, TODAY)
        check(any(x["id"] == rid and x["deal_no"] is None for x in data["rows"]), "번호 없는 건도 리스트에 보임")
        _, qno = await svc.quote_deal(db, uid, FAKE_PROPOSAL, {
            "quote_date": TODAY, "customer": CUSTOMER, "contact": None, "owner": "Victor", "initials": "VT",
            "vat_included": False, "currency": "KRW", "items": [{"name": "TCC1 MASTER", "unit_price": 1_000_000, "qty": 1}]}, 1)
        await db.commit()
        r2 = await svc.get_deal(db, rid, TODAY)
    check(qno and qno.startswith(f"VT{TODAY:%Y%m%d}") and r2["deal_no"] == qno and r2["stage"] == "견적"
          and r2["display_no"] == f"{qno}-01", "첫 견적 발행 → 그때 건 번호(VT+날짜)-01, 견적 단계(추천 확정 이력은 번호 없음)",
          str((qno, r2["display_no"])))

    # ── 건을 지운 뒤 같은 날 같은 담당이 다시 발행(사용자 2026-10-08 버그: 견적번호 중복으로 '견적번호를 정하지 못했습니다') ──
    async with SessionLocal() as db:
        await svc.delete_deals(db, [q["deal_id"]], uid)
    q3 = await pr.quote_manual(user_id=uid, form=head)
    q3 = await pr.quote_edit(q3["id"], user_id=uid, payload={
        "form": {"customer": CUSTOMER, "subject": "MG 그리퍼"}, "delivery": {"case": 1},
        "lines": [{"id": None, "name": "MG10 그리퍼", "qty": 1, "unit_price": 1_000_000, "remark": ""}]})
    try:
        q3 = await pr.quote_issue(q3["id"], user_id=uid)
        check(q3["status"] == "issued" and q3["quote_no"] == no, "같은 날 같은 담당의 새 프로젝트도 같은 번호로 발행(B 안 붙음)",
              f"{no} → {q3['quote_no']}")
    except Exception as e:  # noqa: BLE001
        check(False, "같은 날 같은 담당의 새 프로젝트도 같은 번호로 발행(B 안 붙음)", repr(e))

    async with SessionLocal() as db:
        await cleanup(db)
        left = (await db.execute(text("SELECT COUNT(*) FROM sales_deals WHERE customer = :c"), {"c": CUSTOMER})).scalar()
    check(left == 0, "테스트 건·견적 정리")


asyncio.run(run())
print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
for label, detail in FAIL:
    print(f"  ✗ {label}: {detail}")
sys.exit(1 if FAIL else 0)
