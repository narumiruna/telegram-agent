from __future__ import annotations

import pytest
from gurume.server_models import RestaurantOutput
from gurume.server_models import RestaurantSearchOutput
from gurume.server_models import SearchFiltersOutput
from gurume.server_models import SuggestionListOutput
from gurume.server_models import SuggestionOutput
from pydantic import HttpUrl
from pydantic import TypeAdapter

from telegramagent import gurume_tools
from telegramagent.gurume_tools import build_gurume_tools
from telegramagent.gurume_tools import recommend_japanese_restaurants

HTTP_URL_ADAPTER = TypeAdapter(HttpUrl)


def test_build_gurume_tools_exposes_domain_tool_names() -> None:
    tools = build_gurume_tools()

    assert [tool.name for tool in tools] == [
        "recommend_japanese_restaurants",
        "search_japanese_restaurants",
        "get_japanese_restaurant_details",
        "get_tabelog_area_suggestions",
        "get_tabelog_keyword_suggestions",
        "list_tabelog_cuisines",
    ]


@pytest.mark.asyncio
async def test_recommend_japanese_restaurants_resolves_area_and_cuisine(monkeypatch) -> None:
    captured_search: dict[str, object] = {}

    async def fake_area_suggestions(query: str) -> SuggestionListOutput:
        return SuggestionListOutput(
            status="success",
            query=query,
            items=[
                SuggestionOutput(
                    name="東京都",
                    datatype="AddressMaster",
                    id_in_datatype=13,
                    lat=35.6895,
                    lng=139.6917,
                )
            ],
            returned_count=1,
            error=None,
        )

    async def fake_keyword_suggestions(query: str) -> SuggestionListOutput:
        return SuggestionListOutput(
            status="success",
            query=query,
            items=[
                SuggestionOutput(
                    name="ラーメン",
                    datatype="Genre2",
                    id_in_datatype=501,
                    lat=None,
                    lng=None,
                )
            ],
            returned_count=1,
            error=None,
        )

    async def fake_search_restaurants(**kwargs) -> RestaurantSearchOutput:
        captured_search.update(kwargs)
        return RestaurantSearchOutput(
            status="success",
            items=[
                RestaurantOutput(
                    name="テストラーメン",
                    rating=3.8,
                    review_count=100,
                    area="新宿",
                    genres=["ラーメン"],
                    url=HTTP_URL_ADAPTER.validate_python("https://tabelog.com/tokyo/A1304/A130401/13000001/"),
                    lunch_price="¥1,000～¥1,999",
                    dinner_price=None,
                )
            ],
            returned_count=1,
            limit=5,
            has_more=False,
            meta=None,
            applied_filters=SearchFiltersOutput(
                area="東京都",
                keyword=None,
                cuisine="ラーメン",
                genre_code="RC0501",
                sort="ranking",
                page=1,
                reservation_date=None,
                reservation_time=None,
                party_size=None,
            ),
            warnings=[],
            error=None,
        )

    monkeypatch.setattr(gurume_tools, "tabelog_get_area_suggestions", fake_area_suggestions)
    monkeypatch.setattr(gurume_tools, "tabelog_get_keyword_suggestions", fake_keyword_suggestions)
    monkeypatch.setattr(gurume_tools, "tabelog_search_restaurants", fake_search_restaurants)

    result = await recommend_japanese_restaurants("Tokyo", "ramen", limit=5)

    assert result["status"] == "success"
    assert result["normalized"]["area"] == "東京都"
    assert result["normalized"]["cuisine"] == "ラーメン"
    assert result["normalized"]["keyword"] is None
    assert captured_search["area"] == "東京都"
    assert captured_search["cuisine"] == "ラーメン"
    assert captured_search["keyword"] is None
    assert result["search"]["items"][0]["name"] == "テストラーメン"
