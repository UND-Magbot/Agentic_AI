"""메일 발신 계정 조회 헬퍼.

mail_accounts 테이블에서 active+default row 를 가져온다. row 가 없으면 `.env` 의
HIWORKS_* settings 로 fallback (DB 가 비어 있는 부트스트랩 직전 시점 호환).

발송 직전마다 DB 1회 SELECT. 1차 운영은 호출 빈도가 낮아 캐시 불요. 필요해지면
short TTL 캐시 (TTLCache 등) 를 이 모듈 내부에 두면 된다.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text

from .config import settings
from .database import SessionLocal

logger = logging.getLogger("mail_account_service")


_SELECT_DEFAULT = text(
    """
    SELECT id, provider, username, password, from_name, signature,
           smtp_host, smtp_port, smtp_use_ssl,
           default_to, default_cc, default_cc_all,
           is_active, is_default
    FROM mail_accounts
    WHERE provider = :provider AND is_active = TRUE AND is_default = TRUE
    ORDER BY id DESC
    LIMIT 1
    """
)


def _fallback_from_settings(provider: str) -> dict[str, Any]:
    """DB row 가 없을 때 settings 로 채운 dict — 단일 발신자 1차 호환."""
    if provider != "hiworks":
        return {}
    return {
        "id": None,
        "provider": "hiworks",
        "username": settings.hiworks_smtp_username,
        "password": settings.hiworks_smtp_password,
        "from_name": settings.hiworks_from_name or None,
        "signature": settings.hiworks_signature or None,
        "smtp_host": settings.hiworks_smtp_host,
        "smtp_port": settings.hiworks_smtp_port,
        "smtp_use_ssl": settings.hiworks_smtp_use_ssl,
        "default_to": list(settings.hiworks_default_to),
        "default_cc": list(settings.hiworks_default_cc),
        "default_cc_all": list(settings.hiworks_default_cc_all),
        "is_active": True,
        "is_default": True,
    }


async def get_default_mail_account(provider: str = "hiworks") -> dict[str, Any]:
    """active=true and is_default=true 인 provider row 1건을 반환.
    없으면 settings fallback (개발 환경 / DB 미시드 시점)."""
    async with SessionLocal() as db:
        try:
            r = await db.execute(_SELECT_DEFAULT, {"provider": provider})
            row = r.mappings().first()
        except Exception as e:
            logger.warning("[mail_account_service] DB query failed (%s): %s — using settings fallback",
                           type(e).__name__, e)
            return _fallback_from_settings(provider)

    if row is None:
        logger.warning("[mail_account_service] no active+default row for provider=%s — using settings fallback",
                       provider)
        return _fallback_from_settings(provider)

    account = dict(row)

    def _mask(v: object) -> str:
        # 이메일/식별자 PII 마스킹 — 'a@b.c' → 'a***@b.c', 그 외 길이만. (req.md §8)
        s = str(v or "")
        if "@" in s:
            local, _, domain = s.partition("@")
            return f"{local[:1]}***@{domain}"
        return f"<{len(s)}자>" if s else ""

    def _mask_list(v: object) -> str:
        if isinstance(v, (list, tuple)):
            return "[" + ", ".join(_mask(x) for x in v) + "]"
        return _mask(v)

    logger.warning(
        "[mail_account_service] loaded id=%s provider=%s username=%s signature_len=%d default_to=%s default_cc=%s",
        account.get("id"), account.get("provider"), _mask(account.get("username")),
        len(account.get("signature") or ""),
        _mask_list(account.get("default_to")), _mask_list(account.get("default_cc")),
    )
    return account
