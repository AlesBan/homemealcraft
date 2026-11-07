from __future__ import annotations

import json
import logging

from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from pydantic import ValidationError

from zoneinfo import ZoneInfo

from app.db.models import Dish, DishVersion, Ingredient, Product, ShoppingItem
from app.domain.catalog import CatalogService
from app.domain.import_engine import ImportEngine
from app.domain.json_extract import extract_json_object
from app.domain.schemas import ImportPacket
from bot.filters import IsJsonPayload, IsUnknownText
from bot.keyboards import (
    BTN_DISHES,
    BTN_HELP,
    BTN_HOME,
    BTN_INGREDIENTS,
    BTN_PRODUCTS,
    BTN_SHOPPING,
    back_to_list_keyboard,
    catalog_list_keyboard,
    dish_card_keyboard,
    main_keyboard,
)

log = logging.getLogger("homemealcraft")
router = Router()

DISH_CATEGORY_RU = {
    "breakfast": "Завтраки",
    "soup": "Супы",
    "main": "Основные",
    "side": "Гарниры",
    "salad": "Салаты",
    "bakery": "Выпечка",
    "dessert": "Десерты",
    "quick": "Быстрые",
    "prep": "Заготовки",
}


def _nutrition_line(kcal, protein, fat, carbs) -> str:
    if all(v is None for v in (kcal, protein, fat, carbs)):
        return "БЖУ на 100 г: не указано"
    return (
        f"БЖУ на 100 г: {kcal if kcal is not None else '—'} ккал · "
        f"Б {protein if protein is not None else '—'} · "
        f"Ж {fat if fat is not None else '—'} · "
        f"У {carbs if carbs is not None else '—'}"
    )


def _format_ingredient(item: Ingredient) -> str:
    lines = [f"<b>{item.name}</b>"]
    if item.category:
        lines.append(f"Категория: {item.category}")
    lines.append(_nutrition_line(item.kcal, item.protein_g, item.fat_g, item.carbs_g))
    if item.comments:
        lines.append(f"Комментарий: {item.comments}")
    return "\n".join(lines)


def _format_product(item: Product) -> str:
    title = item.name
    if item.brand:
        title = f"{item.name} · {item.brand}"
    lines = [f"<b>{title}</b>"]
    if item.ingredient:
        lines.append(f"Вид: {item.ingredient.name}")
        if item.ingredient.category:
            lines.append(f"Полка: {item.ingredient.category}")
    if item.barcode:
        lines.append(f"Штрихкод: {item.barcode}")
    if item.fat_percent is not None:
        lines.append(f"Жирность: {item.fat_percent:g}%")
    if item.weight_g is not None:
        lines.append(f"Вес: {item.weight_g:g} г")
    if item.unit_label:
        lines.append(f"Фасовка: {item.unit_label}")
    if item.manufacturer:
        lines.append(f"Производитель: {item.manufacturer}")
    lines.append(_nutrition_line(item.kcal, item.protein_g, item.fat_g, item.carbs_g))
    if item.composition:
        lines.append(f"Состав: {item.composition}")
    if item.comments:
        lines.append(f"Комментарий: {item.comments}")
    return "\n".join(lines)


def _fmt_dt(value) -> str:
    if value is None:
        return ""
    try:
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        local = dt.astimezone(ZoneInfo("Europe/Minsk"))
        return local.strftime("%d.%m.%Y %H:%M")
    except Exception:  # noqa: BLE001
        return str(value)[:16]


def _ingredient_lines(links) -> list[str]:
    lines: list[str] = []
    if not links:
        return ["• состав не указан"]
    for link in links:
        name = link.ingredient.name if link.ingredient else "?"
        if link.amount is not None:
            amount = f"{link.amount:g} {link.unit or 'g'}"
        else:
            amount = link.unit or ""
        note = f" — {link.note}" if link.note else ""
        lines.append(f"• {name}: {amount}{note}".rstrip(": "))
    return lines


