from __future__ import annotations

import json
import logging
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from pydantic import ValidationError

from app.db.models import Delivery, Dish, DishVersion, Ingredient, Product, ShoppingItem, StockItem
from app.domain.catalog import PAGE_SIZE, CatalogService, PageResult
from app.domain.import_engine import ImportEngine
from app.domain.json_extract import extract_json_object
from app.domain.schemas import ImportPacket
from bot.filters import IsJsonPayload, IsUnknownText
from bot.keyboards import (
    BTN_DELIVERIES,
    BTN_DISHES,
    BTN_HELP,
    BTN_HOME,
    BTN_INGREDIENTS,
    BTN_PREPS,
    BTN_PRODUCTS,
    BTN_SHOPPING,
    BTN_STOCK,
    back_to_list_keyboard,
    catalog_list_keyboard,
    confirm_delete_version_keyboard,
    dish_card_keyboard,
    main_keyboard,
    need_checklist_keyboard,
    confirm_keyboard,
    delivery_card_keyboard,
    ingredient_card_keyboard,
    product_card_keyboard,
    shopping_card_keyboard,
    stock_card_keyboard,
)

PREP_CATEGORY = "prep"

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

DELIVERY_CATEGORY_RU = {
    "food": "Еда",
    "grocery": "Продукты",
    "pharmacy": "Аптека",
    "other": "Прочее",
}

INGREDIENT_CATEGORY_RU = {
    "dairy": "Молочка",
    "meat": "Мясо",
    "fish": "Рыба",
    "eggs": "Яйца",
    "vegetables": "Овощи",
    "fruit": "Фрукты",
    "grocery": "Бакалея",
    "bakery": "Хлеб",
    "frozen": "Заморозка",
    "drinks": "Напитки",
    "sauce": "Соусы",
    "spice": "Специи",
    "oil": "Масла",
    "sweet": "Сладкое",
    "snack": "Перекус",
    "household": "Бытовое",
    "other": "Прочее",
}

LIST_TITLES = {
    "ing": "Ингредиенты",
    "prod": "Продукты",
    "dish": "Блюда",
    "prep": "Заготовки",
    "deliv": "Доставки",
    "stock": "Кладовая",
    "shop": "Покупки",
}

MENU_TEXTS = {
    BTN_HOME,
    BTN_HELP,
    BTN_INGREDIENTS,
    BTN_PRODUCTS,
    BTN_DISHES,
    BTN_PREPS,
    BTN_DELIVERIES,
    BTN_STOCK,
    BTN_SHOPPING,
}


class CatalogSearch(StatesGroup):
    waiting_query = State()


class NeedCheck(StatesGroup):
    active = State()


def _nutrition_line(kcal, protein, fat, carbs) -> str:
    if all(v is None for v in (kcal, protein, fat, carbs)):
        return "БЖУ на 100 г: не указано"
    return (
        f"БЖУ на 100 г: {kcal if kcal is not None else '—'} ккал · "
        f"Б {protein if protein is not None else '—'} · "
        f"Ж {fat if fat is not None else '—'} · "
        f"У {carbs if carbs is not None else '—'}"
    )


def _shelf_ru(code: str | None) -> str | None:
    if not code:
        return None
    return INGREDIENT_CATEGORY_RU.get(code, code)


def _format_ingredient(item: Ingredient) -> str:
    lines = [f"<b>{item.name}</b>"]
    shelf = _shelf_ru(item.category)
    if shelf:
        lines.append(f"Полка: {shelf}")
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
        shelf = _shelf_ru(item.ingredient.category)
        if shelf:
            lines.append(f"Полка: {shelf}")
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
    if item.composition:
        lines.append("")
        lines.append(f"Состав: {item.composition}")
    lines.append(_nutrition_line(item.kcal, item.protein_g, item.fat_g, item.carbs_g))
    if item.comments:
        lines.append(f"Комментарий: {item.comments}")
    return "\n".join(lines)


def _ingredient_lines(links) -> list[str]:
    if not links:
        return ["Состав: не указан"]
    lines = ["Состав:"]
    for link in links:
        name = link.ingredient.name if link.ingredient else "?"
        amount = f"{link.amount:g}" if link.amount is not None else "—"
        unit = link.unit or "g"
        note = f" ({link.note})" if link.note else ""
        lines.append(f"· {name}: {amount} {unit}{note}")
    return lines


def _fmt_dt(value) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo("UTC"))
    local = value.astimezone(ZoneInfo("Europe/Minsk"))
    return local.strftime("%d.%m.%Y %H:%M")


def _sorted_versions(dish: Dish) -> list[DishVersion]:
    return sorted(
        getattr(dish, "versions", []) or [],
        key=lambda v: v.created_at or v.id,
        reverse=True,
    )


def _pick_version(dish: Dish, version_id: str | None = None) -> DishVersion | None:
    versions = _sorted_versions(dish)
    if not versions:
        return None
    if version_id:
        for v in versions:
            if v.id == version_id:
                return v
    for v in versions:
        if v.is_current:
            return v
    if dish.current_version_id:
        for v in versions:
            if v.id == dish.current_version_id:
                return v
    return versions[0]


