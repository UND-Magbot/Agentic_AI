# -*- coding: utf-8 -*-
"""수동 검증용 시험 건 — 출하까지 끝난 영업 건의 견적서를 고치는 여러 경로(사용자 2026-11-18: 영업팀 '견적서를 고쳐도 안 바뀐다').

dev DB(agent_ai_dev) 전용. 이름이 '[견적수정검증] ' 로 시작하는 건·견적서·S/N 을 만들고, 올려 볼 파일을
test_cases/fixtures/private/quote_update/ 에 만든다(저장소에는 안 올라감). 다시 돌리면 지우고 새로, --clean 이면 지우기만.
  docker compose run --rm -T --no-deps -e PYTHONPATH=/app -v ./backend/app:/app/app -v ./scripts:/scripts \
      -v ./test_cases/fixtures/private:/out backend python /scripts/seed_quote_update_qa.py [--user=alex] [--clean]
모든 건: 견적서 발행 → 수주 → S/N 등록 → 출하 끝. 품목 MG10 그리퍼 2 × 1,000,000 + TCV3 툴체인저 1 × 500,000 = 2,500,000.
"""
import asyncio
import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path

assert "agent_ai_dev" in os.environ.get("DATABASE_URL", ""), "NOT DEV DB - abort"
from openpyxl import Workbook, load_workbook
from sqlalchemy import text

from app.database import SessionLocal

MARK, INI = "[견적수정검증] ", "QV"
SALES_USER = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--user=")), "alex")
OUT = Path("/out/quote_update")
HEAD = {"initials": INI, "contact_name": "Alex", "contact_mobile": "010-0000-0000", "contact_email": "t@example.com"}
MG = {"name": "MG10 그리퍼", "qty": 2, "unit_price": 1_000_000}
TC = {"name": "TCV3 툴체인저", "qty": 1, "unit_price": 500_000}


async def cleanup():
    async with SessionLocal() as db:
        n = len(MARK)
        dids = "SELECT id FROM sales_deals WHERE left(customer, %d) = :c" % n
        qids = "SELECT id FROM product_quotes WHERE left(customer, %d) = :c OR left(state->'form'->>'customer', %d) = :c" % (n, n)
        p = {"c": MARK}
        await db.execute(text(f"DELETE FROM as_cases WHERE deal_id IN ({dids})"), p)
        await db.execute(text(f"DELETE FROM product_units WHERE serial LIKE 'QV-%' OR deal_id IN ({dids})"), p)
        await db.execute(text(f"DELETE FROM doc_numbers WHERE quote_id IN ({qids}) OR deal_id IN ({dids})"), p)
        await db.execute(text(f"DELETE FROM product_quotes WHERE id IN ({qids})"), p)
        await db.execute(text(f"DELETE FROM sales_deal_events WHERE deal_id IN ({dids})"), p)
        await db.execute(text(f"DELETE FROM sales_deals WHERE id IN ({dids})"), p)
        await db.commit()


async def ship(db, uid, did, qid, tag):
    """수주 → 품목마다 S/N(QV-태그-품목-번호) → 출하."""
    from app.sales_deals import service as svc, units
    await svc.set_won(db, did, uid, True, None)
    iu = await units.issue_units(db, did, qid, 0)
    iu = await units.save_units(db, did, uid, qid, 0, [
        {"line_no": ln["line_no"], "units": [{"serial": f"QV-{tag}-{ln['name'].split()[0]}-{k + 1}"} for k in range(int(ln["qty"]))]}
        for ln in iu["lines"]])
    await svc.add_shipment(db, did, uid, {"date": svc.kst_today(), "unit_ids": [u["id"] for ln in iu["lines"] for u in ln["units"]],
                                          "lines": [{"name": ln["name"], "qty": ln["qty"], "line_no": ln["line_no"], "photos": []}
                                                    for ln in iu["lines"]]})


async def quote_deal(uid, tag, title):
    from app.company_knowledge import product_recommend as pr
    q = await pr.quote_manual(user_id=uid, form=HEAD)
    await pr.quote_edit(q["id"], user_id=uid, payload={"form": {"customer": MARK + f"{tag} {title}", "subject": "견적 수정 검증"},
                                                      "delivery": {"case": 1},
                                                      "lines": [{"id": None, "remark": "", **MG}, {"id": None, "remark": "", **TC}]})
    q = await pr.quote_issue(q["id"], user_id=uid)
    async with SessionLocal() as db:
        did = (await db.execute(text("SELECT deal_id FROM product_quotes WHERE id=:i"), {"i": q["id"]})).scalar()
        await ship(db, uid, did, q["id"], tag)
    return q, did