def _sorted_versions(dish: Dish) -> list[DishVersion]:
    return sorted(
        getattr(dish, "versions", []) or [],
        key=lambda x: x.created_at or 0,
        reverse=True,
    )


def _pick_version(dish: Dish, version_id: str | None = None) -> DishVersion | None:
    versions = _sorted_versions(dish)
    if not versions:
        return None
    if version_id:
        for version in versions:
            if version.id == version_id:
                return version
    for version in versions:
        if version.is_current:
            return version
    return versions[0]


def _format_dish_card(dish: Dish, version: DishVersion | None = None) -> str:
    """Single card for one dish; `version` switches recipe body inside the same card."""
    cat = DISH_CATEGORY_RU.get(dish.category or "", dish.category)
    lines = [f"<b>{dish.name}</b>"]
    if cat:
        lines.append(f"Категория: {cat}")

    if dish.freezable is True:
        lines.append("Можно замораживать")
    elif dish.freezable is False:
        lines.append("Не замораживать")

    versions = _sorted_versions(dish)
    version = version or _pick_version(dish)

    if version:
        mark = "текущая" if version.is_current else "архив"
        lines.append("")
        lines.append(
            f"<b>Рецепт</b> · {_fmt_dt(version.created_at)} · {mark}"
            f" · всего версий: {len(versions)}"
        )
        if version.title:
            lines.append(version.title)
        if version.result_comment:
            lines.append(f"Результат: {version.result_comment}")
        meta: list[str] = []
        if version.cook_time_min is not None:
            meta.append(f"{version.cook_time_min} мин")
        if version.servings is not None:
            meta.append(f"{version.servings} порц.")
        if meta:
            lines.append(" · ".join(meta))
        if version.description:
            lines.append("")
            lines.append(version.description)
        lines.append("")
        lines.append("<b>Состав</b>")
        lines.extend(_ingredient_lines(version.ingredients))
        if version.instructions:
            lines.append("")
            lines.append("<b>Приготовление</b>")
            lines.append(version.instructions)
        lines.append("")
        lines.append(
            _nutrition_line(version.kcal, version.protein_g, version.fat_g, version.carbs_g)
        )
    else:
        # Legacy dish without versions yet
        meta: list[str] = []
        if dish.cook_time_min is not None:
            meta.append(f"{dish.cook_time_min} мин")
        if dish.servings is not None:
            meta.append(f"{dish.servings} порц.")
        if meta:
            lines.append(" · ".join(meta))
        if dish.description:
            lines.append("")
            lines.append(dish.description)
        lines.append("")
        lines.append("<b>Состав</b>")
        lines.extend(_ingredient_lines(dish.ingredients))
        if dish.instructions:
            lines.append("")
            lines.append("<b>Приготовление</b>")
            lines.append(dish.instructions)
        lines.append("")
        lines.append(_nutrition_line(dish.kcal, dish.protein_g, dish.fat_g, dish.carbs_g))
        if dish.comments:
            lines.append(f"Комментарий: {dish.comments}")

    if versions:
        lines.append("")
        lines.append("Переключайте версии кнопками ниже — это одно блюдо.")
    return "\n".join(lines)


async def _show_dish_card(
    target: Message,
    dish: Dish,
    *,
    version_id: str | None = None,
    edit: bool = False,
) -> None:
    version = _pick_version(dish, version_id)
    text = _format_dish_card(dish, version)
    versions = [
        (v.id, _version_button_label(v))
        for v in _sorted_versions(dish)
    ]
    markup = dish_card_keyboard(
        dish.id,
        versions,
        selected_version_id=version.id if version else None,
    )
    if edit:
        try:
            await target.edit_text(text, reply_markup=markup)
            return
        except Exception:  # noqa: BLE001 — message not modified / can't edit
            pass
    await target.answer(text, reply_markup=markup)


