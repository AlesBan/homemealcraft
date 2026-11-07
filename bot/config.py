from __future__ import annotations

from functools import lru_cache
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    telegram_bot_token: str
    telegram_user_ids: list[int] = []
    timezone: str = "Europe/Minsk"
    database_path: str = "data/homemealcraft.sqlite"
    api_token: str = ""
    log_level: str = "INFO"

    @field_validator("telegram_user_ids", mode="before")
    @classmethod
    def parse_user_ids(cls, value: Any) -> list[int]:
        if value is None or value == "":
            return []
        if isinstance(value, list):
            return [int(v) for v in value]
        return [int(part.strip()) for part in str(value).split(",") if part.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
