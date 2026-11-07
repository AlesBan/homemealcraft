from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, User

log = logging.getLogger("homemealcraft")


def _user_label(user: User | None) -> str:
    if user is None:
        return "unknown"
    name = user.username or user.full_name or "-"
    return f"{user.id}(@{name})" if user.username else f"{user.id}({name})"


def _preview(text: str | None, limit: int = 80) -> str:
    if not text:
        return ""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1] + "…"


class AccessControlMiddleware(BaseMiddleware):
    """Allow only whitelisted users; drop the rest quietly (DEBUG log)."""

    def __init__(self, allowed_user_ids: list[int]) -> None:
        self.allowed = set(allowed_user_ids)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        if user is None and isinstance(event, (Message, CallbackQuery)):
            user = event.from_user

        if self.allowed and (user is None or user.id not in self.allowed):
            log.debug("drop unauthorized %s", _user_label(user))
            if isinstance(event, CallbackQuery):
                try:
                    await event.answer()
                except Exception:  # noqa: BLE001
                    pass
            return None

        data["actor"] = _user_label(user)
        return await handler(event, data)


class MessageFilterMiddleware(BaseMiddleware):
    """
    Keep only useful private traffic:
    - text / json documents / commands
    - ignore stickers, voice, photos, etc. without spamming replies
    """

    MENU_TEXTS = {
        "🏠 Главная",
        "🥕 Ингредиенты",
        "📦 Продукты",
        "🍽 Блюда",
        "🛒 Покупки",
        "❓ JSON",
    }

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message):
            return await handler(event, data)

        # Only private chats for this personal bot
        if event.chat and event.chat.type != "private":
            log.debug("drop non-private chat_id=%s", event.chat.id)
            return None

        if event.text or event.caption:
            return await handler(event, data)

        if event.document:
            name = (event.document.file_name or "").lower()
            mime = (event.document.mime_type or "").lower()
            if name.endswith(".json") or mime.endswith("json") or "json" in mime:
                return await handler(event, data)
            log.info("ignore document user=%s name=%s", data.get("actor"), name or mime)
            await event.answer("Нужен файл <code>.json</code> или текст JSON.")
            return None

        # Stickers, voice, photos, video, etc. — ignore quietly
        kind = "media"
        if event.sticker:
            kind = "sticker"
        elif event.voice or event.audio:
            kind = "audio"
        elif event.photo:
            kind = "photo"
        elif event.video:
            kind = "video"
        log.debug("ignore %s user=%s", kind, data.get("actor"))
        return None


class ErrorMiddleware(BaseMiddleware):
    """Catch handler errors: full traceback in logs, short reply to user."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        started = time.perf_counter()
        try:
            result = await handler(event, data)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            if elapsed_ms >= 800:
                log.info(
                    "slow handler %sms user=%s event=%s",
                    elapsed_ms,
                    data.get("actor"),
                    type(event).__name__,
                )
            return result
        except Exception:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            log.exception(
                "handler failed after %sms user=%s event=%s",
                elapsed_ms,
                data.get("actor"),
                type(event).__name__,
            )
            await self._notify_user(event)
            return None

    async def _notify_user(self, event: TelegramObject) -> None:
        text = "Что-то пошло не так. Попробуйте ещё раз или пришлите JSON заново."
        try:
            if isinstance(event, CallbackQuery):
                await event.answer("Ошибка", show_alert=True)
                if event.message:
                    await event.message.answer(text)
            elif isinstance(event, Message):
                await event.answer(text)
        except Exception:  # noqa: BLE001
            log.debug("could not notify user about error", exc_info=True)


class AccessLogMiddleware(BaseMiddleware):
    """Short access log for useful events only."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        actor = data.get("actor", "?")
        if isinstance(event, Message):
            if event.text:
                if event.text.startswith("/"):
                    log.info("cmd %s user=%s", event.text.split()[0], actor)
                elif event.text in MessageFilterMiddleware.MENU_TEXTS:
                    log.info("menu %s user=%s", event.text, actor)
                elif event.text.strip().startswith("{") or "schema_version" in event.text:
                    log.info("json_text len=%s user=%s", len(event.text), actor)
                else:
                    log.info("text %s user=%s", _preview(event.text), actor)
            elif event.document:
                log.info(
                    "json_file %s user=%s",
                    event.document.file_name or "document",
                    actor,
                )
        elif isinstance(event, CallbackQuery):
            log.info("tap %s user=%s", _preview(event.data, 40), actor)

        return await handler(event, data)
