from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from app.db.session import init_db
from app.domain.catalog import CatalogService
from app.domain.import_engine import ImportEngine
from bot.config import get_settings
from bot.handlers import router
from bot.keyboards import BOT_COMMANDS
from bot.logging_setup import setup_logging
from bot.middlewares import (
    AccessControlMiddleware,
    AccessLogMiddleware,
    ErrorMiddleware,
    MessageFilterMiddleware,
)

log = logging.getLogger("homemealcraft")


async def main() -> None:
    root = Path(__file__).resolve().parents[1]
    env_path = root / ".env"
    if env_path.exists():
        from dotenv import load_dotenv

        load_dotenv(env_path)

    get_settings.cache_clear()
    settings = get_settings()
    setup_logging(settings.log_level)

    session_factory = await init_db(settings.database_path)
    import_engine = ImportEngine(session_factory)
    catalog = CatalogService(session_factory)

    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    # Order: errors outer → access → filter → access log → handler
    dp.message.middleware(ErrorMiddleware())
    dp.callback_query.middleware(ErrorMiddleware())

    access = AccessControlMiddleware(settings.telegram_user_ids)
    dp.message.middleware(access)
    dp.callback_query.middleware(access)

    dp.message.middleware(MessageFilterMiddleware())

    dp.message.middleware(AccessLogMiddleware())
    dp.callback_query.middleware(AccessLogMiddleware())

    @dp.message.middleware()
    async def inject_deps(handler, event, data):
        data["settings"] = settings
        data["import_engine"] = import_engine
        data["catalog"] = catalog
        data["bot"] = bot
        return await handler(event, data)

    @dp.callback_query.middleware()
    async def inject_deps_cb(handler, event, data):
        data["settings"] = settings
        data["import_engine"] = import_engine
        data["catalog"] = catalog
        data["bot"] = bot
        return await handler(event, data)

    dp.include_router(router)
    await bot.set_my_commands(BOT_COMMANDS)

    me = await bot.get_me()
    log.info(
        "started @%s tz=%s db=%s allow=%s",
        me.username,
        settings.timezone,
        settings.database_path,
        settings.telegram_user_ids or "any",
    )

    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        await bot.session.close()
        log.info("stopped")


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
