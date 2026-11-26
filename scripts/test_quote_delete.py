# -*- coding: utf-8 -*-
"""작성 중 견적서 지우기(사용자 2026-11-26) — 개발 DB 에서 확인(테스트로 만든 견적서만 지운다).

    MSYS_NO_PATHCONV=1 docker compose run --rm -T --no-deps -e PYTHONPATH=/app -e PYTHONIOENCODING=utf-8 \\
        -v ./backend/app:/app/app -v ./scripts:/scripts backend python /scripts/test_quote_delete.py

- 내가 만든, 한 번도 발행하지 않은 작성 중 견적서 → 목록에 deletable, 지우면 사라짐
- 다른 사람 견적서 → 지울 수 없음(찾을 수 없음)
- 발행한 적이 있는 견적서(번호·발행 기록) → 지울 수 없음
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

from sqlalchemy import text

from app.company_knowledge import product_recommend as pr
from app.company_knowledge import quote_session as qs
from app.database import SessionLocal

assert "agent_ai_dev" in os.environ.get("DATABASE_URL", ""), "개발 DB 에서만 돌린다"
PASS: list[str] = []
FAIL: list[tuple[str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


async def make(user_id: int, *, issued: bool = False) -> int:
    st = qs.draft_state({"applicable": True, "items": []}, model="", payload_kg=None,
                        form={"customer": "[테스트] 지우기 확인 고객"}, manual=True)
    async with SessionLocal() as db:
        qid = (await db.execute(text(
            "INSERT INTO product_quotes (recommendation_id, user_id, status, customer, state, quote_no, history) "
            "VALUES (NULL, :u, :s, '[테스트] 지우기 확인 고객', CAST(:st AS jsonb), :no, CAST(:h AS jsonb)) RETURNING id"),
            {"u": user_id, "s": "issued" if issued else "draft", "st": json.dumps(st, ensure_ascii=False),
             "no": "QT-TS20261126-99" if issued else None, "h": json.dumps([{"revision": 0}] if issued else [])})).scalar()
        await db.commit()
    return qid


async def exists(qid: int) -> bool:
    async with SessionLocal() as db:
        return bool((await db.execute(text("SELECT 1 FROM product_quotes WHERE id = :i"), {"i": qid})).scalar())


async def cleanup(ids: list[int]) -> None:
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM product_quotes WHERE id = ANY(:i) AND customer = '[테스트] 지우기 확인 고객'"), {"i": ids})
        await db.commit()


async def main() -> None:
    async with SessionLocal() as db:
        me, other = [r[0] for r in (await db.execute(text("SELECT id FROM users WHERE is_active AND role <> 'superadmin' ORDER BY id LIMIT 2"))).all()]
    mine, others, issued = await make(me), await make(other), await make(me, issued=True)
    # 발행했다가 다시 고치는 중(작성 중이지만 번호·발행 기록 있음)
    reissue = await make(me, issued=True)
    async with SessionLocal() as db:
        await db.execute(text("UPDATE product_quotes SET status = 'draft' WHERE id = :i"), {"i": reissue})
        await db.commit()
    try:
        rows = {r["id"]: r for r in await pr.quote_list(user_id=me)}
        check(rows.get(mine, {}).get("deletable") is True, "내 작성 중 견적서 — 지울 수 있음 표시")
        check(rows.get(reissue, {}).get("deletable") is False, "발행 후 다시 고치는 견적서 — 지울 수 없음 표시")
        for qid, label in ((others, "다른 사람 견적서"), (issued, "발행한 견적서"), (reissue, "발행 후 다시 고치는 견적서")):
            try:
                await pr.quote_delete(qid, user_id=me)
                check(False, f"{label} — 지우기 거절")
            except pr.RecommendError as e:
                check(await exists(qid), f"{label} — 지우기 거절, 그대로 남음", str(e))
        # 최고 관리자(사용자 2026-11-26) — 모든 사람의 작성 중 견적서가 목록에(작성자 이름과), 열기·지우기 가능
        async with SessionLocal() as db:
            admin = (await db.execute(text("SELECT id FROM users WHERE role = 'superadmin' ORDER BY id LIMIT 1"))).scalar()
        arows = {r["id"]: r for r in await pr.quote_list(user_id=admin)}
        check(others in arows and arows[others]["mine"] is False and arows[others]["deletable"] is True and arows[others]["writer"],
              "최고 관리자 목록 — 다른 사람 작성 중 견적서도 작성자와 함께, 지울 수 있음", str(arows.get(others)))
        check(issued not in arows, "최고 관리자 목록 — 발행한 견적서는 안 나옴")
        check(arows.get(reissue, {}).get("deletable") is False, "최고 관리자 목록 — 발행 후 다시 고치는 견적서는 지울 수 없음")
        opened = await pr.quote_get(others, user_id=admin)
        check(opened.get("id") == others, "최고 관리자 — 다른 사람 작성 중 견적서 열기")
        try:
            await pr.quote_delete(reissue, user_id=admin)
            check(False, "최고 관리자도 발행한 적 있는 견적서는 지우기 거절")
        except pr.RecommendError:
            check(await exists(reissue), "최고 관리자도 발행한 적 있는 견적서는 지우기 거절")
        await pr.quote_delete(others, user_id=admin)
        check(not await exists(others), "최고 관리자 — 다른 사람 작성 중 견적서 지우기")
        try:
            await pr.quote_get(mine, user_id=other)
            check(False, "일반 사용자는 남의 작성 중 견적서 못 염")
        except pr.RecommendError:
            check(True, "일반 사용자는 남의 작성 중 견적서 못 염")
        await pr.quote_delete(mine, user_id=me)
        check(not await exists(mine), "내 작성 중 견적서 — 지워짐")
        check(mine not in {r["id"] for r in await pr.quote_list(user_id=me)}, "지운 뒤 목록에서 빠짐")
    finally:
        await cleanup([mine, others, issued, reissue])


asyncio.run(main())
for label in PASS:
    print("PASS", label)
for label, detail in FAIL:
    print("FAIL", label, detail[:200])
print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
sys.exit(1 if FAIL else 0)
