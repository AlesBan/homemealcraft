"""Catalog section buttons must send the inline list on the same message.

Telegram rejects swapping a reply keyboard for an inline keyboard via
editReplyMarkup, which used to leave section screens without a list.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aiogram.types import InlineKeyboardMarkup

from app.db.session import init_db
from app.domain.catalog import CatalogService
from app.domain.import_engine import ImportEngine
from app.domain.schemas import ImportPacket
from bot.handlers import _send_catalog_list


class _State:
    def __init__(self) -> None:
        self.data: dict = {}

    async def update_data(self, **kwargs) -> None:
        self.data.update(kwargs)


class _Message:
    def __init__(self) -> None:
        self.sent: list[tuple[str, object]] = []
        self.reply_markup_edits: list[object] = []

    async def answer(self, text: str, reply_markup=None):
        self.sent.append((text, reply_markup))
        return self

    async def edit_text(self, text: str, reply_markup=None):
        raise AssertionError("new list screens must not edit the menu message")

    async def edit_reply_markup(self, reply_markup=None):
        self.reply_markup_edits.append(reply_markup)
        raise AssertionError("inline list must be sent with the message")


async def run() -> None:
    db = ROOT / "data" / "test_list_screen.sqlite"
    if db.exists():
        db.unlink()
    factory = await init_db(str(db))
    engine = ImportEngine(factory)
    catalog = CatalogService(factory)
    packet = ImportPacket.model_validate(
        {
            "schema_version": "1.0",
            "idempotency_key": "test-list-screen",
            "source": "test",
            "actions": [
                {"op": "ingredient.upsert", "name": "Творог", "category": "dairy"},
            ],
        }
    )
    assert (await engine.apply_packet(packet)).status == "applied"

    message = _Message()
    await _send_catalog_list(message, catalog, "ing", state=_State())  # type: ignore[arg-type]

    assert len(message.sent) == 1
    assert not message.reply_markup_edits
    text, markup = message.sent[0]
    assert "Ингредиенты" in text
    assert isinstance(markup, InlineKeyboardMarkup)
    labels = [btn.text for row in markup.inline_keyboard for btn in row]
    assert "Творог" in labels
    assert any(btn.callback_data and btn.callback_data.startswith("ing:") for row in markup.inline_keyboard for btn in row)
    print("test_list_screen OK")


if __name__ == "__main__":
    asyncio.run(run())
