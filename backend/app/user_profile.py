"""계정 기본 정보(사용자 2026-10-12, 영업팀 요청 7번) — 이니셜·한글 이름·직함·휴대폰(+ 이메일).

견적서·거래명세서 입력 폼을 이 값으로 미리 채운다. 계정을 빌려 쓰거나 담당자가 다를 수 있어 폼에서는 언제든 고칠 수 있다.
값은 본인이 프로필 [내 정보]에서 넣는다(api_my_profile).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

FIELDS = ("initials", "name_ko", "title", "mobile", "email")


async def get_profile(db: AsyncSession, user_id: int | None) -> dict[str, str]:
    """계정 기본 정보 — 없는 값은 ''."""
    if not user_id:
        return {k: "" for k in FIELDS}
    r = (await db.execute(text("SELECT initials, name_ko, title, mobile, email FROM users WHERE id = :i"),
                          {"i": user_id})).mappings().first()
    return {k: str((r or {}).get(k) or "") for k in FIELDS}


def contact_form(profile: dict[str, Any]) -> dict[str, str]:
    """계정 정보 → 견적서 폼의 담당자 칸(빈 값은 뺀다 — 폼에 이미 있는 값을 지우지 않게)."""
    m = {"initials": "initials", "contact_name": "name_ko", "contact_title": "title", "contact_mobile": "mobile",
         "contact_email": "email"}
    return {k: str(profile.get(src) or "").strip() for k, src in m.items() if str(profile.get(src) or "").strip()}
