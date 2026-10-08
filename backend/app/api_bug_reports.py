"""버그 등록 — 프로필 메뉴의 [버그 등록]에서 누구나(사용자 2026-10-08).

- POST /v1/bug-reports : multipart {title, content, files[]} → 번호 UND-00001 부터 자동(지금 가장 큰 번호 + 1).

번호는 같은 순간에 두 명이 등록해도 겹치지 않게 트랜잭션 잠금(pg_advisory_xact_lock) 안에서 매긴다.
사진은 MinIO bug-reports/{번호}/{순번}.{확장자} — 이미지만, 장당 10MB, 5장까지.
"""
from __future__ import annotations

import io
import json
import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .api_auth import get_current_user
from .database import get_db
from .models import User
from .storage import put_object, remove_object

router = APIRouter(prefix="/v1/bug-reports", tags=["BugReports"])

PREFIX = "UND-"
PHOTO_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}
MAX_PHOTOS = 5
MAX_PHOTO_BYTES = 10 * 1024 * 1024
TITLE_MAX = 120
CONTENT_MAX = 5000
_NUMBER_LOCK = 7_202_610_08          # 번호 매기기 잠금 키(이 기능 전용)


def next_report_no(last: str | None) -> str:
    """가장 큰 번호 다음 — 없으면 UND-00001, UND-00010 다음은 UND-00011."""
    n = int(last[len(PREFIX):]) + 1 if last and re.fullmatch(rf"{PREFIX}\d+", last) else 1
    return f"{PREFIX}{n:05d}"


def _bad(msg: str) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, detail=msg)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_bug_report(
    title: str = Form(...),
    content: str = Form(...),
    files: list[UploadFile] = File(default=[]),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    title, content = title.strip(), content.strip()
    if not title:
        raise _bad("제목을 적어 주세요.")
    if not content:
        raise _bad("상세 내용을 적어 주세요.")
    if len(title) > TITLE_MAX or len(content) > CONTENT_MAX:
        raise _bad(f"제목은 {TITLE_MAX}자, 상세 내용은 {CONTENT_MAX}자까지입니다.")
    files = [f for f in files if f.filename]
    if len(files) > MAX_PHOTOS:
        raise _bad(f"사진은 {MAX_PHOTOS}장까지 올릴 수 있습니다.")
    photos: list[tuple[bytes, str, str]] = []
    for f in files:
        mime = (f.content_type or "").lower()
        if mime not in PHOTO_TYPES:
            raise _bad(f"'{f.filename}' — 사진(PNG·JPG·WEBP·GIF)만 올릴 수 있습니다.")
        data = await f.read(MAX_PHOTO_BYTES + 1)
        if len(data) > MAX_PHOTO_BYTES:
            raise _bad(f"'{f.filename}' — 사진은 장당 10MB까지입니다.")
        photos.append((data, mime, f.filename or ""))

    await db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _NUMBER_LOCK})
    last = (await db.execute(text(
        "SELECT report_no FROM bug_reports WHERE report_no ~ '^UND-[0-9]+$' "
        "ORDER BY CAST(substring(report_no FROM 5) AS BIGINT) DESC LIMIT 1"))).scalar()
    report_no = next_report_no(last)
    saved: list[dict] = []
    try:
        for i, (data, mime, name) in enumerate(photos, 1):
            key = f"bug-reports/{report_no}/{i}{PHOTO_TYPES[mime]}"
            put_object(key=key, data=io.BytesIO(data), length=len(data), mime=mime)
            saved.append({"key": key, "mime": mime, "size": len(data), "filename": name[:200]})
        row = (await db.execute(text(
            "INSERT INTO bug_reports (report_no, title, content, photos, reporter_id) "
            "VALUES (:n, :t, :c, CAST(:p AS jsonb), :u) RETURNING id, created_at"),
            {"n": report_no, "t": title, "c": content, "p": json.dumps(saved, ensure_ascii=False), "u": user.id})).one()
        await db.commit()
    except Exception:
        await db.rollback()
        for p in saved:                        # 등록이 안 됐으면 올린 사진도 지운다
            try:
                remove_object(p["key"])
            except Exception:
                pass
        raise
    return {"id": row.id, "report_no": report_no, "created_at": row.created_at.isoformat(), "photos": len(saved)}