def _format_dish_card(dish: Dish, version: DishVersion | None = None) -> str:
    cat = DISH_CATEGORY_RU.get(dish.category or "", dish.category)
    lines = [f"<b>{dish.name}</b>"]
    if cat:
        lines.append(f"Категория: {cat}")
    if dish.freezable is True:
        lines.append("Заморозка: да")
    elif dish.freezable is False:
        lines.append("Заморозка: нет")

    versions = _sorted_versions(dish)
    version = version or _pick_version(dish)

    if version:
        meta = []
        if version.cook_time_min is not None:
            meta.append(f"{version.cook_time_min} мин")
        if version.servings is not None:
            meta.append(f"{version.servings} порц.")
        if meta:
            lines.append(" · ".join(meta))
        when = _fmt_dt(version.created_at)
        title = version.title or version.result_comment or "рецепт"
        lines.append(f"Версия: {title}" + (f" ({when})" if when else ""))
        if version.description:
            lines.append("")
            lines.append(version.description)
        lines.extend(_ingredient_lines(version.ingredients))
        if version.instructions:
            lines.append("")
            lines.append(version.instructions)
        lines.append("")
        lines.append(
            _nutrition_line(version.kcal, version.protein_g, version.fat_g, version.carbs_g)
        )
        if version.result_comment:
            lines.append(f"Результат: {version.result_comment}")
    else:
        lines.append("Состав: нет версии рецепта (пришлите JSON с ingredients).")
        if dish.comments:
            lines.append(f"Комментарий: {dish.comments}")

    if versions:
        lines.append("")
        noun = "заготовка" if (dish.category or "") == PREP_CATEGORY else "блюдо"
        lines.append(f"Переключайте версии кнопками ниже — это одна {noun}.")
    return "\n".join(lines)


def _dish_list_kind(dish: Dish) -> str:
    return "prep" if (dish.category or "") == PREP_CATEGORY else "dish"


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
    shelf = _shelf_ru(item.category) if item.category else None
    if shelf:
        lines.append(f"Категория: {shelf}")
    elif item.category:
        lines.append(f"Категория: {item.category}")
    if item.ingredient:
        lines.append(f"Вид: {item.ingredient.name}")
    if item.product:
        brand = f" ({item.product.brand})" if item.product.brand else ""
        lines.append(f"Продукт: {item.product.name}{brand}")
        if item.product.ingredient and not item.ingredient:
            lines.append(f"Вид: {item.product.ingredient.name}")
    return "\n".join(lines)


def _format_delivery(item: Delivery) -> str:
    cat = DELIVERY_CATEGORY_RU.get(item.category or "", item.category)
    lines = [f"<b>{item.name}</b>"]
    if cat:
        lines.append(f"Категория: {cat}")
    if item.url:
        lines.append(f'<a href="{item.url}">Ссылка</a>')
        lines.append(item.url)
    if item.description:
        lines.append("")
        lines.append(item.description)
    if item.comments:
        lines.append(f"Комментарий: {item.comments}")
    return "\n".join(lines)


def _format_stock(item: StockItem) -> str:
    lines = [f"<b>{item.name}</b>", "В кладовой"]
    if item.ingredient:
        shelf = _shelf_ru(item.ingredient.category)
        lines.append(f"Вид: {item.ingredient.name}")
        if shelf:
            lines.append(f"Полка: {shelf}")
    if item.amount is not None:
        unit = item.unit or "g"
        lines.append(f"Количество: {item.amount:g} {unit}")
    elif item.unit:
        lines.append(f"Ед.: {item.unit}")
    lines.append(f"Открыто: {'да' if item.is_opened else 'нет'}")
    if item.expires_on:
        lines.append(f"Годен до: {item.expires_on.isoformat()}")
    if item.comments:
        lines.append(f"Комментарий: {item.comments}")
    return "\n".join(lines)


def _product_button_label(item: Product) -> str:
    if item.brand:
        return f"{item.name} · {item.brand}"
    return item.name


async def _edit_or_answer(
    target: Message,
    text: str,
    *,
    markup=None,
    edit: bool = False,
) -> None:
    """Show text, optionally with an inline keyboard.

    Reply keyboard and inline buttons cannot share one message. Telegram
    rejects editReplyMarkup that tries to swap a reply keyboard for an inline
    list, so a new screen is sent with the inline keyboard only. The reply
    keyboard already on screen stays until a later message replaces it.
    """
    if edit:
        try:
            await target.edit_text(text, reply_markup=markup)
            return
        except Exception:  # noqa: BLE001
            log.debug("edit failed, sending a new message", exc_info=True)
    await target.answer(text, reply_markup=markup)


async def _show_dish_card(
    target: Message,
    dish: Dish,
    *,
    version_id: str | None = None,
    edit: bool = False,
) -> None:
    version = _pick_version(dish, version_id)
    text = _format_dish_card(dish, version)
    versions = [(v.id, _version_button_label(v)) for v in _sorted_versions(dish)]
    markup = dish_card_keyboard(
        dish.id,
        versions,
        selected_version_id=version.id if version else None,
        list_kind=_dish_list_kind(dish),
    )
    await _edit_or_answer(target, text, markup=markup, edit=edit)


