"""UI 점검용 구매 건 하나(발주까지) 만들고 토큰·건 id 를 출력 — purchases_smoke 와 같은 실행 방식. 끝나면 '__QA_UI__' 건을 지울 것."""
import asyncio, httpx
from sqlalchemy import text
from app.database import SessionLocal
from app.security import create_access_token
F = "http://frontend:3000/api/purchases"
SUP = "__QA_UI__㈜나우티앤에스"
async def main():
    async with SessionLocal() as db:
        uid = (await db.execute(text("SELECT id FROM users WHERE username='alex'"))).scalar()
        await db.execute(text("DELETE FROM purchases WHERE supplier = ANY(:n)"), {"n": [SUP, "__QA_UI__㈜크레켄", "__QA_UI__Guanzhou Autoxing"]}); await db.commit()
    tok, _ = create_access_token(str(uid))
    pdf = open("/samples/(쥬)유엔디로보틱스_발주서_(주)나우티앤에스_Lu261013.pdf", "rb").read()
    async with httpx.AsyncClient(timeout=120, cookies={"und_cortex_token": tok}) as c:
        p = (await c.post(F, json={"supplier": SUP, "title": "TC700 LTE Router 8대"})).json(); pid = p["id"]
        await c.post(F + f"/{pid}/files/quote", files={"file": ("견적서_나우티앤에스.pdf", pdf, "application/pdf")})
        await c.post(F + f"/{pid}/files/approval", files={"file": ("재가_품의서.pdf", pdf, "application/pdf")})
        await c.post(F + f"/{pid}/approve", json={"date": "2026-11-02"})
        await c.post(F + f"/{pid}/po/draft", json={"initials": "ZZ", "header": {"date": "2026-11-02", "supplier": {"name": SUP, "contact": "권창수 부장님", "email": "row@nwtns.com"},
            "issuer": {"name": "이승학", "title": "주임", "address": "대구광역시 수성구 알파시티1로 31길 31, 5층", "tel": "82-10-5896-0996", "fax": "82-54-462-5468", "attn": "Lucas (lucas@unde.co.kr)"}},
            "items": [{"description": "TC700\n- LTE Router\n- 전원 어답터 포함", "qty": 8, "price": 95000}], "expected_date": "2026-11-20"})
        q = (await c.post(F, json={"supplier": "__QA_UI__㈜크레켄", "title": "M8 커넥터 200개"})).json()
        await c.post(F + f"/{q['id']}/files/quote", files={"file": ("크레켄_견적.pdf", pdf, "application/pdf")})
        o = (await c.post(F, json={"supplier": "__QA_UI__Guanzhou Autoxing", "title": "AMR S300 2대", "region": "overseas"})).json()
        oid = o["id"]
        await c.post(F + f"/{oid}/files/quote", files={"file": ("autoxing_quote.pdf", pdf, "application/pdf")})
        await c.post(F + f"/{oid}/files/approval", files={"file": ("approval.pdf", pdf, "application/pdf")})
        await c.post(F + f"/{oid}/approve", json={"date": "2026-10-01"})
        od = (await c.get(F + f"/{oid}/po/draft")).json()
        r = await c.post(F + f"/{oid}/po/issue", json={"initials": "ZZ", "expected_date": "2026-10-25",
            "header": {**od["header"], "date": "2026-10-01", "swift": "BKCHCNBJ",
                       "supplier": {"name": "__QA_UI__Guanzhou Autoxing", "contact": "", "email": "echo@autoxing.com", "tel": "(+86)13670013044"},
                       "issuer": {**od["header"]["issuer"], "name": "Alex Jeon", "attn": "Mr. Alex Jeon (alex@unde.co.kr)"}},
            "items": [{"description": "AX8114\n(S300)", "qty": 2, "price": 4600}, {"description": "SeaShippingcost", "qty": 1, "price": 2289}]})
        assert r.status_code == 200, r.text
        await c.post(F + f"/{oid}/paid", json={"date": "2026-10-05", "note": "T/T"})
    print("TOKEN", tok); print("PID", pid); print("OID", oid)
asyncio.run(main())