def _version_button_label(version: DishVersion) -> str:
    when = _fmt_dt(version.created_at) or "версия"
    prefix = "★ " if version.is_current else ""
    note = version.result_comment or version.title or "рецепт"
    note = " ".join(note.split())
    if len(note) > 28:
        note = note[:27] + "…"
    return f"{prefix}{when} · {note}"


def _format_shopping(item: ShoppingItem) -> str:
    mark = "куплено" if item.is_bought else "нужно купить"
    lines = [f"<b>{item.name}</b>", f"Статус: {mark}"]
    if item.category:
        lines.append(f"Категория: {item.category}")
    if item.product:
        brand = f" ({item.product.brand})" if item.product.brand else ""
        lines.append(f"Продукт: {item.product.name}{brand}")
        if item.product.ingredient:
            lines.append(f"Тип: {item.product.ingredient.name}")
    return "\n".join(lines)


def _product_button_label(item: Product) -> str:
    if item.brand:
        return f"{item.name} · {item.brand}"
    return item.name


async def _send_ingredients_list(target: Message, catalog: CatalogService) -> None:
    rows = await catalog.list_ingredients()
    if not rows:
        await target.answer("Ингредиентов пока нет. Пришлите JSON.")
        return
    buttons = [(i.id, i.name) for i in rows]
    await target.answer(
        "<b>Ингредиенты</b>\nВыберите позицию:",
        reply_markup=catalog_list_keyboard("ing", buttons),
    )


async def _send_products_list(target: Message, catalog: CatalogService) -> None:
    rows = await catalog.list_products()
    if not rows:
        await target.answer("Продуктов пока нет. Пришлите JSON.")
        return
    buttons = [(p.id, _product_button_label(p)) for p in rows]
    await target.answer(
        "<b>Продукты</b>\nВыберите позицию:",
        reply_markup=catalog_list_keyboard("prod", buttons),
    )


async def _send_dishes_list(
    target: Message, catalog: CatalogService, *, edit: bool = False
) -> None:
    rows = await catalog.list_dishes()
    if not rows:
        text = "Блюд пока нет. Пришлите JSON."
        if edit:
            try:
                await target.edit_text(text)
                return
            except Exception:  # noqa: BLE001
                pass
        await target.answer(text)
        return
    buttons = []
    for dish in rows:
        cat = DISH_CATEGORY_RU.get(dish.category or "", dish.category)
        label = f"{dish.name}" + (f" · {cat}" if cat else "")
        buttons.append((dish.id, label))
    text = "<b>Блюда</b>\nОдно блюдо = одна карточка. Версии рецепта — внутри."
    markup = catalog_list_keyboard("dish", buttons)
    if edit:
        try:
            await target.edit_text(text, reply_markup=markup)
            return
        except Exception:  # noqa: BLE001
            pass
    await target.answer(text, reply_markup=markup)


async def _send_shopping_list(target: Message, catalog: CatalogService) -> None:
    rows = await catalog.list_shopping()
    if not rows:
        await target.answer("Список покупок пуст.")
        return
    buttons = [(s.id, f"{'✓ ' if s.is_bought else ''}{s.name}") for s in rows]
    await target.answer(
        "<b>Покупки</b>\nВыберите позицию:",
        reply_markup=catalog_list_keyboard("shop", buttons),
    )


@router.message(CommandStart())
@router.message(F.text == BTN_HOME)
async def cmd_start(message: Message) -> None:
    await message.answer(
        "<b>Домашняя кухня</b>\n\n"
        "Пришлите JSON текстом или файлом — добавлю в каталог.\n"
        "Кнопки — списки блюд/продуктов.\n"
        "Правила для ИИ-агента: /rules",
        reply_markup=main_keyboard(),
    )


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _prompt_file() -> Path:
    return _project_root() / "docs" / "ai_agent_rules.md"


def _extract_system_prompt_body(raw: str) -> str:
    """Body between the first pair of --- markers (what to paste into the agent)."""
    parts = raw.split("---")
    if len(parts) >= 3:
        return parts[1].strip() + "\n"
    return raw.strip() + "\n"


