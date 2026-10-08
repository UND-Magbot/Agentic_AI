"""제안서 흐름 실제 끝까지(HTTP, 배포된 백엔드) — QA 회차마다 같은 시나리오. 로그를 한 줄씩.
사용: python /out/e2e.py <tag>   (결과 pptx: /out/e2e_<tag>_v1.pptx, _v2.pptx)
"""
import asyncio, json, sys, time
import httpx
from sqlalchemy import text
from app.database import SessionLocal
from app.security import create_access_token
from app.storage import get_object_stream

B = "http://backend:8000/v1/proposal-projects"
TAG = sys.argv[1] if len(sys.argv) > 1 else "x"
REQ = """[고객 메일] 자동차 부품 2차 협력사 생산기술팀입니다. 알루미늄 다이캐스팅 브래킷(약 1.2kg, 180x120x40mm)을
사출(다이캐스팅) 설비 배출 컨베이어에서 꺼내 비전으로 외관 검사 후 양품은 트레이(4x5 칸)에 적재, 불량은 불량함으로
보내는 자동화를 검토합니다. 목표 처리량은 시간당 600개, 2교대입니다. 협동로봇 1대 구성을 희망하고 공압은 있습니다.
설비 앞 공간은 가로 3m x 세로 2.5m 정도입니다. 이번에는 실증(PoC) 후 양산 투자를 결정하려고 합니다."""
LOG = []
SCORE: dict = {}


def log(*a):
    s = " ".join(str(x) for x in a)
    LOG.append(s)
    print(s, flush=True)


async def wait(c, H, pid, cond, what, limit=900):
    t = time.time()
    while time.time() - t < limit:
        p = (await c.get(f"{B}/{pid}", headers=H)).json()
        if cond(p):
            log(f"  ok {what} {round(time.time() - t)}s")
            return p
        if (p.get("job") or {}).get("status") == "failed" and not p.get("job_alive"):
            log(f"  FAIL {what}: {p['job'].get('message')}")
            return p
        await asyncio.sleep(3)
    log(f"  TIMEOUT {what}")
    return p


async def post(c, H, path, body=None, want=(200, 201, 202)):
    r = await c.post(f"{B}/{path}" if path else B, json=body or {}, headers=H)
    if r.status_code not in want:
        log(f"  HTTP {r.status_code} {path}: {r.text[:200]}")
    return r


