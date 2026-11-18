# -*- coding: utf-8 -*-
"""출하까지 끝난 영업 건의 견적서를 고치는 여러 경로 — 영업 건 금액·품목이 따라 바뀌는지(사용자 2026-11-18: 영업팀 '고쳐도 안 바뀐다').

경로마다 출하·S/N 등록까지 끝난 건(MG10 2 × 1,000,000 + TCV3 1 × 500,000 = 2,500,000)을 만들고 MG10 단가를 1,100,000 으로 고쳐
(= 2,700,000) 각 경로로 반영한 뒤 영업 건 금액을 본다. 올리는 파일은 화면과 같게 저장소에 두고 qu.read(AI·회사 양식 되읽기)로 읽은 값을 쓴다.
dev DB 전용, 만든 것은 끝에 지운다:
  docker compose run --rm -T --no-deps -e PYTHONPATH=/app -v ./backend/app:/app/app -v ./scripts:/scripts backend python /scripts/test_quote_update_paths.py
"""
import asyncio
import io
import os
import sys

assert "agent_ai_dev" in os.environ.get("DATABASE_URL", ""), "NOT DEV DB - abort"
from openpyxl import Workbook, load_workbook
from sqlalchemy import text

from app.database import SessionLocal

MARK, INI = "__QA_QUP__", "QU"
HEAD = {"initials": INI, "contact_name": "Alex", "contact_mobile": "010-0000-0000", "contact_email": "t@example.com"}
MG = {"name": "MG10 그리퍼", "qty": 2, "unit_price": 1_000_000}
TC = {"name": "TCV3 툴체인저", "qty": 1, "unit_price": 500_000}
OK: list[bool] = []
ROWS: list[tuple] = []


def check(c, label, extra=""):
    OK.append(bool(c))
    print(("PASS " if c else "FAIL ") + label + ("" if c else f"  :: {extra}"))


async def cleanup():
    async with SessionLocal() as db:
        c, n = MARK.lower(), len(MARK)
        dids = "SELECT id FROM sales_deals WHERE lower(left(customer, %d)) = :c" % n
        qids = ("SELECT id FROM product_quotes WHERE lower(left(customer, %d)) = :c OR lower(left(state->'form'->>'customer', %d)) = :c"
                % (n, n))
        await db.execute(text(f"DELETE FROM as_cases WHERE deal_id IN ({dids})"), {"c": c})
        await db.execute(text(f"DELETE FROM product_units WHERE lower(serial) LIKE 'qu-%' OR deal_id IN ({dids})"), {"c": c})
        await db.execute(text(f"DELETE FROM doc_numbers WHERE upper(issuer) = :i OR quote_id IN ({qids}) OR deal_id IN ({dids})"),
                         {"c": c, "i": INI})
        await db.execute(text(f"DELETE FROM product_quotes WHERE id IN ({qids})"), {"c": c})
        await db.execute(text(f"DELETE FROM sales_deal_events WHERE deal_id IN ({dids})"), {"c": c})
        await db.execute(text(f"DELETE FROM sales_deals WHERE id IN ({dids})"), {"c": c})
        await db.commit()


async def uid_of():
    async with SessionLocal() as db:
        return (await db.execute(text("SELECT id FROM users WHERE username = 'alex'"))).scalar()


async def amount(did):
    from app.sales_deals import service as svc
    async with SessionLocal() as db:
        d = await svc.get_deal(db, did, svc.kst_today())
    return d["flow"]["total"], d["quote"].get("no")


