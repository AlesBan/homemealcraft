from __future__ import annotations

from aiogram.types import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

BOT_COMMANDS = [
    BotCommand(command="start", description="Главное меню"),
    BotCommand(command="help", description="Как импортировать JSON"),
    BotCommand(command="rules", description="Правила для ИИ-агента"),
    BotCommand(command="ingredients", description="Ингредиенты"),
    BotCommand(command="products", description="Продукты"),
    BotCommand(command="dishes", description="Блюда"),
    BotCommand(command="preps", description="Заготовки"),
    BotCommand(command="deliveries", description="Доставки"),
    BotCommand(command="shopping", description="Покупки"),
]

BTN_HOME = "🏠 Главная"
BTN_INGREDIENTS = "🥕 Ингредиенты"
BTN_PRODUCTS = "📦 Продукты"
BTN_DISHES = "🍽 Блюда"
BTN_PREPS = "🫙 Заготовки"
BTN_DELIVERIES = "🛵 Доставки"
BTN_SHOPPING = "🛒 Покупки"
BTN_HELP = "❓ JSON"


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_HOME), KeyboardButton(text=BTN_HELP)],
            [
                KeyboardButton(text=BTN_INGREDIENTS),
                KeyboardButton(text=BTN_PRODUCTS),
            ],
            [
                KeyboardButton(text=BTN_DISHES),
                KeyboardButton(text=BTN_PREPS),
            ],
            [
                KeyboardButton(text=BTN_DELIVERIES),
                KeyboardButton(text=BTN_SHOPPING),
            ],
        ],
        resize_keyboard=True,
    )


def _clip(text: str, limit: int = 56) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def catalog_list_keyboard(
    kind: str,
    items: list[tuple[str, str]],
    *,
    page: int = 0,
    pages: int = 1,
    query: str | None = None,
) -> InlineKeyboardMarkup:
    """kind: ing | prod | dish | prep | deliv | shop; items: (id, label)."""
    rows: list[list[InlineKeyboardButton]] = []
    for item_id, label in items:
        rows.append(
            [
                InlineKeyboardButton(
                    text=_clip(label),
                    callback_data=f"{kind}:{item_id}",
                )
            ]
        )

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(text="←", callback_data=f"page:{kind}:{page - 1}")
        )
    if pages > 1:
        nav.append(
            InlineKeyboardButton(
                text=f"{page + 1}/{pages}",
                callback_data="noop",
            )
        )
    if page + 1 < pages:
        nav.append(
            InlineKeyboardButton(text="→", callback_data=f"page:{kind}:{page + 1}")
        )
    if nav:
        rows.append(nav)

    search_row = [
        InlineKeyboardButton(text="🔍 Поиск", callback_data=f"search:{kind}"),
    ]
    if query:
        search_row.append(
            InlineKeyboardButton(text="Сбросить", callback_data=f"clearq:{kind}")
        )
    rows.append(search_row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_to_list_keyboard(kind: str) -> InlineKeyboardMarkup:
    labels = {
        "ing": "← К ингредиентам",
        "prod": "← К продуктам",
        "dish": "← К блюдам",
        "prep": "← К заготовкам",
        "deliv": "← К доставкам",
        "shop": "← К покупкам",
    }
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=labels.get(kind, "← Назад"), callback_data=f"list:{kind}")]
        ]
    )


def dish_card_keyboard(
    dish_id: str,
    versions: list[tuple[str, str]],
    *,
    selected_version_id: str | None = None,
    list_kind: str = "dish",
) -> InlineKeyboardMarkup:
    back_labels = {
        "dish": "← К блюдам",
        "prep": "← К заготовкам",
    }
    rows: list[list[InlineKeyboardButton]] = []
    rows.append(
        [
            InlineKeyboardButton(
                text="Проверить состав",
                callback_data=f"need:{dish_id}",
            )
        ]
    )
    if versions:
        rows.append(
            [InlineKeyboardButton(text="— версии рецепта —", callback_data="noop")]
        )
    for version_id, label in versions:
        mark = "· " if version_id == selected_version_id else ""
        rows.append(
            [
                InlineKeyboardButton(
                    text=_clip(f"{mark}{label}" if mark else label),
                    callback_data=f"ver:{version_id}",
                )
            ]
        )
    if selected_version_id:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🗑 Удалить эту версию",
                    callback_data=f"verd:{selected_version_id}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text=back_labels.get(list_kind, "← Назад"),
                callback_data=f"list:{list_kind}",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_delete_version_keyboard(version_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, удалить",
                    callback_data=f"verdok:{version_id}",
                ),
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=f"ver:{version_id}",
                ),
            ]
        ]
    )


def need_checklist_keyboard(
    dish_id: str,
    items: list[tuple[str, str]],
) -> InlineKeyboardMarkup:
    """items: (ingredient_id, label). Indices in callbacks; ids live in FSM."""
    rows: list[list[InlineKeyboardButton]] = []
    for idx, (_ing_id, label) in enumerate(items):
        rows.append(
            [
                InlineKeyboardButton(
                    text=_clip(f"✓ Есть · {label}", 40),
                    callback_data=f"needh:{idx}",
                ),
                InlineKeyboardButton(
                    text="В покупки",
                    callback_data=f"needb:{idx}",
                ),
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="Готово · к блюду",
                callback_data=f"needdone:{dish_id}",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def shopping_card_keyboard(item_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✓ Куплено",
                    callback_data=f"shopok:{item_id}",
                ),
                InlineKeyboardButton(
                    text="🗑 Удалить",
                    callback_data=f"shoprm:{item_id}",
                ),
            ],
            [InlineKeyboardButton(text="← К покупкам", callback_data="list:shop")],
        ]
    )
