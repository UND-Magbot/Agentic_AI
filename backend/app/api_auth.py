"""인증/사용자 라우터 — /v1/auth/*."""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordBearer
from jwt import PyJWTError
from sqlalchemy.ext.asyncio import AsyncSession

from . import auth_service
from .database import get_db
from .models import User
from .schemas_auth import (
    ChangePasswordRequest,
    LoginRequest,
    TokenResponse,
    UserResponse,
    UserWithPermissionsResponse,
)
from .security import create_access_token, decode_access_token

# 인증 실패 사유 → HTTP 상태/메시지/문제 필드 매핑.
# 사내 시스템이라 username enumeration 보호보다 명확한 UX 우선 정책.
_FAIL_MESSAGES: dict[str, tuple[int, str, str]] = {
    # reason: (status_code, detail, field)
    "user_not_found": (
        status.HTTP_401_UNAUTHORIZED,
        "아이디가 존재하지 않습니다.",
        "username",
    ),
    "inactive": (
        status.HTTP_403_FORBIDDEN,
        "비활성화된 계정입니다. 관리자에게 문의해주세요.",
        "username",
    ),
    "wrong_password": (
        status.HTTP_401_UNAUTHORIZED,
        "비밀번호가 일치하지 않습니다.",
        "password",
    ),
}

router = APIRouter(prefix="/v1/auth", tags=["Auth"])

# tokenUrl 은 Swagger UI 의 Authorize 버튼 동작에만 영향. 실제 로그인은 /v1/auth/login.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/v1/auth/login", auto_error=False)


async def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="인증 토큰이 필요합니다.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_access_token(token)
    except PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="토큰이 유효하지 않거나 만료되었습니다.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    sub = payload.get("sub")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="잘못된 토큰입니다."
        )
    try:
        user_id = int(sub)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="잘못된 토큰입니다."
        )

    user = await auth_service.get_user_by_id(db, user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="비활성 사용자입니다."
        )
    return user


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await auth_service.authenticate(db, req.username, req.password)
    if not result.ok:
        reason = result.reason or "wrong_password"
        code, detail, field = _FAIL_MESSAGES[reason]
        # detail + field 를 동시에 전달하기 위해 JSONResponse 직접 반환.
        # FastAPI HTTPException 의 detail 은 dict 도 받지만, 클라이언트에서 detail 키만 보고
        # 메시지를 꺼내는 관행이 있어 평탄한 응답이 더 다루기 쉽다.
        return JSONResponse(
            status_code=code,
            content={"detail": detail, "field": field, "reason": reason},
        )

    user = result.user
    assert user is not None  # for type-checker
    token, expires_in = create_access_token(
        subject=str(user.id),
        extra_claims={
            "username": user.username,
            "role": user.role.value,
            "domain": user.domain.value,
        },
    )
    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        user=UserResponse.model_validate(user),
    )


@router.get("/me", response_model=UserWithPermissionsResponse)
async def me(current_user: User = Depends(get_current_user)):
    return current_user


@router.get("/me/permissions", response_model=list[str])
async def my_permissions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await auth_service.list_user_permission_codes(db, current_user.id)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    req: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ok = await auth_service.change_password(
        db, current_user, req.current_password, req.new_password
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="현재 비밀번호가 올바르지 않습니다.",
        )