def _page_header(kind: str, page: PageResult) -> str:
    title = LIST_TITLES.get(kind, kind)
    bits = [f"<b>{title}</b>"]
    if page.query:
        bits.append(f"Поиск: «{page.query}»")
    if page.total:
        bits.append(f"{page.total} шт. · стр. {page.page + 1}/{page.pages}")
    return "\n".join(bits)


async def _fetch_page(
    catalog: CatalogService,
    kind: str,
    *,
    page: int = 0,
    q: str | None = None,
) -> PageResult:
    if kind == "ing":
        return await catalog.list_ingredients(page=page, q=q)
    if kind == "prod":
        return await catalog.list_products(page=page, q=q)
    if kind == "dish":
        return await catalog.list_dishes(page=page, q=q, exclude_category=PREP_CATEGORY)
    if kind == "prep":
        return await catalog.list_dishes(page=page, q=q, category=PREP_CATEGORY)
    if kind == "deliv":
        return await catalog.list_deliveries(page=page, q=q)
    if kind == "stock":
        return await catalog.list_stock(page=page, q=q)
    if kind == "shop":
        return await catalog.list_shopping(page=page, q=q)
    return PageResult([], 0, 0, PAGE_SIZE, q)


def _page_buttons(kind: str, page: PageResult) -> list[tuple[str, str]]:
    if kind == "ing":
        return [(i.id, i.name) for i in page.items]
    if kind == "prod":
        return [(p.id, _product_button_label(p)) for p in page.items]
    if kind == "dish":
        buttons = []
        for dish in page.items:
            cat = DISH_CATEGORY_RU.get(dish.category or "", dish.category)
            label = f"{dish.name}" + (f" · {cat}" if cat else "")
            buttons.append((dish.id, label))
        return buttons
    if kind == "prep":
        return [(d.id, d.name) for d in page.items]
    if kind == "deliv":
        buttons = []
        for item in page.items:
            cat = DELIVERY_CATEGORY_RU.get(item.category or "", item.category)
            label = f"{item.name}" + (f" · {cat}" if cat else "")
            buttons.append((item.id, label))
        return buttons
    if kind == "stock":
        buttons = []
        for item in page.items:
            if item.amount is not None:
                label = f"{item.name} · {item.amount:g}{item.unit or 'g'}"
            else:
                label = item.name
            buttons.append((item.id, label))
        return buttons
    if kind == "shop":
        return [(s.id, f"{'✓ ' if s.is_bought else ''}{s.name}") for s in page.items]
    return []


def _empty_list_text(kind: str, query: str | None) -> str:
    if query:
        return f"Ничего не найдено по «{query}»."
    defaults = {
        "ing": "Ингредиентов пока нет. Пришлите JSON.",
        "prod": "Продуктов пока нет. Пришлите JSON.",
        "dish": "Блюд пока нет. Пришлите JSON.",
        "prep": 'Заготовок пока нет. Пришлите JSON с category: "prep".',
        "deliv": "Доставок пока нет. Пришлите JSON.",
        "stock": "Кладовая пуста. Отметьте «Есть» в составе блюда или пришлите stock.upsert.",
        "shop": "Список покупок пуст.",
    }
    return defaults.get(kind, "Пусто.")


async def _send_catalog_list(
    target: Message,
    catalog: CatalogService,
    kind: str,
    *,
    page: int = 0,
    q: str | None = None,
    edit: bool = False,
    state: FSMContext | None = None,
) -> None:
    if state is not None:
        await state.update_data(list_kind=kind, list_page=page, list_q=q)

    result = await _fetch_page(catalog, kind, page=page, q=q)
    if not result.items:
        text = _empty_list_text(kind, q)
        markup = catalog_list_keyboard(kind, [], page=0, pages=1, query=q)
        await _edit_or_answer(target, text, markup=markup, edit=edit)
        return

    subtitle = {
        "dish": "Одно блюдо = одна карточка. Версии — внутри.",
        "prep": 'Те же поля, что у блюд. JSON: dish.upsert + category "prep".',
        "deliv": "Где что заказывать — ссылки и заметки.",
        "stock": "Что есть дома. «Есть» в составе блюда тоже кладёт сюда.",
        "shop": "Отметьте купленное или удалите позицию.",
    }.get(kind, "Выберите позицию:")

    text = _page_header(kind, result) + "\n" + subtitle
    markup = catalog_list_keyboard(
        kind,
        _page_buttons(kind, result),
        page=result.page,
        pages=result.pages,
        query=result.query,
    )
    await _edit_or_answer(target, text, markup=markup, edit=edit)


async def _show_need_checklist(
    target: Message,
    dish: Dish,
    remaining: list[tuple[str, str]],
    *,
    in_stock: set[str] | None = None,
    edit: bool = True,
) -> None:
    stock_ids = in_stock or set()
    if not remaining:
        text = (
            f"<b>{dish.name}</b>\n\n"
            "Состав разобран: всё отмечено «есть» или ушло в покупки."
        )
        markup = need_checklist_keyboard(dish.id, [])
        await _edit_or_answer(target, text, markup=markup, edit=edit)
        return

    lines = [
        f"<b>{dish.name}</b> — проверить состав",
        "Нет в кладовой: Есть → кладовая / В покупки.",
        "Уже в кладовой: Ещё есть / Кончилось → покупки.",
        "",
    ]
    items: list[tuple[str, str, bool]] = []
    for ing_id, label in remaining:
        has = ing_id in stock_ids
        mark = " · в кладовой" if has else ""
        lines.append(f"· {label}{mark}")
        items.append((ing_id, label, has))
    markup = need_checklist_keyboard(dish.id, items)
    await _edit_or_answer(target, "\n".join(lines), markup=markup, edit=edit)


