"""영업 건 관리·툴체인저 대화 배포 점검(로그인 상태) — 결과를 한 줄씩. 컨테이너 안에서 실행.

    MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -e PYTHONPATH=/app -e PYTHONIOENCODING=utf-8 \\
        -v ./scripts/qa_sales:/out -v ./docs:/docs backend python /out/deals_smoke.py

실제 사내 AI 로 샘플 1.1 대화를 한 번 돌린다(추천 기록은 끝에 지운다).
"""
import asyncio
import json
import re
import time
from pathlib import Path

import httpx
from sqlalchemy import text

from app.company_knowledge import atc_meeting as am
from app.company_knowledge import plain_text as pt
from app.company_knowledge import product_recommend as pr
from app.database import SessionLocal
from app.security import create_access_token

B, F = "http://backend:8000", "http://frontend:3000"
BAD = ("Application error", "Internal Server Error", "Unhandled Runtime Error", "NEXT_NOT_FOUND")
QUESTION = ("TCV1, TCV2 같은 ATC 는 왜 후보에 없나요? M-LTC-0005A, MTC, DTC 는 다른 계열 툴체인저 후보를 말하는 거 아닌가요? "
            "그 다음 추천 2순위 후보가 궁금합니다")


CODE = re.compile(pt._CODE)


def sample_intake(name: str = "샘플1.1_협동_사출품이송_추가질문완료", robots: list | None = None,
                  tool_count: int = 2, env: bool = False) -> dict:
    f = am.parse_form(Path(f"/docs/atc_meeting/{name}.docx").read_bytes())
    ok = {"master_4m_sufficient": True, "system_1m_sufficient": True}
    robots = robots or [{"id": "R1", "model": "M1013", "type": "cobot", "quantity": 1, "speed": {"value": 60, "unit": "%"},
                         "cables": dict(ok)}]
    b = {"project": {"process": {"category": "transfer"}, "tool_count": tool_count,
                     "environment": {"has_special_conditions": env}}, "robots": robots, "tools": []}
    am.overlay(b, f)
    return b


def leftovers(o, key=None, path="") -> list[str]:
    """응답 안 사람이 읽는 문장에 남은 규칙 번호(값 칸·입력은 제외 — plain_text.SKIP_KEYS)."""
    if key in pt.SKIP_KEYS:
        return []
    if isinstance(o, str):
        return [f"{path}: {o[:70]}"] if CODE.search(o) and not pt._FULL.fullmatch(o) else []
    if isinstance(o, dict):
        return [x for k, v in o.items() for x in leftovers(v, k, f"{path}.{k}")]
    if isinstance(o, list):
        return [x for i, v in enumerate(o) for x in leftovers(v, key, f"{path}[{i}]")]
    return []


