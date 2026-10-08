"""MinIO(S3 호환) 클라이언트 + 버킷 부트스트랩.

설계:
- 단일 글로벌 클라이언트. 첫 사용 시 lazy 초기화하고 버킷 존재 보장(없으면 생성).
- 업로드는 BinaryIO(스트림) 그대로 put_object — FastAPI UploadFile.file 을 그대로 흘릴 수 있다.
- 다운로드는 presigned URL 반환 — 백엔드를 거치지 않고 클라이언트가 직접 가져가게 하여 대역폭 절약.
- 키 형식: `{user_id}/{yyyy-mm-dd}/{uuid}{ext}` — user 단위 격리 + 일자별 prefix 로 버킷 정리 용이.
"""
from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import BinaryIO

from minio import Minio
from minio.error import S3Error

from .config import settings

logger = logging.getLogger("und_cortex.storage")

_client: Minio | None = None
_lock = threading.Lock()


def get_client() -> Minio:
    """글로벌 MinIO 클라이언트. 첫 호출 시 버킷 보장."""
    global _client
    if _client is not None:
        return _client
    with _lock:
        if _client is None:
            _client = Minio(
                settings.minio_endpoint,
                access_key=settings.minio_access_key,
                secret_key=settings.minio_secret_key,
                secure=settings.minio_secure,
            )
            _ensure_bucket(_client, settings.minio_bucket)
    return _client


def _ensure_bucket(client: Minio, bucket: str) -> None:
    try:
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
            logger.info("[storage] created bucket: %s", bucket)
    except S3Error as e:
        # 이미 존재(다른 노드가 동시 생성) 또는 권한 — 로깅 후 통과(이후 put 에서 다시 실패시 raise).
        logger.warning("[storage] bucket ensure failed for %s: %s", bucket, e)


def make_object_key(user_id: int, original_filename: str) -> str:
    """공유 안전한 object key 생성. 원본 확장자만 보존하고 본명은 UUID 로 대체."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ext = "".join(PurePosixPath(original_filename).suffixes)[-16:]  # ".tar.gz" 같은 다중 ext 일부 보존, 길이 캡
    name = f"{uuid.uuid4().hex}{ext}"
    return f"{user_id}/{today}/{name}"


def put_object(
    *,
    key: str,
    data: BinaryIO,
    length: int,
    mime: str,
) -> None:
    client = get_client()
    client.put_object(
        bucket_name=settings.minio_bucket,
        object_name=key,
        data=data,
        length=length,
        content_type=mime,
    )


def presigned_download_url(
    key: str,
    *,
    filename: str | None = None,
    expires_seconds: int = 600,
) -> str:
    """presigned GET URL. 기본 10분. filename 이 주어지면 Content-Disposition 으로 강제 다운로드."""
    client = get_client()
    response_headers: dict[str, str] | None = None
    if filename:
        # 파일명 ASCII fallback + RFC 5987 utf-8 인코딩 동시 노출.
        safe = filename.replace('"', "")
        response_headers = {
            "response-content-disposition": (
                f'attachment; filename="{safe}"; filename*=UTF-8\'\'{safe}'
            ),
        }
    return client.presigned_get_object(
        bucket_name=settings.minio_bucket,
        object_name=key,
        expires=timedelta(seconds=expires_seconds),
        response_headers=response_headers,
    )


def remove_object(key: str) -> None:
    client = get_client()
    client.remove_object(settings.minio_bucket, key)


def get_object_stream(key: str):
    """MinIO 객체를 read-stream 으로 가져온다.

    반환 객체는 urllib3 HTTPResponse — `.stream(amt=...)` 로 청크 iter,
    사용 종료 후 `.close()` + `.release_conn()` 호출 필요.
    """
    client = get_client()
    return client.get_object(settings.minio_bucket, key)
