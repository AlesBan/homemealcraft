from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.db.models import (
    Dish,
    DishIngredient,
    DishVersion,
    DishVersionIngredient,
    ImportEvent,
    Ingredient,
    Product,
    ShoppingItem,
)
from app.domain.schemas import (
    DishIngredientLine,
    DishUpsert,
    DishVersionCreate,
    DishVersionDelete,
    ImportPacket,
    IngredientUpsert,
    ProductUpsert,
    ShoppingAdd,
    ShoppingCheck,
    ShoppingRemove,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid4())


def _patch(entity: Any, data: dict[str, Any], clear_fields: list[str] | None = None) -> list[str]:
    """Apply non-null fields; null/missing keep old values unless listed in clear_fields."""
    changed: list[str] = []
    clear = set(clear_fields or [])
    for key, value in data.items():
        if not hasattr(entity, key):
            continue
        if key in clear:
            if getattr(entity, key) is not None:
                setattr(entity, key, None)
                changed.append(key)
            continue
        if value is None:
            continue
        if getattr(entity, key) != value:
            setattr(entity, key, value)
            changed.append(key)
    if changed:
        if hasattr(entity, "updated_at"):
            entity.updated_at = _utcnow()
    return changed


@dataclass
class ActionResult:
    op: str
    status: str
    id: str | None = None
    detail: str | None = None


@dataclass
class ImportReport:
    status: str
    idempotency_key: str
    results: list[ActionResult] = field(default_factory=list)
    message: str | None = None

    def summary_text(self) -> str:
        if self.status == "already_applied":
            return f"Пакет уже применялся (<code>{self.idempotency_key}</code>)."
        if self.status == "error":
            return f"Ошибка импорта: {self.message}"
        labels = {
            "ingredient.upsert": "ингредиенты",
            "product.upsert": "продукты",
            "dish.upsert": "блюда",
            "dish_version.create": "версии рецептов",
            "dish_version.delete": "удалены версии",
            "shopping.add": "покупки",
            "shopping.check": "отмечено купленным",
            "shopping.remove": "удалено из покупок",
        }
        created: dict[str, int] = {}
        updated: dict[str, int] = {}
        other: dict[str, int] = {}
        for item in self.results:
            bucket = (
                created
                if item.status == "created"
                else updated
                if item.status == "updated"
                else other
            )
            name = labels.get(item.op, item.op)
            bucket[name] = bucket.get(name, 0) + 1
        lines = ["Готово."]
        if created:
            lines.append("Добавлено: " + ", ".join(f"{k} ×{v}" for k, v in created.items()))
        if updated:
            lines.append("Обновлено: " + ", ".join(f"{k} ×{v}" for k, v in updated.items()))
        if other:
            lines.append("Ещё: " + ", ".join(f"{k} ×{v}" for k, v in other.items()))
        return "\n".join(lines)


