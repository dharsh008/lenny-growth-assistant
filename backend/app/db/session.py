"""Async engine/session management with startup retry (Postgres may still be
booting when the API container starts)."""
import asyncio
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.errors import DatabaseUnavailable
from app.core.logging import get_logger
from app.db.models import Base

log = get_logger("db")

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def init_engine(url: str | None = None) -> AsyncEngine:
    global _engine, _sessionmaker
    url = url or get_settings().database_url
    kwargs = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs = {}
    _engine = create_async_engine(url, **kwargs)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_engine() -> AsyncEngine:
    if _engine is None:
        init_engine()
    assert _engine is not None
    return _engine


@asynccontextmanager
async def session_scope():
    if _sessionmaker is None:
        init_engine()
    assert _sessionmaker is not None
    async with _sessionmaker() as s:
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise


async def get_db():
    """FastAPI dependency."""
    async with session_scope() as s:
        yield s


async def wait_for_db_and_create_schema() -> None:
    cfg = get_settings()
    last: Exception | None = None
    for attempt in range(1, cfg.db_connect_retries + 1):
        try:
            async with get_engine().begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            log.info("db_ready", attempt=attempt)
            return
        except Exception as exc:  # noqa: BLE001
            last = exc
            log.warning("db_connect_failed", attempt=attempt, error=str(exc)[:200])
            await asyncio.sleep(cfg.db_connect_retry_delay_s)
    raise DatabaseUnavailable(f"Could not connect to the database: {last}")


async def db_ping() -> tuple[bool, str | None]:
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:200]
