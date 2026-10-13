# -*- coding: utf-8 -*-
"""밖에서 고친 견적서 파일 첨부(사용자 2026-10-12) — 개발 DB(agent_ai_dev)·MinIO 에 시험 견적·건·파일을 만들고 끝에 지운다.

    docker compose run --rm --no-deps -e PYTHONPATH=/app -v ./backend/app:/app/app -v ./scripts:/scripts backend \\
        python /scripts/test_quote_upload.py

확인: 품목 검사 / 견적서 탭에서 올린 파일로 발행 → 견적서에 적힌 발행번호 그대로·판 +1·영업 건 금액·히스토리(외부 수정본) /
적힌 번호가 우리 형식이면 기록돼 그날 다음 번호와 안 겹침 / 다른 건이 쓰는 번호 거절 / 번호가 없으면 새 번호 /
다시 받으면 올린 파일 그대로 / 영업 건 상세에서(견적 기록 없는 건) 올리면 기록을 만들어 발행 / 다른 폴더 파일 거절 / 읽을 수 없는 파일.
AI 판독(read)은 사내 AI 를 부르므로 여기서는 '읽을 수 없는 파일' 경로만 본다.
"""
from __future__ import annotations

import asyncio
import io
import sys
import uuid

from openpyxl import Workbook
from sqlalchemy import text

from app.company_knowledge import product_recommend as pr
from app.database import SessionLocal
from app.sales_deals import doc_numbers as dn
from app.sales_deals import quote_upload as qu
from app.sales_deals import service as svc
from app.storage import put_object, remove_object

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []
CUSTOMER = "__테스트_견적첨부__"
TODAY = svc.kst_today()
KEYS: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


def xlsx_bytes() -> bytes:
    wb = Workbook()
    wb.active.append(["Commodity", "Specifications", "Quantity", "Price"])
    wb.active.append(["Magbot ATC", "TCV1 - MASTER T.C", 1, 2_100_000])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def store(folder: str, data: bytes, ext: str = ".xlsx") -> dict:
    key = f"{folder}test{len(KEYS)}{ext}"
    put_object(key=key, data=io.BytesIO(data), length=len(data), mime=qu.TYPES[ext])
    KEYS.append(key)
    return {"key": key, "filename": f"고친견적서{ext}", "mime": qu.TYPES[ext], "size": len(data)}


async def cleanup() -> None:
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM doc_numbers WHERE quote_id IN (SELECT id FROM product_quotes WHERE customer = :c "
                              "OR state->'form'->>'customer' = :c) OR deal_id IN (SELECT id FROM sales_deals WHERE customer = :c)"),
                         {"c": CUSTOMER})
        await db.execute(text("DELETE FROM product_quotes WHERE customer = :c OR state->'form'->>'customer' = :c"), {"c": CUSTOMER})
        await db.execute(text("DELETE FROM sales_deals WHERE customer = :c"), {"c": CUSTOMER})
        await db.commit()


