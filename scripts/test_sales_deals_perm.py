# -*- coding: utf-8 -*-
"""영업 건 관리 — 관리자 전용 동작이 일반 영업 사용자에게 막히는지(사용자 2026-10-08: 권한 제어 철저).

    docker compose run --rm --no-deps -e PYTHONPATH=/app -v ./backend/app:/app/app -v ./scripts:/scripts backend \\
        python /scripts/test_sales_deals_perm.py

DB 는 건드리지 않는다 — 권한 검사에서 막히는지만 본다(없는 건 번호 999999999 로 요청: 통과하면 404, 막히면 403).
"""
from __future__ import annotations

import asyncio
import sys

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app import api_sales_deals as api
from app.api_auth import get_current_user
from app.database import get_db
from app.models import User, UserDomain, UserRole

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


def app_as(role: UserRole) -> FastAPI:
    app = FastAPI()
    app.include_router(api.router)
    u = User(id=999999, username="perm_test", alias="권한점검", role=role, domain=UserDomain.sales, is_active=True)
    app.dependency_overrides[get_current_user] = lambda: u

    async def no_db():                       # 권한에서 막히면 DB 까지 오지 않는다 — 오면 404 로 '통과'를 알린다
        class _Db:
            async def execute(self, *_a, **_k):
                from fastapi import HTTPException
                raise HTTPException(404, "통과(DB 도달)")

            async def rollback(self):
                pass

            async def commit(self):
                pass
        yield _Db()
    app.dependency_overrides[get_db] = no_db
    return app


ADMIN_ONLY = [
    ("/v1/sales-deals/delete", {"ids": [999999999]}),
    ("/v1/sales-deals/999999999/remove", {"kind": "payments", "index": 0}),
    ("/v1/sales-deals/999999999/reset-to-quote", {}),
    ("/v1/sales-deals/999999999/unconfirm-term", {"index": 0}),
    ("/v1/sales-deals/999999999/won", {"won": None}),
    ("/v1/sales-deals/999999999/paid-full", {"full": False}),
    ("/v1/sales-deals/999999999/drop", {"dropped": False}),
]
OPEN = [
    ("/v1/sales-deals/999999999/drop", {"dropped": True, "reason": "x"}),
    ("/v1/sales-deals/999999999/confirm-term", {"index": 0}),
    ("/v1/sales-deals/999999999/won", {"won": True}),
]


async def run() -> None:
    for role, blocked in ((UserRole.member, True), (UserRole.domain_admin, False)):
        async with AsyncClient(transport=ASGITransport(app=app_as(role)), base_url="http://t") as c:
            for path, body in ADMIN_ONLY:
                r = await c.post(path, json=body)
                want = 403 if blocked else 404
                check(r.status_code == want, f"{role.value}: {path.split('/')[-1]} {body} → {want}", f"{r.status_code} {r.text[:80]}")
            for path, body in OPEN:
                r = await c.post(path, json=body)
                check(r.status_code == 404, f"{role.value}: {path.split('/')[-1]} {body} 은 누구나(→ DB 도달)", f"{r.status_code} {r.text[:80]}")
            r = await c.get("/v1/sales-deals")
            if r.status_code == 200:
                check(r.json()["me"]["admin"] is (not blocked), f"{role.value}: 리스트 me.admin")


asyncio.run(run())
print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
for label, detail in FAIL:
    print(f"  ✗ {label}: {detail}")
sys.exit(1 if FAIL else 0)
