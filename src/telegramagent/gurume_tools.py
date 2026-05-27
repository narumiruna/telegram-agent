from __future__ import annotations

import re
from typing import Any
from typing import Literal
from typing import cast

from gurume.genre_mapping import get_all_genres
from gurume.genre_mapping import get_genre_code
from gurume.server import tabelog_get_area_suggestions
from gurume.server import tabelog_get_keyword_suggestions
from gurume.server import tabelog_get_restaurant_details
from gurume.server import tabelog_list_cuisines
from gurume.server import tabelog_search_restaurants
from gurume.server_models import SuggestionListOutput
from pydantic import BaseModel
from pydantic_ai import Tool

GurumeSortOption = Literal["ranking", "review-count", "new-open", "standard"]


async def recommend_japanese_restaurants(
    area_query: str,
    food_query: str | None = None,
    sort: GurumeSortOption = "ranking",
    limit: int = 5,
    reservation_date: str | None = None,
    reservation_time: str | None = None,
    party_size: int | None = None,
) -> dict[str, Any]:
    """Recommend Japanese restaurants on Tabelog using a fixed Gurume validation workflow.

    Use this first for natural-language restaurant recommendation requests in Japan. It resolves the area,
    maps supported cuisine names, searches Tabelog, and returns structured results plus warnings.
    """
    normalized_area = area_query.strip()
    if not normalized_area:
        return {
            "status": "error",
            "error": "area_query must not be empty",
            "suggested_action": "Ask the user for a Japanese area, city, station, or prefecture.",
        }

    is_nationwide = _is_nationwide_area_query(normalized_area)
    area_suggestions: SuggestionListOutput | None = None
    search_area: str | None = None if is_nationwide else normalized_area
    if search_area is not None:
        area_suggestions = await tabelog_get_area_suggestions(search_area)
    if area_suggestions is not None and area_suggestions.items:
        normalized_area = area_suggestions.items[0].name
        search_area = normalized_area

    keyword_suggestions: SuggestionListOutput | None = None
    cuisine: str | None = None
    keyword: str | None = None
    normalized_food_query = _normalize_food_query(food_query) if food_query else None
    if normalized_food_query:
        cuisine = _resolve_supported_cuisine(normalized_food_query)
        if cuisine is None:
            keyword_suggestions = await tabelog_get_keyword_suggestions(normalized_food_query)
            cuisine = _first_supported_cuisine(keyword_suggestions)
        if cuisine is None:
            keyword = normalized_food_query

    search_result = await tabelog_search_restaurants(
        area=search_area,
        keyword=keyword,
        cuisine=cuisine,
        sort=sort,
        limit=_clamp_limit(limit),
        page=1,
        reservation_date=reservation_date,
        reservation_time=reservation_time,
        party_size=party_size,
    )

    warnings = list(search_result.warnings)
    if area_suggestions is not None and area_suggestions.status == "error" and area_suggestions.error is not None:
        warnings.append(area_suggestions.error.suggested_action)
    if keyword_suggestions is not None and keyword_suggestions.status == "error" and keyword_suggestions.error:
        warnings.append(keyword_suggestions.error.suggested_action)

    return {
        "status": search_result.status,
        "input": {
            "area_query": area_query,
            "food_query": food_query,
            "sort": sort,
            "limit": limit,
            "reservation_date": reservation_date,
            "reservation_time": reservation_time,
            "party_size": party_size,
        },
        "normalized": {
            "area": search_area,
            "is_nationwide": is_nationwide,
            "cuisine": cuisine,
            "keyword": keyword,
            "used_area_suggestion": bool(area_suggestions and area_suggestions.items),
            "used_keyword_suggestion": cuisine is not None and cuisine != normalized_food_query,
        },
        "area_suggestions": _model_to_json(area_suggestions) if area_suggestions is not None else None,
        "keyword_suggestions": _model_to_json(keyword_suggestions) if keyword_suggestions is not None else None,
        "search": _model_to_json(search_result),
        "warnings": warnings,
    }