@router.message(CommandStart())
@router.message(F.text == BTN_HOME)
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "<b>Домашняя кухня</b>\n\n"
        "Пришлите JSON текстом или файлом — добавлю в каталог.\n"
        "Кнопки — списки. У блюда: «Проверить состав» → покупки.\n"
        "Правила для ИИ-агента: /rules",
        reply_markup=main_keyboard(),
    )


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _prompt_file() -> Path:
    return _project_root() / "docs" / "ai_agent_rules.md"


def _extract_system_prompt_body(raw: str) -> str:
    lines = raw.splitlines()
    fences = [i for i, line in enumerate(lines) if line.strip() == "---"]
    if len(fences) >= 2:
        start, end = fences[0] + 1, fences[1]
        return "\n".join(lines[start:end]).strip() + "\n"
    return raw.strip() + "\n"


@router.message(Command("help"))
@router.message(F.text == BTN_HELP)
async def cmd_help(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "<b>Импорт JSON</b>\n\n"
        "1. /rules — скачайте правила для ИИ-агента\n"
        "2. Вставьте их в system prompt агента\n"
        "3. Надиктуйте блюдо/заготовку/продукт/доставку → JSON боту\n\n"
        "То же имя блюда + состав = новая версия. "
        "У блюда «Проверить состав»: Есть → кладовая, В покупки → список. БЖУ на 100 г.",
        reply_markup=main_keyboard(),
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
    log.info(
        "prompt downloaded user=%s bytes=%s",
        message.from_user.id if message.from_user else "?",
        len(body),
    )


@router.message(Command("ingredients"))
@router.message(F.text == BTN_INGREDIENTS)
async def cmd_ingredients(
    message: Message, catalog: CatalogService, state: FSMContext
) -> None:
    await state.clear()
    await _send_catalog_list(message, catalog, "ing", state=state)


@router.message(Command("products"))
@router.message(F.text == BTN_PRODUCTS)
async def cmd_products(
    message: Message, catalog: CatalogService, state: FSMContext
) -> None:
    await state.clear()
    await _send_catalog_list(message, catalog, "prod", state=state)


@router.message(Command("dishes"))
@router.message(F.text == BTN_DISHES)
async def cmd_dishes(
    message: Message, catalog: CatalogService, state: FSMContext
) -> None:
    await state.clear()
    await _send_catalog_list(message, catalog, "dish", state=state)


@router.message(Command("preps"))
@router.message(F.text == BTN_PREPS)
async def cmd_preps(
    message: Message, catalog: CatalogService, state: FSMContext
) -> None:
    await state.clear()
    await _send_catalog_list(message, catalog, "prep", state=state)


@router.message(Command("deliveries"))
@router.message(F.text == BTN_DELIVERIES)
async def cmd_deliveries(
    message: Message, catalog: CatalogService, state: FSMContext
) -> None:
    await state.clear()
    await _send_catalog_list(message, catalog, "deliv", state=state)


@router.message(Command("stock"))
@router.message(F.text == BTN_STOCK)
async def cmd_stock(
    message: Message, catalog: CatalogService, state: FSMContext
) -> None:
    await state.clear()
    await _send_catalog_list(message, catalog, "stock", state=state)


@router.message(Command("shopping"))
@router.message(F.text == BTN_SHOPPING)
async def cmd_shopping(
    message: Message, catalog: CatalogService, state: FSMContext
) -> None:
    await state.clear()
    await _send_catalog_list(message, catalog, "shop", state=state)


@router.callback_query(F.data == "noop")
async def on_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.startswith("list:"))
async def on_back_to_list(
    callback: CallbackQuery, catalog: CatalogService, state: FSMContext
) -> None:
    kind = (callback.data or "").split(":", 1)[1]
    message = callback.message
    if not isinstance(message, Message):
        await callback.answer()
        return
    await callback.answer()
    data = await state.get_data()
    q = data.get("list_q") if data.get("list_kind") == kind else None
    await state.set_state(None)
    await _send_catalog_list(
        message, catalog, kind, page=0, q=q, edit=True, state=state
    )


@router.callback_query(F.data.startswith("page:"))
async def on_page(
    callback: CallbackQuery, catalog: CatalogService, state: FSMContext
) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 3:
        await callback.answer()
        return
    _, kind, page_s = parts
    try:
        page = int(page_s)
    except ValueError:
        await callback.answer()
        return
    message = callback.message
    if not isinstance(message, Message):
        await callback.answer()
        return
    data = await state.get_data()
    q = data.get("list_q") if data.get("list_kind") == kind else None
    await callback.answer()
    await _send_catalog_list(
        message, catalog, kind, page=page, q=q, edit=True, state=state
    )


