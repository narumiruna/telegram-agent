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
from telegramagent.gurume_tools import search_japanese_restaurants

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
    assert result["display_items"] == [
        "1. テストラーメン | rating: 3.80 | reviews: 100 | area: 新宿 | genres: ラーメン | "
        "url: https://tabelog.com/tokyo/A1304/A130401/13000001/"
    ]
    assert result["search"]["display_items"] == result["display_items"]


@pytest.mark.asyncio
async def test_recommend_japanese_restaurants_treats_japan_as_nationwide(monkeypatch) -> None:
    captured_search: dict[str, object] = {}

    async def fail_area_suggestions(query: str) -> SuggestionListOutput:
        raise AssertionError(f"nationwide searches must not resolve area suggestions: {query}")

    async def fail_keyword_suggestions(query: str) -> SuggestionListOutput:
        raise AssertionError(f"漢堡排 should resolve to supported cuisine without keyword suggestions: {query}")

    async def fake_search_restaurants(**kwargs) -> RestaurantSearchOutput:
        captured_search.update(kwargs)
        return RestaurantSearchOutput(
            status="success",
            items=[
                RestaurantOutput(
                    name="テストハンバーグ",
                    rating=3.9,
                    review_count=200,
                    area="銀座",
                    genres=["ハンバーグ"],
                    url=HTTP_URL_ADAPTER.validate_python("https://tabelog.com/tokyo/A1301/A130101/13000002/"),
                    lunch_price="¥1,000～¥1,999",
                    dinner_price="¥2,000～¥2,999",
                )
            ],
            returned_count=1,
            limit=10,
            has_more=False,
            meta=None,
            applied_filters=SearchFiltersOutput(
                area=None,
                keyword=None,
                cuisine="ハンバーグ",
                genre_code="RC1202",
                sort="ranking",
                page=1,
                reservation_date=None,
                reservation_time=None,
                party_size=None,
            ),
            warnings=[],
            error=None,
        )

    monkeypatch.setattr(gurume_tools, "tabelog_get_area_suggestions", fail_area_suggestions)
    monkeypatch.setattr(gurume_tools, "tabelog_get_keyword_suggestions", fail_keyword_suggestions)
    monkeypatch.setattr(gurume_tools, "tabelog_search_restaurants", fake_search_restaurants)

    result = await recommend_japanese_restaurants("日本", "漢堡排店", limit=10)

    assert result["status"] == "success"
    assert result["normalized"]["area"] is None
    assert result["normalized"]["is_nationwide"] is True
    assert result["normalized"]["cuisine"] == "ハンバーグ"
    assert result["normalized"]["keyword"] is None
    assert result["area_suggestions"] is None
    assert captured_search["area"] is None
    assert captured_search["cuisine"] == "ハンバーグ"
    assert captured_search["keyword"] is None
    assert result["display_items"][0].endswith("url: https://tabelog.com/tokyo/A1301/A130101/13000002/")


@pytest.mark.asyncio
async def test_search_japanese_restaurants_normalizes_nationwide_keyword_cuisine(monkeypatch) -> None:
    captured_search: dict[str, object] = {}

    async def fake_search_restaurants(**kwargs) -> RestaurantSearchOutput:
        captured_search.update(kwargs)
        return RestaurantSearchOutput(
            status="success",
            items=[],
            returned_count=0,
            limit=10,
            has_more=False,
            meta=None,
            applied_filters=SearchFiltersOutput(
                area=kwargs["area"],
                keyword=kwargs["keyword"],
                cuisine=kwargs["cuisine"],
                genre_code="RC1202",
                sort="ranking",
                page=1,
                reservation_date=None,
                reservation_time=None,
                party_size=None,
            ),
            warnings=[],
            error=None,
        )

    monkeypatch.setattr(gurume_tools, "tabelog_search_restaurants", fake_search_restaurants)

    result = await search_japanese_restaurants(area="全国", keyword="漢堡排", limit=10)

    assert result["status"] == "success"
    assert captured_search["area"] is None
    assert captured_search["keyword"] is None
    assert captured_search["cuisine"] == "ハンバーグ"
    assert result["display_items"] == []
    assert "Copy each row exactly" in result["response_contract"]


