"""Broader than smoke: stock deduct, shopping→stock, category coerce, conflict."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db.session import init_db
from app.domain.catalog import CatalogService
from app.domain.import_engine import ImportEngine
from app.domain.schemas import ImportPacket
from app.domain.taxonomy import DISH_CATEGORY_SET, coerce_category


async def run() -> None:
    db = ROOT / "data" / "test_flow.sqlite"
    if db.exists():
        db.unlink()
    factory = await init_db(str(db))
    engine = ImportEngine(factory)
    catalog = CatalogService(factory)

    assert coerce_category("breakfast", DISH_CATEGORY_SET) == "breakfast"
    assert coerce_category("BREKFAST", DISH_CATEGORY_SET) == "other"

    packet = ImportPacket.model_validate(
        {
            "schema_version": "1.0",
            "idempotency_key": "test-flow-1",
            "source": "test",
            "actions": [
                {"op": "ingredient.upsert", "name": "Творог", "category": "dairy"},
                {"op": "ingredient.upsert", "name": "Мука", "category": "grocery"},
                {
                    "op": "dish.upsert",
                    "name": "Сырники",
                    "category": "breakfast",
                    "ingredients": [
                        {"ingredient_name": "Творог", "amount": 200, "unit": "g"},
                        {"ingredient_name": "Мука", "amount": 40, "unit": "g"},
                    ],
                },
                {
                    "op": "shopping.add",
                    "ingredient_name": "Творог",
                },
                {
                    "op": "stock.upsert",
                    "ingredient_name": "Мука",
                    "amount": 100,
                    "unit": "g",
                    "is_opened": True,
                    "expires_on": "2026-12-01",
                },
            ],
        }
    )
    report = await engine.apply_packet(packet)
    assert report.status == "applied"
    summary = report.summary_text()
    assert "Творог" in summary
    assert "Мука" in summary
    assert "Сырники" in summary
    assert "Добавлено" in summary
    assert "id:" not in summary
    assert "<code>" not in summary

    conflict = ImportPacket.model_validate(
        {
            "schema_version": "1.0",
            "idempotency_key": "test-flow-1",
            "source": "test",
            "actions": [{"op": "stock.upsert", "ingredient_name": "Мука", "amount": 1}],
        }
    )
    assert (await engine.apply_packet(conflict)).status == "conflict"

    shopping = await catalog.list_shopping()
    assert shopping.total >= 1
    item = shopping.items[0]
    assert item.ingredient_id is not None
    ok = await catalog.mark_shopping_bought(item.id, to_stock=True)
    assert ok
    stock_ids = await catalog.stock_ingredient_ids()
    assert item.ingredient_id in stock_ids

    dishes = await catalog.list_dishes(q="Сырники")
    dish = await catalog.get_dish(dishes.items[0].id)
    assert dish and dish.versions
    stats = await catalog.deduct_dish_from_stock(dish.id)
    assert stats["deducted"] + stats["depleted"] + stats["missing"] >= 1

    names_before = {i.name for i in (await catalog.list_ingredients()).items}
    assert "Творог" in names_before and "Мука" in names_before
    assert await catalog.delete_dish(dish.id) is True
    names_after = {i.name for i in (await catalog.list_ingredients()).items}
    assert names_before <= names_after

    removed = await engine.apply_packet(
        ImportPacket.model_validate(
            {
                "schema_version": "1.0",
                "idempotency_key": "test-flow-del-tvorog",
                "source": "test",
                "actions": [{"op": "ingredient.delete", "name": "Творог"}],
            }
        )
    )
    assert removed.status == "applied"
    assert "Удалено" in removed.summary_text()
    assert "Творог" in removed.summary_text()
    left = {i.name for i in (await catalog.list_ingredients()).items}
    assert "Творог" not in left
    assert "Мука" in left

    packed = await engine.apply_packet(
        ImportPacket.model_validate(
            {
                "schema_version": "1.0",
                "idempotency_key": "test-flow-product-del",
                "source": "test",
                "actions": [
                    {"op": "ingredient.upsert", "name": "Йогурт", "category": "dairy"},
                    {
                        "op": "product.upsert",
                        "name": "Йогурт Teos",
                        "brand": "Teos",
                        "ingredient_name": "Йогурт",
                    },
                    {
                        "op": "dish.upsert",
                        "name": "Миска йогурта",
                        "category": "breakfast",
                        "ingredients": [
                            {"ingredient_name": "Йогурт", "amount": 150, "unit": "g"},
                            {"ingredient_name": "Мука", "amount": 10, "unit": "g"},
                        ],
                    },
                ],
            }
        )
    )
    assert packed.status == "applied"
    gone_dish = await engine.apply_packet(
        ImportPacket.model_validate(
            {
                "schema_version": "1.0",
                "idempotency_key": "test-flow-del-dish",
                "source": "test",
                "actions": [{"op": "dish.delete", "name": "Миска йогурта"}],
            }
        )
    )
    assert gone_dish.status == "applied"
    dishes_left = await catalog.list_dishes(q="Миска йогурта")
    assert dishes_left.total == 0
    still = {i.name for i in (await catalog.list_ingredients()).items}
    assert "Йогурт" in still and "Мука" in still
    products = await catalog.list_products(q="Teos")
    assert products.total == 1
    assert await catalog.delete_product(products.items[0].id) is True
    assert (await catalog.list_products(q="Teos")).total == 0
    assert "Йогурт" in {i.name for i in (await catalog.list_ingredients()).items}

    names = {i.name for i in (await catalog.list_ingredients()).items}
    custom = await catalog.add_shopping_by_name("Губки")
    assert custom.ingredient_id is None and custom.product_id is None
    assert {i.name for i in (await catalog.list_ingredients()).items} == names
    assert (await catalog.list_products(q="Губки")).total == 0
    assert await catalog.mark_shopping_bought(custom.id, to_stock=False)
    assert (await catalog.list_stock(q="Губки")).total == 0
    both = await engine.apply_packet(
        ImportPacket.model_validate(
            {
                "schema_version": "1.0",
                "idempotency_key": "test-flow-both-1",
                "source": "test",
                "actions": [
                    {
                        "op": "dish.upsert",
                        "name": "Борщ",
                        "category": "soup",
                        "ingredients": [
                            {"ingredient_name": "Мука", "amount": 10, "unit": "g"},
                        ],
                    }
                ],
            }
        )
    )
    assert both.status == "applied"
    dish_names = {d.name for d in (await catalog.list_dishes(prep=False)).items}
    prep_names = {d.name for d in (await catalog.list_dishes(prep=True)).items}
    assert "Борщ" in dish_names and "Борщ" not in prep_names

    added = await engine.apply_packet(
        ImportPacket.model_validate(
            {
                "schema_version": "1.0",
                "idempotency_key": "test-flow-both-2",
                "source": "test",
                "actions": [{"op": "dish.upsert", "name": "Борщ", "is_prep": True}],
            }
        )
    )
    assert added.status == "applied"
    dish_names = {d.name for d in (await catalog.list_dishes(prep=False)).items}
    prep_names = {d.name for d in (await catalog.list_dishes(prep=True)).items}
    assert "Борщ" in dish_names and "Борщ" in prep_names
    borscht = next(d for d in (await catalog.list_dishes(q="Борщ")).items)
    assert borscht.category == "soup" and borscht.is_prep is True

    # category prep on a menu dish adds preps and keeps the meal section
    via_cat = await engine.apply_packet(
        ImportPacket.model_validate(
            {
                "schema_version": "1.0",
                "idempotency_key": "test-flow-both-3",
                "source": "test",
                "actions": [{"op": "dish.upsert", "name": "Борщ", "category": "prep"}],
            }
        )
    )
    assert via_cat.status == "applied"
    borscht = await catalog.get_dish(borscht.id)
    assert borscht and borscht.category == "soup" and borscht.is_prep is True

    only_prep = await engine.apply_packet(
        ImportPacket.model_validate(
            {
                "schema_version": "1.0",
                "idempotency_key": "test-flow-prep-only",
                "source": "test",
                "actions": [
                    {"op": "dish.upsert", "name": "Бульон", "category": "prep"},
                ],
            }
        )
    )
    assert only_prep.status == "applied"
    dish_names = {d.name for d in (await catalog.list_dishes(prep=False)).items}
    prep_names = {d.name for d in (await catalog.list_dishes(prep=True)).items}
    assert "Бульон" not in dish_names and "Бульон" in prep_names

    promoted = await engine.apply_packet(
        ImportPacket.model_validate(
            {
                "schema_version": "1.0",
                "idempotency_key": "test-flow-prep-to-both",
                "source": "test",
                "actions": [{"op": "dish.upsert", "name": "Бульон", "category": "soup"}],
            }
        )
    )
    assert promoted.status == "applied"
    dish_names = {d.name for d in (await catalog.list_dishes(prep=False)).items}
    prep_names = {d.name for d in (await catalog.list_dishes(prep=True)).items}
    assert "Бульон" in dish_names and "Бульон" in prep_names

    exclusive = await engine.apply_packet(
        ImportPacket.model_validate(
            {
                "schema_version": "1.0",
                "idempotency_key": "test-flow-placement-prep",
                "source": "test",
                "actions": [
                    {"op": "dish.upsert", "name": "Борщ", "placement": "prep"},
                ],
            }
        )
    )
    assert exclusive.status == "applied"
    borscht = await catalog.get_dish(borscht.id)
    assert borscht and borscht.category == "prep" and borscht.is_prep is True
    dish_names = {d.name for d in (await catalog.list_dishes(prep=False)).items}
    assert "Борщ" not in dish_names

    print("test_flow OK")


if __name__ == "__main__":
    asyncio.run(run())