async def main():
    async with SessionLocal() as db:
        uid = (await db.execute(text("SELECT id FROM users WHERE username='alex'"))).scalar()
    tok, _ = create_access_token(str(uid))
    H = {"Authorization": f"Bearer {tok}"}
    fails = []

    def report(ok: bool, *msg) -> None:
        print("OK  " if ok else "FAIL", *msg)
        if not ok:
            fails.append(msg[0])

    async with httpx.AsyncClient(timeout=300) as c:
        r = await c.get(B + "/v1/sales-deals", headers=H)
        rows = r.json().get("rows", []) if r.status_code == 200 else []
        dates = [x["base_date"] for x in rows if x["base_date"]]
        report(r.status_code == 200 and len(rows) >= 188 and dates == sorted(dates, reverse=True),
               "백엔드 /v1/sales-deals", r.status_code, f"{len(rows)}건 최신순")
        r = await c.get(F + "/deals", cookies={"und_cortex_token": tok}, follow_redirects=False)
        report(r.status_code in (307, 308) and "tab=deals" in r.headers.get("location", ""), "예전 주소 /deals → 영업 건 탭", r.status_code)
        for path in ["/proposals/recommend?tab=deals", "/proposals/recommend"]:
            t = time.time()
            r = await c.get(F + path, cookies={"und_cortex_token": tok})
            bad = [b for b in BAD if b in r.text]
            report(r.status_code == 200 and not bad, "화면", path, r.status_code, f"{time.time() - t:.1f}s", bad or "")
        r = await c.get(F + "/api/sales-deals", cookies={"und_cortex_token": tok})
        report(r.status_code == 200 and len(r.json().get("rows", [])) >= 188, "BFF 리스트", r.status_code)
        if rows:
            r = await c.get(F + f"/api/sales-deals/{rows[0]['id']}", cookies={"und_cortex_token": tok})
            report(r.status_code == 200 and "events" in r.json(), "BFF 한 건 + 이력", r.status_code)

        # 실제 사내 AI — 샘플 1.1 결과에 '왜 TCV1·TCV2 가 없나 / 2순위' 질문
        intake = sample_intake()
        rec = await pr.atc_recommend(intake, user_id=uid)
        try:
            t = time.time()
            async with c.stream("POST", B + "/v1/product-recommend/atc/agent", headers=H,
                                json={"intake": intake, "message": QUESTION, "recommendation_id": rec["id"]}) as resp:
                answer = ""
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    ev = json.loads(line)
                    if ev["t"] == "answer":
                        answer += ev["text"]
                    elif ev["t"] in ("answer_set",):
                        answer = ev["text"]
                    elif ev["t"] == "done":
                        answer = ev["answer"]
            print(f"     AI 답({time.time() - t:.0f}s): {answer}")
            report("화면 안내" not in answer and "이대로 답" not in answer and "Q&A 고치기" not in answer,
                   "지시문이 답에 새지 않음")
            report("TCV1" in answer, "다음으로 볼 모델 TCV1 안내")
            report(not any(f"{m}" in s and "2순위" in s for s in answer.split(".") for m in ("M-LTC", "MTC", "DTC")
                           if "아니" not in s and "아닙" not in s),
                   "다른 계열을 2순위라고 하지 않음")
            report(not CODE.search(answer), "AI 답에 규칙 번호(R02·C11…) 없음")
        finally:
            async with SessionLocal() as db:
                await db.execute(text("DELETE FROM product_recommendations WHERE id = :i"), {"i": rec["id"]})
                await db.commit()

        # 질문지 화면 원본·샘플 목록·확정 제안 내역에도 규칙 번호가 없는지
        for path in ["/atc/spec", "/atc/test-samples", "/proposals"]:
            r = await c.get(B + "/v1/product-recommend" + path, headers=H)
            left = leftovers(r.json()) if r.status_code == 200 else ["응답 실패"]
            report(not left, f"{path} 화면 문장에 규칙 번호 없음", r.status_code, "; ".join(left[:3]))

        # 화면으로 가는 추천 결과(배포된 API 응답)에 규칙 번호가 남지 않는지 — 검토 사유가 많은 샘플들
        cobot = lambda m, v, **c: {"id": "R1", "model": m, "type": "cobot", "quantity": 1,  # noqa: E731
                                   "speed": {"value": v, "unit": "%"}, "cables": {"master_4m_sufficient": True,
                                                                                  "system_1m_sufficient": True, **c}}
        cases = {"샘플1.1_협동_사출품이송_추가질문완료": ([cobot("M1013", 60)], 2, False),
                 "샘플2.1_산업용_차체패널_추가질문완료": ([{**cobot("HS220", 40, master_4m_sufficient=False),
                                                       "type": "industrial", "quantity": 2}], 3, True),
                 "샘플4.1_압력경계_통신_추가질문완료": ([cobot("UR10e", 50, system_1m_sufficient=False)], 2, True),
                 "샘플9_산업용_도어이송": ([{**cobot("R-2000iC", 45), "type": "industrial"}], 2, False)}
        made = []
        try:
            for name, (robots, n, env) in cases.items():
                it = sample_intake(name, robots, n, env)
                r = await c.post(B + "/v1/product-recommend/atc/recommend", headers=H, json={"intake": it})
                body = r.json()
                if r.status_code == 200:
                    made.append(body["recommendation"]["id"])
                left = leftovers(body)
                report(r.status_code == 200 and not left, f"{name.split('_')[0]} 화면 문장에 규칙 번호 없음", "; ".join(left[:3]))
        finally:
            if made:
                async with SessionLocal() as db:
                    await db.execute(text("DELETE FROM product_recommendations WHERE id = ANY(:i)"), {"i": made})
                    await db.commit()
    print("ALL OK" if not fails else f"FAILED {fails}")


asyncio.run(main())
