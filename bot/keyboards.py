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
    BotCommand(command="shopping", description="Покупки"),
]

BTN_HOME = "🏠 Главная"
BTN_INGREDIENTS = "🥕 Ингредиенты"
BTN_PRODUCTS = "📦 Продукты"
BTN_DISHES = "🍽 Блюда"
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
    kind: str, items: list[tuple[str, str]]
) -> InlineKeyboardMarkup:
    """kind: ing | prod | dish | shop; items: (id, label)."""
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
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_to_list_keyboard(kind: str) -> InlineKeyboardMarkup:
    labels = {
        "ing": "← К ингредиентам",
        "prod": "← К продуктам",
        "dish": "← К блюдам",
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
) -> InlineKeyboardMarkup:
    """
    One card for one dish. versions: (version_id, label).
    Selected version is marked; all switches stay on the same message.
    """
    rows: list[list[InlineKeyboardButton]] = []
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
        [InlineKeyboardButton(text="← К блюдам", callback_data="list:dish")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
