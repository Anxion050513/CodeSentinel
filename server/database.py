"""SQLAlchemy async engine and session for MySQL.

Creates the async SQLAlchemy engine, session factory, and declarative Base.
Tables are auto-created on startup (in development mode).
"""
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from server.config import settings

logger = logging.getLogger(__name__)

# Async engine bound to MySQL via aiomysql
engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=3600,
)

# Async session factory
async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""
    pass


async def get_db() -> AsyncSession:
    """FastAPI dependency that yields an async database session.

    Usage:
        @app.get("/path")
        async def handler(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    """Create all tables on startup (development mode only).

    In production, migrations (Alembic) should be used instead.
    """
    if not settings.is_development:
        logger.info("Skipping auto-create tables (production mode)")
        return

    # Import all models so they register with Base
    import server.models.repository  # noqa: F401
    import server.models.review_session  # noqa: F401
    import server.models.review_finding  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    logger.info("Database tables created/verified (development mode)")