@router.message(Command("help"))
@router.message(F.text == BTN_HELP)
async def cmd_help(message: Message) -> None:
    await message.answer(
        "<b>Импорт JSON</b>\n\n"
        "1. /rules — скачайте правила для ИИ-агента\n"
        "2. Вставьте их в system prompt агента\n"
        "3. Надиктуйте блюдо/продукт → пришлите JSON боту\n\n"
        "Блюда и продукты отдельно. Версии рецепта — внутри одного блюда. БЖУ на 100 г."
    )


@router.message(Command("rules"))
@router.message(Command("prompt"))
async def cmd_prompt(message: Message) -> None:
    path = _prompt_file()
    if not path.exists():
        log.error("prompt file missing: %s", path)
        await message.answer("Файл правил на сервере не найден. Нужен деплой docs/.")
        return

    raw = path.read_text(encoding="utf-8")
    body = _extract_system_prompt_body(raw)
    document = BufferedInputFile(
        body.encode("utf-8"),
        filename="ai_agent_rules.txt",
    )
    await message.answer_document(document)
    log.info("prompt downloaded user=%s bytes=%s", message.from_user.id if message.from_user else "?", len(body))


@router.message(Command("ingredients"))
@router.message(F.text == BTN_INGREDIENTS)
async def cmd_ingredients(message: Message, catalog: CatalogService) -> None:
    await _send_ingredients_list(message, catalog)


@router.message(Command("products"))
@router.message(F.text == BTN_PRODUCTS)
async def cmd_products(message: Message, catalog: CatalogService) -> None:
    await _send_products_list(message, catalog)


@router.message(Command("dishes"))
@router.message(F.text == BTN_DISHES)
async def cmd_dishes(message: Message, catalog: CatalogService) -> None:
    await _send_dishes_list(message, catalog)


@router.message(Command("shopping"))
@router.message(F.text == BTN_SHOPPING)
async def cmd_shopping(message: Message, catalog: CatalogService) -> None:
    await _send_shopping_list(message, catalog)


@router.callback_query(F.data == "noop")
async def on_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.startswith("list:"))
async def on_back_to_list(callback: CallbackQuery, catalog: CatalogService) -> None:
    kind = (callback.data or "").split(":", 1)[1]
    message = callback.message
    if not isinstance(message, Message):
        await callback.answer()
        return
    await callback.answer()
    if kind == "ing":
        await _send_ingredients_list(message, catalog)
    elif kind == "prod":
        await _send_products_list(message, catalog)
    elif kind == "dish":
        await _send_dishes_list(message, catalog, edit=True)
    elif kind == "shop":
        await _send_shopping_list(message, catalog)