async def search_japanese_restaurants(
    area: str | None = None,
    keyword: str | None = None,
    cuisine: str | None = None,
    sort: GurumeSortOption = "ranking",
    limit: int = 10,
    page: int = 1,
    reservation_date: str | None = None,
    reservation_time: str | None = None,
    party_size: int | None = None,
) -> dict[str, Any]:
    """Search Tabelog restaurants with structured Gurume filters.

    Prefer `recommend_japanese_restaurants` for vague recommendation requests. Use this when filters are already clear.
    """
    area, keyword, cuisine = _normalize_search_filters(area=area, keyword=keyword, cuisine=cuisine)
    result = await tabelog_search_restaurants(
        area=area,
        keyword=keyword,
        cuisine=cuisine,
        sort=sort,
        limit=limit,
        page=page,
        reservation_date=reservation_date,
        reservation_time=reservation_time,
        party_size=party_size,
    )
    return _model_to_json(result)


async def get_japanese_restaurant_details(
    restaurant_url: str,
    fetch_reviews: bool = True,
    fetch_menu: bool = True,
    fetch_courses: bool = True,
    max_review_pages: int = 1,
) -> dict[str, Any]:
    """Fetch Tabelog restaurant details for a URL returned by Gurume search."""
    result = await tabelog_get_restaurant_details(
        restaurant_url=restaurant_url,
        fetch_reviews=fetch_reviews,
        fetch_menu=fetch_menu,
        fetch_courses=fetch_courses,
        max_review_pages=max_review_pages,
    )
    return _model_to_json(result)


async def get_tabelog_area_suggestions(query: str) -> dict[str, Any]:
    """Get Tabelog area/station suggestions before searching ambiguous Japanese locations."""
    return _model_to_json(await tabelog_get_area_suggestions(query))


async def get_tabelog_keyword_suggestions(query: str) -> dict[str, Any]:
    """Get Tabelog keyword/cuisine/restaurant-name suggestions before searching food terms."""
    return _model_to_json(await tabelog_get_keyword_suggestions(query))


async def list_tabelog_cuisines() -> dict[str, Any]:
    """List Gurume's supported Tabelog cuisine filters and genre codes."""
    return _model_to_json(await tabelog_list_cuisines())


def build_gurume_tools() -> tuple[Tool[Any], ...]:
    return (
        Tool(
            recommend_japanese_restaurants,
            name="recommend_japanese_restaurants",
            description=(
                "Primary tool for Japanese restaurant recommendations on Tabelog. "
                "Use for best/top restaurant requests, where-to-eat questions, broad area searches, "
                "and natural-language area+cuisine requests in Japan. "
                "For nationwide requests such as 日本, 全国, 全國, or Japan, use this tool with that area text; "
                "the tool treats it as nationwide and searches without a Tabelog area filter. "
                "It resolves ambiguous area text, maps supported cuisine terms, searches Gurume/Tabelog, "
                "and returns normalized filters, warnings, and ranked restaurant results with name, genres, "
                "rating, review_count, area/station text, lunch/dinner price ranges, and Tabelog URL."
            ),
        ),
        Tool(
            search_japanese_restaurants,
            name="search_japanese_restaurants",
            description=(
                "Lower-level Gurume/Tabelog search for explicit filters. "
                "Use when the area, keyword, cuisine, sort, page, or reservation filters are already known. "
                "For nationwide Japan requests, leave area unset or pass 日本/全国/全國/Japan; do not search "
                "for 日本 as a local area. "
                "For vague recommendation requests, prefer recommend_japanese_restaurants. "
                "Returns RestaurantSearchOutput with applied filters, warnings, pagination metadata, "
                "and restaurant items containing name, genres, rating, review_count, area, price ranges, and URL."
            ),
        ),
        Tool(
            get_japanese_restaurant_details,
            name="get_japanese_restaurant_details",
            description=(
                "Fetch detailed Tabelog data for one restaurant URL returned by Gurume search. "
                "Use when the user asks about a specific restaurant, menu, course, reviews, hours, address, "
                "station access, phone, closed days, or reservation information."
            ),
        ),
        Tool(
            get_tabelog_area_suggestions,
            name="get_tabelog_area_suggestions",
            description=(
                "Resolve ambiguous Japanese area, city, prefecture, or station text before restaurant search. "
                "Do not use this helper for nationwide Japan requests such as 日本, 全国, 全國, or Japan. "
                "Returns candidate area names, datatypes, IDs, and coordinates. "
                "This is a lookup helper, not a restaurant recommendation tool."
            ),
        ),
        Tool(
            get_tabelog_keyword_suggestions,
            name="get_tabelog_keyword_suggestions",
            description=(
                "Resolve food, cuisine, or restaurant-name text before restaurant search. "
                "Use to decide whether a user food query should be searched as a supported cuisine, "
                "a free-text keyword, or a restaurant name. "
                "Returns suggestion names, datatypes, and IDs."
            ),
        ),
        Tool(
            list_tabelog_cuisines,
            name="list_tabelog_cuisines",
            description=(
                "List Gurume-supported Tabelog cuisine filters and genre codes. "
                "Use when checking whether a food query can be passed as the cuisine filter "
                "instead of a free-text keyword."
            ),
        ),
    )


