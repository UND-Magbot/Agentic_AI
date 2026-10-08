"""
SQLAlchemy 2.0 비동기 엔진 / 세션 / Base / FastAPI 의존성.

- 엔진은 모듈 임포트 시 1회 생성되어 프로세스 수명동안 재사용.
- 세션은 요청 단위(Depends(get_db))로 발급/종료.
- Base.metadata 는 ORM 매핑의 공통 루트.
"""
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import settings


class Base(DeclarativeBase):
    """모든 ORM 모델이 상속할 Declarative Base."""


engine = create_async_engine(
    settings.database_url,
    echo=settings.db_echo,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=True,   # 끊긴 커넥션을 사전 감지해 자동 재연결.
    pool_recycle=300,     # 원격 DB(150) idle 종료 대비 — 5분 경과 커넥션은 재생성.
    future=True,
)

SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 의존성. 요청 단위로 세션 발급, 예외 시 롤백 보장."""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def ping() -> bool:
    """DB 헬스 체크 — /health 엔드포인트에서 호출."""
    from sqlalchemy import text
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def dispose() -> None:
    """앱 종료 시 커넥션 풀 정리."""
    await engine.dispose()
