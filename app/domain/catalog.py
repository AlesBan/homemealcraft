from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.db.models import (
    Delivery,
    Dish,
    DishVersion,
    DishVersionIngredient,
    Ingredient,
    Product,
    ShoppingItem,
    StockItem,
)

PAGE_SIZE = 15


@dataclass
class PageResult:
    items: list
    total: int
    page: int
    page_size: int
    query: str | None = None

    @property
    def pages(self) -> int:
        if self.total <= 0:
            return 1
        return max(1, (self.total + self.page_size - 1) // self.page_size)


class CatalogService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_ingredients(
        self,
        *,
        page: int = 0,
        page_size: int = PAGE_SIZE,
        q: str | None = None,
    ) -> PageResult:
        async with self._session_factory() as session:
            stmt = select(Ingredient)
            count_stmt = select(func.count()).select_from(Ingredient)
            if q:
                like = f"%{q.strip()}%"
                stmt = stmt.where(Ingredient.name.ilike(like))
                count_stmt = count_stmt.where(Ingredient.name.ilike(like))
            total = int(await session.scalar(count_stmt) or 0)
            rows = await session.scalars(
                stmt.order_by(Ingredient.name)
                .offset(page * page_size)
                .limit(page_size)
            )
            return PageResult(list(rows), total, page, page_size, q)

    async def list_products(
        self,
        *,
        page: int = 0,
        page_size: int = PAGE_SIZE,
        q: str | None = None,
    ) -> PageResult:
        async with self._session_factory() as session:
            stmt = select(Product).options(selectinload(Product.ingredient))
            count_stmt = select(func.count()).select_from(Product)
            if q:
                like = f"%{q.strip()}%"
                filt = or_(Product.name.ilike(like), Product.brand.ilike(like))
                stmt = stmt.where(filt)
                count_stmt = count_stmt.where(filt)
            total = int(await session.scalar(count_stmt) or 0)
            rows = await session.scalars(
                stmt.order_by(Product.name)
                .offset(page * page_size)
                .limit(page_size)
            )
            return PageResult(list(rows), total, page, page_size, q)

    async def list_dishes(
        self,
        *,
        page: int = 0,
        page_size: int = PAGE_SIZE,
        q: str | None = None,
        category: str | None = None,
        exclude_category: str | None = None,
    ) -> PageResult:
        async with self._session_factory() as session:
            stmt = select(Dish)
            count_stmt = select(func.count()).select_from(Dish)
            if category is not None:
                stmt = stmt.where(Dish.category == category)
                count_stmt = count_stmt.where(Dish.category == category)
            if exclude_category is not None:
                filt = (Dish.category.is_(None)) | (Dish.category != exclude_category)
                stmt = stmt.where(filt)
                count_stmt = count_stmt.where(filt)
            if q:
                like = f"%{q.strip()}%"
                stmt = stmt.where(Dish.name.ilike(like))
                count_stmt = count_stmt.where(Dish.name.ilike(like))
            total = int(await session.scalar(count_stmt) or 0)
            rows = await session.scalars(
                stmt.order_by(Dish.name)
                .offset(page * page_size)
                .limit(page_size)
            )
            return PageResult(list(rows), total, page, page_size, q)

    async def list_shopping(
        self,
        *,
        include_bought: bool = False,
        page: int = 0,
        page_size: int = PAGE_SIZE,
        q: str | None = None,
    ) -> PageResult:
        async with self._session_factory() as session:
            stmt = select(ShoppingItem).options(
                selectinload(ShoppingItem.product).selectinload(Product.ingredient)
            )
            count_stmt = select(func.count()).select_from(ShoppingItem)
            if not include_bought:
                stmt = stmt.where(ShoppingItem.is_bought.is_(False))
                count_stmt = count_stmt.where(ShoppingItem.is_bought.is_(False))
            if q:
                like = f"%{q.strip()}%"
                stmt = stmt.where(ShoppingItem.name.ilike(like))
                count_stmt = count_stmt.where(ShoppingItem.name.ilike(like))
            total = int(await session.scalar(count_stmt) or 0)
            rows = await session.scalars(
                stmt.order_by(ShoppingItem.created_at.desc())
                .offset(page * page_size)
                .limit(page_size)
            )
            return PageResult(list(rows), total, page, page_size, q)

    async def list_deliveries(
        self,
        *,
        page: int = 0,
        page_size: int = PAGE_SIZE,
        q: str | None = None,
    ) -> PageResult:
        async with self._session_factory() as session:
            stmt = select(Delivery)
            count_stmt = select(func.count()).select_from(Delivery)
            if q:
                like = f"%{q.strip()}%"
                stmt = stmt.where(Delivery.name.ilike(like))
                count_stmt = count_stmt.where(Delivery.name.ilike(like))
            total = int(await session.scalar(count_stmt) or 0)
            rows = await session.scalars(
                stmt.order_by(Delivery.name)
                .offset(page * page_size)
                .limit(page_size)
            )
            return PageResult(list(rows), total, page, page_size, q)

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
                    selectinload(Dish.versions).selectinload(DishVersion.ingredients).selectinload(
                        DishVersionIngredient.ingredient
                    ),
                )
            )

    async def list_stock(
        self,
        *,
        page: int = 0,
        page_size: int = PAGE_SIZE,
        q: str | None = None,
    ) -> PageResult:
        async with self._session_factory() as session:
            stmt = select(StockItem).options(selectinload(StockItem.ingredient))
            count_stmt = select(func.count()).select_from(StockItem)
            if q:
                like = f"%{q.strip()}%"
                stmt = stmt.where(StockItem.name.ilike(like))
                count_stmt = count_stmt.where(StockItem.name.ilike(like))
            total = int(await session.scalar(count_stmt) or 0)
            rows = await session.scalars(
                stmt.order_by(StockItem.name)
                .offset(page * page_size)
                .limit(page_size)
            )
            return PageResult(list(rows), total, page, page_size, q)

    async def get_stock(self, item_id: str) -> StockItem | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(StockItem)
                .where(StockItem.id == item_id)
                .options(selectinload(StockItem.ingredient))
            )

    async def stock_ingredient_ids(self) -> set[str]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(StockItem.ingredient_id).where(StockItem.ingredient_id.is_not(None))
            )
            return {r for r in rows if r}

    async def upsert_stock(
        self,
        *,
        name: str,
        ingredient_id: str | None = None,
        amount: float | None = None,
        unit: str | None = None,
    ) -> StockItem:
        async with self._session_factory() as session:
            entity: StockItem | None = None
            if ingredient_id:
                entity = await session.scalar(
                    select(StockItem).where(StockItem.ingredient_id == ingredient_id)
                )
            if entity is None:
                entity = await session.scalar(
                    select(StockItem).where(StockItem.name.ilike(name.strip()))
                )
            if entity is None:
                entity = StockItem(name=name.strip(), ingredient_id=ingredient_id)
                session.add(entity)
            else:
                entity.name = name.strip()
                if ingredient_id:
                    entity.ingredient_id = ingredient_id
            if amount is not None:
                entity.amount = amount
            if unit is not None:
                entity.unit = unit
            await session.commit()
            await session.refresh(entity)
            return entity

    async def remove_stock(self, item_id: str) -> bool:
        async with self._session_factory() as session:
            item = await session.get(StockItem, item_id)
            if item is None:
                return False
            await session.delete(item)
            await session.commit()
            return True

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

    async def get_delivery(self, item_id: str) -> Delivery | None:
        async with self._session_factory() as session:
            return await session.get(Delivery, item_id)

    async def add_shopping_by_name(
        self, name: str, *, category: str | None = None
    ) -> ShoppingItem:
        async with self._session_factory() as session:
            item = ShoppingItem(
                name=name.strip(),
                category=category,
                is_bought=False,
            )
            session.add(item)
            await session.commit()
            await session.refresh(item)
            return item

    async def mark_shopping_bought(self, item_id: str) -> bool:
        async with self._session_factory() as session:
            item = await session.get(ShoppingItem, item_id)
            if item is None:
                return False
            item.is_bought = True
            await session.commit()
            return True

    async def remove_shopping(self, item_id: str) -> bool:
        async with self._session_factory() as session:
            item = await session.get(ShoppingItem, item_id)
            if item is None:
                return False
            await session.delete(item)
            await session.commit()
            return True
