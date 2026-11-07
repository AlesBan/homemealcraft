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

    report2 = await engine.apply_packet(packet)
    print("second:", report2.status)

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
    dishes = await catalog.list_dishes()
    shopping = await catalog.list_shopping()
    print("products:", [(p.brand, p.kcal, p.ingredient.name if p.ingredient else None) for p in products])
    print(
        "dishes:",
        [
            (
                d.name,
                [(li.ingredient.name, li.amount) for li in d.ingredients],
            )
            for d in dishes
        ],
    )
    print("shopping:", [(s.name, s.product.ingredient.name if s.product and s.product.ingredient else None) for s in shopping])


if __name__ == "__main__":
    asyncio.run(main())
