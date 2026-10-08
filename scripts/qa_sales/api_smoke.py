"""영업부 API·화면 점검(로그인 상태) — 결과를 한 줄씩."""
import asyncio, sys, time
import httpx
from sqlalchemy import text
from app.database import SessionLocal
from app.security import create_access_token

B, F = "http://backend:8000", "http://frontend:3000"
BAD = ("Application error", "Internal Server Error", "Unhandled Runtime Error", "NEXT_NOT_FOUND")

async def main():
    async with SessionLocal() as db:
        uid = (await db.execute(text("SELECT id FROM users WHERE username='alex'"))).scalar()
        pid = (await db.execute(text("SELECT id FROM proposal_projects WHERE stage='done' ORDER BY updated_at DESC LIMIT 1"))).scalar()
    tok, _ = create_access_token(str(uid))
    H = {"Authorization": f"Bearer {tok}"}
    fails = []
    async with httpx.AsyncClient(timeout=120) as c:
        for path in ["/v1/proposal-projects/catalog", "/v1/proposal-projects/knowledge-reviews",
                     "/v1/proposal-projects/product-images", f"/v1/proposal-projects/{pid}",
                     "/v1/product-recommend/corrections"]:
            t = time.time(); r = await c.get(B + path, headers=H)
            ok = r.status_code == 200
            print(f"API GET {path} {r.status_code} {round(time.time()-t,2)}s"); ok or fails.append(path)
        for path in ["/proposals/new", f"/proposals/{pid}", "/proposals/knowledge", "/proposals/recommend", "/"]:
            t = time.time(); r = await c.get(F + path, cookies={"und_cortex_token": tok}, follow_redirects=False)
            bad = [b for b in BAD if b in r.text]
            print(f"PAGE {path} {r.status_code} {round(time.time()-t,2)}s bytes={len(r.text)} {bad or ''}")
            if r.status_code != 200 or bad: fails.append(path)
        for path in ["/api/product-recommend/corrections", "/api/product-images", "/api/knowledge-reviews"]:
            r = await c.get(F + path, cookies={"und_cortex_token": tok})
            print(f"BFF GET {path} {r.status_code}"); r.status_code == 200 or fails.append(path)
        # 잘못된 입력 처리
        for path, body, want in [("/v1/product-recommend", {"request": "", "mode": "recommend"}, 400),
                                 ("/v1/product-recommend", {"request": "x", "mode": "chat"}, 400),
                                 (f"/v1/proposal-projects/{pid}/deck-edit", {"request": " "}, 400),
                                 (f"/v1/proposal-projects/{pid}/plan-confirm", {"picks": []}, 409),
                                 ("/v1/product-recommend/corrections/999999/review", {"action": "off"}, 400)]:
            r = await c.post(B + path, json=body, headers=H)
            print(f"NEG POST {path} -> {r.status_code} (want {want}) {r.text[:80]}")
            r.status_code == want or fails.append(f"NEG {path}")
    print("FAILS", fails)
asyncio.run(main())
