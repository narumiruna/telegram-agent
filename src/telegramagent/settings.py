from __future__ import annotations

import shlex
from pathlib import Path
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
    bot_group_passive_context_enabled: bool = Field(default=True, alias="BOT_GROUP_PASSIVE_CONTEXT_ENABLED")
    bot_skills_dir: Path = Field(default=Path(".agents/skills"), alias="BOT_SKILLS_DIR")
    bot_enabled_skills: Annotated[set[str], NoDecode] = Field(default_factory=set, alias="BOT_ENABLED_SKILLS")
    bot_skill_admins: Annotated[set[int], NoDecode] = Field(default_factory=set, alias="BOT_SKILL_ADMINS")
    bot_soul_path: Path = Field(default=Path("SOUL.md"), alias="BOT_SOUL_PATH")
    bot_soul_required: bool = Field(default=False, alias="BOT_SOUL_REQUIRED")
    bot_soul_max_chars: int = Field(default=8000, ge=1, alias="BOT_SOUL_MAX_CHARS")
    bot_proactive_enabled: bool = Field(default=True, alias="BOT_PROACTIVE_ENABLED")
    bot_proactive_url_timeout_seconds: float = Field(default=15.0, gt=0, alias="BOT_PROACTIVE_URL_TIMEOUT_SECONDS")
    bot_kabigon_timeout_seconds: float = Field(default=180.0, gt=0, alias="BOT_KABIGON_TIMEOUT_SECONDS")
    bot_proactive_max_extracted_chars: int = Field(default=12000, ge=100, alias="BOT_PROACTIVE_MAX_EXTRACTED_CHARS")
    bot_proactive_pending_ttl_seconds: int = Field(default=900, ge=1, alias="BOT_PROACTIVE_PENDING_TTL_SECONDS")
    bot_proactive_allowed_schemes: Annotated[set[str], NoDecode] = Field(
        default_factory=lambda: {"http", "https"}, alias="BOT_PROACTIVE_ALLOWED_SCHEMES"
    )
    bot_events_enabled: bool = Field(default=False, alias="BOT_EVENTS_ENABLED")
    bot_events_dir: Path = Field(default=Path(".events"), alias="BOT_EVENTS_DIR")
    bot_events_scan_seconds: float = Field(default=2.0, gt=0, alias="BOT_EVENTS_SCAN_SECONDS")
    bot_events_max_queued_per_chat: int = Field(default=5, ge=1, alias="BOT_EVENTS_MAX_QUEUED_PER_CHAT")
    bot_events_max_text_chars: int = Field(default=4000, ge=1, alias="BOT_EVENTS_MAX_TEXT_CHARS")
    bot_events_archive_processed: bool = Field(default=True, alias="BOT_EVENTS_ARCHIVE_PROCESSED")
    bot_session_log_dir: Path = Field(default=Path(".telegramagent/sessions"), alias="BOT_SESSION_LOG_DIR")
    bot_tasks_max_concurrent_per_chat: int = Field(default=1, ge=1, alias="BOT_TASKS_MAX_CONCURRENT_PER_CHAT")
    bot_image_input_enabled: bool = Field(default=True, alias="BOT_IMAGE_INPUT_ENABLED")
    bot_image_max_bytes: int = Field(default=8_000_000, ge=1, alias="BOT_IMAGE_MAX_BYTES")
    bot_image_generation_enabled: bool = Field(default=False, alias="BOT_IMAGE_GENERATION_ENABLED")
    bot_image_generation_model: str = Field(default="gpt-image-1", alias="BOT_IMAGE_GENERATION_MODEL")
    bot_image_generation_size: str = Field(default="1024x1024", alias="BOT_IMAGE_GENERATION_SIZE")
    bot_image_generation_timeout_seconds: float = Field(
        default=120.0, gt=0, alias="BOT_IMAGE_GENERATION_TIMEOUT_SECONDS"
    )
    bot_yfinance_mcp_enabled: bool = Field(default=True, alias="BOT_YFINANCE_MCP_ENABLED")
    bot_yfinance_mcp_command: str = Field(default="yfmcp", alias="BOT_YFINANCE_MCP_COMMAND")
    bot_yfinance_mcp_args: Annotated[tuple[str, ...], NoDecode] = Field(
        default_factory=tuple, alias="BOT_YFINANCE_MCP_ARGS"
    )
    bot_yfinance_mcp_init_timeout_seconds: float = Field(
        default=10.0, gt=0, alias="BOT_YFINANCE_MCP_INIT_TIMEOUT_SECONDS"
    )
    bot_yfinance_mcp_read_timeout_seconds: float = Field(
        default=120.0, gt=0, alias="BOT_YFINANCE_MCP_READ_TIMEOUT_SECONDS"
    )
    bot_gurume_tools_enabled: bool = Field(default=True, alias="BOT_GURUME_TOOLS_ENABLED")
    bot_container_tools_enabled: bool = Field(default=False, alias="BOT_CONTAINER_TOOLS_ENABLED")
    bot_container_tools_root: Path = Field(default=Path(), alias="BOT_CONTAINER_TOOLS_ROOT")
    bot_container_tools_timeout_seconds: float = Field(default=10.0, gt=0, alias="BOT_CONTAINER_TOOLS_TIMEOUT_SECONDS")
    bot_container_tools_max_output_chars: int = Field(
        default=12000, ge=100, alias="BOT_CONTAINER_TOOLS_MAX_OUTPUT_CHARS"
    )
    bot_container_tools_max_read_chars: int = Field(default=20000, ge=100, alias="BOT_CONTAINER_TOOLS_MAX_READ_CHARS")
    bot_container_tools_max_results: int = Field(default=200, ge=1, alias="BOT_CONTAINER_TOOLS_MAX_RESULTS")

    openai_base_url: str = Field(default="https://api.openai.com/v1", alias="OPENAI_BASE_URL")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")

    logfire_enabled: bool = Field(default=True, alias="LOGFIRE_ENABLED")
    logfire_token: str | None = Field(default=None, alias="LOGFIRE_TOKEN")
    logfire_environment: str | None = Field(default=None, alias="LOGFIRE_ENVIRONMENT")
    logfire_service_name: str = Field(default="telegramagent", alias="LOGFIRE_SERVICE_NAME")
    logfire_include_content: bool = Field(default=False, alias="LOGFIRE_INCLUDE_CONTENT")

    @field_validator("bot_whitelist", "bot_skill_admins", mode="before")
    @classmethod
    def parse_whitelist(cls, value: object) -> set[int] | object:
        if value is None or value == "":
            return set()
        if isinstance(value, str):
            return {int(item.strip()) for item in value.split(",") if item.strip()}
        return value

    @field_validator("bot_proactive_allowed_schemes", mode="before")
    @classmethod
    def parse_allowed_schemes(cls, value: object) -> set[str] | object:
        if value is None or value == "":
            return {"http", "https"}
        if isinstance(value, str):
            return {item.strip().casefold() for item in value.split(",") if item.strip()}
        return value

    @field_validator("bot_enabled_skills", mode="before")
    @classmethod
    def parse_enabled_skills(cls, value: object) -> set[str] | object:
        if value is None or value == "":
            return set()
        if isinstance(value, str):
            return {item.strip() for item in value.split(",") if item.strip()}
        return value

    @field_validator("bot_yfinance_mcp_args", mode="before")
    @classmethod
    def parse_mcp_args(cls, value: object) -> tuple[str, ...] | object:
        if value is None or value == "":
            return ()
        if isinstance(value, str):
            return tuple(shlex.split(value))
        return value
