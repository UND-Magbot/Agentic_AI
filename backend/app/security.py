"""비밀번호 해싱·검증 + JWT 발급·디코딩."""
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

from .config import settings


# ---------- 비밀번호 ----------
# DB 측은 pgcrypto 의 crypt+gen_salt('bf') 로 bcrypt 해시를 저장하고 있다.
# bcrypt 라이브러리로 그대로 검증/생성 가능 ($2a$/$2b$ 호환).
_BCRYPT_ROUNDS = 12


def hash_password(plain: str) -> str:
    """bcrypt cost=12 해시를 생성한다."""
    salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    return bcrypt.hashpw(plain.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """평문과 저장된 해시를 비교한다. 잘못된 해시 형식이어도 False 반환."""
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------- JWT ----------

_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhd]?)\s*$", re.IGNORECASE)


def _parse_duration_seconds(value: str | int) -> int:
    """'30m' / '1h' / '7d' / '900' (초로 간주) 등을 초 단위로 변환."""
    if isinstance(value, int):
        return value
    m = _DURATION_RE.match(str(value))
    if not m:
        return 1800  # 기본 30분.
    n, unit = int(m.group(1)), (m.group(2) or "m").lower()
    return n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


def jwt_expires_in_seconds() -> int:
    return _parse_duration_seconds(settings.jwt_expires_in)


def create_access_token(
    subject: str,
    extra_claims: dict[str, Any] | None = None,
    expires_in: int | None = None,
) -> tuple[str, int]:
    """JWT 발급. (token, expires_in_seconds) 튜플 반환."""
    exp_seconds = expires_in if expires_in is not None else jwt_expires_in_seconds()
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=exp_seconds)).timestamp()),
    }
    if extra_claims:
        payload.update(extra_claims)
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, exp_seconds


def decode_access_token(token: str) -> dict[str, Any]:
    """JWT 디코딩. 만료/서명 오류는 jwt.PyJWTError 로 전파."""
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
