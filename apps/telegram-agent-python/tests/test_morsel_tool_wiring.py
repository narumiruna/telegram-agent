from __future__ import annotations

from telegramagent.cli import _morsel_tools_from_settings
from telegramagent.settings import Settings


def test_morsel_tool_is_registered_when_smart_mode_is_configured() -> None:
    settings = Settings.model_validate(
        {
            "MORSEL_URL": "https://morsel.example.com/",
            "MORSEL_API_KEY": " test-key, fallback-key ",
            "MORSEL_LONG_REPLY_THRESHOLD": 3200,
            "MORSEL_SHARE_EXPIRES_IN_SECONDS": 3600,
            "MORSEL_TELEGRAM_INSTANT_VIEW": True,
            "TELEGRAM_INSTANT_VIEW_RHASH": "abc123def45678",
            "MORSEL_TIMEOUT_SECONDS": 8,
        }
    )

    publisher, tools, capability, long_reply_threshold = _morsel_tools_from_settings(settings)

    assert publisher.base_url == "https://morsel.example.com"
    assert publisher.api_key == "test-key"
    assert publisher.is_configured is True
    assert publisher.expires_in_seconds == 3600
    assert publisher.telegram_instant_view is True
    assert publisher.telegram_instant_view_rhash == "abc123def45678"
    assert publisher.timeout.read == 8
    assert [tool.name for tool in tools] == ["publish_markdown_to_morsel"]
    assert capability.name == "tool.morsel"
    assert "Vega-Lite" in capability.description
    assert capability.available is True
    assert capability.reason == ""
    assert long_reply_threshold == 3200


def test_morsel_tool_is_not_registered_without_usable_api_key() -> None:
    settings = Settings.model_validate({"MORSEL_API_KEY": " , "})

    publisher, tools, capability, long_reply_threshold = _morsel_tools_from_settings(settings)

    assert publisher.is_configured is False
    assert tools == ()
    assert capability.name == "tool.morsel"
    assert capability.available is False
    assert capability.reason == "MORSEL_API_KEY not configured"
    assert long_reply_threshold is None


def test_morsel_rich_only_mode_registers_tool_without_long_reply_routing() -> None:
    settings = Settings.model_validate({"MORSEL_API_KEY": "test-key", "MORSEL_MODE": "rich_only"})

    publisher, tools, capability, long_reply_threshold = _morsel_tools_from_settings(settings)

    assert publisher.is_configured is True
    assert [tool.name for tool in tools] == ["publish_markdown_to_morsel"]
    assert capability.available is True
    assert long_reply_threshold is None


def test_morsel_disabled_mode_registers_nothing_even_with_api_key() -> None:
    settings = Settings.model_validate({"MORSEL_API_KEY": "test-key", "MORSEL_MODE": "disabled"})

    publisher, tools, capability, long_reply_threshold = _morsel_tools_from_settings(settings)

    assert publisher.is_configured is True
    assert tools == ()
    assert capability.available is False
    assert capability.reason == "disabled"
    assert long_reply_threshold is None