@router.callback_query(F.data.startswith("search:"))
async def on_search_start(callback: CallbackQuery, state: FSMContext) -> None:
    kind = (callback.data or "").split(":", 1)[1]
    await state.set_state(CatalogSearch.waiting_query)
    await state.update_data(list_kind=kind)
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer(
            f"Введите текст поиска для «{LIST_TITLES.get(kind, kind)}» "
            "(или /start чтобы отменить):"
        )


@router.callback_query(F.data.startswith("clearq:"))
async def on_clear_query(
    callback: CallbackQuery, catalog: CatalogService, state: FSMContext
) -> None:
    kind = (callback.data or "").split(":", 1)[1]
    message = callback.message
    if not isinstance(message, Message):
        await callback.answer()
        return
    await callback.answer()
    await _send_catalog_list(
        message, catalog, kind, page=0, q=None, edit=True, state=state
    )


@router.message(StateFilter(CatalogSearch.waiting_query), F.text)
async def on_search_query(
    message: Message, catalog: CatalogService, state: FSMContext
) -> None:
    text = (message.text or "").strip()
    if text.startswith("/") or text in MENU_TEXTS:
        await state.clear()
        return
    data = await state.get_data()
    kind = data.get("list_kind") or "dish"
    await state.set_state(None)
    await _send_catalog_list(
        message, catalog, kind, page=0, q=text, edit=False, state=state
    )


@router.callback_query(F.data.startswith("ing:"))
async def on_ingredient(callback: CallbackQuery, catalog: CatalogService) -> None:
    item_id = (callback.data or "")[4:]
    item = await catalog.get_ingredient(item_id)
    if not item or not isinstance(callback.message, Message):
        await callback.answer("Не найдено", show_alert=True)
        return
    await callback.answer()
    await _edit_or_answer(
        callback.message,
        _format_ingredient(item),
        markup=ingredient_card_keyboard(item.id),
        edit=True,
    )


@router.callback_query(F.data.startswith("ingdel:"))
async def on_ingredient_delete_ask(callback: CallbackQuery) -> None:
    item_id = (callback.data or "")[7:]
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    await callback.answer()
    await callback.message.edit_reply_markup(
        reply_markup=confirm_keyboard(f"ingok:{item_id}", f"ing:{item_id}")
    )


@router.callback_query(F.data.startswith("ingok:"))
async def on_ingredient_delete_ok(
    callback: CallbackQuery, catalog: CatalogService, state: FSMContext
) -> None:
    item_id = (callback.data or "")[6:]
    ok = await catalog.delete_ingredient(item_id)
    await callback.answer("Удалено" if ok else "Уже нет")
    if isinstance(callback.message, Message):
        await _send_catalog_list(
            callback.message, catalog, "ing", edit=True, state=state
        )


@router.callback_query(F.data.startswith("prod:"))
async def on_product(callback: CallbackQuery, catalog: CatalogService) -> None:
    item_id = (callback.data or "")[5:]
    if item_id.startswith("del:"):
        return
    item = await catalog.get_product(item_id)
    if not item or not isinstance(callback.message, Message):
        await callback.answer("Не найдено", show_alert=True)
        return
    await callback.answer()
    await _edit_or_answer(
        callback.message,
        _format_product(item),
        markup=product_card_keyboard(item.id),
        edit=True,
    )


@router.callback_query(F.data.startswith("proddel:"))
async def on_product_delete_ask(callback: CallbackQuery) -> None:
    item_id = (callback.data or "")[8:]
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    await callback.answer()
    await callback.message.edit_reply_markup(
        reply_markup=confirm_keyboard(f"prodok:{item_id}", f"prod:{item_id}")
    )


@router.callback_query(F.data.startswith("prodok:"))
async def on_product_delete_ok(
    callback: CallbackQuery, catalog: CatalogService, state: FSMContext
) -> None:
    item_id = (callback.data or "")[7:]
    ok = await catalog.delete_product(item_id)
    await callback.answer("Удалено" if ok else "Уже нет")
    if isinstance(callback.message, Message):
        await _send_catalog_list(
            callback.message, catalog, "prod", edit=True, state=state
        )


@router.callback_query(F.data.startswith("dish:"))
async def on_dish(callback: CallbackQuery, catalog: CatalogService) -> None:
    item_id = (callback.data or "")[5:]
    item = await catalog.get_dish(item_id)
    if not item or not isinstance(callback.message, Message):
        await callback.answer("Не найдено", show_alert=True)
        return
    await callback.answer()
    await _show_dish_card(callback.message, item, edit=True)


@router.callback_query(F.data.startswith("prep:"))
async def on_prep(callback: CallbackQuery, catalog: CatalogService) -> None:
    item_id = (callback.data or "")[5:]
    item = await catalog.get_dish(item_id)
    if not item or not isinstance(callback.message, Message):
        await callback.answer("Не найдено", show_alert=True)
        return
    await callback.answer()
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
async def on_version_delete_ask(callback: CallbackQuery) -> None:
    version_id = (callback.data or "")[5:]
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    await callback.answer()
    await callback.message.edit_reply_markup(
        reply_markup=confirm_delete_version_keyboard(version_id)
    )


