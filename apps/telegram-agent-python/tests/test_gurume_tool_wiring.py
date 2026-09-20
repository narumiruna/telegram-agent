from __future__ import annotations

from telegramagent.cli import _gurume_tools_from_settings
from telegramagent.settings import Settings


def test_gurume_tools_are_enabled_by_default() -> None:
    tools, capability = _gurume_tools_from_settings(Settings.model_validate({}))

    assert capability.available is True
    assert capability.reason == ""
    assert [tool.name for tool in tools] == [
        "recommend_japanese_restaurants",
        "search_japanese_restaurants",
        "get_japanese_restaurant_details",
        "get_tabelog_area_suggestions",
        "get_tabelog_keyword_suggestions",
        "list_tabelog_cuisines",
    ]


def test_gurume_tools_can_be_disabled() -> None:
    tools, capability = _gurume_tools_from_settings(Settings.model_validate({"BOT_GURUME_TOOLS_ENABLED": False}))

    assert tools == ()
    assert capability.available is False
    assert capability.reason == "disabled"
