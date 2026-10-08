"""메일 계정 시드 — .env 의 HIWORKS_* 값을 mail_accounts 에 UPSERT.

추후 다중 사용자 / 다중 provider 로 확장되면 이 시드는 1회성 부트스트랩으로 의미가
줄어들지만, 1차에는 .env 단일 발신자를 DB 에 자동 동기화하는 용도로 사용한다.
멱등 — (provider, username) UNIQUE 제약 위에 ON CONFLICT DO UPDATE.
"""
from __future__ import annotations

import logging

from sqlalchemy import text

from .config import settings
from .database import SessionLocal

logger = logging.getLogger("und_cortex.seed.mail_accounts")


_UPSERT_SQL = text(
    """
    INSERT INTO mail_accounts (
        user_id, provider, username, password, from_name, signature,
        smtp_host, smtp_port, smtp_use_ssl,
        default_to, default_cc, default_cc_all,
        is_active, is_default
    )
    VALUES (
        NULL, :provider, :username, :password, :from_name, :signature,
        :smtp_host, :smtp_port, :smtp_use_ssl,
        :default_to, :default_cc, :default_cc_all,
        TRUE, TRUE
    )
    ON CONFLICT (provider, username) DO UPDATE SET
        password       = EXCLUDED.password,
        from_name      = EXCLUDED.from_name,
        signature      = EXCLUDED.signature,
        smtp_host      = EXCLUDED.smtp_host,
        smtp_port      = EXCLUDED.smtp_port,
        smtp_use_ssl   = EXCLUDED.smtp_use_ssl,
        default_to     = EXCLUDED.default_to,
        default_cc     = EXCLUDED.default_cc,
        default_cc_all = EXCLUDED.default_cc_all,
        is_active      = TRUE,
        is_default     = TRUE,
        updated_at     = NOW()
    RETURNING id, (xmax = 0) AS inserted;
    """
)


async def seed_mail_accounts_on_startup() -> dict[str, int | bool | None]:
    """.env 의 hiworks 계정을 mail_accounts 에 UPSERT.

    자격증명이 비어있으면 시드를 건너뛴다(개발 환경에서 .env 미설정 케이스).
    반환: {"inserted": bool, "id": row_id} 또는 {"skipped": True}.
    """
    if not settings.hiworks_smtp_username or not settings.hiworks_smtp_password:
        logger.info("[seed.mail_accounts] skipped: missing HIWORKS_SMTP_USERNAME/PASSWORD")
        return {"skipped": True, "id": None}

    params = {
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
    }

    async with SessionLocal() as db:
        try:
            res = await db.execute(_UPSERT_SQL, params)
            row = res.first()
            await db.commit()
        except Exception:
            await db.rollback()
            raise

    row_id = row[0] if row else None
    inserted = bool(row[1]) if row else False
    logger.info(
        "[seed.mail_accounts] %s id=%s provider=hiworks username=%s signature_len=%d",
        "inserted" if inserted else "updated",
        row_id, params["username"], len(params["signature"] or ""),
    )
    return {"inserted": inserted, "id": row_id}
