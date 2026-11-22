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


def _table_exists(connection, name: str) -> bool:
    row = connection.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:n"),
        {"n": name},
    ).fetchone()
    return row is not None


def _columns(connection, table: str) -> set[str]:
    if not _table_exists(connection, table):
        return set()
    return {row[1] for row in connection.execute(text(f"PRAGMA table_info({table})")).fetchall()}


def _dedupe_name_column(connection, table: str) -> None:
    if not _table_exists(connection, table):
        return
    dupes = connection.execute(
        text(f"SELECT name FROM {table} GROUP BY name HAVING COUNT(*) > 1")
    ).fetchall()
    for (name,) in dupes:
        rows = connection.execute(
            text(
                f"SELECT id FROM {table} WHERE name = :name ORDER BY created_at ASC, id ASC"
            ),
            {"name": name},
        ).fetchall()
        for i, (row_id,) in enumerate(rows[1:], start=2):
            new_name = f"{name} ({i})"
            n = i
            while connection.execute(
                text(f"SELECT 1 FROM {table} WHERE name = :n LIMIT 1"),
                {"n": new_name},
            ).fetchone():
                n += 1
                new_name = f"{name} ({n})"
            connection.execute(
                text(f"UPDATE {table} SET name = :new_name WHERE id = :id"),
                {"new_name": new_name, "id": row_id},
            )


def _ensure_unique_name(connection, table: str, index_name: str) -> None:
    if not _table_exists(connection, table):
        return
    indexes = connection.execute(text(f"PRAGMA index_list({table})")).fetchall()
    for idx in indexes:
        if idx[2] and idx[1]:
            info = connection.execute(text(f"PRAGMA index_info('{idx[1]}')")).fetchall()
            if {row[2] for row in info} == {"name"}:
                return
    _dedupe_name_column(connection, table)
    connection.execute(text(f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} ON {table}(name)"))


def _migrate_legacy_dish_ingredients_to_versions(connection) -> None:
    if not _table_exists(connection, "dish_ingredients"):
        return
    if not _table_exists(connection, "dish_versions"):
        return
    import uuid

    dishes = connection.execute(
        text(
            """
            SELECT d.id FROM dishes d
            WHERE EXISTS (SELECT 1 FROM dish_ingredients di WHERE di.dish_id = d.id)
              AND NOT EXISTS (SELECT 1 FROM dish_versions dv WHERE dv.dish_id = d.id)
            """
        )
    ).fetchall()
    for (dish_id,) in dishes:
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
        for ingredient_id, amount, unit, note in connection.execute(
            text(
                "SELECT ingredient_id, amount, unit, note FROM dish_ingredients WHERE dish_id = :did"
            ),
            {"did": dish_id},
        ).fetchall():
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


def _drop_dish_ingredients(connection) -> None:
    if _table_exists(connection, "dish_ingredients"):
        connection.execute(text("DROP TABLE dish_ingredients"))


def _ensure_extra_columns(connection) -> None:
    dish_cols = _columns(connection, "dishes")
    if dish_cols and "current_version_id" not in dish_cols:
        connection.execute(text("ALTER TABLE dishes ADD COLUMN current_version_id VARCHAR(36)"))

    shop_cols = _columns(connection, "shopping_items")
    if shop_cols and "ingredient_id" not in shop_cols:
        connection.execute(
            text("ALTER TABLE shopping_items ADD COLUMN ingredient_id VARCHAR(36)")
        )

    stock_cols = _columns(connection, "stock_items")
    if stock_cols:
        if "is_opened" not in stock_cols:
            connection.execute(
                text("ALTER TABLE stock_items ADD COLUMN is_opened BOOLEAN DEFAULT 0")
            )
        if "expires_on" not in stock_cols:
            connection.execute(text("ALTER TABLE stock_items ADD COLUMN expires_on DATE"))


def _run_schema_upgrades(connection) -> None:
    """Lightweight migration path (Alembic-compatible stamped revisions in schema_meta)."""
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                version VARCHAR(64) PRIMARY KEY,
                applied_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )
    applied = {
        row[0]
        for row in connection.execute(text("SELECT version FROM schema_meta")).fetchall()
    }

    steps = [
        ("2026_11_22_unique_names", lambda c: (
            _ensure_unique_name(c, "dishes", "uq_dishes_name"),
            _ensure_unique_name(c, "deliveries", "uq_deliveries_name"),
        )),
        ("2026_11_22_versions_only", lambda c: (
            _migrate_legacy_dish_ingredients_to_versions(c),
            _drop_dish_ingredients(c),
        )),
        ("2026_11_22_stock_shopping_cols", lambda c: _ensure_extra_columns(c)),
    ]
    for version, fn in steps:
        if version in applied:
            continue
        fn(connection)
        connection.execute(
            text("INSERT INTO schema_meta(version) VALUES (:v)"),
            {"v": version},
        )


async def init_db(database_path: str) -> async_sessionmaker[AsyncSession]:
    global _session_factory
    engine = create_async_engine(_to_async_url(database_path), echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_run_schema_upgrades)
    _session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return _session_factory


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("Database is not initialized. Call init_db() first.")
    return _session_factory
