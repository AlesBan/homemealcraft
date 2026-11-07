from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.db.models import (
    Dish,
    DishIngredient,
    DishVersion,
    DishVersionIngredient,
    Ingredient,
    Product,
    ShoppingItem,
)


class CatalogService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_ingredients(self, limit: int = 30) -> list[Ingredient]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(Ingredient).order_by(Ingredient.name).limit(limit)
            )
            return list(rows)

    async def list_products(self, limit: int = 30) -> list[Product]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(Product)
                .options(selectinload(Product.ingredient))
                .order_by(Product.name)
                .limit(limit)
            )
            return list(rows)

    async def list_dishes(self, limit: int = 30) -> list[Dish]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(Dish)
                .options(selectinload(Dish.ingredients).selectinload(DishIngredient.ingredient))
                .order_by(Dish.name)
                .limit(limit)
            )
            return list(rows)

    async def list_shopping(
        self, include_bought: bool = False, limit: int = 50
    ) -> list[ShoppingItem]:
        async with self._session_factory() as session:
            stmt = select(ShoppingItem).options(
                selectinload(ShoppingItem.product).selectinload(Product.ingredient)
            )
            if not include_bought:
                stmt = stmt.where(ShoppingItem.is_bought.is_(False))
            rows = await session.scalars(
                stmt.order_by(ShoppingItem.created_at.desc()).limit(limit)
            )
            return list(rows)

    async def get_ingredient(self, item_id: str) -> Ingredient | None:
        async with self._session_factory() as session:
            return await session.get(Ingredient, item_id)

    async def get_product(self, item_id: str) -> Product | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(Product)
                .where(Product.id == item_id)
                .options(selectinload(Product.ingredient))
            )

    async def get_dish(self, item_id: str) -> Dish | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(Dish)
                .where(Dish.id == item_id)
                .options(
                    selectinload(Dish.ingredients).selectinload(DishIngredient.ingredient),
                    selectinload(Dish.versions).selectinload(DishVersion.ingredients).selectinload(
                        DishVersionIngredient.ingredient
                    ),
                )
            )

    async def get_dish_version(self, version_id: str) -> DishVersion | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(DishVersion)
                .where(DishVersion.id == version_id)
                .options(
                    selectinload(DishVersion.dish),
                    selectinload(DishVersion.ingredients).selectinload(
                        DishVersionIngredient.ingredient
                    ),
                )
            )

    async def delete_dish_version(self, version_id: str) -> tuple[bool, str | None]:
        """Manual cleanup. Returns (ok, dish_id)."""
        async with self._session_factory() as session:
            version = await session.get(DishVersion, version_id)
            if version is None:
                return False, None
            dish_id = version.dish_id
            was_current = version.is_current
            await session.delete(version)
            await session.flush()
            dish = await session.get(Dish, dish_id)
            if dish and was_current:
                latest = await session.scalar(
                    select(DishVersion)
                    .where(DishVersion.dish_id == dish_id)
                    .order_by(DishVersion.created_at.desc())
                    .limit(1)
                )
                if latest:
                    latest.is_current = True
                    dish.current_version_id = latest.id
                else:
                    dish.current_version_id = None
            await session.commit()
            return True, dish_id

    async def get_shopping(self, item_id: str) -> ShoppingItem | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(ShoppingItem)
                .where(ShoppingItem.id == item_id)
                .options(selectinload(ShoppingItem.product).selectinload(Product.ingredient))
            )
