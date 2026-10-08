"""제품 추천 평가 세트 — 회차마다 같은 10문제로 채점. 결과 행은 지운다."""
import asyncio, json, re, sys, time
from sqlalchemy import text
from app.database import SessionLocal
from app.company_knowledge import product_recommend as pr

MAG = r"마그네틱|Magnet|MG series|MGP|MGM|^MG\d"
UMB = {"Magbot EOAT", "Magbot ATC", "스위칭 마그네틱 기술", "Magbot ATC (스위칭 마그네틱)", "magbot DC Pnuematic Gripper (mDPG 시리즈)"}
GRIP = r"그리퍼|GRIPPER|Gripper|mDPG|EOAT|End Tool"
CASES = [
 ("알루미늄 판재(약 3kg, 400x300mm)를 컨베이어에서 집어 적재대로 옮긴다. 협동로봇에 장착.", r"mDPG|FINGER|JAW|CYLINDRICAL|Shape|시프트락|SMG|Finger", MAG),
 ("두께 3mm 강판(철, 약 20kg)을 프레스에서 꺼내 적재한다. 현장에 공압 설비 없음.", MAG, r"AMR|4족|LYNX|X30"),
 ("유리병(500ml, 약 0.8kg, 물기 있음)을 컨베이어에서 집어 박스에 넣는다.", r"mDPG|FINGER|JAW|CYLINDRICAL|Shape|시프트락|SMG|Finger", MAG),
 ("협동로봇 한 대로 여러 공정을 하려고 작업마다 그리퍼를 자동으로 바꿔 끼우고 싶다.", r"TC|ATC|툴체인저|툴체인져", r"AMR|4족|LYNX|X30|관제"),
 ("공장 안에서 부품이 담긴 선반 카트를 공정 사이로 자율 운반하고 싶다.", r"AMR|이송 로봇|ACR", GRIP),
 ("야외 변전소 험지를 돌아다니며 설비를 순찰·점검하는 로봇이 필요하다.", r"4족|LYNX|X30|Outdoor", GRIP),
 ("창고에서 팔레트에 실린 화물을 지게차처럼 들어 옮기는 자율 로봇이 필요하다.", r"포크리프트", GRIP),
 ("지름 50mm 원통형 파이프 부품을 옆에서 감싸 잡아 옮긴다.", r"CYLINDRICAL|FINGER|JAW|Shape|시프트락|SMG|Finger", r"AMR|4족"),
 ("모양이 제각각인 비정형 소형 부품을 형상에 맞춰 감싸 잡고 싶다.", r"Shape|시프트락|FINGER|SMG|Finger", r"AMR|4족"),
 ("공장에 있는 여러 대 AMR과 로봇 상태를 한 화면에서 통합 모니터링하고 싶다.", r"관제", GRIP),
]

async def main():
    rows, ids = [], []
    for q, ok_re, bad_re in CASES:
        t = time.time()
        try:
            r = await pr.recommend(q, user_id=None)
        except Exception as e:
            rows.append({"q": q[:30], "err": str(e)[:80]}); continue
        ids.append(r["id"])
        names = [i["product"] for i in r["items"]]
        top_ok = bool(names) and bool(re.search(ok_re, names[0]))
        forb = [n for n in names if re.search(bad_re, n)]
        umb = [n for n in names if n in UMB]
        rows.append({"q": q[:34], "items": names, "top_ok": top_ok, "forbidden": forb, "umbrella": umb,
                     "sec": round(time.time() - t, 1)})
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM product_recommendations WHERE id = ANY(:i)"), {"i": ids}); await db.commit()
    n = len(CASES)
    s_top = sum(r.get("top_ok", False) for r in rows); s_forb = sum(1 for r in rows if not r.get("forbidden") and "err" not in r)
    s_umb = sum(1 for r in rows if not r.get("umbrella") and "err" not in r)
    for r in rows: print(json.dumps(r, ensure_ascii=False))
    print(f"SCORE top1 {s_top}/{n} · 금지 없음 {s_forb}/{n} · 묶음카드 없음 {s_umb}/{n}")
asyncio.run(main())
