from __future__ import annotations

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
