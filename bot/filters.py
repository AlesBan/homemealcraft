from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import Message

from bot.keyboards import (
    BTN_DISHES,
    BTN_HELP,
    BTN_HOME,
    BTN_INGREDIENTS,
    BTN_PRODUCTS,
    BTN_PROMPT,
    BTN_SHOPPING,
)

MENU_BUTTONS = {
    BTN_HOME,
    BTN_HELP,
    BTN_PROMPT,
    BTN_INGREDIENTS,
    BTN_PRODUCTS,
    BTN_DISHES,
    BTN_SHOPPING,
}


class IsJsonPayload(BaseFilter):
    """True when message looks like an import JSON packet."""

    async def __call__(self, message: Message) -> bool:
        text = (message.text or message.caption or "").strip()
        if not text:
            return False
        if text in MENU_BUTTONS or text.startswith("/"):
            return False
        if text.startswith("{") or text.startswith("```"):
            return True
        return '"schema_version"' in text or "'schema_version'" in text


class IsUnknownText(BaseFilter):
    """Non-menu, non-command, non-JSON text — short hint, not import attempt."""

    async def __call__(self, message: Message) -> bool:
        text = (message.text or "").strip()
        if not text or text.startswith("/"):
            return False
        if text in MENU_BUTTONS:
            return False
        if text.startswith("{") or text.startswith("```"):
            return False
        if '"schema_version"' in text or "'schema_version'" in text:
            return False
        return True