async def shipped_quote_deal(uid, tag):
    """견적서 발행 → 수주 → S/N → 출하."""
    from app.company_knowledge import product_recommend as pr
    from app.sales_deals import service as svc, units
    q = await pr.quote_manual(user_id=uid, form=HEAD)
    await pr.quote_edit(q["id"], user_id=uid, payload={"form": {"customer": MARK + tag, "subject": "견적수정"}, "delivery": {"case": 1},
                                                      "lines": [{"id": None, "remark": "", **MG}, {"id": None, "remark": "", **TC}]})
    q = await pr.quote_issue(q["id"], user_id=uid)
    async with SessionLocal() as db:
        did = (await db.execute(text("SELECT deal_id FROM product_quotes WHERE id=:i"), {"i": q["id"]})).scalar()
        await svc.set_won(db, did, uid, True, None)
        iu = await units.issue_units(db, did, q["id"], 0)
        iu = await units.save_units(db, did, uid, q["id"], 0, [
            {"line_no": ln["line_no"], "units": [{"serial": f"QU-{tag}-{ln['line_no']}-{k}"} for k in range(int(ln["qty"]))]} for ln in iu["lines"]])
        await svc.add_shipment(db, did, uid, {"date": svc.kst_today(), "unit_ids": [u["id"] for ln in iu["lines"] for u in ln["units"]],
                                              "lines": [{"name": ln["name"], "qty": ln["qty"], "line_no": ln["line_no"], "photos": []}
                                                        for ln in iu["lines"]]})
    return q, did


def edited_company_file(data: bytes, new_no: str | None = None) -> bytes:
    """회사 양식 발행본(엑셀)에서 MG10 단가 1,000,000 → 1,100,000(금액 칸 수식은 그대로), new_no 면 발행번호 칸도 바꾼다."""
    wb = load_workbook(io.BytesIO(data))
    ws = wb.active
    old_no = None
    for row in ws.iter_rows():
        for c in row:
            if c.value == 1_000_000:
                c.value = 1_100_000
            if isinstance(c.value, str) and c.value.strip().upper().startswith("QT-"):
                old_no = c.value
                if new_no:
                    c.value = c.value.replace(c.value.strip().split()[0], new_no)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def external_file() -> bytes:
    """회사 양식이 아닌 견적서(고객사·다른 양식) — 발행번호 없음."""
    wb = Workbook()
    ws = wb.active
    ws.append(["견적서"])
    ws.append(["수신", MARK + "외부 양식"])
    ws.append([])
    ws.append(["품명", "수량", "단가", "금액"])
    ws.append(["MG10 그리퍼", 2, 1_100_000, 2_200_000])
    ws.append(["TCV3 툴체인저", 1, 500_000, 500_000])
    ws.append(["합계", None, None, 2_700_000])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


class Up:
    def __init__(self, name, data):
        self.filename, self._d = name, data

    async def read(self):
        return self._d


async def upload(folder, name, data):
    from app.sales_deals import quote_upload as qu
    return await qu.store_and_read(Up(name, data), folder)


def items_of(read):
    return [{"name": x.get("name"), "qty": x.get("qty"), "unit_price": x.get("unit_price"), **({"unit": x["unit"]} if x.get("unit") else {})}
            for x in read.get("items") or []]


async def run_upload(*, qid, did, uid, res, doc_no, overwrite):
    from app.company_knowledge import product_recommend as pr
    try:
        r = await pr.quote_issue_upload(qid=qid, deal_id=None if qid else did, user_id=uid, owner_check=False, upload=res["upload"],
                                        items=items_of(res["read"]), initials=INI, doc_no=doc_no, overwrite=False, sync=True)
        return "발행", r
    except pr.OverwriteNeeded as e:
        if not overwrite:
            return "교체 물음", str(e)
        r = await pr.quote_issue_upload(qid=qid, deal_id=None if qid else did, user_id=uid, owner_check=False, upload=res["upload"],
                                        items=items_of(res["read"]), initials=INI, doc_no=doc_no, overwrite=True, sync=True)
        return "교체", r


def row(case, path, before, after, expect, note=""):
    ROWS.append((case, path, before, after, expect, note))
    print(f"  {case}: {path} → {before:,.0f} → {after:,.0f} (기대 {expect:,.0f}) {note}")


