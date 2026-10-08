"""원클릭 작업 공용 첨부 입출력 — 요청 원문(.txt 첨부) 읽기, 결과 파일 저장.

영업 원클릭(공정 개념도, 제안서 본문)은 모달이 요청 원문을 .txt 첨부로 올리고,
결과 파일을 같은 소유자(=요청자)의 첨부로 저장해 챗에 다운로드 링크로 돌려준다.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass

from sqlalchemy import select

from .config import settings
from .database import SessionLocal
from .models import Attachment
from .storage import get_object_stream, make_object_key, put_object

MAX_REQUEST_CHARS = 4000


@dataclass
class ResultFile:
    filename: str
    download_url: str
    attachment_id: int | None = None


def decode_text(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp949"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def safe_name(s: str, default: str) -> str:
    s = re.sub(r'[\\/:*?"<>|\s]+', "_", s).strip("_.")
    return s[:40] or default


async def fetch_request_text(attachment_ids: list[int], *, what: str) -> tuple[str, int]:
    """첨부 중 첫 텍스트 파일 → (요청 원문, 소유자 id). what 은 오류 문구용("개념도 요청 내용")."""
    if not attachment_ids:
        raise ValueError(f"{what}이 없습니다.")
    async with SessionLocal() as db:
        res = await db.execute(select(Attachment).where(Attachment.id.in_(attachment_ids)))
        by_id = {a.id: a for a in res.scalars().all()}
    for att_id in attachment_ids:
        att = by_id.get(att_id)
        if att is None:
            continue
        if not ((att.mime or "").startswith("text/")
                or (att.original_filename or "").lower().endswith(".txt")):
            continue
        resp = get_object_stream(att.object_key)
        try:
            data = b"".join(resp.stream(amt=64 * 1024))
        finally:
            resp.close()
            resp.release_conn()
        text = decode_text(data).strip()
        if not text:
            raise ValueError(f"{what}이 비어 있습니다.")
        if len(text) > MAX_REQUEST_CHARS:
            raise ValueError(f"요청 내용이 너무 깁니다({len(text)}자). "
                             f"{MAX_REQUEST_CHARS}자 이내로 줄여 주세요.")
        return text, att.user_id
    raise ValueError(f"{what}(텍스트)을 찾지 못했습니다.")


async def save_result(owner_id: int, filename: str, data: bytes, mime: str) -> ResultFile:
    key = make_object_key(owner_id, filename)
    put_object(key=key, data=io.BytesIO(data), length=len(data), mime=mime)
    async with SessionLocal() as db:
        att = Attachment(user_id=owner_id, bucket=settings.minio_bucket, object_key=key,
                         original_filename=filename, mime=mime, size_bytes=len(data))
        db.add(att)
        await db.commit()
        await db.refresh(att)
        return ResultFile(filename, f"/api/attachments/{att.id}/download", att.id)
