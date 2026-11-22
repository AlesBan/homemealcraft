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


def _dedupe_name_column(connection, table: str) -> None:
    """Keep oldest row per name; rename duplicates to 'Name (2)' …"""
    dupes = connection.execute(
        text(
            f"""
            SELECT name FROM {table}
            GROUP BY name
            HAVING COUNT(*) > 1
            """
        )
    ).fetchall()
    for (name,) in dupes:
        rows = connection.execute(
            text(
                f"""
                SELECT id FROM {table}
                WHERE name = :name
                ORDER BY created_at ASC, id ASC
                """
            ),
            {"name": name},
        ).fetchall()
        for i, (row_id,) in enumerate(rows[1:], start=2):
            new_name = f"{name} ({i})"
            n = i
            while True:
                exists = connection.execute(
                    text(f"SELECT 1 FROM {table} WHERE name = :n LIMIT 1"),
                    {"n": new_name},
                ).fetchone()
                if not exists:
                    break
                n += 1
                new_name = f"{name} ({n})"
            connection.execute(
                text(f"UPDATE {table} SET name = :new_name WHERE id = :id"),
                {"new_name": new_name, "id": row_id},
            )


def _has_unique_name_index(connection, table: str) -> bool:
    indexes = connection.execute(text(f"PRAGMA index_list({table})")).fetchall()
    for idx in indexes:
        if not idx[2] or not idx[1]:
            continue
        info = connection.execute(text(f"PRAGMA index_info('{idx[1]}')")).fetchall()
        cols = {row[2] for row in info}
        if cols == {"name"}:
            return True
    return False


def _ensure_dish_name_unique(connection) -> None:
    rows = connection.execute(text("PRAGMA table_info(dishes)")).fetchall()
    if not rows:
        return
    if _has_unique_name_index(connection, "dishes"):
        return
    _dedupe_name_column(connection, "dishes")
    connection.execute(
        text("CREATE UNIQUE INDEX IF NOT EXISTS uq_dishes_name ON dishes(name)")
    )


def _ensure_delivery_name_unique(connection) -> None:
    rows = connection.execute(text("PRAGMA table_info(deliveries)")).fetchall()
    if not rows:
        return
    if _has_unique_name_index(connection, "deliveries"):
        return
    _dedupe_name_column(connection, "deliveries")
    connection.execute(
        text("CREATE UNIQUE INDEX IF NOT EXISTS uq_deliveries_name ON deliveries(name)")
    )


def _migrate_legacy_dish_ingredients_to_versions(connection) -> None:
    """One-shot: dishes with composition mirror but no versions → create a version."""
    dishes = connection.execute(
        text(
            """
            SELECT d.id FROM dishes d
            WHERE EXISTS (
                SELECT 1 FROM dish_ingredients di WHERE di.dish_id = d.id
            )
            AND NOT EXISTS (
                SELECT 1 FROM dish_versions dv WHERE dv.dish_id = d.id
            )
            """
        )
    ).fetchall()
    for (dish_id,) in dishes:
        import uuid

        version_id = str(uuid.uuid4())
        connection.execute(
            text(
                """
                INSERT INTO dish_versions (
                    id, dish_id, title, description, cook_time_min, servings,
                    instructions, result_comment, kcal, protein_g, fat_g, carbs_g,
                    is_current, created_at
                )
                SELECT
                    :vid, id, 'миграция', description, cook_time_min, servings,
                    instructions, comments, kcal, protein_g, fat_g, carbs_g,
                    1, CURRENT_TIMESTAMP
                FROM dishes WHERE id = :did
                """
            ),
            {"vid": version_id, "did": dish_id},
        )
        lines = connection.execute(
            text(
                """
                SELECT ingredient_id, amount, unit, note
                FROM dish_ingredients WHERE dish_id = :did
                """
            ),
            {"did": dish_id},
        ).fetchall()
        for ingredient_id, amount, unit, note in lines:
            connection.execute(
                text(
                    """
                    INSERT INTO dish_version_ingredients (
                        id, version_id, ingredient_id, amount, unit, note
                    ) VALUES (:id, :vid, :iid, :amount, :unit, :note)
                    """
                ),
                {
                    "id": str(uuid.uuid4()),
                    "vid": version_id,
                    "iid": ingredient_id,
                    "amount": amount,
                    "unit": unit or "g",
                    "note": note,
                },
            )
        connection.execute(
            text("UPDATE dishes SET current_version_id = :vid WHERE id = :did"),
            {"vid": version_id, "did": dish_id},
        )


async def init_db(database_path: str) -> async_sessionmaker[AsyncSession]:
    global _session_factory
    engine = create_async_engine(_to_async_url(database_path), echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_sqlite_columns)
        await conn.run_sync(_ensure_dish_name_unique)
        await conn.run_sync(_ensure_delivery_name_unique)
        await conn.run_sync(_migrate_legacy_dish_ingredients_to_versions)
    _session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return _session_factory


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("Database is not initialized. Call init_db() first.")
    return _session_factory