@router.callback_query(F.data.startswith("verdok:"))
async def on_version_delete_confirm(
    callback: CallbackQuery, catalog: CatalogService
) -> None:
    version_id = (callback.data or "")[7:]
    ok, dish_id = await catalog.delete_dish_version(version_id)
    if not ok:
        await callback.answer("Уже удалено", show_alert=True)
        return
    await callback.answer("Версия удалена")
    if dish_id and isinstance(callback.message, Message):
        dish = await catalog.get_dish(dish_id)
        if dish:
            await _show_dish_card(callback.message, dish, edit=True)


@router.callback_query(F.data.startswith("need:"))
async def on_need_start(
    callback: CallbackQuery, catalog: CatalogService, state: FSMContext
) -> None:
    dish_id = (callback.data or "")[5:]
    dish = await catalog.get_dish(dish_id)
    if not dish or not isinstance(callback.message, Message):
        await callback.answer("Не найдено", show_alert=True)
        return

    version = _pick_version(dish)
    if not version:
        await callback.answer("Нет версии рецепта", show_alert=True)
        return
    remaining: list[tuple[str, str]] = []
    for link in version.ingredients or []:
        if not link.ingredient:
            continue
        label = link.ingredient.name
        if link.amount is not None:
            label = f"{label} ({link.amount:g} {link.unit or 'g'})"
        remaining.append((link.ingredient_id, label))

    if not remaining:
        await callback.answer("Состав пуст", show_alert=True)
        return

    in_stock = await catalog.stock_ingredient_ids()
    await state.set_state(NeedCheck.active)
    await state.update_data(need_dish_id=dish_id, need_items=remaining)
    await callback.answer()
    await _show_need_checklist(
        callback.message, dish, remaining, in_stock=in_stock, edit=True
    )


@router.callback_query(F.data.startswith("needh:"), StateFilter(NeedCheck.active))
async def on_need_have(
    callback: CallbackQuery, catalog: CatalogService, state: FSMContext
) -> None:
    try:
        idx = int((callback.data or "").split(":")[1])
    except (IndexError, ValueError):
        await callback.answer()
        return
    data = await state.get_data()
    remaining: list = list(data.get("need_items") or [])
    dish_id = data.get("need_dish_id")
    if idx < 0 or idx >= len(remaining) or not dish_id:
        await callback.answer("Устарело", show_alert=True)
        return
    ing_id, label = remaining.pop(idx)
    name = label.split(" (", 1)[0]
    amount = None
    unit = None
    dish = await catalog.get_dish(dish_id)
    if dish:
        version = _pick_version(dish)
        for link in (version.ingredients if version else []) or []:
            if link.ingredient_id == ing_id and link.ingredient:
                name = link.ingredient.name
                amount = link.amount
                unit = link.unit
                break
    await catalog.upsert_stock(
        name=name, ingredient_id=ing_id, amount=amount, unit=unit
    )
    await state.update_data(need_items=remaining)
    if not dish or not isinstance(callback.message, Message):
        await callback.answer("В кладовую")
        return
    await callback.answer(f"В кладовую: {name}")
    if not remaining:
        await state.clear()
    in_stock = await catalog.stock_ingredient_ids()
    await _show_need_checklist(
        callback.message, dish, remaining, in_stock=in_stock, edit=True
    )


@router.callback_query(F.data.startswith("needb:"), StateFilter(NeedCheck.active))
async def on_need_buy(
    callback: CallbackQuery, catalog: CatalogService, state: FSMContext
) -> None:
    try:
        idx = int((callback.data or "").split(":")[1])
    except (IndexError, ValueError):
        await callback.answer()
        return
    data = await state.get_data()
    remaining: list = list(data.get("need_items") or [])
    dish_id = data.get("need_dish_id")
    if idx < 0 or idx >= len(remaining) or not dish_id:
        await callback.answer("Устарело", show_alert=True)
        return
    ing_id, label = remaining.pop(idx)
    # label may include amount — shopping name = ingredient name before " ("
    name = label.split(" (", 1)[0]
    dish = await catalog.get_dish(dish_id)
    category = None
    if dish:
        version = _pick_version(dish)
        for link in (version.ingredients if version else []) or []:
            if link.ingredient_id == ing_id and link.ingredient:
                name = link.ingredient.name
                category = link.ingredient.category
                break
    await catalog.add_shopping_by_name(
        name, category=category, ingredient_id=ing_id
    )
    await state.update_data(need_items=remaining)
    if not dish or not isinstance(callback.message, Message):
        await callback.answer("В покупки")
        return
    await callback.answer(f"В покупки: {name}")
    if not remaining:
        await state.clear()
    in_stock = await catalog.stock_ingredient_ids()
    await _show_need_checklist(
        callback.message, dish, remaining, in_stock=in_stock, edit=True
    )


@router.callback_query(F.data.startswith("needk:"), StateFilter(NeedCheck.active))
async def on_need_keep(
    callback: CallbackQuery, catalog: CatalogService, state: FSMContext
) -> None:
    """Still have in pantry — just remove from checklist."""
    try:
        idx = int((callback.data or "").split(":")[1])
    except (IndexError, ValueError):
        await callback.answer()
        return
    data = await state.get_data()
    remaining: list = list(data.get("need_items") or [])
    dish_id = data.get("need_dish_id")
    if idx < 0 or idx >= len(remaining) or not dish_id:
        await callback.answer("Устарело", show_alert=True)
        return
    remaining.pop(idx)
    await state.update_data(need_items=remaining)
    dish = await catalog.get_dish(dish_id)
    if not dish or not isinstance(callback.message, Message):
        await callback.answer("Ещё есть")
        return
    await callback.answer("Ещё есть")
    if not remaining:
        await state.clear()
    in_stock = await catalog.stock_ingredient_ids()
    await _show_need_checklist(
        callback.message, dish, remaining, in_stock=in_stock, edit=True
    )


