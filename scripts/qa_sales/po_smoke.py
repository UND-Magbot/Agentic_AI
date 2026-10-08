"""발주서 첨부(수주 확정) 배포 점검 — 실제 백엔드·저장소(MinIO)로. 테스트 건은 끝에 지운다.

    MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -e PYTHONPATH=/app -e PYTHONIOENCODING=utf-8 \\
        -v ./scripts/qa_sales:/out backend python /out/po_smoke.py
"""
import asyncio
import datetime as dt

import httpx
from sqlalchemy import text

from app.database import SessionLocal
from app.sales_deals import service as svc
from app.security import create_access_token
from app.storage import get_object_stream

B = "http://backend:8000/v1/sales-deals"
CUST = "__테스트_발주서__"
PDF = b"%PDF-1.4\n% test purchase order\n1 0 obj <<>> endobj\ntrailer <<>>\n%%EOF\n"


async def main():
    fails = []

    def report(ok, label, detail=""):
        print("OK  " if ok else "FAIL", label, detail if not ok else "")
        if not ok:
            fails.append(label)

    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM sales_deals WHERE customer = :c"), {"c": CUST})
        await db.commit()
        uid = (await db.execute(text("SELECT id FROM users WHERE username='alex'"))).scalar()
        deal_id = await svc.create_deal(db, uid, {
            "customer": CUST, "contact": None, "owner": "Alex", "title": None, "quote_date": dt.date.today(),
            "vat_included": False, "currency": "KRW", "items": [{"name": "TCC1", "unit_price": 1_800_000, "qty": 1}],
            "pay_terms_text": "선금 100%", "note": ""})
    H = {"Authorization": f"Bearer {create_access_token(str(uid))[0]}"}
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            # 1) 수주 확정(날짜) → 발주서 올리기 — 화면이 하는 순서 그대로
            r = await c.post(f"{B}/{deal_id}/won", headers=H, json={"won": True, "date": str(dt.date.today())})
            report(r.status_code == 200 and r.json()["stage"] == "수주", "수주 확정")
            r = await c.post(f"{B}/{deal_id}/po", headers=H, files={"file": ("발주서_테스트.pdf", PDF, "application/pdf")})
            d = r.json()
            files = d.get("po_files") or []
            report(r.status_code == 200 and len(files) == 1 and files[0]["filename"] == "발주서_테스트.pdf"
                   and "key" not in files[0] and files[0]["uploaded_by"], "발주서 첨부(저장소 키는 화면에 안 보냄)", str(d)[:200])
            report(any(e["action"] == "po_upload" for e in d.get("events", [])), "변경 이력에 '발주서 첨부'")

            # 2) 내려받기 — 같은 내용, PDF 는 브라우저에서 바로 보기(inline)
            r = await c.get(f"{B}/{deal_id}/po/0", headers=H)
            report(r.status_code == 200 and r.content == PDF and r.headers["content-type"] == "application/pdf"
                   and r.headers["content-disposition"].startswith("inline") and "UTF-8''" in r.headers["content-disposition"],
                   "발주서 내려받기(같은 내용·한글 파일명)", f"{r.status_code} {dict(r.headers)}")
            report((await c.get(f"{B}/{deal_id}/po/5", headers=H)).status_code == 404, "없는 순번은 404")

            # 3) 막아야 하는 파일
            r = await c.post(f"{B}/{deal_id}/po", headers=H, files={"file": ("virus.exe", b"MZ....", "application/pdf")})
            report(r.status_code == 415, "실행 파일(.exe)은 거부 — 브라우저가 보낸 형식이 아니라 확장자로 판단", str(r.status_code))
            r = await c.post(f"{B}/{deal_id}/po", headers=H, files={"file": ("빈파일.pdf", b"", "application/pdf")})
            report(r.status_code == 400, "빈 파일 거부", str(r.status_code))
            r = await c.post(f"{B}/{deal_id}/po", headers=H, files={"file": ("큰파일.pdf", b"0" * (20 * 1024 * 1024 + 1), "application/pdf")})
            report(r.status_code == 413, "20MB 초과 거부", str(r.status_code))

            # 4) 수주 미확인 건에 발주서를 올리면 수주로
            await c.post(f"{B}/{deal_id}/won", headers=H, json={"won": None})
            r = await c.post(f"{B}/{deal_id}/po", headers=H, files={"file": ("주문서.xlsx", b"PK\x03\x04xlsx", "application/octet-stream")})
            d = r.json()
            report(r.status_code == 200 and d["won"] is True and len(d["po_files"]) == 2
                   and d["po_files"][1]["mime"].endswith("spreadsheetml.sheet"), "미확인 건에 발주서 → 수주로 + 엑셀 형식 인식")
            r = await c.get(f"{B}/{deal_id}/po/1", headers=H)
            report(r.headers.get("content-disposition", "").startswith("attachment"), "엑셀은 내려받기(attachment)")

            # 5) 잘못 올린 파일 삭제 → 기록과 저장소 파일 모두
            async with SessionLocal() as db:
                key = (await svc.get_po_file(db, deal_id, 0))["key"]
            r = await c.post(f"{B}/{deal_id}/remove", headers=H, json={"kind": "po_files", "index": 0})
            d = r.json()
            report(r.status_code == 200 and len(d["po_files"]) == 1 and d["po_files"][0]["filename"] == "주문서.xlsx", "발주서 삭제")
            try:
                get_object_stream(key).close()
                gone = False
            except Exception:
                gone = True
            report(gone, "저장소의 파일도 지워짐")
            report(any(e["action"] == "remove_po_file" for e in d.get("events", [])), "변경 이력에 '발주서 삭제'")

            # 6) 권한 없는 접근
            report((await c.get(f"{B}/{deal_id}/po/0")).status_code == 401, "로그인 없이 내려받기 불가")
    finally:
        async with SessionLocal() as db:  # 테스트 건과 남은 발주서 파일 정리
            rows = (await db.execute(text("SELECT po_files FROM sales_deals WHERE id = :i"), {"i": deal_id})).scalar() or []
            await db.execute(text("DELETE FROM sales_deals WHERE customer = :c"), {"c": CUST})
            await db.commit()
        from app.storage import remove_object
        for f in rows:
            try:
                remove_object(f["key"])
            except Exception:
                pass
    print("ALL OK" if not fails else f"FAILED {fails}")


asyncio.run(main())
