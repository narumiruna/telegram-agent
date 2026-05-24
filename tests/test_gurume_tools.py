from __future__ import annotations

from typing import Any

import pytest
from gurume.detail import Course
from gurume.detail import MenuItem
from gurume.detail import RestaurantDetail
from gurume.detail import RestaurantDetailRequest
from gurume.detail import Review
from gurume.restaurant import Restaurant
from gurume.search import SearchMeta
from gurume.search import SearchRequest
from gurume.search import SearchResponse
from gurume.search import SearchStatus
from gurume.suggest import AreaSuggestion
from gurume.suggest import KeywordSuggestion

from telegramagent.gurume_tools import GurumeToolConfig
from telegramagent.gurume_tools import GurumeToolRuntime


@pytest.mark.asyncio
async def test_gurume_search_tool_calls_search_request_and_caps_results(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_search(self: SearchRequest) -> SearchResponse:
        captured["request"] = self
        return SearchResponse(
            status=SearchStatus.SUCCESS,
            restaurants=[
                Restaurant(name="A", url="https://tabelog.com/tokyo/A0001/A000101/1/", rating=4.1),
                Restaurant(name="B", url="https://tabelog.com/tokyo/A0001/A000101/2/", rating=3.9),
            ],
            meta=SearchMeta(
                total_count=2,
                current_page=1,
                results_per_page=2,
                total_pages=2,
                has_next_page=True,
                has_prev_page=False,
            ),
        )

    monkeypatch.setattr(SearchRequest, "search", fake_search)
    runtime = GurumeToolRuntime(GurumeToolConfig(timeout_seconds=1, max_results=1))

    result = await runtime.tabelog_search_restaurants(area="Tokyo", keyword="sushi", limit=5)

    request = captured["request"]
    assert request.area == "Tokyo"
    assert request.keyword == "sushi"
    assert request.timeout == 1
    assert result["status"] == "success"
    assert result["limit"] == 1
    assert result["returned_count"] == 1
    assert result["items"][0]["name"] == "A"
    assert result["has_more"] is True
    assert result["meta"]["current_page"] == 1
    assert "5 -> 1" in result["warnings"][0]


@pytest.mark.asyncio
async def test_gurume_search_tool_returns_structured_validation_errors() -> None:
    runtime = GurumeToolRuntime(GurumeToolConfig())

    unknown_cuisine = await runtime.tabelog_search_restaurants(cuisine="definitely-not-supported")
    mixed_filters = await runtime.tabelog_search_restaurants(keyword="sushi", cuisine="ramen")
    missing_time = await runtime.tabelog_search_restaurants(reservation_date="20260427")

    assert unknown_cuisine["status"] == "error"
    assert unknown_cuisine["error"]["code"] == "unsupported_cuisine"
    assert mixed_filters["error"]["code"] == "invalid_parameters"
    assert missing_time["error"]["message"] == "reservation_time is required when using reservation_date."


@pytest.mark.asyncio
async def test_gurume_suggestion_tools_return_serializable_items(monkeypatch) -> None:
    async def fake_area_suggestions(query: str, request_timeout: float = 10.0) -> list[AreaSuggestion]:
        assert query == "Tokyo"
        assert request_timeout == 2
        return [AreaSuggestion(name="Tokyo", datatype="Prefecture", id_in_datatype=13, lat=35.0, lng=139.0)]

    async def fake_keyword_suggestions(query: str, request_timeout: float = 10.0) -> list[KeywordSuggestion]:
        assert query == "sushi"
        assert request_timeout == 2
        return [KeywordSuggestion(name="sushi", datatype="Genre2", id_in_datatype="RC010201")]

    monkeypatch.setattr("telegramagent.gurume_tools.get_area_suggestions_async", fake_area_suggestions)
    monkeypatch.setattr("telegramagent.gurume_tools.get_keyword_suggestions_async", fake_keyword_suggestions)
    runtime = GurumeToolRuntime(GurumeToolConfig(timeout_seconds=2))

    area = await runtime.tabelog_get_area_suggestions(" Tokyo ")
    keyword = await runtime.tabelog_get_keyword_suggestions("sushi")
    empty = await runtime.tabelog_get_area_suggestions(" ")

    assert area["items"] == [
        {"name": "Tokyo", "datatype": "Prefecture", "id_in_datatype": 13, "lat": 35.0, "lng": 139.0}
    ]
    assert keyword["items"][0]["datatype"] == "Genre2"
    assert empty["status"] == "error"
    assert empty["error"]["code"] == "invalid_parameters"


@pytest.mark.asyncio
async def test_gurume_detail_tool_returns_restaurant_details(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_fetch(self: RestaurantDetailRequest) -> RestaurantDetail:
        captured["request"] = self
        return RestaurantDetail(
            restaurant=Restaurant(
                name="A",
                url="https://tabelog.com/tokyo/A0001/A000101/1/",
                rating=4.1,
                review_count=20,
                address="Tokyo",
                station="Tokyo Station",
                business_hours="18:00-22:00",
                closed_days="Sunday",
            ),
            reviews=[Review(reviewer="alice", content="great", rating=4.0)],
            menu_items=[MenuItem(name="course", price="10000")],
            courses=[Course(name="dinner", price="12000", items=["starter"])],
        )

    monkeypatch.setattr(RestaurantDetailRequest, "fetch", fake_fetch)
    runtime = GurumeToolRuntime(GurumeToolConfig(timeout_seconds=1))

    result = await runtime.tabelog_get_restaurant_details(
        "https://tabelog.com/tokyo/A0001/A000101/1/",
        fetch_menu=False,
    )

    request = captured["request"]
    assert request.fetch_menu is False
    assert result["status"] == "success"
    assert result["restaurant"]["name"] == "A"
    assert result["review_count"] == 1
    assert result["menu_item_count"] == 1
    assert result["course_count"] == 1
    assert result["reviews"][0]["reviewer"] == "alice"


@pytest.mark.asyncio
async def test_gurume_detail_tool_rejects_non_tabelog_url() -> None:
    runtime = GurumeToolRuntime(GurumeToolConfig())

    result = await runtime.tabelog_get_restaurant_details("https://example.com/restaurant")

    assert result["status"] == "error"
    assert result["error"]["code"] == "invalid_parameters"