@router.callback_query(F.data.startswith("needo:"), StateFilter(NeedCheck.active))
async def on_need_out(
    callback: CallbackQuery, catalog: CatalogService, state: FSMContext
) -> None:
    """Ran out: remove from stock, add to shopping."""
    try:
        idx = int((callback.data or "").split(":")[1])
    except (IndexError, ValueError):
        await callback.answer()
        return
    data = await state.get_data()
    remaining: list = list(data.get("need_items") or [])
    dish_id = data.get("need_dish_id")
    if idx < 0 or idx >= len(remaining) or not dish_id:
        await callback.answer("Устарело", show_alert=True)
        return
    ing_id, label = remaining.pop(idx)
    name = label.split(" (", 1)[0]
    category = None
    dish = await catalog.get_dish(dish_id)
    if dish:
        version = _pick_version(dish)
        for link in (version.ingredients if version else []) or []:
            if link.ingredient_id == ing_id and link.ingredient:
                name = link.ingredient.name
                category = link.ingredient.category
                break
    await catalog.remove_stock_by_ingredient(ing_id)
    await catalog.add_shopping_by_name(
        name, category=category, ingredient_id=ing_id
    )
    await state.update_data(need_items=remaining)
    if not dish or not isinstance(callback.message, Message):
        await callback.answer("Кончилось → покупки")
        return
    await callback.answer(f"Кончилось: {name}")
    if not remaining:
        await state.clear()
    in_stock = await catalog.stock_ingredient_ids()
    await _show_need_checklist(
        callback.message, dish, remaining, in_stock=in_stock, edit=True
    )


@router.callback_query(F.data.startswith("needdone:"))
async def on_need_done(
    callback: CallbackQuery, catalog: CatalogService, state: FSMContext
) -> None:
    dish_id = (callback.data or "")[9:]
    await state.clear()
    dish = await catalog.get_dish(dish_id)
    if not dish or not isinstance(callback.message, Message):
        await callback.answer("Не найдено", show_alert=True)
        return
    await callback.answer()
    await _show_dish_card(callback.message, dish, edit=True)


@router.callback_query(F.data.startswith("needh:"))
@router.callback_query(F.data.startswith("needb:"))
@router.callback_query(F.data.startswith("needk:"))
@router.callback_query(F.data.startswith("needo:"))
async def on_need_stale(callback: CallbackQuery) -> None:
    await callback.answer("Сначала нажмите «Проверить состав»", show_alert=True)


@router.callback_query(F.data.startswith("shop:"))
async def on_shopping(callback: CallbackQuery, catalog: CatalogService) -> None:
    item_id = (callback.data or "")[5:]
    item = await catalog.get_shopping(item_id)
    if not item or not isinstance(callback.message, Message):
        await callback.answer("Не найдено", show_alert=True)
        return
    await callback.answer()
    await _edit_or_answer(
        callback.message,
        _format_shopping(item),
        markup=shopping_card_keyboard(item.id),
        edit=True,
    )


@router.callback_query(F.data.startswith("shopok:"))
async def on_shopping_ok(
    callback: CallbackQuery, catalog: CatalogService, state: FSMContext
) -> None:
    item_id = (callback.data or "")[7:]
    ok = await catalog.mark_shopping_bought(item_id, to_stock=True)
    if not ok:
        await callback.answer("Уже нет", show_alert=True)
        return
    await callback.answer("Куплено → кладовая")
    if isinstance(callback.message, Message):
        await _send_catalog_list(
            callback.message, catalog, "shop", edit=True, state=state
        )


@router.callback_query(F.data.startswith("shoprm:"))
async def on_shopping_rm(
    callback: CallbackQuery, catalog: CatalogService, state: FSMContext
) -> None:
    item_id = (callback.data or "")[7:]
    ok = await catalog.remove_shopping(item_id)
    if not ok:
        await callback.answer("Уже нет", show_alert=True)
        return
    await callback.answer("Удалено")
    if isinstance(callback.message, Message):
        await _send_catalog_list(
            callback.message, catalog, "shop", edit=True, state=state
        )


@router.callback_query(F.data.startswith("deliv:"))
async def on_delivery(callback: CallbackQuery, catalog: CatalogService) -> None:
    item_id = (callback.data or "")[6:]
    if item_id.startswith("del:"):
        return
    item = await catalog.get_delivery(item_id)
    if not item or not isinstance(callback.message, Message):
        await callback.answer("Не найдено", show_alert=True)
        return
    await callback.answer()
    await _edit_or_answer(
        callback.message,
        _format_delivery(item),
        markup=delivery_card_keyboard(item.id),
        edit=True,
    )


@router.callback_query(F.data.startswith("delivdel:"))
async def on_delivery_delete_ask(callback: CallbackQuery) -> None:
    item_id = (callback.data or "")[9:]
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    await callback.answer()
    await callback.message.edit_reply_markup(
        reply_markup=confirm_keyboard(f"delivok:{item_id}", f"deliv:{item_id}")
    )


