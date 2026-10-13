# -*- coding: utf-8 -*-
"""발행번호(doc_numbers)·내 정보(/v1/me/profile) 점검(사용자 2026-10-12) — 개발 DB(agent_ai_dev)에 시험 번호를 만들고 끝에 지운다.

    docker compose run --rm --no-deps -e PYTHONPATH=/app -v ./backend/app:/app/app -v ./scripts:/scripts backend \\
        python /scripts/test_doc_numbers.py

확인: 같은 발행자·날짜는 01, 02 … 순서(동시에 내도 안 겹침) / 발행자·문서 종류·날짜별로 따로 / 잘못된 이니셜 거절 /
내 정보 저장·불러오기·검사(본인 것만).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import sys

import httpx
from sqlalchemy import select, text

from app.api_auth import get_current_user
from app.database import SessionLocal
from app.main import app
from app.models import User
from app.sales_deals import doc_numbers as dn

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []
DAY = dt.date(2000, 1, 3)              # 실제 발행과 겹치지 않는 지난 날짜
ISSUERS = ("ZQ", "ZX")


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


async def take(doc_type: str, issuer: str, day: dt.date = DAY) -> str:
    async with SessionLocal() as db:
        no = await dn.allocate(db, doc_type, issuer, day)
        await db.commit()
        return no


async def cleanup() -> None:
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM doc_numbers WHERE issuer = ANY(:i)"), {"i": list(ISSUERS)})
        await db.commit()


async def main() -> None:
    await cleanup()
    try:
        a1, a2 = await take(dn.QUOTE, "zq"), await take(dn.QUOTE, "ZQ")
        check((a1, a2) == ("QT-ZQ20000103-01", "QT-ZQ20000103-02"), "같은 발행자·같은 날 → 01, 02", f"{a1} {a2}")
        check(await take(dn.QUOTE, "ZX") == "QT-ZX20000103-01", "다른 발행자는 그 사람 01부터")
        check(await take(dn.STATEMENT, "ZQ") == "TS-ZQ20000103-01", "거래명세서는 견적서와 따로(첫 장 01)")
        check(await take(dn.QUOTE, "ZQ", DAY + dt.timedelta(days=1)) == "QT-ZQ20000104-01", "날짜가 바뀌면 다시 01")
        # 동시에 10장 — 잠금으로 번호가 겹치지 않음
        many = await asyncio.gather(*(take(dn.QUOTE, "ZQ") for _ in range(10)))
        seqs = sorted(int(n.rsplit("-", 1)[1]) for n in many)
        check(seqs == list(range(3, 13)), "동시에 발행해도 번호가 겹치지 않음(03~12)", str(seqs))
        # 실패해 되돌린 발행은 번호도 안 나감
        async with SessionLocal() as db:
            await dn.allocate(db, dn.QUOTE, "ZQ", DAY)
            await db.rollback()
        check(await take(dn.QUOTE, "ZQ") == "QT-ZQ20000103-13", "되돌린(발행 실패) 번호는 남지 않아 다음이 13")
        for bad in ("", "Z", "Z1", "ABCDE"):
            try:
                async with SessionLocal() as db:
                    await dn.allocate(db, dn.QUOTE, bad, DAY)
                check(False, f"잘못된 이니셜 거절: {bad!r}")
            except ValueError:
                check(True, f"잘못된 이니셜 거절: {bad!r}")
    finally:
        await cleanup()

    # ── 내 정보 ──
    async with SessionLocal() as db:
        user = (await db.execute(select(User).where(User.username == "mason"))).scalar_one()
        before = dict((await db.execute(text("SELECT initials, name_ko, title, mobile, email FROM users WHERE id = :i"),
                                        {"i": user.id})).mappings().one())
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            r = await c.put("/v1/me/profile", json={"initials": " ms ", "name_ko": "메이슨  테스트", "title": "과장",
                                                    "mobile": "010-1234-5678", "email": before["email"] or ""})
            got = (await c.get("/v1/me/profile")).json()
            check(r.status_code == 200 and got["initials"] == "MS" and got["name_ko"] == "메이슨 테스트" and got["mobile"] == "010-1234-5678",
                  "내 정보 저장·불러오기(이니셜 대문자, 공백 정리)", str(got))
            for body, why in (({"initials": "M1"}, "이니셜"), ({"mobile": "전화"}, "휴대폰"), ({"email": "x@"}, "이메일")):
                r = await c.put("/v1/me/profile", json=body)
                check(r.status_code == 422, f"내 정보 검사 — 잘못된 {why} 거절", str(r.status_code))
    finally:
        app.dependency_overrides.clear()
        async with SessionLocal() as db:
            await db.execute(text("UPDATE users SET initials = :initials, name_ko = :name_ko, title = :title, mobile = :mobile, "
                                  "email = :email WHERE id = :i"), {**before, "i": user.id})
            await db.commit()


asyncio.run(main())
print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
for label, detail in FAIL:
    print(f"  ✗ {label}: {detail}")
sys.exit(1 if FAIL else 0)
