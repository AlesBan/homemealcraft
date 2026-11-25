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
    BotCommand(command="stock", description="Кладовая"),
    BotCommand(command="shopping", description="Покупки"),
]

BTN_HOME = "🏠 Главная"
BTN_INGREDIENTS = "🥕 Ингредиенты"
BTN_PRODUCTS = "📦 Продукты"
BTN_DISHES = "🍽 Блюда"
BTN_PREPS = "🫙 Заготовки"
BTN_DELIVERIES = "🛵 Доставки"
BTN_STOCK = "🧺 Кладовая"
BTN_SHOPPING = "🛒 Покупки"
BTN_HELP = "❓ JSON"


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_SHOPPING)],
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
                KeyboardButton(text=BTN_STOCK),
            ],
        ],
        resize_keyboard=True,
        is_persistent=True,
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
        nav.append(InlineKeyboardButton(text="←", callback_data=f"page:{kind}:{page - 1}"))
    if pages > 1:
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="noop"))
    if page + 1 < pages:
        nav.append(InlineKeyboardButton(text="→", callback_data=f"page:{kind}:{page + 1}"))
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
        "stock": "← К кладовой",
        "shop": "← К покупкам",
    }
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=labels.get(kind, "← Назад"), callback_data=f"list:{kind}")]
        ]
    )


def confirm_keyboard(yes_data: str, no_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да", callback_data=yes_data),
                InlineKeyboardButton(text="Отмена", callback_data=no_data),
            ]
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
    rows.append(
        [
            InlineKeyboardButton(
                text="Списать состав из кладовой",
                callback_data=f"cook:{dish_id}",
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
                text="🗑 Удалить блюдо",
                callback_data=f"dishdel:{dish_id}",
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
    return confirm_keyboard(f"verdok:{version_id}", f"ver:{version_id}")


def need_checklist_keyboard(
    dish_id: str,
    items: list[tuple[str, str, bool]],
) -> InlineKeyboardMarkup:
    """items: (ingredient_id, label, in_stock)."""
    rows: list[list[InlineKeyboardButton]] = []
    for idx, (_ing_id, label, in_stock) in enumerate(items):
        if in_stock:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=_clip(f"Ещё есть · {label}", 40),
                        callback_data=f"needk:{idx}",
                    ),
                    InlineKeyboardButton(
                        text="Кончилось",
                        callback_data=f"needo:{idx}",
                    ),
                ]
            )
        else:
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
                    text="✓ Куплено → кладовая",
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


def stock_card_keyboard(item_id: str, *, is_opened: bool = False) -> InlineKeyboardMarkup:
    opened_label = "Открыто: да → нет" if is_opened else "Открыто: нет → да"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=opened_label,
                    callback_data=f"stockop:{item_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗑 Убрать из кладовой",
                    callback_data=f"stockrm:{item_id}",
                )
            ],
            [InlineKeyboardButton(text="← К кладовой", callback_data="list:stock")],
        ]
    )


def ingredient_card_keyboard(item_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑 Удалить ингредиент",
                    callback_data=f"ingdel:{item_id}",
                )
            ],
            [InlineKeyboardButton(text="← К ингредиентам", callback_data="list:ing")],
        ]
    )


def product_card_keyboard(item_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑 Удалить продукт",
                    callback_data=f"proddel:{item_id}",
                )
            ],
            [InlineKeyboardButton(text="← К продуктам", callback_data="list:prod")],
        ]
    )


def delivery_card_keyboard(item_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑 Удалить доставку",
                    callback_data=f"delivdel:{item_id}",
                )
            ],
            [InlineKeyboardButton(text="← К доставкам", callback_data="list:deliv")],
        ]
    )
