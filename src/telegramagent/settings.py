from __future__ import annotations

from typing import Annotated

from pydantic import Field
from pydantic import field_validator
from pydantic_settings import BaseSettings
from pydantic_settings import NoDecode
from pydantic_settings import SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables and .env."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str = Field(default="", alias="BOT_TOKEN")
    bot_whitelist: Annotated[set[int], NoDecode] = Field(default_factory=set, alias="BOT_WHITELIST")
    bot_max_consecutive_replies_to_bots: int = Field(default=1, ge=0, alias="BOT_MAX_CONSECUTIVE_REPLIES_TO_BOTS")

    openai_base_url: str = Field(default="https://api.openai.com/v1", alias="OPENAI_BASE_URL")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")

    logfire_token: str | None = Field(default=None, alias="LOGFIRE_TOKEN")
    logfire_environment: str | None = Field(default=None, alias="LOGFIRE_ENVIRONMENT")

    @field_validator("bot_whitelist", mode="before")
    @classmethod
    def parse_whitelist(cls, value: object) -> set[int] | object:
        if value is None or value == "":
            return set()
        if isinstance(value, str):
            return {int(item.strip()) for item in value.split(",") if item.strip()}
        return value