async def main():
    async with SessionLocal() as db:
        uid = (await db.execute(text("SELECT id FROM users WHERE username='alex'"))).scalar()
    tok, _ = create_access_token(str(uid))
    H = {"Authorization": f"Bearer {tok}"}
    t0 = time.time()
    async with httpx.AsyncClient(timeout=300) as c:
        r = await post(c, H, "", {"title": f"QA {TAG} 알루미늄 브래킷 취출·검사·적재", "request_text": REQ, "intake": {}})
        pid = r.json()["id"]
        log(f"project {pid}")
        p = await wait(c, H, pid, lambda p: p["stage"] == "questioning" and not p.get("reading"), "자료 읽기")
        filled = {k: v["status"] for k, v in p["items"].items() if v["status"] in ("confirmed", "assumed")}
        log(f"  읽은 문항 {len(filled)}개: {sorted(filled)[:20]}")
        rounds = 0
        while p["stage"] == "questioning" and rounds < 6:
            qs = p.get("questions") or []
            if not qs:
                break
            ans = []
            known = {"P02": "실증(PoC) 후 양산 투자 결정", "P03": "배출 컨베이어 이후 취출·외관 검사·트레이 적재·불량 배출까지",
                     "P06": "알루미늄 다이캐스팅 브래킷 1종", "P13": "협동로봇 1대 단일 컨셉"}
            req = set(p.get("required_open") or [])
            for q in qs:
                code = q["code"]
                if code in known:
                    ans.append({"code": code, "value": known[code]})
                elif code in req:
                    ans.append({"code": code, "value": "현장 확인 후 확정 — 실증 범위 기준으로 진행"})
                else:
                    ans.append({"code": code, "value": "", "unknown": True})
            log(f"  질문 {len(qs)}개 답함({[q['code'] for q in qs]})")
            await post(c, H, f"{pid}/answers", {"answers": ans})
            p = await wait(c, H, pid, lambda p: not p.get("reading"), "답 반영", 300)
            rounds += 1
        r = await post(c, H, f"{pid}/finish-questions")
        p = await wait(c, H, pid, lambda p: p.get("plan_pending") and not p.get("job_alive"), "계획 만들기(회상까지)")
        for a in p["alternatives"]:
            pl = a.get("plan") or {}
            log(f"  컨셉 {a['id']} {a['name']} | {a.get('robot')} | need={json.dumps(pl.get('robot_need'), ensure_ascii=False)[:160]}")
            log(f"     로봇 후보 {[ (x['label'], x.get('policy_note')) for x in pl.get('robot_candidates') or []]} 선택={pl.get('robot_pick')}")
            log(f"     그리퍼 AI={pl.get('gripper_ai')} 선택={pl.get('gripper_pick')}")
        log(f"  회사 경험 {[(h['title'][:30], h['fit']) for h in p['experience']]}")
        aid = p["alternatives"][0]["id"]
        await post(c, H, f"{pid}/revise", {"alt_id": aid, "request": "로봇 팔은 한화 제품 중에서 골라 주고, 불량함은 로봇 왼쪽에 둬 줘."})
        p = await wait(c, H, pid, lambda p: not p.get("job_alive"), "계획 수정(글만)")
        a = next(x for x in p["alternatives"] if x["id"] == aid)
        SCORE["plan_revise_hanwha"] = bool(a["plan"]["robot_candidates"]) and all("한화" in x["label"] for x in a["plan"]["robot_candidates"])
        SCORE["plan_revise_changed"] = bool((a.get("changes") or {}).get("added"))
        log(f"  수정 후 로봇 후보 {[x['label'] for x in a['plan']['robot_candidates']]} 바뀐점 +{len((a.get('changes') or {}).get('added', []))} -{len((a.get('changes') or {}).get('removed', []))} 이미지 {len(p['images'])}장")
        await post(c, H, f"{pid}/plan-confirm", {"picks": []})
        p = await wait(c, H, pid, lambda p: not p.get("job_alive") and not p.get("plan_pending"), "계획 확정·이미지", 1200)
        imgs = [i for i in p["images"] if i["status"] == "draft"]
        log(f"  이미지 {[(i['alt_id'], i['version'], i['status'], i.get('ref_sent'), i.get('ref_used')) for i in p['images']]}")
        for i in imgs:
            await post(c, H, f"{pid}/image-approve", {"image_id": i["id"]})
        await post(c, H, f"{pid}/finish-concept")
        p = await wait(c, H, pid, lambda p: p["stage"] == "structure" and not p.get("job_alive"), "구성·견적 초안", 600)
        log(f"  쪽 {[pg['type'] for pg in p['pages']]} 견적줄 {len(p['quote_lines'])}")
        await post(c, H, f"{pid}/approve-structure")
        p = await wait(c, H, pid, lambda p: p["stage"] == "done" and not p.get("job_alive"), "PPT 제작", 600)
        out = p["outputs"][-1]
        SCORE["deck_clean"] = not (out["report"].get("overflow") or out["report"].get("warnings"))
        log(f"  v{out['version']} report={json.dumps(out['report'], ensure_ascii=False)[:400]}")
        await post(c, H, f"{pid}/deck-edit", {"request": "2쪽 요지를 '실증으로 확인할 3가지(인식·파지·적재)'가 드러나게 바꾸고, 실증 쪽 확인 자료에 배출 영상도 넣어 줘."})
        p = await wait(c, H, pid, lambda p: not p.get("job_alive") and len(p["outputs"]) >= 2, "프롬프트 수정", 600)
        if len(p["outputs"]) >= 2:
            log(f"  v2 edit={json.dumps(p['outputs'][-1]['report'].get('edit'), ensure_ascii=False)[:300]}")
        async with SessionLocal() as db:
            deck = (await db.execute(text("SELECT deck FROM project_outputs WHERE project_id=:p ORDER BY version DESC LIMIT 1"), {"p": pid})).scalar()
        pages = {int(k): v for k, v in (deck or {}).get("text", {}).items()}
        types = {pg["page_no"]: pg["type"] for pg in (deck or {}).get("pages", [])}
        poc = next((pages[n] for n, t_ in types.items() if t_ == "poc" and n in pages), {})
        ov = next((pages[n] for n, t_ in types.items() if t_ == "overview" and n in pages), {})
        SCORE["edit_confirm"] = "영상" in (poc.get("confirm") or "")
        SCORE["edit_headline"] = all(w in (ov.get("headline") or "") for w in ("인식", "파지", "적재"))
        log(f"  수정 반영: confirm='{poc.get('confirm')}' headline='{ov.get('headline')}'")
        r = await post(c, H, f"{pid}/save-knowledge-all")
        log(f"  지식 저장 {r.status_code}")
    async with SessionLocal() as db:
        for o in p["outputs"]:
            key = (await db.execute(text("SELECT object_key FROM attachments WHERE id=:a"), {"a": o["attachment_id"]})).scalar()
            resp = get_object_stream(key)
            open(f"/out/e2e_{TAG}_v{o['version']}.pptx", "wb").write(b"".join(resp.stream(amt=65536)))
            resp.close(); resp.release_conn()
    SCORE["experience_hits"] = len(p.get("experience") or [])
    log(f"SCORE {json.dumps(SCORE, ensure_ascii=False)}")
    log(f"TOTAL {round(time.time() - t0)}s project={pid}")
    # 점검 데이터 정리 — 관리자 명의 '지식 저장'이 바로 승인돼 다음 회상을 오염시켰다(QA 3회차).
    async with SessionLocal() as db:
        keys = [f"project:{pid}:{x}" for x in "ABC"]
        cards = [r[0] for r in (await db.execute(text("SELECT id FROM experience_cards WHERE source_key = ANY(:k)"), {"k": keys})).all()]
        await db.execute(text("DELETE FROM knowledge_cues WHERE card_table='experience_cards' AND card_id = ANY(:c)"), {"c": cards})
        await db.execute(text("DELETE FROM experience_feedback WHERE project_id = :p"), {"p": pid})
        await db.execute(text("DELETE FROM experience_cards WHERE id = ANY(:c)"), {"c": cards})
        await db.execute(text("DELETE FROM recall_cache"))
        await db.execute(text("DELETE FROM proposal_projects WHERE id = :p"), {"p": pid})
        await db.commit()
    log(f"cleanup project {pid} cards {cards}")
    open(f"/out/e2e_{TAG}.log", "w", encoding="utf-8").write("\n".join(LOG))

asyncio.run(main())
