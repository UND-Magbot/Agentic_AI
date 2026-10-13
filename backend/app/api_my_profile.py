"""내 정보 — 프로필 메뉴의 [내 정보]에서 누구나 본인 것만(사용자 2026-10-12, 영업팀 요청 7번).

- GET /v1/me/profile : {initials, name_ko, title, mobile, email}
- PUT /v1/me/profile : 같은 값 저장. 견적서·거래명세서 폼이 이 값으로 미리 채워진다(폼에서는 고칠 수 있음).
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .api_auth import get_current_user
from .database import get_db
from .models import User
from .user_profile import get_profile

router = APIRouter(prefix="/v1/me", tags=["MyProfile"])

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MOBILE = re.compile(r"^[0-9+\-() ]{0,40}$")


class ProfileIn(BaseModel):
    initials: str = Field(default="", max_length=4)
    name_ko: str = Field(default="", max_length=40)
    title: str = Field(default="", max_length=40)
    mobile: str = Field(default="", max_length=40)
    email: str = Field(default="", max_length=255)

    @field_validator("initials")
    @classmethod
    def _initials(cls, v: str) -> str:
        v = v.strip().upper()
        if v and not re.fullmatch(r"[A-Z]{2,4}", v):
            raise ValueError("이니셜은 영문 2~4자로 적어 주세요(예: LU).")
        return v

    @field_validator("name_ko", "title")
    @classmethod
    def _text(cls, v: str) -> str:
        return re.sub(r"\s+", " ", v).strip()

    @field_validator("mobile")
    @classmethod
    def _mobile(cls, v: str) -> str:
        v = v.strip()
        if not _MOBILE.fullmatch(v):
            raise ValueError("휴대폰은 숫자와 - 로 적어 주세요(예: 010-1234-5678).")
        return v

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip()
        if v and not _EMAIL.fullmatch(v):
            raise ValueError("이메일 형식을 확인해 주세요.")
        return v


@router.get("/profile")
async def my_profile(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    return await get_profile(db, user.id)


@router.put("/profile")
async def save_profile(body: ProfileIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    try:
        await db.execute(text("UPDATE users SET initials = :initials, name_ko = :name_ko, title = :title, mobile = :mobile, "
                              "email = :email, updated_at = NOW() WHERE id = :id"),
                         {**{k: v or None for k, v in body.model_dump().items()}, "id": user.id})
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="이미 다른 계정이 쓰는 이메일입니다.") from e
    return await get_profile(db, user.id)