async def run() -> None:
    await cleanup()
    # ── 품목 검사 ──
    rows = qu.clean_items([{"name": "Magbot ATC", "spec": "TCV1 - MASTER T.C", "qty": 1, "unit_price": 2100000.0}])
    check(rows == [{"name": "Magbot ATC\n- TCV1 - MASTER T.C", "qty": 1, "unit_price": 2100000}],
          "확인한 품목 → '품명\\n- 규격'(견적서 표기·명세서 나눔과 같게), 정수는 정수로", str(rows))
    for bad, why in (([], "빈 목록"), ([{"name": "", "qty": 1, "unit_price": 1}], "품명"),
                     ([{"name": "A", "qty": None, "unit_price": 1}], "수량"), ([{"name": "A", "qty": 0, "unit_price": 1}], "0개")):
        try:
            qu.clean_items(bad)
            check(False, f"품목 검사 — {why} 거절")
        except ValueError:
            check(True, f"품목 검사 — {why} 거절")
    unread = await qu.read(b"not an office file", "x.pdf", "application/pdf")
    check(unread["status"] == "unreadable" and unread["items"] == [], "읽을 수 없는 파일 → 직접 적으라고 안내(AI 안 부름)", str(unread))

    async with SessionLocal() as db:
        uid = (await db.execute(text("SELECT id FROM users WHERE username = 'alex'"))).scalar()

    # ── 견적서 탭: AI 견적 발행 → 밖에서 고친 파일로 다시 발행 ──
    head = {"initials": "AL", "contact_name": "Alex", "contact_mobile": "010-0000-0000", "contact_email": "t@example.com"}
    q = await pr.quote_manual(user_id=uid, form=head)
    q = await pr.quote_edit(q["id"], user_id=uid, payload={"form": {"customer": CUSTOMER, "subject": "MG"}, "delivery": {"case": 1},
                                                          "lines": [{"id": None, "name": "MG10", "qty": 1, "unit_price": 1_000_000, "remark": ""}]})
    q = await pr.quote_issue(q["id"], user_id=uid)
    first_no, deal_id = q["quote_no"], q["deal_id"]
    data = xlsx_bytes()
    up = store(f"product-quotes/{q['id']}/", data)
    try:
        await pr.quote_issue_upload(qid=q["id"], deal_id=None, user_id=uid, owner_check=True, upload={**up, "key": "elsewhere/x.xlsx"},
                                    items=[{"name": "A", "spec": "", "qty": 1, "unit_price": 1}], initials="AL")
        check(False, "다른 폴더의 파일로는 발행 거절")
    except pr.RecommendError:
        check(True, "다른 폴더의 파일로는 발행 거절")
    async with SessionLocal() as db:
        last_vt = (await db.execute(text("SELECT COALESCE(MAX(seq), 0) FROM doc_numbers WHERE doc_type = 'QT' AND issuer = 'VT' "
                                         "AND issue_day = :d"), {"d": TODAY})).scalar()
    typed = f"QT-VT{TODAY:%Y%m%d}-{last_vt + 5:02d}"          # 담당자가 견적서에 직접 고쳐 적은 번호(그날 순서보다 앞서 감)
    res = await pr.quote_issue_upload(qid=q["id"], deal_id=None, user_id=uid, owner_check=True, upload=up, initials="",
                                      doc_no=typed, items=[{"name": "Magbot ATC", "spec": "TCV1 - MASTER T.C", "qty": 1, "unit_price": 2_100_000},
                                             {"name": "물류비", "spec": "", "qty": 1, "unit_price": 100_000}])
    no = res["quote_no"]
    hist = res["quote"]["history"]
    check(no == typed and res["deal_id"] == deal_id and res["quote"]["revision"] == 1
          and hist[-1]["uploaded"] and hist[-1]["quote_no"] == no and not hist[0]["uploaded"],
          "올린 파일로 발행 → 견적서에 적힌 발행번호 그대로·판 +1·같은 영업 건, 발행 이력에 '외부 수정본'", f"{first_no} → {no} {hist}")
    async with SessionLocal() as db:
        nxt = await dn.allocate(db, dn.QUOTE, "VT", TODAY)
        await db.rollback()
    check(nxt == f"QT-VT{TODAY:%Y%m%d}-{last_vt + 6:02d}", "적힌 번호(우리 형식)는 기록돼 그 발행자의 다음 번호가 겹치지 않음", nxt)
    async with SessionLocal() as db:
        d = await svc.get_deal(db, deal_id, TODAY)
    check(d["quote"]["amount"] == 2_200_000 and d["quote"]["no"] == no and d["display_no"] == no
          and d["quote"]["items"][0]["name"] == "Magbot ATC\n- TCV1 - MASTER T.C",
          "영업 건 견적(품목·공급가액·번호)이 올린 견적서로 바뀜", str(d["quote"]))
    h = [(x["action"], x["doc_no"], x["superseded"]) for x in d["history"] if x["doc_no"]]
    check(h[0] == ("quote_upload", no, False) and h[1][2] is True, "히스토리: 맨 위 '견적서 첨부(외부 수정본)' + 새 번호, 예전 발행 줄은 버전 업데이트됨", str(h))
    name, got = await pr.quote_file(q["id"], user_id=uid, fmt="xlsx")
    check(got == data and name.endswith(f"_{no}.xlsx"), "다시 받으면 올린 파일 그대로(이름에 새 번호)", name)
    issued = [x for x in d["quotes"][0]["issues"] if x["uploaded"]]
    check(len(issued) == 1 and issued[0]["quote_no"] == no, "영업 건 상세 견적서 목록에도 외부 수정본 판(번호 포함)")

    # ── 이미 있는 발행번호로 다시 발행 → 거절(사용자 2026-10-13: 같은 건이든 다른 건이든, 예전 형식 번호도) ──
    async def dup(doc_no: str) -> str | None:
        try:
            await pr.quote_issue_upload(qid=q["id"], deal_id=None, user_id=uid, owner_check=True, upload=up, initials="",
                                        doc_no=doc_no, items=[{"name": "A", "spec": "", "qty": 1, "unit_price": 1}])
        except pr.DuplicateDocNo as e:
            return str(e)
        return None
    msg = await dup(typed)
    check(bool(msg) and "이미 존재하는 발행번호" in msg and typed in msg, "같은 영업 건에서 쓴 번호로 다시 발행 → 거절", str(msg))
    check(bool(await dup(typed.lower())), "대소문자만 달라도 같은 번호로 봄")
    check(bool(await dup(first_no)), "AI 견적서로 처음 발행한 번호(시스템이 매긴 번호)도 거절")
    old_no = f"S{TODAY:%y}-{uuid.uuid4().hex[:6].upper()}"          # 예전 형식 번호(doc_numbers 에 기록 안 됨)
    r_old = await pr.quote_issue_upload(qid=q["id"], deal_id=None, user_id=uid, owner_check=True, upload=up, initials="",
                                        doc_no=old_no, items=[{"name": "A", "spec": "", "qty": 1, "unit_price": 2_200_000}])
    check(r_old["quote_no"] == old_no and bool(await dup(old_no)), "예전 형식 번호도 한 번 발행하면 다시는 못 씀", old_no)
    async with SessionLocal() as db:
        cnt = (await db.execute(text("SELECT COUNT(*) FROM sales_deal_events WHERE deal_id = :d AND detail->>'no' = :n"),
                                {"d": deal_id, "n": typed})).scalar()
    check(cnt == 1, "거절된 발행은 영업 건 이력에 남지 않음", str(cnt))

    # ── 품목 사진(사용자 2026-10-12) — 줄에 붙이고 견적서 Remark 칸에 ──
    from PIL import Image as _Img
    _b = io.BytesIO()
    _Img.new("RGBA", (1200, 900), (0, 128, 255, 128)).save(_b, "PNG")
    q3 = await pr.quote_line_photo(q["id"], "L1", user_id=uid, data=_b.getvalue())
    key = q3["state"]["lines"][0].get("photo") or ""
    KEYS.append(key)
    small = await pr.quote_photo_file(q["id"], key.rsplit("/", 1)[-1], user_id=uid)
    from PIL import Image as _I2
    im = _I2.open(io.BytesIO(small))
    check(key.startswith(f"product-quotes/{q['id']}/photos/") and im.format == "JPEG" and max(im.size) <= 480,
          "품목 사진 올리기 → 긴 변 480px JPEG 로 줄여 그 줄에 붙음(투명 배경 → 흰색)", str((key, im.size)))
    from app.company_knowledge import quote_session as _qs
    rl = _qs.render_lines(q3["state"], "ko", None)
    check(rl[0].get("photo") == key and key in pr.line_photos(rl), "견적서 줄에 사진 키가 넘어가고 저장소에서 읽힘")
    try:
        await pr.quote_line_photo(q["id"], "L1", user_id=uid, data=b"not image")
        check(False, "이미지가 아니면 거절")
    except pr.RecommendError:
        check(True, "이미지가 아니면 거절")
    q4 = await pr.quote_line_photo(q["id"], "L1", user_id=uid, data=None)
    check(not q4["state"]["lines"][0].get("photo"), "사진 빼기")

    # ── [견적서 수기 작성] 직후(품목·발행 없음) 바로 고친 파일로 발행(사용자 2026-10-13 질문) ──
    blank = await pr.quote_manual(user_id=uid, form=head)
    upb = store(f"product-quotes/{blank['id']}/", data)
    try:
        await pr.quote_issue_upload(qid=blank["id"], deal_id=None, user_id=uid, owner_check=True, upload=upb, initials="AL",
                                    doc_no="", items=[{"name": "MG10", "spec": "", "qty": 1, "unit_price": 1_000_000}])
        check(False, "수기 작성 직후 고객사가 비어 있으면 거절")
    except pr.RecommendError as e:
        check("고객사" in str(e), "수기 작성 직후 고객사가 비어 있으면 거절(고객사부터 적으라고 안내)", str(e))
    await pr.quote_edit(blank["id"], user_id=uid, payload={"form": {"customer": CUSTOMER}})
    rb = await pr.quote_issue_upload(qid=blank["id"], deal_id=None, user_id=uid, owner_check=True, upload=upb, initials="AL",
                                     doc_no="", items=[{"name": "MG10", "spec": "그리퍼", "qty": 2, "unit_price": 1_000_000}])
    async with SessionLocal() as db:
        db_ = await svc.get_deal(db, rb["deal_id"], TODAY)
    check(rb["quote_no"].startswith(f"QT-AL{TODAY:%Y%m%d}-") and rb["deal_id"] and rb["deal_id"] != deal_id
          and db_["customer"] == CUSTOMER and db_["quote"]["amount"] == 2_000_000 and db_["display_no"] == rb["quote_no"]
          and rb["quote"]["history"][-1]["uploaded"],
          "수기 작성 직후(고객사만 적음) 고친 파일로 바로 발행 → 새 번호·새 영업 건(금액 반영)", str((rb["quote_no"], db_.get("quote"))))

    # ── 영업 건 상세: 견적 기록이 없는 건(직접 등록) ──
    async with SessionLocal() as db:
        manual_id = await svc.create_deal(db, uid, {"customer": CUSTOMER, "contact": "홍길동", "owner": "Victor", "title": "직접 등록",
                                                    "quote_date": TODAY, "vat_included": False, "currency": "KRW",
                                                    "items": [{"name": "TCV3", "unit_price": 3_000_000, "qty": 1}],
                                                    "pay_terms_text": None, "note": None})
    pdf = b"%PDF-1.4 test"
    up2 = store(f"sales-deals/{manual_id}/quote/", pdf, ".pdf")
    try:
        await pr.quote_issue_upload(qid=None, deal_id=manual_id, user_id=uid, owner_check=False, upload=up2, initials="VT",
                                    doc_no=typed, items=[{"name": "TCV3", "spec": "", "qty": 1, "unit_price": 1}])
        check(False, "다른 영업 건이 쓰는 번호는 거절")
    except pr.DuplicateDocNo as e:
        check("이미 존재하는 발행번호" in str(e) and CUSTOMER in str(e), "다른 영업 건이 쓰는 번호는 거절(어느 건에서 발행했는지 안내)", str(e))
    res2 = await pr.quote_issue_upload(qid=None, deal_id=manual_id, user_id=uid, owner_check=False, upload=up2, initials="VT",
                                       doc_no="", items=[{"name": "TCV3", "spec": "본체", "qty": 1, "unit_price": 2_900_000}])
    async with SessionLocal() as db:
        d2 = await svc.get_deal(db, manual_id, TODAY)
    check(d2["quote"]["amount"] == 2_900_000 and d2["display_no"] == res2["quote_no"] and len(d2["quotes"]) == 1
          and d2["quotes"][0]["issues"][0]["uploaded"]
          and res2["quote_no"].startswith(f"QT-VT{TODAY:%Y%m%d}-"),
          "견적 기록 없는 건 + 견적서에 번호 없음 → 기록을 만들고 새 번호로 발행(금액 반영, 상세 목록에 보임)", str((d2["display_no"], d2["quote"]["amount"])))
    n2, got2 = await pr.quote_file(d2["quotes"][0]["id"], user_id=None, fmt="xlsx")
    check(got2 == pdf and n2.endswith(".pdf"), "올린 PDF 는 엑셀로 달라도 PDF 그대로", n2)

    await cleanup()
    for k in KEYS:
        try:
            remove_object(k)
        except Exception:
            pass


asyncio.run(run())
print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
for label, detail in FAIL:
    print(f"  ✗ {label}: {detail}")
sys.exit(1 if FAIL else 0)