def company_edit(data: bytes, new_no: str | None = None) -> bytes:
    """회사 양식 발행본에서 MG10 단가 1,000,000 → 1,100,000(금액은 엑셀 수식이 다시 계산), new_no 면 발행번호도 바꾼다."""
    wb = load_workbook(io.BytesIO(data))
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if c.value == 1_000_000:
                    c.value = 1_100_000
                if new_no and isinstance(c.value, str) and c.value.strip().upper().startswith("QT-"):
                    c.value = new_no
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def external_xlsx(customer: str) -> bytes:
    """회사 양식이 아닌 견적서(고객사·다른 양식) — 발행번호 없음, MG10 단가 1,100,000."""
    wb = Workbook()
    ws = wb.active
    ws.title = "견적서"
    for r in (["견 적 서"], [], ["수신", customer], ["건명", "MG10·TCV3 견적"], [],
              ["No", "품명", "수량", "단가", "금액"], [1, "MG10 그리퍼", 2, 1_100_000, 2_200_000],
              [2, "TCV3 툴체인저", 1, 500_000, 500_000], [], ["", "합계(VAT 별도)", "", "", 2_700_000]):
        ws.append(r)
    for col, w in zip("ABCDE", (6, 24, 8, 14, 14)):
        ws.column_dimensions[col].width = w
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def to_pdf(xlsx: bytes) -> bytes | None:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "f.xlsx"
        src.write_bytes(xlsx)
        subprocess.run(["soffice", f"-env:UserInstallation=file://{tmp}/p", "--headless", "--convert-to", "pdf", "--outdir", tmp, str(src)],
                       capture_output=True, timeout=120)
        pdf = Path(tmp) / "f.pdf"
        return pdf.read_bytes() if pdf.exists() else None


def save(name: str, data: bytes | None):
    if data:
        (OUT / name).write_bytes(data)
        print("  파일", name)


async def main():
    from app.company_knowledge import product_recommend as pr
    from app.sales_deals import service as svc
    await cleanup()
    if "--clean" in sys.argv:
        print("지웠습니다.")
        return
    OUT.mkdir(parents=True, exist_ok=True)
    for f in OUT.glob("*"):
        f.unlink()
    async with SessionLocal() as db:
        uid = (await db.execute(text("SELECT id FROM users WHERE username = :u"), {"u": SALES_USER})).scalar_one()

    cases = [("U1", "고치기-수주한 견적서 고치기"), ("U2", "고치기-새 견적서로 발행"), ("U3", "견적서탭 같은번호 파일"),
             ("U4", "견적서탭 번호바꾼 파일"), ("U5", "영업건 같은번호 파일 첨부"), ("U6", "영업건 외부양식 첨부"),
             ("U7", "외부양식 수주번호 적기"), ("U9", "선금 입금 확인한 출하 건")]
    made = {}
    for tag, title in cases:
        q, did = await quote_deal(uid, tag, title)
        made[tag] = (q, did)
        print(f"{tag} {q['quote_no']} (건 {did}) — {title}")
    # U8 — 견적서 발행 없이 영업 건 관리에서 직접 등록한 건
    async with SessionLocal() as db:
        did = await svc.create_deal(db, uid, {"customer": MARK + "U8 직접 등록 건", "title": "견적 수정 검증", "owner": "Alex", "contact": None,
                                              "quote_date": svc.kst_today(), "vat_included": False, "currency": "KRW",
                                              "items": [MG, TC], "note": "", "pay_terms_text": None, "rental": None})
        await svc.set_won(db, did, uid, True, None)
        await svc.add_shipment(db, did, uid, {"date": svc.kst_today(), "unit_ids": None,
                                              "lines": [{"name": MG["name"], "qty": 2, "photos": []}, {"name": TC["name"], "qty": 1, "photos": []}]})
    print(f"U8 (직접 등록, 견적서 번호 없음, 건 {did})")
    # U9 — 선금 50% 입금 확인
    async with SessionLocal() as db:
        d9 = made["U9"][1]
        await svc.set_terms(db, d9, uid, [{"label": "선금", "pct": 50}, {"label": "잔금", "pct": 50}])
        await svc.confirm_term(db, d9, uid, 0, svc.kst_today())

    # 올려 볼 파일
    for tag in ("U3", "U5", "U9"):
        q = made[tag][0]
        _, data = await pr.quote_file(q["id"], user_id=None, revision=0, fmt="xlsx")
        save(f"{tag}_같은번호_{q['quote_no']}_MG10단가110만.xlsx", company_edit(data))
    q = made["U4"][0]
    _, data = await pr.quote_file(q["id"], user_id=None, revision=0, fmt="xlsx")
    new_no = q["quote_no"][:-2] + "77"
    save(f"U4_번호바꿈_{new_no}_MG10단가110만.xlsx", company_edit(data, new_no))
    for tag in ("U6", "U7", "U8"):
        x = external_xlsx(MARK + tag)
        save(f"{tag}_외부양식_번호없음_MG10단가110만.xlsx", x)
    save("U6_외부양식_번호없음_MG10단가110만.pdf", to_pdf(external_xlsx(MARK + "U6")))
    print("끝 — 파일은 test_cases/fixtures/private/quote_update/")


asyncio.run(main())
