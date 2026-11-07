from __future__ import annotations

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base

_session_factory: async_sessionmaker[AsyncSession] | None = None


def _to_async_url(database_path: str | Path) -> str:
    path = Path(database_path)
    if not path.is_absolute():
        root = Path(__file__).resolve().parents[2]
        path = root / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite+aiosqlite:///{path.as_posix()}"


def _ensure_sqlite_columns(connection) -> None:
    rows = connection.execute(text("PRAGMA table_info(dishes)")).fetchall()
    if not rows:
        return
    columns = {row[1] for row in rows}
    if "current_version_id" not in columns:
        connection.execute(text("ALTER TABLE dishes ADD COLUMN current_version_id VARCHAR(36)"))


async def init_db(database_path: str) -> async_sessionmaker[AsyncSession]:
    global _session_factory
    engine = create_async_engine(_to_async_url(database_path), echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_sqlite_columns)
    _session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return _session_factory


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("Database is not initialized. Call init_db() first.")
    return _session_factory