@pytest.mark.asyncio
async def test_sukiyaki_uses_keyword_search_to_avoid_broad_cuisine_results(monkeypatch) -> None:
    captured_searches: list[dict[str, object]] = []

    async def fake_search_restaurants(**kwargs) -> RestaurantSearchOutput:
        captured_searches.append(kwargs)
        if kwargs["page"] == 1:
            items = [
                RestaurantOutput(
                    name="片折",
                    rating=4.66,
                    review_count=460,
                    area="金沢市",
                    genres=["日本料理"],
                    url=HTTP_URL_ADAPTER.validate_python("https://tabelog.com/ishikawa/A1701/A170101/17011166/"),
                    lunch_price=None,
                    dinner_price=None,
                ),
                RestaurantOutput(
                    name="すき焼割烹 日山",
                    rating=3.73,
                    review_count=548,
                    area="人形町駅 66m",
                    genres=["すき焼き、日本料理"],
                    url=HTTP_URL_ADAPTER.validate_python("https://tabelog.com/tokyo/A1302/A130204/13003043/"),
                    lunch_price=None,
                    dinner_price=None,
                ),
            ]
            has_more = True
        else:
            items = [
                RestaurantOutput(
                    name="すき焼き あさい",
                    rating=3.68,
                    review_count=123,
                    area="虎ノ門ヒルズ駅 224m",
                    genres=["すき焼き"],
                    url=HTTP_URL_ADAPTER.validate_python("https://tabelog.com/tokyo/A1307/A130704/13293553/"),
                    lunch_price=None,
                    dinner_price=None,
                ),
                RestaurantOutput(
                    name="博多味処 すきやき・水たき いろは 本店",
                    rating=3.65,
                    review_count=1007,
                    area="中洲川端駅 230m",
                    genres=["水炊き、すき焼き、郷土料理"],
                    url=HTTP_URL_ADAPTER.validate_python("https://tabelog.com/fukuoka/A4001/A400102/40000122/"),
                    lunch_price=None,
                    dinner_price=None,
                ),
            ]
            has_more = False
        return RestaurantSearchOutput(
            status="success",
            items=items,
            returned_count=len(items),
            limit=3,
            has_more=has_more,
            meta=None,
            applied_filters=SearchFiltersOutput(
                area=kwargs["area"],
                keyword="すき焼き",
                cuisine=None,
                genre_code=None,
                sort="ranking",
                page=kwargs["page"],
                reservation_date=None,
                reservation_time=None,
                party_size=None,
            ),
            warnings=[
                "If the keyword is actually a cuisine type, call helpers and pass it as `cuisine` only when supported."
            ],
            error=None,
        )

    monkeypatch.setattr(gurume_tools, "tabelog_search_restaurants", fake_search_restaurants)

    result = await search_japanese_restaurants(area="日本", cuisine="壽喜燒", limit=3)

    assert result["status"] == "success"
    assert [search["page"] for search in captured_searches] == [1, 2]
    assert captured_searches[0]["area"] == "全国"
    assert captured_searches[0]["keyword"] == "すき焼き"
    assert captured_searches[0]["cuisine"] is None
    assert result["returned_count"] == 3
    assert [item["name"] for item in result["items"]] == [
        "すき焼割烹 日山",
        "すき焼き あさい",
        "博多味処 すきやき・水たき いろは 本店",
    ]
    assert result["display_items"] == [
        "1. すき焼割烹 日山 | rating: 3.73 | reviews: 548 | area: 人形町駅 66m | genres: すき焼き、日本料理 | "
        "url: https://tabelog.com/tokyo/A1302/A130204/13003043/",
        "2. すき焼き あさい | rating: 3.68 | reviews: 123 | area: 虎ノ門ヒルズ駅 224m | genres: すき焼き | "
        "url: https://tabelog.com/tokyo/A1307/A130704/13293553/",
        "3. 博多味処 すきやき・水たき いろは 本店 | rating: 3.65 | reviews: 1007 | area: 中洲川端駅 230m | "
        "genres: 水炊き、すき焼き、郷土料理 | url: https://tabelog.com/fukuoka/A4001/A400102/40000122/",
    ]
    assert any("genre filtering for すき焼き" in warning for warning in result["warnings"])
    assert any("Discarded 1" in warning for warning in result["warnings"])
    assert not any("pass it as `cuisine`" in warning for warning in result["warnings"])


@pytest.mark.asyncio
async def test_recommend_sukiyaki_uses_nationwide_keyword_genre_filter(monkeypatch) -> None:
    captured_search: dict[str, object] = {}

    async def fail_area_suggestions(query: str) -> SuggestionListOutput:
        raise AssertionError(f"nationwide searches must not resolve area suggestions: {query}")

    async def fake_search_restaurants(**kwargs) -> RestaurantSearchOutput:
        captured_search.update(kwargs)
        return RestaurantSearchOutput(
            status="success",
            items=[
                RestaurantOutput(
                    name="すき焼割烹 日山",
                    rating=3.73,
                    review_count=548,
                    area="人形町駅 66m",
                    genres=["すき焼き、日本料理"],
                    url=HTTP_URL_ADAPTER.validate_python("https://tabelog.com/tokyo/A1302/A130204/13003043/"),
                    lunch_price=None,
                    dinner_price=None,
                )
            ],
            returned_count=1,
            limit=1,
            has_more=False,
            meta=None,
            applied_filters=SearchFiltersOutput(
                area=kwargs["area"],
                keyword=kwargs["keyword"],
                cuisine=kwargs["cuisine"],
                genre_code=None,
                sort="ranking",
                page=1,
                reservation_date=None,
                reservation_time=None,
                party_size=None,
            ),
            warnings=[],
            error=None,
        )

    monkeypatch.setattr(gurume_tools, "tabelog_get_area_suggestions", fail_area_suggestions)
    monkeypatch.setattr(gurume_tools, "tabelog_search_restaurants", fake_search_restaurants)

    result = await recommend_japanese_restaurants("日本", "壽喜燒", limit=1)

    assert result["status"] == "success"
    assert result["normalized"]["area"] == "全国"
    assert result["normalized"]["is_nationwide"] is True
    assert result["normalized"]["keyword"] == "すき焼き"
    assert result["normalized"]["cuisine"] is None
    assert result["normalized"]["required_genre"] == "すき焼き"
    assert captured_search["area"] == "全国"
    assert captured_search["keyword"] == "すき焼き"
    assert captured_search["cuisine"] is None
