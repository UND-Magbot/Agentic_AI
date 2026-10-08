"""SMTP 메일 발송 클라이언트.

발신 계정·SMTP 설정·시그니처는 `mail_accounts` 테이블(provider 별 row)에 저장. 호출자가
account dict 를 명시적으로 넘기면 그것을 사용하고, 생략하면 active+default(hiworks)
row 를 조회한다. row 가 없으면 mail_account_service 가 settings 로 fallback.

account dict 키:
  username, password, from_name, smtp_host, smtp_port, smtp_use_ssl
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any

import aiosmtplib

from .mail_account_service import get_default_mail_account

# uvicorn 기본 설정에서 WARNING 이상만 stdout 노출되므로 발송 흐름 가시화는 warning 으로.
_log = logging.getLogger("hiworks_mailer")


@dataclass
class SendResult:
    ok: bool
    detail: str
    message_id: str | None = None


def _format_from(username: str, display_name: str | None) -> str:
    return formataddr((display_name, username)) if display_name else username


def _mask_email(addr: str) -> str:
    """이메일을 로그용으로 마스킹. 'user@example.com' → 'u***@example.com'. (req.md §8 PII)"""
    addr = (addr or "").strip()
    if "@" not in addr:
        return "***" if addr else ""
    local, _, domain = addr.partition("@")
    head = local[0] if local else ""
    return f"{head}***@{domain}"


def _mask_emails(addrs: list[str] | None) -> list[str]:
    return [_mask_email(a) for a in (addrs or [])]


async def send_mail(
    *,
    subject: str,
    body: str,
    to: list[str],
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    reply_to: str | None = None,
    is_html: bool = False,
    html_body: str | None = None,
    account: dict[str, Any] | None = None,
) -> SendResult:
    """단일 메일 발송.

    - body: plain text 본문 (필수).
    - html_body: HTML alternative. 있으면 multipart/alternative 로 plain + html 동시 발송.
                 HTML 호환 클라이언트는 html, 텍스트 전용 클라이언트는 plain 으로 렌더.
    - is_html=True 인 레거시 호출은 body 를 HTML 로 간주.
    - account 미지정 시 mail_accounts active+default(hiworks) row 사용.
    """
    if account is None:
        account = await get_default_mail_account("hiworks")

    username = account.get("username") or ""
    password = account.get("password") or ""
    from_name = account.get("from_name")
    smtp_host = account.get("smtp_host") or ""
    smtp_port = int(account.get("smtp_port") or 465)
    smtp_use_ssl = bool(account.get("smtp_use_ssl"))

    _log.warning(
        "[send_mail] called provider=%s username=%s subject_len=%d to=%s cc=%s bcc=%s "
        "plain_len=%d html_len=%d is_html=%s",
        account.get("provider"), _mask_email(username), len(subject or ""),
        _mask_emails(to), _mask_emails(cc), _mask_emails(bcc),
        len(body or ""), len(html_body or ""), is_html,
    )

    if not username or not password:
        _log.warning("[send_mail] aborted: missing SMTP credentials in account row")
        return SendResult(
            ok=False,
            detail=(
                "SMTP 자격증명이 설정되지 않았습니다. mail_accounts 테이블에 "
                "active+default row 가 있는지 또는 .env 의 HIWORKS_SMTP_USERNAME/PASSWORD 를 확인하세요."
            ),
        )
    if not smtp_host:
        _log.warning("[send_mail] aborted: missing smtp_host")
        return SendResult(ok=False, detail="smtp_host 가 비어 있습니다.")
    if not to:
        _log.warning("[send_mail] aborted: empty 'to'")
        return SendResult(ok=False, detail="수신자(to) 가 비어있습니다.")

    msg = EmailMessage()
    msg["From"] = _format_from(username, from_name)
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if bcc:
        # BCC 는 헤더가 아닌 RCPT TO 만 — aiosmtplib 가 recipients 인자로 처리.
        pass
    if reply_to:
        msg["Reply-To"] = reply_to
    msg["Subject"] = subject
    if html_body:
        # 표준 multipart/alternative — plain text + HTML 동시 전송.
        msg.set_content(body)
        msg.add_alternative(html_body, subtype="html")
    elif is_html:
        # 레거시: body 자체가 HTML 인 호출. plain alternative 는 안내 문구로 대체.
        msg.set_content("이 메일은 HTML 형식입니다. HTML 호환 클라이언트로 열어주세요.")
        msg.add_alternative(body, subtype="html")
    else:
        msg.set_content(body)

    recipients = list(to) + list(cc or []) + list(bcc or [])
    _log.warning(
        "[send_mail] connecting host=%s:%s ssl=%s username=%s recipients=%s",
        smtp_host, smtp_port, smtp_use_ssl, _mask_email(username), _mask_emails(recipients),
    )
    try:
        result = await aiosmtplib.send(
            msg,
            hostname=smtp_host,
            port=smtp_port,
            username=username,
            password=password,
            use_tls=smtp_use_ssl,
            recipients=recipients,
            timeout=30,
        )
    except aiosmtplib.SMTPException as e:
        _log.error("[send_mail] SMTPException %s: %s", type(e).__name__, e)
        return SendResult(ok=False, detail=f"SMTP 오류: {type(e).__name__}: {e}")
    except OSError as e:
        _log.error("[send_mail] OSError: %s", e)
        return SendResult(ok=False, detail=f"네트워크 오류: {e}")

    # aiosmtplib.send 결과 — (errors_dict, response_string)
    errors = result[0] if isinstance(result, tuple) and len(result) > 0 else {}
    response_str = result[1] if isinstance(result, tuple) and len(result) > 1 else str(result)
    _log.warning(
        "[send_mail] OK errors=%s response=%r message_id=%s",
        errors, str(response_str)[:200], msg.get("Message-ID"),
    )
    return SendResult(ok=True, detail=str(response_str)[:200], message_id=msg.get("Message-ID"))