class ImportEngine:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def apply_packet(self, packet: ImportPacket) -> ImportReport:
        payload_hash = hashlib.sha256(
            packet.model_dump_json().encode("utf-8")
        ).hexdigest()

        async with self._session_factory() as session:
            existing = await session.scalar(
                select(ImportEvent).where(ImportEvent.idempotency_key == packet.idempotency_key)
            )
            if existing is not None:
                return ImportReport(
                    status="already_applied",
                    idempotency_key=packet.idempotency_key,
                    message=existing.report_json,
                )

            results: list[ActionResult] = []
            try:
                for action in packet.actions:
                    if isinstance(action, IngredientUpsert):
                        results.append(await self._upsert_ingredient(session, action))
                    elif isinstance(action, ProductUpsert):
                        results.append(await self._upsert_product(session, action))
                    elif isinstance(action, DishUpsert):
                        results.append(await self._upsert_dish(session, action))
                    elif isinstance(action, DishVersionCreate):
                        results.append(await self._create_dish_version(session, action))
                    elif isinstance(action, DishVersionDelete):
                        results.append(await self._delete_dish_version(session, action))
                    elif isinstance(action, ShoppingAdd):
                        results.append(await self._shopping_add(session, action))
                    elif isinstance(action, ShoppingCheck):
                        results.append(await self._shopping_check(session, action))
                    elif isinstance(action, ShoppingRemove):
                        results.append(await self._shopping_remove(session, action))
                    else:
                        raise ValueError(f"unsupported action: {action}")

                report = ImportReport(
                    status="applied",
                    idempotency_key=packet.idempotency_key,
                    results=results,
                )
                event = ImportEvent(
                    idempotency_key=packet.idempotency_key,
                    payload_hash=payload_hash,
                    status="applied",
                    report_json=json.dumps(
                        {
                            "results": [r.__dict__ for r in results],
                        },
                        ensure_ascii=False,
                    ),
                )
                session.add(event)
                await session.commit()
                return report
            except Exception as exc:
                await session.rollback()
                return ImportReport(
                    status="error",
                    idempotency_key=packet.idempotency_key,
                    message=str(exc),
                )

    async def _resolve_ingredient(
        self,
        session: AsyncSession,
        *,
        ingredient_id: str | None,
        ingredient_name: str | None,
        create_if_missing: bool = False,
    ) -> Ingredient | None:
        if ingredient_id:
            ing = await session.get(Ingredient, ingredient_id)
            if ing:
                return ing
        if ingredient_name:
            name = ingredient_name.strip()
            ing = await session.scalar(select(Ingredient).where(Ingredient.name == name))
            if ing:
                return ing
            if create_if_missing:
                ing = Ingredient(id=_new_id(), name=name)
                session.add(ing)
                await session.flush()
                return ing
        return None

    async def _upsert_ingredient(
        self, session: AsyncSession, action: IngredientUpsert
    ) -> ActionResult:
        entity: Ingredient | None = None
        if action.id:
            entity = await session.get(Ingredient, action.id)
        if entity is None:
            entity = await session.scalar(
                select(Ingredient).where(Ingredient.name == action.name.strip())
            )

        payload = {
            "name": action.name.strip(),
            "category": action.category,
            "kcal": action.kcal,
            "protein_g": action.protein_g,
            "fat_g": action.fat_g,
            "carbs_g": action.carbs_g,
            "comments": action.comments,
        }

        if entity is None:
            entity = Ingredient(id=action.id or _new_id(), **{k: v for k, v in payload.items()})
            session.add(entity)
            await session.flush()
            return ActionResult(op=action.op, status="created", id=entity.id)

        _patch(entity, payload, action.clear_fields)
        await session.flush()
        return ActionResult(op=action.op, status="updated", id=entity.id)

    async def _upsert_product(self, session: AsyncSession, action: ProductUpsert) -> ActionResult:
        entity: Product | None = None
        if action.id:
            entity = await session.get(Product, action.id)
        if entity is None and action.barcode:
            entity = await session.scalar(
                select(Product).where(Product.barcode == action.barcode.strip())
            )
        if entity is None:
            entity = await session.scalar(
                select(Product).where(Product.name == action.name.strip())
            )

        ingredient = await self._resolve_ingredient(
            session,
            ingredient_id=action.ingredient_id,
            ingredient_name=action.ingredient_name,
            create_if_missing=bool(action.ingredient_name),
        )

        raw_scan = None
        if action.raw_scan_json is not None:
            raw_scan = (
                action.raw_scan_json
                if isinstance(action.raw_scan_json, str)
                else json.dumps(action.raw_scan_json, ensure_ascii=False)
            )

        payload = {
            "name": action.name.strip(),
            "brand": action.brand,
            "barcode": action.barcode.strip() if action.barcode else None,
            "fat_percent": action.fat_percent,
            "weight_g": action.weight_g,
            "unit_label": action.unit_label,
            "kcal": action.kcal,
            "protein_g": action.protein_g,
            "fat_g": action.fat_g,
            "carbs_g": action.carbs_g,
            "composition": action.composition,
            "manufacturer": action.manufacturer,
            "comments": action.comments,
            "raw_scan_json": raw_scan,
            "ingredient_id": ingredient.id if ingredient else None,
        }

        if entity is None:
            # Don't force ingredient_id None into create if missing — allow null
            entity = Product(
                id=action.id or _new_id(),
                name=payload["name"],
                brand=payload["brand"],
                barcode=payload["barcode"],
                fat_percent=payload["fat_percent"],
                weight_g=payload["weight_g"],
                unit_label=payload["unit_label"],
                kcal=payload["kcal"],
                protein_g=payload["protein_g"],
                fat_g=payload["fat_g"],
                carbs_g=payload["carbs_g"],
                composition=payload["composition"],
                manufacturer=payload["manufacturer"],
                comments=payload["comments"],
                raw_scan_json=payload["raw_scan_json"],
                ingredient_id=payload["ingredient_id"],
            )
            session.add(entity)
            await session.flush()
            return ActionResult(op=action.op, status="created", id=entity.id)

        # For patch: only set ingredient_id when provided
        patch_data = dict(payload)
        if action.ingredient_id is None and action.ingredient_name is None:
            patch_data.pop("ingredient_id", None)
        _patch(entity, patch_data, action.clear_fields)
        await session.flush()
        return ActionResult(op=action.op, status="updated", id=entity.id)

    async def _upsert_dish(self, session: AsyncSession, action: DishUpsert) -> ActionResult:
        # Match by id; if no id — by exact name (create only when missing)
        entity: Dish | None = None
        if action.id:
            entity = await session.scalar(
                select(Dish)
                .where(Dish.id == action.id)
                .options(selectinload(Dish.ingredients))
            )
        else:
            entity = await session.scalar(
                select(Dish)
                .where(Dish.name == action.name.strip())
                .options(selectinload(Dish.ingredients))
            )

        payload = {
            "name": action.name.strip(),
            "category": action.category,
            "description": action.description,
            "cook_time_min": action.cook_time_min,
            "servings": action.servings,
            "instructions": action.instructions,
            "comments": action.comments,
            "freezable": action.freezable,
            "kcal": action.kcal,
            "protein_g": action.protein_g,
            "fat_g": action.fat_g,
            "carbs_g": action.carbs_g,
        }

        created = False
        if entity is None:
            entity = Dish(id=action.id or _new_id(), name=payload["name"])
            session.add(entity)
            created = True
            for key, value in payload.items():
                setattr(entity, key, value)
        else:
            _patch(entity, payload, action.clear_fields)

        await session.flush()

        # Recipe body: first create or explicit new_version → append version history
        if action.ingredients is not None:
            has_versions = await session.scalar(
                select(DishVersion.id).where(DishVersion.dish_id == entity.id).limit(1)
            )
            should_version = created or action.new_version or not has_versions
            if should_version:
                await self._add_version(
                    session,
                    dish=entity,
                    ingredients=action.ingredients,
                    title=None,
                    description=action.description,
                    cook_time_min=action.cook_time_min,
                    servings=action.servings,
                    instructions=action.instructions,
                    result_comment=action.result_comment,
                    kcal=action.kcal,
                    protein_g=action.protein_g,
                    fat_g=action.fat_g,
                    carbs_g=action.carbs_g,
                    set_current=True,
                )
            else:
                # Soft edit of identity-only dish.upsert without new_version:
                # refresh current composition mirror without new history entry
                await self._replace_dish_ingredients(session, entity.id, action.ingredients)

        await session.flush()
        return ActionResult(
            op=action.op,
            status="created" if created else "updated",
            id=entity.id,
        )

    async def _replace_dish_ingredients(
        self,
        session: AsyncSession,
        dish_id: str,
        lines: list[DishIngredientLine],
    ) -> None:
        await session.execute(delete(DishIngredient).where(DishIngredient.dish_id == dish_id))
        for line in lines:
            ing = await self._resolve_ingredient(
                session,
                ingredient_id=line.ingredient_id,
                ingredient_name=line.ingredient_name,
                create_if_missing=True,
            )
            if ing is None:
                raise ValueError("cannot resolve ingredient")
            session.add(
                DishIngredient(
                    id=_new_id(),
                    dish_id=dish_id,
                    ingredient_id=ing.id,
                    amount=line.amount,
                    unit=line.unit or "g",
                    note=line.note,
                )
            )

    async def _add_version(
        self,
        session: AsyncSession,
        *,
        dish: Dish,
        ingredients: list[DishIngredientLine],
        title: str | None,
        description: str | None,
        cook_time_min: int | None,
        servings: int | None,
        instructions: str | None,
        result_comment: str | None,
        kcal: float | None,
        protein_g: float | None,
        fat_g: float | None,
        carbs_g: float | None,
        set_current: bool,
        version_id: str | None = None,
    ) -> DishVersion:
        if set_current:
            existing = await session.scalars(
                select(DishVersion).where(DishVersion.dish_id == dish.id, DishVersion.is_current.is_(True))
            )
            for row in existing:
                row.is_current = False

        version = DishVersion(
            id=version_id or _new_id(),
            dish_id=dish.id,
            title=title,
            description=description,
            cook_time_min=cook_time_min,
            servings=servings,
            instructions=instructions,
            result_comment=result_comment,
            kcal=kcal,
            protein_g=protein_g,
            fat_g=fat_g,
            carbs_g=carbs_g,
            is_current=set_current,
        )
        session.add(version)
        await session.flush()

        for line in ingredients:
            ing = await self._resolve_ingredient(
                session,
                ingredient_id=line.ingredient_id,
                ingredient_name=line.ingredient_name,
                create_if_missing=True,
            )
            if ing is None:
                raise ValueError("cannot resolve ingredient for version")
            session.add(
                DishVersionIngredient(
                    id=_new_id(),
                    version_id=version.id,
                    ingredient_id=ing.id,
                    amount=line.amount,
                    unit=line.unit or "g",
                    note=line.note,
                )
            )

        if set_current:
            dish.current_version_id = version.id
            if cook_time_min is not None:
                dish.cook_time_min = cook_time_min
            if servings is not None:
                dish.servings = servings
            if instructions is not None:
                dish.instructions = instructions
            if description is not None:
                dish.description = description
            if result_comment is not None:
                dish.comments = result_comment
            for field_name, value in (
                ("kcal", kcal),
                ("protein_g", protein_g),
                ("fat_g", fat_g),
                ("carbs_g", carbs_g),
            ):
                if value is not None:
                    setattr(dish, field_name, value)
            await self._replace_dish_ingredients(session, dish.id, ingredients)

        await session.flush()
        return version

    async def _create_dish_version(
        self, session: AsyncSession, action: DishVersionCreate
    ) -> ActionResult:
        dish: Dish | None = None
        if action.dish_id:
            dish = await session.get(Dish, action.dish_id)
        if dish is None and action.dish_name:
            dish = await session.scalar(
                select(Dish).where(Dish.name == action.dish_name.strip())
            )
        if dish is None:
            if not action.dish_name:
                raise ValueError("dish not found for version")
            dish = Dish(id=_new_id(), name=action.dish_name.strip())
            session.add(dish)
            await session.flush()

        version = await self._add_version(
            session,
            dish=dish,
            ingredients=action.ingredients,
            title=action.title,
            description=action.description,
            cook_time_min=action.cook_time_min,
            servings=action.servings,
            instructions=action.instructions,
            result_comment=action.result_comment,
            kcal=action.kcal,
            protein_g=action.protein_g,
            fat_g=action.fat_g,
            carbs_g=action.carbs_g,
            set_current=action.set_current,
            version_id=action.id,
        )
        return ActionResult(op=action.op, status="created", id=version.id)

    async def _delete_dish_version(
        self, session: AsyncSession, action: DishVersionDelete
    ) -> ActionResult:
        if action.version_id:
            version = await session.get(DishVersion, action.version_id)
            if version is None:
                raise ValueError("version not found")
            dish = await session.get(Dish, version.dish_id)
            was_current = version.is_current
            await session.delete(version)
            await session.flush()
            if dish and was_current:
                latest = await session.scalar(
                    select(DishVersion)
                    .where(DishVersion.dish_id == dish.id)
                    .order_by(DishVersion.created_at.desc())
                    .limit(1)
                )
                if latest:
                    latest.is_current = True
                    dish.current_version_id = latest.id
                else:
                    dish.current_version_id = None
            return ActionResult(op=action.op, status="deleted", id=action.version_id)

        dish = await session.scalar(
            select(Dish).where(Dish.name == (action.dish_name or "").strip())
        )
        if dish is None:
            raise ValueError("dish not found")
        versions = list(
            await session.scalars(select(DishVersion).where(DishVersion.dish_id == dish.id))
        )
        deleted = 0
        for version in versions:
            if version.is_current:
                continue
            await session.delete(version)
            deleted += 1
        await session.flush()
        return ActionResult(
            op=action.op,
            status="deleted",
            id=dish.id,
            detail=f"removed {deleted} old versions",
        )

    async def _shopping_add(self, session: AsyncSession, action: ShoppingAdd) -> ActionResult:
        name = action.name
        category = action.category
        product_id = action.product_id
        if product_id:
            product = await session.get(Product, product_id)
            if product is None:
                raise ValueError(f"product not found: {product_id}")
            if not name:
                brand = f" {product.brand}" if product.brand else ""
                name = f"{product.name}{brand}".strip()
        assert name is not None
        item = ShoppingItem(
            id=_new_id(),
            product_id=product_id,
            name=name.strip(),
            category=category,
            is_bought=False,
        )
        session.add(item)
        await session.flush()
        return ActionResult(op=action.op, status="created", id=item.id)

    async def _find_shopping(
        self, session: AsyncSession, *, shopping_id: str | None, product_id: str | None, name: str | None
    ) -> ShoppingItem | None:
        if shopping_id:
            return await session.get(ShoppingItem, shopping_id)
        if product_id:
            return await session.scalar(
                select(ShoppingItem)
                .where(ShoppingItem.product_id == product_id, ShoppingItem.is_bought.is_(False))
                .order_by(ShoppingItem.created_at.desc())
            )
        if name:
            return await session.scalar(
                select(ShoppingItem)
                .where(ShoppingItem.name == name.strip(), ShoppingItem.is_bought.is_(False))
                .order_by(ShoppingItem.created_at.desc())
            )
        return None

    async def _shopping_check(self, session: AsyncSession, action: ShoppingCheck) -> ActionResult:
        item = await self._find_shopping(
            session,
            shopping_id=action.shopping_id,
            product_id=action.product_id,
            name=action.name,
        )
        if item is None:
            raise ValueError("shopping item not found")
        item.is_bought = True
        await session.flush()
        return ActionResult(op=action.op, status="updated", id=item.id)

    async def _shopping_remove(self, session: AsyncSession, action: ShoppingRemove) -> ActionResult:
        item = await self._find_shopping(
            session,
            shopping_id=action.shopping_id,
            product_id=action.product_id,
            name=action.name,
        )
        if item is None:
            raise ValueError("shopping item not found")
        item_id = item.id
        await session.delete(item)
        await session.flush()
        return ActionResult(op=action.op, status="deleted", id=item_id)
