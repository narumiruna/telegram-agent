from __future__ import annotations

from pathlib import Path

from telegramagent.settings import Settings


def test_proactive_settings_parse_env(monkeypatch) -> None:
    monkeypatch.setenv("BOT_PROACTIVE_ENABLED", "false")
    monkeypatch.setenv("BOT_PROACTIVE_URL_TIMEOUT_SECONDS", "3.5")
    monkeypatch.setenv("BOT_KABIGON_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("BOT_PROACTIVE_MAX_EXTRACTED_CHARS", "500")
    monkeypatch.setenv("BOT_PROACTIVE_PENDING_TTL_SECONDS", "60")
    monkeypatch.setenv("BOT_PROACTIVE_ALLOWED_SCHEMES", "https")

    settings = Settings()

    assert settings.bot_proactive_enabled is False
    assert settings.bot_proactive_url_timeout_seconds == 3.5
    assert settings.bot_kabigon_timeout_seconds == 12
    assert settings.bot_proactive_max_extracted_chars == 500
    assert settings.bot_proactive_pending_ttl_seconds == 60
    assert settings.bot_proactive_allowed_schemes == {"https"}


def test_group_passive_context_setting_parse_env(monkeypatch) -> None:
    monkeypatch.setenv("BOT_GROUP_PASSIVE_CONTEXT_ENABLED", "false")

    settings = Settings()

    assert settings.bot_group_passive_context_enabled is False


def test_event_settings_parse_env(monkeypatch) -> None:
    monkeypatch.setenv("BOT_EVENTS_ENABLED", "true")
    monkeypatch.setenv("BOT_EVENTS_DIR", ".custom-events")
    monkeypatch.setenv("BOT_EVENTS_SCAN_SECONDS", "1.5")
    monkeypatch.setenv("BOT_EVENTS_MAX_QUEUED_PER_CHAT", "2")
    monkeypatch.setenv("BOT_EVENTS_MAX_TEXT_CHARS", "123")
    monkeypatch.setenv("BOT_EVENTS_ARCHIVE_PROCESSED", "false")

    settings = Settings()

    assert settings.bot_events_enabled is True
    assert settings.bot_events_dir == Path(".custom-events")
    assert settings.bot_events_scan_seconds == 1.5
    assert settings.bot_events_max_queued_per_chat == 2
    assert settings.bot_events_max_text_chars == 123
    assert settings.bot_events_archive_processed is False


def test_proactive_runtime_settings_parse_env(monkeypatch) -> None:
    monkeypatch.setenv("BOT_SESSION_LOG_DIR", ".state/sessions")
    monkeypatch.setenv("BOT_TASKS_MAX_CONCURRENT_PER_CHAT", "3")

    settings = Settings()

    assert settings.bot_session_log_dir == Path(".state/sessions")
    assert settings.bot_tasks_max_concurrent_per_chat == 3


def test_image_settings_parse_env(monkeypatch) -> None:
    monkeypatch.setenv("BOT_IMAGE_INPUT_ENABLED", "false")
    monkeypatch.setenv("BOT_IMAGE_MAX_BYTES", "12345")
    monkeypatch.setenv("BOT_IMAGE_GENERATION_ENABLED", "true")
    monkeypatch.setenv("BOT_IMAGE_GENERATION_MODEL", "image-model")
    monkeypatch.setenv("BOT_IMAGE_GENERATION_SIZE", "512x512")
    monkeypatch.setenv("BOT_IMAGE_GENERATION_TIMEOUT_SECONDS", "30")

    settings = Settings()

    assert settings.bot_image_input_enabled is False
    assert settings.bot_image_max_bytes == 12345
    assert settings.bot_image_generation_enabled is True
    assert settings.bot_image_generation_model == "image-model"
    assert settings.bot_image_generation_size == "512x512"
    assert settings.bot_image_generation_timeout_seconds == 30


def test_yfinance_mcp_settings_parse_env(monkeypatch) -> None:
    monkeypatch.setenv("BOT_YFINANCE_MCP_ENABLED", "false")
    monkeypatch.setenv("BOT_YFINANCE_MCP_COMMAND", "uvx")
    monkeypatch.setenv("BOT_YFINANCE_MCP_ARGS", "--from yfmcp yfmcp")
    monkeypatch.setenv("BOT_YFINANCE_MCP_INIT_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("BOT_YFINANCE_MCP_READ_TIMEOUT_SECONDS", "30")

    settings = Settings()

    assert settings.bot_yfinance_mcp_enabled is False
    assert settings.bot_yfinance_mcp_command == "uvx"
    assert settings.bot_yfinance_mcp_args == ("--from", "yfmcp", "yfmcp")
    assert settings.bot_yfinance_mcp_init_timeout_seconds == 5
    assert settings.bot_yfinance_mcp_read_timeout_seconds == 30


def test_gurume_tool_settings_parse_env(monkeypatch) -> None:
    monkeypatch.setenv("BOT_GURUME_TOOLS_ENABLED", "false")
    monkeypatch.setenv("BOT_GURUME_TOOLS_TIMEOUT_SECONDS", "3.5")
    monkeypatch.setenv("BOT_GURUME_TOOLS_MAX_RESULTS", "7")

    settings = Settings()

    assert settings.bot_gurume_tools_enabled is False
    assert settings.bot_gurume_tools_timeout_seconds == 3.5
    assert settings.bot_gurume_tools_max_results == 7


def test_gurume_tools_are_enabled_by_default() -> None:
    settings = Settings.model_validate({})

    assert settings.bot_gurume_tools_enabled is True


def test_container_tool_settings_parse_env(monkeypatch) -> None:
    monkeypatch.setenv("BOT_CONTAINER_TOOLS_ENABLED", "true")
    monkeypatch.setenv("BOT_CONTAINER_TOOLS_ROOT", "/app")
    monkeypatch.setenv("BOT_CONTAINER_TOOLS_TIMEOUT_SECONDS", "3.5")
    monkeypatch.setenv("BOT_CONTAINER_TOOLS_MAX_OUTPUT_CHARS", "500")
    monkeypatch.setenv("BOT_CONTAINER_TOOLS_MAX_READ_CHARS", "600")
    monkeypatch.setenv("BOT_CONTAINER_TOOLS_MAX_RESULTS", "7")

    settings = Settings()

    assert settings.bot_container_tools_enabled is True
    assert settings.bot_container_tools_root == Path("/app")
    assert settings.bot_container_tools_timeout_seconds == 3.5
    assert settings.bot_container_tools_max_output_chars == 500
    assert settings.bot_container_tools_max_read_chars == 600
    assert settings.bot_container_tools_max_results == 7


def test_logfire_settings_parse_env(monkeypatch) -> None:
    monkeypatch.setenv("LOGFIRE_ENABLED", "false")
    monkeypatch.setenv("LOGFIRE_TOKEN", "token")
    monkeypatch.setenv("LOGFIRE_ENVIRONMENT", "prod")
    monkeypatch.setenv("LOGFIRE_SERVICE_NAME", "custom-bot")
    monkeypatch.setenv("LOGFIRE_INCLUDE_CONTENT", "true")

    settings = Settings()

    assert settings.logfire_enabled is False
    assert settings.logfire_token == "token"
    assert settings.logfire_environment == "prod"
    assert settings.logfire_service_name == "custom-bot"
    assert settings.logfire_include_content is True