@router.callback_query(F.data.startswith("delivok:"))
async def on_delivery_delete_ok(
    callback: CallbackQuery, catalog: CatalogService, state: FSMContext
) -> None:
    item_id = (callback.data or "")[8:]
    ok = await catalog.delete_delivery(item_id)
    await callback.answer("Удалено" if ok else "Уже нет")
    if isinstance(callback.message, Message):
        await _send_catalog_list(
            callback.message, catalog, "deliv", edit=True, state=state
        )


@router.callback_query(F.data.startswith("dishdel:"))
async def on_dish_delete_ask(callback: CallbackQuery) -> None:
    dish_id = (callback.data or "")[8:]
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    await callback.answer()
    await callback.message.edit_reply_markup(
        reply_markup=confirm_keyboard(f"dishok:{dish_id}", f"dish:{dish_id}")
    )


@router.callback_query(F.data.startswith("dishok:"))
async def on_dish_delete_ok(
    callback: CallbackQuery, catalog: CatalogService, state: FSMContext
) -> None:
    dish_id = (callback.data or "")[7:]
    dish = await catalog.get_dish(dish_id)
    kind = "prep" if dish and (dish.category or "") == PREP_CATEGORY else "dish"
    ok = await catalog.delete_dish(dish_id)
    await callback.answer("Удалено" if ok else "Уже нет")
    if isinstance(callback.message, Message):
        await _send_catalog_list(
            callback.message, catalog, kind, edit=True, state=state
        )


@router.callback_query(F.data.startswith("cook:"))
async def on_cook_deduct(
    callback: CallbackQuery, catalog: CatalogService
) -> None:
    dish_id = (callback.data or "")[5:]
    stats = await catalog.deduct_dish_from_stock(dish_id)
    await callback.answer(
        f"Списано: {stats.get('deducted', 0)}, кончилось: {stats.get('depleted', 0)}, "
        f"не было: {stats.get('missing', 0)}",
        show_alert=True,
    )


@router.callback_query(F.data.startswith("stock:"))
async def on_stock(callback: CallbackQuery, catalog: CatalogService) -> None:
    item_id = (callback.data or "")[6:]
    if item_id.startswith("rm:") or item_id.startswith("op:"):
        return
    item = await catalog.get_stock(item_id)
    if not item or not isinstance(callback.message, Message):
        await callback.answer("Не найдено", show_alert=True)
        return
    await callback.answer()
    await _edit_or_answer(
        callback.message,
        _format_stock(item),
        markup=stock_card_keyboard(item.id, is_opened=bool(item.is_opened)),
        edit=True,
    )


@router.callback_query(F.data.startswith("stockop:"))
async def on_stock_opened(
    callback: CallbackQuery, catalog: CatalogService
) -> None:
    item_id = (callback.data or "")[8:]
    item = await catalog.get_stock(item_id)
    if not item or not isinstance(callback.message, Message):
        await callback.answer("Не найдено", show_alert=True)
        return
    new_val = not bool(item.is_opened)
    await catalog.set_stock_opened(item_id, new_val)
    item = await catalog.get_stock(item_id)
    await callback.answer("Открыто" if new_val else "Закрыто")
    if item:
        await _edit_or_answer(
            callback.message,
            _format_stock(item),
            markup=stock_card_keyboard(item.id, is_opened=bool(item.is_opened)),
            edit=True,
        )


@router.callback_query(F.data.startswith("stockrm:"))
async def on_stock_rm(
    callback: CallbackQuery, catalog: CatalogService, state: FSMContext
) -> None:
    item_id = (callback.data or "")[8:]
    ok = await catalog.remove_stock(item_id)
    if not ok:
        await callback.answer("Уже нет", show_alert=True)
        return
    await callback.answer("Убрано")
    if isinstance(callback.message, Message):
        await _send_catalog_list(
            callback.message, catalog, "stock", edit=True, state=state
        )


def _format_import_error(exc: Exception) -> str:
    msg = str(exc).strip()
    if len(msg) > 350:
        msg = msg[:347] + "…"
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
    if report.status == "conflict":
        log.warning("import conflict key=%s", report.idempotency_key)
        await message.answer(report.summary_text())
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


@router.message(F.voice | F.audio)
async def on_voice(message: Message) -> None:
    """No built-in STT: point to agent → JSON loop (avoids fake transcription)."""
    await message.answer(
        "<b>Голос</b>\n\n"
        "Бот принимает готовый JSON, а не расшифровку речи.\n"
        "1. /rules — скачайте правила агента\n"
        "2. Надиктуйте агенту (DeepSeek и т.п.)\n"
        "3. Пришлите сюда JSON текстом или файлом\n\n"
        "Так надёжнее, чем тащить Whisper в бота без отдельного ключа.",
        reply_markup=main_keyboard(),
    )


@router.message(IsUnknownText())
async def on_unknown_text(message: Message) -> None:
    log.debug("unknown text user message ignored politely")
    await message.answer(
        "Не похоже на JSON.\n"
        "Выберите раздел кнопкой или пришлите JSON-пакет от ИИ-агента.",
        reply_markup=main_keyboard(),
    )
