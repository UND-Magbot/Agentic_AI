"""버그 등록(api_bug_reports.py) 점검 — 번호 자동(UND-00001 … 가장 큰 번호 + 1), 사진 저장, 잘못된 입력 거절.
테스트로 만든 등록·사진은 스스로 지운다.

    docker compose run --rm --no-deps -e PYTHONPATH=/app -v ./backend/app:/app/app -v ./scripts:/scripts backend \\
        python /scripts/test_bug_reports.py
"""
from __future__ import annotations

import asyncio
import base64
import sys

import httpx
from sqlalchemy import select, text

from app.api_auth import get_current_user
from app.api_bug_reports import next_report_no
from app.database import SessionLocal
from app.main import app
from app.models import User
from app.storage import get_object_stream, remove_object

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []
# 1×1 PNG
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


async def main() -> int:
    check(next_report_no(None) == "UND-00001" and next_report_no("UND-00010") == "UND-00011"
          and next_report_no("UND-99999") == "UND-100000", "번호: 처음 00001, 10 다음 00011")
    async with SessionLocal() as db:
        user = (await db.execute(select(User).where(User.username == "alex"))).scalar_one()
        last = (await db.execute(text("SELECT report_no FROM bug_reports ORDER BY CAST(substring(report_no FROM 5) AS BIGINT) DESC LIMIT 1"))).scalar()
    app.dependency_overrides[get_current_user] = lambda: user
    made: list[str] = []
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            r1 = await c.post("/v1/bug-reports", data={"title": "  테스트 버그  ", "content": "견적서 다운로드 안 됨"},
                              files=[("files", ("cap.png", PNG, "image/png"))])
            r2 = await c.post("/v1/bug-reports", data={"title": "두 번째", "content": "내용"})
            made += [r.json().get("report_no") for r in (r1, r2) if r.status_code == 201]
            want1 = next_report_no(last)
            check(r1.status_code == 201 and r1.json()["report_no"] == want1 and r1.json()["photos"] == 1,
                  "첫 등록 = 가장 큰 번호 + 1, 사진 1장", f"{r1.status_code} {r1.text[:120]} 기대 {want1}")
            check(r2.status_code == 201 and r2.json()["report_no"] == next_report_no(want1), "다음 등록은 그다음 번호", r2.text[:120])
            bad = await c.post("/v1/bug-reports", data={"title": "x", "content": "y"},
                               files=[("files", ("a.pdf", b"%PDF-1.4", "application/pdf"))])
            empty = await c.post("/v1/bug-reports", data={"title": " ", "content": "y"})
            many = await c.post("/v1/bug-reports", data={"title": "x", "content": "y"},
                                files=[("files", (f"{i}.png", PNG, "image/png")) for i in range(6)])
            check(bad.status_code == 400 and "사진" in bad.json()["detail"], "사진이 아닌 파일 거절", bad.text[:100])
            check(empty.status_code == 400 and many.status_code == 400, "빈 제목·사진 6장 거절", f"{empty.status_code} {many.status_code}")
            guest = app.dependency_overrides.pop(get_current_user)
            anon = await c.post("/v1/bug-reports", data={"title": "x", "content": "y"})
            app.dependency_overrides[get_current_user] = guest
            check(anon.status_code == 401, "로그인 안 하면 거절", str(anon.status_code))
        async with SessionLocal() as db:
            row = (await db.execute(text("SELECT title, photos, reporter_id FROM bug_reports WHERE report_no = :n"), {"n": made[0]})).one()
        obj = get_object_stream(row.photos[0]["key"])
        data = obj.read()
        obj.close()
        obj.release_conn()
        check(row.title == "테스트 버그" and row.reporter_id == user.id and data == PNG
              and row.photos[0]["key"] == f"bug-reports/{made[0]}/1.png", "저장: 제목 정리·등록자·사진 MinIO", str(row.photos))
    finally:
        async with SessionLocal() as db:
            rows = (await db.execute(text("SELECT photos FROM bug_reports WHERE report_no = ANY(:n)"), {"n": made})).scalars().all()
            for photos in rows:
                for p in photos:
                    try:
                        remove_object(p["key"])
                    except Exception:
                        pass
            await db.execute(text("DELETE FROM bug_reports WHERE report_no = ANY(:n)"), {"n": made})
            await db.commit()
    print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
    for label, detail in FAIL:
        print(f"  ✗ {label}: {detail}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
