"""Local smoke test for JSON import without Telegram."""

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


SAMPLE = {
    "schema_version": "1.0",
    "idempotency_key": "smoke-syrniki-1",
    "source": "test",
    "actions": [
        {
            "op": "ingredient.upsert",
            "id": "11111111-1111-1111-1111-111111111111",
            "name": "Творог",
            "category": "dairy",
        },
        {
            "op": "ingredient.upsert",
            "id": "11111111-1111-1111-1111-111111111112",
            "name": "Мука",
            "category": "grocery",
        },
        {
            "op": "dish.upsert",
            "id": "22222222-2222-2222-2222-222222222222",
            "name": "Сырники",
            "category": "breakfast",
            "ingredients": [
                {"ingredient_name": "Творог", "amount": 200, "unit": "g"},
                {"ingredient_name": "Мука", "amount": 40, "unit": "g"},
            ],
        },
        {
            "op": "product.upsert",
            "id": "33333333-3333-3333-3333-333333333333",
            "ingredient_name": "Творог",
            "name": "Творог",
            "brand": "Бабушкина Крынка",
            "barcode": "4810123456789",
            "weight_g": 200,
            "kcal": 121,
            "protein_g": 17,
            "fat_g": 5,
            "carbs_g": 1.8,
        },
        {
            "op": "shopping.add",
            "product_id": "33333333-3333-3333-3333-333333333333",
        },
        {
            "op": "delivery.upsert",
            "id": "44444444-4444-4444-4444-444444444444",
            "name": "Пицца Dominos",
            "category": "food",
            "url": "https://dominos.example/",
            "description": "Маргарита + чеснок",
            "comments": "~40 мин",
        },
        {
            "op": "stock.upsert",
            "ingredient_name": "Мука",
            "amount": 1000,
            "unit": "g",
        },
    ],
}


async def main() -> None:
    db_path = ROOT / "data" / "smoke_test.sqlite"
    if db_path.exists():
        db_path.unlink()
    factory = await init_db(str(db_path))
    engine = ImportEngine(factory)
    catalog = CatalogService(factory)

    packet = ImportPacket.model_validate(SAMPLE)
    report = await engine.apply_packet(packet)
    print("first:", report.status, report.summary_text())
    assert report.status == "applied"

    report2 = await engine.apply_packet(packet)
    print("second:", report2.status)
    assert report2.status == "already_applied"

    conflict = {
        **SAMPLE,
        "actions": [
            {
                "op": "dish.upsert",
                "name": "Сырники",
                "category": "breakfast",
                "ingredients": [
                    {"ingredient_name": "Творог", "amount": 250, "unit": "g"},
                ],
            }
        ],
    }
    report_conflict = await engine.apply_packet(ImportPacket.model_validate(conflict))
    print("conflict:", report_conflict.status, report_conflict.summary_text())
    assert report_conflict.status == "conflict"

    # Same dish name + ingredients → new version
    version_packet = {
        "schema_version": "1.0",
        "idempotency_key": "smoke-syrniki-v2",
        "source": "test",
        "actions": [
            {
                "op": "dish.upsert",
                "name": "Сырники",
                "category": "breakfast",
                "result_comment": "влажнее",
                "ingredients": [
                    {"ingredient_name": "Творог", "amount": 250, "unit": "g"},
                    {"ingredient_name": "Мука", "amount": 30, "unit": "g"},
                ],
            }
        ],
    }
    report_v = await engine.apply_packet(ImportPacket.model_validate(version_packet))
    print("new version:", report_v.status, report_v.summary_text())
    assert report_v.status == "applied"

    patch = {
        **SAMPLE,
        "idempotency_key": "smoke-syrniki-2",
        "actions": [
            {
                "op": "product.upsert",
                "barcode": "4810123456789",
                "name": "Творог",
                "kcal": 118,
                "protein_g": 16.5,
            }
        ],
    }
    report3 = await engine.apply_packet(ImportPacket.model_validate(patch))
    print("patch:", report3.status, report3.summary_text())

    products = await catalog.list_products()
    dishes = await catalog.list_dishes(exclude_category="prep")
    shopping = await catalog.list_shopping()
    deliveries = await catalog.list_deliveries()
    stock = await catalog.list_stock()
    dish = (await catalog.list_dishes(q="Сырники")).items[0]
    full = await catalog.get_dish(dish.id)
    assert full is not None
    versions = full.versions or []
    print("products:", [(p.brand, p.kcal, p.ingredient.name if p.ingredient else None) for p in products.items])
    print("dishes:", dishes.total, "versions:", len(versions))
    assert len(versions) >= 2
    # Composition only from versions
    assert versions[0].ingredients
    print("shopping:", [(s.name, s.product.ingredient.name if s.product and s.product.ingredient else None) for s in shopping.items])
    print("deliveries:", [(d.name, d.url, d.category) for d in deliveries.items])
    print("stock:", [(s.name, s.amount) for s in stock.items])
    assert stock.total >= 1
    print("OK")


if __name__ == "__main__":
    asyncio.run(main())
