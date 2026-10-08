"""첨부 파일 업로드/다운로드 라우터.

- POST /v1/attachments               : multipart 업로드 → MinIO + DB 메타.
- GET  /v1/attachments/{id}          : 메타 조회 (본인/superadmin 만).
- GET  /v1/attachments/{id}/download : presigned URL 로 302 redirect (대역폭 절감).

화이트리스트(MIME) 와 사이즈 제한은 settings.attachment_max_bytes 로 제어.
1차에서는 이미지/PDF/문서/텍스트 위주로 한정 — 멀티모달 도입 시 자연 확장.
"""
from __future__ import annotations

import os
import urllib.parse
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .api_auth import get_current_user
from .config import settings
from .database import get_db
from .models import Attachment, User
from .storage import (
    get_object_stream,
    make_object_key,
    put_object,
    remove_object,
)

router = APIRouter(prefix="/v1/attachments", tags=["Attachments"])


# 1차 화이트리스트 — 사내 일반 업무 첨부 위주. 필요 시 확장.
_ALLOWED_MIME: set[str] = {
    # 이미지
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/webp",
    "image/gif",
    "image/bmp",
    "image/svg+xml",
    # 문서
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.oasis.opendocument.text",
    "application/rtf",
    # 텍스트/코드
    "text/plain",
    "text/markdown",
    "text/csv",
    "text/html",
    "application/json",
    "application/xml",
    "text/xml",
    # 압축 (메타만, 본문 추출은 후속)
    "application/zip",
    "application/x-zip-compressed",
    # 오디오 — 회의 녹음 → 주간 회의록 보고 STT 입력.
    "audio/mp4",
    "audio/x-m4a",
    "audio/m4a",
    "audio/aac",
    "audio/mpeg",
    "audio/mp3",
    "audio/wav",
    "audio/x-wav",
    "audio/wave",
    "audio/webm",
    "audio/ogg",
    "audio/flac",
}


# 확장자 → MIME 보정 테이블.
# 일부 OS/브라우저(특히 Windows + 한글 파일명)는 m4a / xlsx 같이 명확한 확장자에도
# `application/octet-stream` 또는 빈 값을 보낸다. 화이트리스트만으로는 정당한 파일이
# 415 로 차단되므로, content_type 이 octet-stream/빈 값일 때만 확장자로 1차 추론.
_EXT_MIME_FALLBACK: dict[str, str] = {
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".aac": "audio/aac",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".webm": "audio/webm",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".json": "application/json",
    ".xml": "application/xml",
    # draw.io 원본 — 개념도 수정본을 올려 확정할 때(confirmed.py). 본문은 mxfile XML.
    ".drawio": "application/xml",
    ".zip": "application/zip",
}


def _resolve_mime(content_type: str | None, filename: str | None) -> str:
    """업로드 mime 결정. 모호하면(빈 값/octet-stream) 확장자로 보정."""
    ct = (content_type or "").lower().strip()
    if ct and ct != "application/octet-stream":
        return ct
    if filename:
        ext = os.path.splitext(filename)[1].lower()
        guess = _EXT_MIME_FALLBACK.get(ext)
        if guess:
            return guess
    return ct or "application/octet-stream"


class AttachmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    mime: str
    size_bytes: int
    created_at: datetime
    # 백엔드 프록시 다운로드 경로. 호스트 브라우저에서 안전하게 호출 가능.
    download_url: str


def _serialize(att: Attachment) -> AttachmentResponse:
    return AttachmentResponse(
        id=att.id,
        filename=att.original_filename,
        mime=att.mime,
        size_bytes=att.size_bytes,
        created_at=att.created_at,
        download_url=f"/v1/attachments/{att.id}/download",
    )


async def _load_owned(
    db: AsyncSession, att_id: int, user: User
) -> Attachment:
    res = await db.execute(select(Attachment).where(Attachment.id == att_id))
    att = res.scalar_one_or_none()
    if att is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="첨부를 찾을 수 없습니다.")
    if user.role.value != "superadmin" and att.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="첨부를 찾을 수 없습니다.")
    return att


