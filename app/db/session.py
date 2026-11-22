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


def _ensure_dish_name_unique(connection) -> None:
    """Dedupe dish names then ensure unique index (SQLite-safe)."""
    rows = connection.execute(text("PRAGMA table_info(dishes)")).fetchall()
    if not rows:
        return

    indexes = connection.execute(text("PRAGMA index_list(dishes)")).fetchall()
    # PRAGMA index_list: seq, name, unique, origin, partial
    for idx in indexes:
        if idx[2] and idx[1] and "name" in str(idx[1]).lower():
            # already have a unique index involving name — check columns
            info = connection.execute(text(f"PRAGMA index_info('{idx[1]}')")).fetchall()
            cols = {row[2] for row in info}
            if cols == {"name"}:
                return

    # Dedupe: keep first by created_at, rename the rest
    dupes = connection.execute(
        text(
            """
            SELECT name FROM dishes
            GROUP BY name
            HAVING COUNT(*) > 1
            """
        )
    ).fetchall()
    for (name,) in dupes:
        dish_rows = connection.execute(
            text(
                """
                SELECT id FROM dishes
                WHERE name = :name
                ORDER BY created_at ASC, id ASC
                """
            ),
            {"name": name},
        ).fetchall()
        for i, (dish_id,) in enumerate(dish_rows[1:], start=2):
            new_name = f"{name} ({i})"
            # ensure new_name itself free
            n = i
            while True:
                exists = connection.execute(
                    text("SELECT 1 FROM dishes WHERE name = :n LIMIT 1"),
                    {"n": new_name},
                ).fetchone()
                if not exists:
                    break
                n += 1
                new_name = f"{name} ({n})"
            connection.execute(
                text("UPDATE dishes SET name = :new_name WHERE id = :id"),
                {"new_name": new_name, "id": dish_id},
            )

    connection.execute(
        text("CREATE UNIQUE INDEX IF NOT EXISTS uq_dishes_name ON dishes(name)")
    )


async def init_db(database_path: str) -> async_sessionmaker[AsyncSession]:
    global _session_factory
    engine = create_async_engine(_to_async_url(database_path), echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_sqlite_columns)
        await conn.run_sync(_ensure_dish_name_unique)
    _session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return _session_factory


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("Database is not initialized. Call init_db() first.")
    return _session_factory