async def main():
    from app.company_knowledge import product_recommend as pr
    from app.sales_deals import service as svc
    await cleanup()
    uid = await uid_of()
    lines_edit = [{"id": None, "remark": "", **MG, "unit_price": 1_100_000}, {"id": None, "remark": "", **TC}]

    # U1 [견적서 고치기] → [재발행] → [수주한 견적서 고치기]
    q, did = await shipped_quote_deal(uid, "U1")
    await pr.quote_edit(q["id"], user_id=uid, payload={"lines": lines_edit})
    await pr.quote_issue(q["id"], user_id=uid, replace_won=True)
    a, no = await amount(did)
    row("U1", "고치기 → 재발행 → 수주한 견적서 고치기", 2_500_000, a, 2_700_000)
    check(a == 2_700_000 and no == q["quote_no"], "U1 수주한 견적서 고치기 — 같은 번호, 영업 건 270만", (a, no))

    # U2 [견적서 고치기] → [재발행] → [새 견적서로 발행]
    q, did = await shipped_quote_deal(uid, "U2")
    await pr.quote_edit(q["id"], user_id=uid, payload={"lines": lines_edit})
    r2 = await pr.quote_issue(q["id"], user_id=uid)
    a, no = await amount(did)
    row("U2", "고치기 → 재발행 → 새 견적서로 발행", 2_500_000, a, 2_500_000, f"(새 번호 {r2['quote_no']}, 수주 번호 {no})")
    check(a == 2_500_000 and no == q["quote_no"], "U2 새 견적서로 발행 — 영업 건은 수주한 견적서 그대로(설계)", (a, no))

    # U3 견적서 탭 [고친 파일로 발행] — 회사 양식 발행본을 받아 단가만 고친 파일(같은 번호)
    q, did = await shipped_quote_deal(uid, "U3")
    _, data = await pr.quote_file(q["id"], user_id=None, revision=0, fmt="xlsx")
    res = await upload(f"product-quotes/{q['id']}/", "수정본.xlsx", edited_company_file(data))
    print("    U3 읽은 번호:", res["read"].get("doc_no"), "| 품목:", [(x.get("name"), x.get("unit_price")) for x in res["read"].get("items") or []])
    kind, _r = await run_upload(qid=q["id"], did=did, uid=uid, res=res, doc_no=res["read"].get("doc_no") or "", overwrite=True)
    a, no = await amount(did)
    row("U3", f"견적서 탭 고친 파일로 발행(같은 번호) — {kind}", 2_500_000, a, 2_700_000)
    check(kind == "교체" and a == 2_700_000, "U3 같은 번호 파일 — 교체 묻고 교체하면 영업 건 270만", (kind, a))

    # U4 견적서 탭 [고친 파일로 발행] — 번호를 바꾼 파일(새 번호)
    q, did = await shipped_quote_deal(uid, "U4")
    _, data = await pr.quote_file(q["id"], user_id=None, revision=0, fmt="xlsx")
    newno = q["quote_no"][:-2] + "77"
    res = await upload(f"product-quotes/{q['id']}/", "수정본_새번호.xlsx", edited_company_file(data, newno))
    kind, _r = await run_upload(qid=q["id"], did=did, uid=uid, res=res, doc_no=res["read"].get("doc_no") or "", overwrite=True)
    a, no = await amount(did)
    row("U4", f"견적서 탭 고친 파일로 발행(번호 바꿈 {res['read'].get('doc_no')}) — {kind}", 2_500_000, a, 2_500_000, f"(수주 번호 {no})")
    check(a == 2_500_000, "U4 다른 번호 파일 — 새 판으로 추가, 영업 건 그대로(설계)", (kind, a))

    # U5 영업 건 상세 [견적서 파일 첨부] — 같은 번호 회사 양식 파일
    q, did = await shipped_quote_deal(uid, "U5")
    _, data = await pr.quote_file(q["id"], user_id=None, revision=0, fmt="xlsx")
    res = await upload(f"sales-deals/{did}/quote/", "수정본.xlsx", edited_company_file(data))
    kind, _r = await run_upload(qid=None, did=did, uid=uid, res=res, doc_no=res["read"].get("doc_no") or "", overwrite=True)
    a, no = await amount(did)
    row("U5", f"영업 건 상세 파일 첨부(같은 번호) — {kind}", 2_500_000, a, 2_700_000)
    check(kind == "교체" and a == 2_700_000, "U5 영업 건에서 같은 번호 파일 첨부 — 교체하면 270만", (kind, a))

    # U6 영업 건 상세 [견적서 파일 첨부] — 외부 양식(번호 없음)
    q, did = await shipped_quote_deal(uid, "U6")
    res = await upload(f"sales-deals/{did}/quote/", "고객사_견적서.xlsx", external_file())
    print("    U6 읽은 번호:", res["read"].get("doc_no"), "| 품목:", [(x.get("name"), x.get("qty"), x.get("unit_price")) for x in res["read"].get("items") or []])
    kind, r6 = await run_upload(qid=None, did=did, uid=uid, res=res, doc_no=res["read"].get("doc_no") or "", overwrite=True)
    a, no = await amount(did)
    row("U6", f"영업 건 상세 외부 양식 첨부(번호 없음) — {kind}", 2_500_000, a, 2_500_000, f"(새 번호 {r6.get('quote_no') if isinstance(r6, dict) else ''})")
    check(a == 2_500_000, "U6 외부 양식(번호 없음) — 새 번호 판으로 추가, 영업 건 그대로", (kind, a))

    # U7 외부 양식이지만 첨부 창에서 발행번호를 수주 번호로 적음
    q, did = await shipped_quote_deal(uid, "U7")
    res = await upload(f"sales-deals/{did}/quote/", "고객사_견적서.xlsx", external_file())
    kind, _r = await run_upload(qid=None, did=did, uid=uid, res=res, doc_no=q["quote_no"], overwrite=True)
    a, no = await amount(did)
    row("U7", f"외부 양식 + 발행번호를 수주 번호로 적음 — {kind}", 2_500_000, a, 2_700_000)
    check(kind == "교체" and a == 2_700_000, "U7 외부 양식에 수주 번호를 적으면 교체 → 270만", (kind, a))

    # U8 직접 등록(견적서 발행 기록 없음) 건 → 수주 → 출하 → 파일 첨부
    async with SessionLocal() as db:
        did = await svc.create_deal(db, uid, {"customer": MARK + "U8 직접 등록", "title": "견적수정", "owner": "Alex",
                                              "quote_date": svc.kst_today(), "vat_included": False, "currency": "KRW",
                                              "items": [MG, TC], "note": "", "pay_terms_text": None, "contact": None, "rental": None})
        await svc.set_won(db, did, uid, True, None)
        await svc.add_shipment(db, did, uid, {"date": svc.kst_today(), "unit_ids": None,
                                              "lines": [{"name": MG["name"], "qty": 2, "photos": []}, {"name": TC["name"], "qty": 1, "photos": []}]})
    res = await upload(f"sales-deals/{did}/quote/", "고객사_견적서.xlsx", external_file())
    kind, r8 = await run_upload(qid=None, did=did, uid=uid, res=res, doc_no=res["read"].get("doc_no") or "", overwrite=True)
    a, no = await amount(did)
    row("U8", f"직접 등록 건 파일 첨부 — {kind}", 2_500_000, a, 2_700_000, f"(번호 {r8.get('quote_no') if isinstance(r8, dict) else ''}, 건 번호 {no})")
    check(a == 2_700_000, "U8 직접 등록(발행 기록 없음) 건 — 첨부한 견적서로 영업 건이 바뀌어야 함", (kind, a, no))


async def run():
    try:
        await main()
    finally:
        await cleanup()
    print()
    print("PASS %d / FAIL %d" % (sum(OK), len(OK) - sum(OK)))

asyncio.run(run())