_NATIONWIDE_AREA_QUERIES = {
    "日本",
    "日本全國",
    "日本全国",
    "日本國內",
    "日本国内",
    "日本各地",
    "日本全域",
    "全國",
    "全国",
    "全日本",
    "japan",
    "alljapan",
    "nationwide",
    "japanwide",
}

_CUISINE_ALIASES = {
    "漢堡排": "ハンバーグ",
    "汉堡排": "ハンバーグ",
    "漢堡扒": "ハンバーグ",
    "汉堡扒": "ハンバーグ",
    "日式漢堡排": "ハンバーグ",
    "日式汉堡排": "ハンバーグ",
    "hamburg": "ハンバーグ",
    "hamburgsteak": "ハンバーグ",
    "hamburgersteak": "ハンバーグ",
}


def _is_nationwide_area_query(query: str) -> bool:
    return _compact_text(query) in _NATIONWIDE_AREA_QUERIES


def _normalize_search_filters(
    *, area: str | None, keyword: str | None, cuisine: str | None
) -> tuple[str | None, str | None, str | None]:
    normalized_area = None if area is not None and _is_nationwide_area_query(area) else area
    normalized_cuisine = _normalize_food_query(cuisine) if cuisine else None
    normalized_keyword = _normalize_food_query(keyword) if keyword else None
    if normalized_cuisine is None and normalized_keyword is not None:
        resolved_keyword_cuisine = _resolve_supported_cuisine(normalized_keyword)
        if resolved_keyword_cuisine is not None:
            return normalized_area, None, resolved_keyword_cuisine
    return normalized_area, normalized_keyword, normalized_cuisine


def _normalize_food_query(query: str) -> str:
    normalized = query.strip()
    for suffix in ("のお店", "の店", "餐廳", "餐厅", "名店", "店"):
        if normalized.endswith(suffix):
            trimmed = normalized[: -len(suffix)].strip()
            if trimmed:
                normalized = trimmed
                break
    return _CUISINE_ALIASES.get(_compact_text(normalized), normalized)


def _compact_text(text: str) -> str:
    return re.sub(r"[\s　_-]+", "", text.strip()).casefold()


def _resolve_supported_cuisine(query: str) -> str | None:
    return query if get_genre_code(query) is not None else None


def _first_supported_cuisine(suggestions: SuggestionListOutput) -> str | None:
    supported_cuisines = set(get_all_genres())
    for suggestion in suggestions.items:
        if suggestion.name in supported_cuisines:
            return suggestion.name
    return None


def _clamp_limit(limit: int) -> int:
    return max(1, min(limit, 20))


def _model_to_json(model: BaseModel) -> dict[str, Any]:
    return cast(dict[str, Any], model.model_dump(mode="json"))
