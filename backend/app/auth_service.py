"""사용자 조회 / 인증 / 권한 매핑 비즈니스 로직."""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .models import Permission, User, UserPermission
from .security import hash_password, verify_password

# 인증 실패 사유. 호출부에서 detail/field 메시지를 분기할 때 사용.
AuthFailReason = Literal["user_not_found", "inactive", "wrong_password"]


@dataclass(slots=True)
class AuthResult:
    """인증 결과. user 가 채워지면 성공, 아니면 reason 으로 실패 사유 표시."""
    user: User | None = None
    reason: AuthFailReason | None = None

    @property
    def ok(self) -> bool:
        return self.user is not None


async def get_user_by_username(db: AsyncSession, username: str) -> User | None:
    stmt = (
        select(User)
        .where(User.username == username)
        .options(selectinload(User.permissions))
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: int) -> User | None:
    stmt = (
        select(User)
        .where(User.id == user_id)
        .options(selectinload(User.permissions))
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def authenticate(db: AsyncSession, username: str, password: str) -> AuthResult:
    """로그인 검증. 사유별로 분기 가능한 AuthResult 반환.

    아이디 우선 검증:
      1) 사용자 존재 여부
      2) 비활성 여부
      3) 비밀번호 일치 여부
    성공 시 last_login_at 갱신.
    """
    user = await get_user_by_username(db, username)
    if user is None:
        return AuthResult(reason="user_not_found")
    if not user.is_active:
        return AuthResult(reason="inactive")
    if not verify_password(password, user.password_hash):
        return AuthResult(reason="wrong_password")

    user.last_login_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(user)
    return AuthResult(user=user)


async def list_user_permission_codes(db: AsyncSession, user_id: int) -> list[str]:
    stmt = (
        select(Permission.code)
        .join(UserPermission, UserPermission.permission_id == Permission.id)
        .where(UserPermission.user_id == user_id)
        .order_by(Permission.code)
    )
    return [row[0] for row in (await db.execute(stmt)).all()]


async def change_password(
    db: AsyncSession, user: User, current_password: str, new_password: str
) -> bool:
    if not verify_password(current_password, user.password_hash):
        return False
    user.password_hash = hash_password(new_password)
    user.password_changed_at = datetime.now(timezone.utc)
    await db.commit()
    return True
