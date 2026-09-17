from __future__ import annotations

from telegramagent.cli import _morsel_tools_from_settings
from telegramagent.settings import Settings


def test_morsel_tool_is_registered_when_api_key_is_configured() -> None:
    settings = Settings.model_validate(
        {
            "MORSEL_URL": "https://morsel.example.com/",
            "MORSEL_API_KEY": " test-key, fallback-key ",
        }
    )

    publisher, tools, capability = _morsel_tools_from_settings(settings)

    assert publisher.base_url == "https://morsel.example.com"
    assert publisher.api_key == "test-key"
    assert publisher.is_configured is True
    assert [tool.name for tool in tools] == ["publish_markdown_to_morsel"]
    assert capability.name == "tool.morsel"
    assert capability.available is True
    assert capability.reason == ""


def test_morsel_tool_is_not_registered_without_usable_api_key() -> None:
    settings = Settings.model_validate({"MORSEL_API_KEY": " , "})

    publisher, tools, capability = _morsel_tools_from_settings(settings)

    assert publisher.is_configured is False
    assert tools == ()
    assert capability.name == "tool.morsel"
    assert capability.available is False
    assert capability.reason == "MORSEL_API_KEY not configured"
