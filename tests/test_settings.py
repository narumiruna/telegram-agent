from __future__ import annotations

from pathlib import Path

from telegramagent.settings import Settings


def test_proactive_settings_parse_env(monkeypatch) -> None:
    monkeypatch.setenv("BOT_PROACTIVE_ENABLED", "false")
    monkeypatch.setenv("BOT_PROACTIVE_URL_TIMEOUT_SECONDS", "3.5")
    monkeypatch.setenv("BOT_PROACTIVE_MAX_EXTRACTED_CHARS", "500")
    monkeypatch.setenv("BOT_PROACTIVE_PENDING_TTL_SECONDS", "60")
    monkeypatch.setenv("BOT_PROACTIVE_ALLOWED_SCHEMES", "https")

    settings = Settings()

    assert settings.bot_proactive_enabled is False
    assert settings.bot_proactive_url_timeout_seconds == 3.5
    assert settings.bot_proactive_max_extracted_chars == 500
    assert settings.bot_proactive_pending_ttl_seconds == 60
    assert settings.bot_proactive_allowed_schemes == {"https"}


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
