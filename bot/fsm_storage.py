"""JSON-file FSM storage so checklist state survives bot restarts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey


class JsonFileStorage(BaseStorage):
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, dict[str, Any]] = {}
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def _key(self, key: StorageKey) -> str:
        return f"{key.bot_id}:{key.chat_id}:{key.user_id}:{key.destiny}"

    def _save(self) -> None:
        self._path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=0),
            encoding="utf-8",
        )

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        bucket = self._data.setdefault(self._key(key), {"state": None, "data": {}})
        if isinstance(state, State):
            bucket["state"] = state.state
        else:
            bucket["state"] = state
        self._save()

    async def get_state(self, key: StorageKey) -> str | None:
        bucket = self._data.get(self._key(key))
        if not bucket:
            return None
        return bucket.get("state")

    async def set_data(self, key: StorageKey, data: dict[str, Any]) -> None:
        bucket = self._data.setdefault(self._key(key), {"state": None, "data": {}})
        bucket["data"] = data
        self._save()

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        bucket = self._data.get(self._key(key))
        if not bucket:
            return {}
        return dict(bucket.get("data") or {})

    async def close(self) -> None:
        self._save()
