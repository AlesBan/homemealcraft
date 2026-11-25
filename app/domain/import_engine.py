from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

log = logging.getLogger("homemealcraft")

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    Delivery,
    Dish,
    DishVersion,
    DishVersionIngredient,
    ImportEvent,
    Ingredient,
    Product,
    ShoppingItem,
    StockItem,
)
from app.domain.schemas import (
    DeliveryUpsert,
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
    StockRemove,
    StockUpsert,
)
from app.domain.taxonomy import (
    DELIVERY_CATEGORY_SET,
    DISH_CATEGORY_SET,
    INGREDIENT_CATEGORY_SET,
    canonical_type_name,
    coerce_category,
    normalize_entity_name,
    normalize_type_name,
    suggest_category,
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
    title: str | None = None


def _html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _thing_title(name: str | None, extra: str | None = None) -> str:
    base = " ".join((name or "").split()) or "без названия"
    extra = " ".join((extra or "").split())
    if extra and extra.casefold() not in base.casefold():
        return f"{base} · {extra}"
    return base


def _version_title(dish_name: str, note: str | None = None) -> str:
    base = " ".join(dish_name.split()) or "блюдо"
    note = " ".join((note or "").split())
    if len(note) > 48:
        note = note[:47] + "…"
    if note and note.casefold() != base.casefold():
        return f"{base} — {note}"
    return base


_OP_GROUPS = {
    "ingredient.upsert": "Ингредиенты",
    "product.upsert": "Продукты",
    "dish.upsert": "Блюда",
    "delivery.upsert": "Доставки",
    "stock.upsert": "Кладовая",
    "stock.remove": "Кладовая",
    "dish_version.create": "Версии рецептов",
    "dish_version.delete": "Версии рецептов",
    "shopping.add": "Покупки",
    "shopping.check": "Покупки",
    "shopping.remove": "Покупки",
}

_SECTIONS = (
    ("created", "Добавлено"),
    ("updated", "Обновлено"),
    ("deleted", "Удалено"),
)


@dataclass
class ImportReport:
    status: str
    idempotency_key: str
    results: list[ActionResult] = field(default_factory=list)
    message: str | None = None

    def summary_text(self) -> str:
        if self.status == "already_applied":
            return "Этот пакет уже применялся."
        if self.status == "conflict":
            msg = _html(
                (self.message or "Такой пакет уже приходил с другим содержимым.").strip()
            )
            return f"Пакет не применён.\n{msg}"
        if self.status == "error":
            return f"Ошибка импорта: {_html(self.message or '')}"

        rows: list[tuple[str, str, str]] = []
        for item in self.results:
            group = _OP_GROUPS.get(item.op, item.op)
            title = item.title or "без названия"
            rows.append((item.status, group, title))
            if item.detail == "new_version":
                rows.append(("created", "Версии рецептов", title))

        lines = ["<b>Готово.</b>"]
        known = {status for status, _heading in _SECTIONS}
        any_rows = False
        for status, heading in _SECTIONS:
            section = [(group, title) for row_status, group, title in rows if row_status == status]
            if not section:
                continue
            any_rows = True
            lines.append("")
            lines.append(f"<b>{heading}</b>")
            grouped: dict[str, list[str]] = {}
            for group, title in section:
                grouped.setdefault(group, []).append(title)
            for group, titles in grouped.items():
                lines.append(group)
                for title in titles:
                    lines.append(f"· {_html(title)}")
        other = [
            (group, title)
            for row_status, group, title in rows
            if row_status not in known
        ]
        if other:
            any_rows = True
            lines.append("")
            lines.append("<b>Ещё</b>")
            for group, title in other:
                lines.append(f"· {_html(title)}")
                lines.append(group)
        if not any_rows:
            lines.append("Изменений нет.")
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
                if existing.payload_hash and existing.payload_hash != payload_hash:
                    return ImportReport(
                        status="conflict",
                        idempotency_key=packet.idempotency_key,
                        message=(
                            "Такой пакет уже приходил с другим содержимым. "
                            "Нужен новый JSON."
                        ),
                    )
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
                    elif isinstance(action, DeliveryUpsert):
                        results.append(await self._upsert_delivery(session, action))
                    elif isinstance(action, StockUpsert):
                        results.append(await self._upsert_stock(session, action))
                    elif isinstance(action, StockRemove):
                        results.append(await self._remove_stock(session, action))
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
        category: str | None = None,
    ) -> Ingredient | None:
        if ingredient_id:
            ing = await session.get(Ingredient, ingredient_id)
            if ing:
                return ing
        if ingredient_name:
            raw_name = normalize_type_name(ingredient_name)
            name = canonical_type_name(raw_name) or raw_name
            ing = await session.scalar(
                select(Ingredient).where(Ingredient.name.ilike(name))
            )
            if ing:
                return ing
            if create_if_missing:
                ing = Ingredient(
                    id=_new_id(),
                    name=name,
                    category=category or suggest_category(name),
                )
                session.add(ing)
                await session.flush()
                log.info("created ingredient type name=%s category=%s", name, ing.category)
                return ing
        return None

    async def _upsert_ingredient(
        self, session: AsyncSession, action: IngredientUpsert
    ) -> ActionResult:
        raw = normalize_type_name(action.name)
        name = canonical_type_name(raw) or raw
        entity: Ingredient | None = None
        if action.id:
            entity = await session.get(Ingredient, action.id)
        if entity is None:
            entity = await session.scalar(
                select(Ingredient).where(Ingredient.name.ilike(name))
            )

        payload = {
            "name": name,
            "category": coerce_category(action.category, INGREDIENT_CATEGORY_SET),
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
            return ActionResult(op=action.op, status="created", id=entity.id, title=entity.name)

        _patch(entity, payload, action.clear_fields)
        await session.flush()
        return ActionResult(op=action.op, status="updated", id=entity.id, title=entity.name)

    async def _upsert_product(self, session: AsyncSession, action: ProductUpsert) -> ActionResult:
        entity: Product | None = None
        if action.id:
            entity = await session.get(Product, action.id)
        if entity is None and action.barcode:
            entity = await session.scalar(
                select(Product).where(Product.barcode == action.barcode.strip())
            )
        product_name = normalize_entity_name(action.name)
        if entity is None:
            entity = await session.scalar(
                select(Product).where(Product.name.ilike(product_name))
            )

        type_name = action.ingredient_name
        if not type_name and product_name:
            # «Йогурт Teos …» → тип «Йогурт» if first token is a known/new type
            first = product_name.split(" ")[0]
            type_name = canonical_type_name(first) or first

        ingredient = await self._resolve_ingredient(
            session,
            ingredient_id=action.ingredient_id,
            ingredient_name=type_name,
            create_if_missing=bool(type_name),
        )

        raw_scan = None
        if action.raw_scan_json is not None:
            raw_scan = (
                action.raw_scan_json
                if isinstance(action.raw_scan_json, str)
                else json.dumps(action.raw_scan_json, ensure_ascii=False)
            )

        payload = {
            "name": product_name,
            "brand": action.brand.strip() if action.brand else None,
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
            return ActionResult(
                op=action.op,
                status="created",
                id=entity.id,
                title=_thing_title(entity.name, entity.brand),
            )

        # For patch: only set ingredient_id when provided
        patch_data = dict(payload)
        if action.ingredient_id is None and action.ingredient_name is None:
            patch_data.pop("ingredient_id", None)
        _patch(entity, patch_data, action.clear_fields)
        await session.flush()
        return ActionResult(
            op=action.op,
            status="updated",
            id=entity.id,
            title=_thing_title(entity.name, entity.brand),
        )

    async def _upsert_dish(self, session: AsyncSession, action: DishUpsert) -> ActionResult:
        # Match by id; if no id — by normalized name (case-insensitive)
        dish_name = normalize_entity_name(action.name)
        entity: Dish | None = None
        if action.id:
            entity = await session.scalar(
                select(Dish).where(Dish.id == action.id)
            )
        else:
            entity = await session.scalar(
                select(Dish).where(Dish.name.ilike(dish_name))
            )

        payload = {
            "name": dish_name,
            "category": coerce_category(action.category, DISH_CATEGORY_SET),
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

        # Any ingredients payload → append a new recipe version (UI reads versions).
        if action.ingredients is not None:
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

        await session.flush()
        return ActionResult(
            op=action.op,
            status="created" if created else "updated",
            id=entity.id,
            title=entity.name,
            detail="new_version" if (not created and action.ingredients is not None) else None,
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
            # Composition source of truth = dish_versions only (no DishIngredient mirror).

        await session.flush()
        return version

    async def _create_dish_version(
        self, session: AsyncSession, action: DishVersionCreate
    ) -> ActionResult:
        dish: Dish | None = None
        if action.dish_id:
            dish = await session.get(Dish, action.dish_id)
        if dish is None and action.dish_name:
            dish_name = normalize_entity_name(action.dish_name)
            dish = await session.scalar(
                select(Dish).where(Dish.name.ilike(dish_name))
            )
        if dish is None:
            if not action.dish_name:
                raise ValueError("dish not found for version")
            dish = Dish(id=_new_id(), name=normalize_entity_name(action.dish_name))
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
        return ActionResult(
            op=action.op,
            status="created",
            id=version.id,
            title=_version_title(dish.name, action.title or action.result_comment),
        )

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
            return ActionResult(
                op=action.op,
                status="deleted",
                id=action.version_id,
                title=dish.name if dish else "версия",
            )

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
            title=dish.name,
            detail=f"removed {deleted} old versions",
        )

    async def _upsert_delivery(
        self, session: AsyncSession, action: DeliveryUpsert
    ) -> ActionResult:
        name = normalize_entity_name(action.name)
        entity: Delivery | None = None
        if action.id:
            entity = await session.get(Delivery, action.id)
        else:
            entity = await session.scalar(
                select(Delivery).where(Delivery.name.ilike(name))
            )

        payload = {
            "name": name,
            "category": coerce_category(action.category, DELIVERY_CATEGORY_SET),
            "url": action.url.strip() if action.url else None,
            "description": action.description,
            "comments": action.comments,
        }

        if entity is None:
            entity = Delivery(id=action.id or _new_id(), name=payload["name"])
            session.add(entity)
            for key, value in payload.items():
                setattr(entity, key, value)
            await session.flush()
            return ActionResult(op=action.op, status="created", id=entity.id, title=entity.name)

        _patch(entity, payload, action.clear_fields)
        await session.flush()
        return ActionResult(op=action.op, status="updated", id=entity.id, title=entity.name)

    async def _find_stock(
        self,
        session: AsyncSession,
        *,
        stock_id: str | None = None,
        ingredient_id: str | None = None,
        ingredient_name: str | None = None,
        name: str | None = None,
    ) -> StockItem | None:
        if stock_id:
            return await session.get(StockItem, stock_id)
        if ingredient_id:
            return await session.scalar(
                select(StockItem).where(StockItem.ingredient_id == ingredient_id)
            )
        if ingredient_name:
            ing = await self._resolve_ingredient(
                session,
                ingredient_id=None,
                ingredient_name=ingredient_name,
                create_if_missing=False,
            )
            if ing:
                found = await session.scalar(
                    select(StockItem).where(StockItem.ingredient_id == ing.id)
                )
                if found:
                    return found
            key = normalize_entity_name(ingredient_name)
            return await session.scalar(
                select(StockItem).where(StockItem.name.ilike(key))
            )
        if name:
            key = normalize_entity_name(name)
            return await session.scalar(
                select(StockItem).where(StockItem.name.ilike(key))
            )
        return None

    async def _upsert_stock(self, session: AsyncSession, action: StockUpsert) -> ActionResult:
        ingredient = await self._resolve_ingredient(
            session,
            ingredient_id=action.ingredient_id,
            ingredient_name=action.ingredient_name,
            create_if_missing=bool(action.ingredient_name),
        )
        display_name = None
        if action.name:
            display_name = normalize_entity_name(action.name)
        elif ingredient:
            display_name = ingredient.name
        elif action.ingredient_name:
            display_name = normalize_entity_name(action.ingredient_name)
        assert display_name is not None

        entity = await self._find_stock(
            session,
            stock_id=action.id,
            ingredient_id=ingredient.id if ingredient else None,
            name=display_name if not ingredient else None,
        )

        expires = None
        if action.expires_on:
            from datetime import date as date_cls

            try:
                expires = date_cls.fromisoformat(action.expires_on.strip())
            except ValueError as exc:
                raise ValueError("expires_on must be YYYY-MM-DD") from exc

        payload = {
            "name": display_name,
            "ingredient_id": ingredient.id if ingredient else None,
            "amount": action.amount,
            "unit": action.unit,
            "is_opened": action.is_opened,
            "expires_on": expires,
            "comments": action.comments,
        }

        if entity is None:
            entity = StockItem(id=action.id or _new_id(), name=display_name)
            session.add(entity)
            for key, value in payload.items():
                if value is not None or key in {"name", "ingredient_id"}:
                    setattr(entity, key, value)
            await session.flush()
            return ActionResult(op=action.op, status="created", id=entity.id, title=entity.name)

        _patch(entity, payload, action.clear_fields)
        await session.flush()
        return ActionResult(op=action.op, status="updated", id=entity.id, title=entity.name)

    async def _remove_stock(self, session: AsyncSession, action: StockRemove) -> ActionResult:
        entity = await self._find_stock(
            session,
            stock_id=action.id,
            ingredient_id=action.ingredient_id,
            ingredient_name=action.ingredient_name,
            name=action.name,
        )
        if entity is None:
            raise ValueError("stock item not found")
        item_id = entity.id
        title = entity.name
        await session.delete(entity)
        await session.flush()
        return ActionResult(op=action.op, status="deleted", id=item_id, title=title)

    async def _shopping_add(self, session: AsyncSession, action: ShoppingAdd) -> ActionResult:
        name = action.name
        category = action.category
        product_id = action.product_id
        ingredient_id = action.ingredient_id
        if product_id:
            product = await session.get(Product, product_id)
            if product is None:
                raise ValueError(f"product not found: {product_id}")
            if not name:
                brand = f" {product.brand}" if product.brand else ""
                name = f"{product.name}{brand}".strip()
            if not ingredient_id:
                ingredient_id = product.ingredient_id
        if action.ingredient_name or ingredient_id:
            ing = await self._resolve_ingredient(
                session,
                ingredient_id=ingredient_id,
                ingredient_name=action.ingredient_name,
                create_if_missing=bool(action.ingredient_name),
            )
            if ing:
                ingredient_id = ing.id
                if not name:
                    name = ing.name
                if not category:
                    category = ing.category
        assert name is not None
        item = ShoppingItem(
            id=_new_id(),
            product_id=product_id,
            ingredient_id=ingredient_id,
            name=normalize_entity_name(name),
            category=category,
            is_bought=False,
        )
        session.add(item)
        await session.flush()
        return ActionResult(op=action.op, status="created", id=item.id, title=item.name)

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
        return ActionResult(op=action.op, status="updated", id=item.id, title=item.name)

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
        title = item.name
        await session.delete(item)
        await session.flush()
        return ActionResult(op=action.op, status="deleted", id=item_id, title=title)
