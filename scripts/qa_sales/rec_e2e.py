"""제품 추천 화면 흐름(HTTP): Q&A 추천 → 대화 질문 → 조건 바꿔 재추천 → 대화 중 정정(확인) → 정보 정정 저장 →
비슷한 조건 재추천에서 재고되는지. 만든 기록은 지운다."""
import asyncio, json, time
import httpx
from sqlalchemy import text
from app.database import SessionLocal
from app.security import create_access_token

B = "http://backend:8000/v1/product-recommend"
QNA = ("Q. 무엇을 다룹니까?\nA. 알루미늄 압출 프로파일 조각\nQ. 크기·무게·재질?\nA. 약 2kg, 300mm 길이, 표면 매끈\n"
       "Q. 어떤 동작?\nA. 컨베이어에서 집어 상자에 담기\nQ. 환경?\nA. 협동로봇 장착, 공압 있음")


async def main():
    async with SessionLocal() as db:
        uid = (await db.execute(text("SELECT id FROM users WHERE username='alex'"))).scalar()
    H = {"Authorization": f"Bearer {create_access_token(str(uid))[0]}"}
    score, rec_ids, cor_ids = {}, [], []
    async with httpx.AsyncClient(timeout=300) as c:
        async def ask(body):
            t = time.time()
            r = await c.post(B, json=body, headers=H)
            d = r.json()
            print(f"  [{round(time.time()-t)}s] {body['mode']}: {body['request'][:40]!r} -> {r.status_code} {d.get('kind')}")
            return d
        d = await ask({"request": QNA, "mode": "recommend"})
        rec = d["recommendation"]; rec_ids.append(rec["id"])
        names = [i["product"] for i in rec["items"]]
        print("   추천", names, "사진", [i.get("image_id") for i in rec["items"]])
        score["no_magnet_for_aluminum"] = not any("마그네틱" in n or "Magnet" in n for n in names)
        hist = []
        d = await ask({"request": "1번은 왜 골랐어?", "mode": "chat", "recommendation_id": rec["id"], "history": hist})
        score["chat_answer"] = d.get("kind") == "answer" and bool(d.get("answer")) and not d.get("recommendation")
        hist += [{"role": "user", "text": "1번은 왜 골랐어?"}, {"role": "assistant", "text": d.get("answer", "")}]
        d = await ask({"request": "공압 없이 쓸 수 있는 제품으로 다시 골라 줘", "mode": "chat", "recommendation_id": rec["id"], "history": hist})
        score["chat_re_recommend"] = d.get("kind") == "answer" and bool(d.get("recommendation"))
        if d.get("recommendation"):
            rec_ids.append(d["recommendation"]["id"]); print("   재추천", [i["product"] for i in d["recommendation"]["items"]])
        wrong = names[0] if names else "mDPG-S series"
        d = await ask({"request": f"{wrong} 말고 2FINGER_GRIPPER 를 쓸 거야. 프로파일 단면이 요철이라 흡착이 새서.",
                       "mode": "chat", "recommendation_id": rec["id"], "history": hist})
        score["chat_correction_confirm"] = d.get("kind") == "confirm"
        d = await ask({"request": f"{wrong} 말고 2FINGER_GRIPPER 를 쓸 거야. 프로파일 단면이 요철이라 흡착이 새서.",
                       "mode": "correct", "recommendation_id": rec["id"]})
        score["correct_saved"] = d.get("kind") == "saved"
        if d.get("kind") == "saved":
            cor_ids.append(d["correction"]["id"]); print("   정정", json.dumps({k: d["correction"][k] for k in ("wrong_name", "right_name", "reason")}, ensure_ascii=False))
        d = await ask({"request": QNA.replace("압출 프로파일 조각", "요철 있는 압출 프로파일 부품").replace("2kg", "1.5kg"), "mode": "recommend"})
        rec2 = d["recommendation"]; rec_ids.append(rec2["id"])
        names2 = [i["product"] for i in rec2["items"]]
        print("   정정 뒤 추천", names2, "재고", [r["correction_id"] for r in rec2["reconsidered"]])
        score["reconsidered"] = bool(cor_ids) and any(r["correction_id"] == cor_ids[0] for r in rec2["reconsidered"])
        score["avoided_wrong"] = not names2 or names2[0] != wrong
        r = await c.get(B + "/corrections", headers=H)
        score["list_has_it"] = any(x["id"] in cor_ids for x in r.json()["items"])
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM product_corrections WHERE id = ANY(:i)"), {"i": cor_ids})
        await db.execute(text("DELETE FROM product_recommendations WHERE id = ANY(:i)"), {"i": rec_ids})
        await db.commit()
    print("SCORE", json.dumps(score, ensure_ascii=False), f"{sum(score.values())}/{len(score)}")

asyncio.run(main())
