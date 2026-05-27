from __future__ import annotations

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

    area_suggestions = await tabelog_get_area_suggestions(normalized_area)
    if area_suggestions.items:
        normalized_area = area_suggestions.items[0].name

    keyword_suggestions: SuggestionListOutput | None = None
    cuisine: str | None = None
    keyword: str | None = None
    normalized_food_query = food_query.strip() if food_query else None
    if normalized_food_query:
        cuisine = _resolve_supported_cuisine(normalized_food_query)
        if cuisine is None:
            keyword_suggestions = await tabelog_get_keyword_suggestions(normalized_food_query)
            cuisine = _first_supported_cuisine(keyword_suggestions)
        if cuisine is None:
            keyword = normalized_food_query

    search_result = await tabelog_search_restaurants(
        area=normalized_area,
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
    if area_suggestions.status == "error" and area_suggestions.error is not None:
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
            "area": normalized_area,
            "cuisine": cuisine,
            "keyword": keyword,
            "used_area_suggestion": bool(area_suggestions.items),
            "used_keyword_suggestion": cuisine is not None and cuisine != normalized_food_query,
        },
        "area_suggestions": _model_to_json(area_suggestions),
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
                "High-level Tabelog restaurant recommendation for Japan. Resolves area and cuisine, searches Gurume, "
                "and returns structured results with warnings."
            ),
        ),
        Tool(
            search_japanese_restaurants,
            name="search_japanese_restaurants",
            description="Search Tabelog restaurants through Gurume with explicit area, keyword, cuisine, and filters.",
        ),
        Tool(
            get_japanese_restaurant_details,
            name="get_japanese_restaurant_details",
            description="Fetch Tabelog detail data for one restaurant URL returned by Gurume search.",
        ),
        Tool(
            get_tabelog_area_suggestions,
            name="get_tabelog_area_suggestions",
            description="Resolve ambiguous Japanese area, city, prefecture, or station names before restaurant search.",
        ),
        Tool(
            get_tabelog_keyword_suggestions,
            name="get_tabelog_keyword_suggestions",
            description="Resolve food keywords into Tabelog cuisine or restaurant-name suggestions.",
        ),
        Tool(
            list_tabelog_cuisines,
            name="list_tabelog_cuisines",
            description="List supported Gurume cuisine filters and Tabelog genre codes.",
        ),
    )


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