@router.post(
    "",
    response_model=AttachmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_attachment(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # 1) MIME / 사이즈 가드
    mime = _resolve_mime(file.content_type, file.filename)
    if mime not in _ALLOWED_MIME:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"지원하지 않는 파일 형식입니다 ({mime}).",
        )

    # UploadFile 은 SpooledTemporaryFile 이라 size 를 정확히 모르는 경우가 있다.
    # seek(0,2) 로 끝까지 가서 길이 측정 후 다시 0 으로 복귀.
    file.file.seek(0, 2)
    size = file.file.tell()
    file.file.seek(0)
    if size <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="빈 파일입니다.")
    if size > settings.attachment_max_bytes:
        mb = settings.attachment_max_bytes // (1024 * 1024)
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"파일이 너무 큽니다. 최대 {mb}MB 까지 허용됩니다.",
        )

    original_name = file.filename or "untitled"

    # 2) MinIO 업로드
    key = make_object_key(current_user.id, original_name)
    try:
        put_object(key=key, data=file.file, length=size, mime=mime)
    except Exception as e:  # pragma: no cover — MinIO 장애 가시화.
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=f"객체 저장 실패: {e}",
        )

    # 3) DB 메타데이터 INSERT
    att = Attachment(
        user_id=current_user.id,
        bucket=settings.minio_bucket,
        object_key=key,
        original_filename=original_name,
        mime=mime,
        size_bytes=size,
    )
    db.add(att)
    try:
        await db.commit()
        await db.refresh(att)
    except Exception:
        # DB 실패 시 업로드된 객체는 정리 — 고아 객체 방지.
        try:
            remove_object(key)
        except Exception:
            pass
        raise

    return _serialize(att)


@router.get("/{att_id}", response_model=AttachmentResponse)
async def get_attachment(
    att_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    att = await _load_owned(db, att_id, current_user)
    return _serialize(att)


@router.get("/{att_id}/download")
async def download_attachment(
    att_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """MinIO 객체를 backend 가 stream 으로 흘려준다.

    presigned URL redirect 가 더 효율적이지만 compose 내부 endpoint(`minio:9000`) 가
    호스트 브라우저에서 resolve 안 되는 문제를 회피하기 위해 1차는 backend proxy.
    공개 endpoint(MINIO_PUBLIC_ENDPOINT) 가 별도로 노출되는 환경에서는 storage 모듈의
    presigned_download_url 로 자연 교체 가능.
    """
    att = await _load_owned(db, att_id, current_user)

    response = get_object_stream(att.object_key)
    # Content-Disposition 은 latin-1 만 허용. 한글/유니코드 파일명은 ASCII fallback +
    # RFC 5987 percent-encoded `filename*=UTF-8''...` 로 안전하게 노출.
    original = att.original_filename or "file"
    ascii_fallback = "".join(ch if 32 <= ord(ch) < 127 and ch != '"' else "_" for ch in original) or "file"
    encoded = urllib.parse.quote(original, safe="")
    headers = {
        "Content-Disposition": (
            f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{encoded}'
        ),
        "Cache-Control": "private, max-age=0, no-store",
    }

    def iter_stream():
        try:
            for chunk in response.stream(amt=64 * 1024):
                yield chunk
        finally:
            response.close()
            response.release_conn()

    return StreamingResponse(iter_stream(), media_type=att.mime, headers=headers)


@router.delete(
    "/{att_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_attachment(
    att_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """본인이 올린 첨부 삭제. MinIO 객체 + DB row 둘 다 정리."""
    att = await _load_owned(db, att_id, current_user)
    key = att.object_key
    await db.delete(att)
    await db.commit()
    try:
        remove_object(key)
    except Exception:
        # 객체 삭제 실패는 silent — DB 가 source of truth, MinIO 는 후속 cleanup 으로 회수 가능.
        pass


__all__ = ["router", "AttachmentResponse"]
