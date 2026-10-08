"""인증/사용자/권한 Pydantic DTO."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

UserRoleStr = Literal["superadmin", "domain_admin", "member", "viewer"]
UserDomainStr = Literal["all", "finance", "sales", "design", "develop"]


# ---------- Permission ----------

class PermissionResponse(BaseModel):
    id: int
    code: str
    domain: UserDomainStr
    description: str | None = None

    model_config = ConfigDict(from_attributes=True)


# ---------- User ----------

# email 은 str 로 받는다. EmailStr 은 RFC 7230 의 special-use 도메인(.local 등)을 거부해서
# 사내 도메인(und.local)을 막는다. 엄격한 검증이 필요하면 서비스 레이어에서 별도 처리.
class UserBase(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    alias: str | None = Field(default=None, max_length=100)
    email: str | None = Field(default=None, max_length=255)
    role: UserRoleStr = "member"
    domain: UserDomainStr = "all"


class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)


class UserUpdate(BaseModel):
    alias: str | None = Field(default=None, max_length=100)
    email: str | None = Field(default=None, max_length=255)
    role: UserRoleStr | None = None
    domain: UserDomainStr | None = None
    is_active: bool | None = None


class UserResponse(UserBase):
    id: int
    is_active: bool
    last_login_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UserWithPermissionsResponse(UserResponse):
    permissions: list[PermissionResponse] = []


# ---------- Auth ----------

class LoginRequest(BaseModel):
    # 길이 제약은 비어있지 않음만 강제. 4자 미만 등 짧은 입력도 백엔드에 흘려야
    # user_not_found(아이디 검사가 먼저)가 평가되어 사용자에게 정확한 메시지를 줄 수 있다.
    # 빈 입력은 클라이언트가 1차로 막는다.
    username: str = Field(min_length=1, max_length=64, examples=["superadmin"])
    password: str = Field(min_length=1, max_length=128, examples=["<password>"])


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int  # 초
    user: UserResponse


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=4, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)