@router.callback_query(F.data.startswith("ing:"))
async def on_ingredient(callback: CallbackQuery, catalog: CatalogService) -> None:
    item_id = (callback.data or "")[4:]
    item = await catalog.get_ingredient(item_id)
    if not item or not callback.message:
        await callback.answer("Не найдено", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(
        _format_ingredient(item),
        reply_markup=back_to_list_keyboard("ing"),
    )


@router.callback_query(F.data.startswith("prod:"))
async def on_product(callback: CallbackQuery, catalog: CatalogService) -> None:
    item_id = (callback.data or "")[5:]
    item = await catalog.get_product(item_id)
    if not item or not callback.message:
        await callback.answer("Не найдено", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(
        _format_product(item),
        reply_markup=back_to_list_keyboard("prod"),
    )


@router.callback_query(F.data.startswith("dish:"))
async def on_dish(callback: CallbackQuery, catalog: CatalogService) -> None:
    item_id = (callback.data or "")[5:]
    item = await catalog.get_dish(item_id)
    if not item or not isinstance(callback.message, Message):
        await callback.answer("Не найдено", show_alert=True)
        return
    await callback.answer()
    # Open dish inside the same message (list → card → versions all one bubble)
    await _show_dish_card(callback.message, item, edit=True)


@router.callback_query(F.data.startswith("ver:"))
async def on_version(callback: CallbackQuery, catalog: CatalogService) -> None:
    version_id = (callback.data or "")[4:]
    version = await catalog.get_dish_version(version_id)
    if not version or not isinstance(callback.message, Message):
        await callback.answer("Не найдено", show_alert=True)
        return
    dish = await catalog.get_dish(version.dish_id)
    if not dish:
        await callback.answer("Блюдо не найдено", show_alert=True)
        return
    await callback.answer()
    await _show_dish_card(
        callback.message,
        dish,
        version_id=version_id,
        edit=True,
    )


@router.callback_query(F.data.startswith("verd:"))
async def on_version_delete(callback: CallbackQuery, catalog: CatalogService) -> None:
    version_id = (callback.data or "")[5:]
    ok, dish_id = await catalog.delete_dish_version(version_id)
    if not ok:
        await callback.answer("Уже удалено", show_alert=True)
        return
    await callback.answer("Версия удалена")
    if dish_id and isinstance(callback.message, Message):
        dish = await catalog.get_dish(dish_id)
        if dish:
            await _show_dish_card(callback.message, dish, edit=True)


@router.callback_query(F.data.startswith("shop:"))
async def on_shopping(callback: CallbackQuery, catalog: CatalogService) -> None:
    item_id = (callback.data or "")[5:]
    item = await catalog.get_shopping(item_id)
    if not item or not callback.message:
        await callback.answer("Не найдено", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(
        _format_shopping(item),
        reply_markup=back_to_list_keyboard("shop"),
    )


def _format_import_error(exc: Exception) -> str:
    msg = str(exc).strip()
    if len(msg) > 350:
        msg = msg[:347] + "…"
    # Escape HTML special chars for Telegram
    msg = msg.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (
        "Не удалось принять пакет.\n"
        f"<code>{msg}</code>\n\n"
        "Проверьте schema_version, idempotency_key и actions."
    )


async def _apply_json_text(message: Message, text: str, import_engine: ImportEngine) -> None:
    try:
        raw = extract_json_object(text)
        packet = ImportPacket.model_validate(raw)
    except (json.JSONDecodeError, ValueError, ValidationError) as exc:
        log.warning("json parse failed: %s", exc)
        await message.answer(_format_import_error(exc))
        return

    report = await import_engine.apply_packet(packet)
    if report.status == "error":
        log.warning(
            "import error key=%s: %s",
            report.idempotency_key,
            report.message,
        )
        await message.answer(_format_import_error(Exception(report.message or "import error")))
        return

    log.info(
        "import ok key=%s status=%s actions=%s",
        report.idempotency_key,
        report.status,
        len(report.results),
    )
    await message.answer(report.summary_text())


@router.message(F.document)
async def on_document(message: Message, import_engine: ImportEngine, bot) -> None:
    doc = message.document
    if not doc:
        return
    if doc.file_size and doc.file_size > 2_000_000:
        await message.answer("Файл слишком большой (лимит 2 МБ).")
        return

    try:
        file = await bot.get_file(doc.file_id)
        assert file.file_path
        buffer = await bot.download_file(file.file_path)
        text = buffer.read().decode("utf-8")
    except UnicodeDecodeError:
        log.warning("json file not utf-8 name=%s", doc.file_name)
        await message.answer("Файл должен быть в UTF-8.")
        return

    await _apply_json_text(message, text, import_engine)


@router.message(IsJsonPayload())
async def on_text_json(message: Message, import_engine: ImportEngine) -> None:
    await _apply_json_text(message, message.text or "", import_engine)


@router.message(IsUnknownText())
async def on_unknown_text(message: Message) -> None:
    log.debug("unknown text user message ignored politely")
    await message.answer(
        "Не похоже на JSON.\n"
        "Выберите раздел кнопкой или пришлите JSON-пакет от ИИ-агента.",
        reply_markup=main_keyboard(),
    )
