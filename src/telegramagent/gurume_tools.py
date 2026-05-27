from __future__ import annotations

import re
from dataclasses import dataclass
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
from gurume.server_models import RestaurantOutput
from gurume.server_models import RestaurantSearchOutput
from gurume.server_models import SuggestionListOutput
from pydantic import BaseModel
from pydantic_ai import Tool

GurumeSortOption = Literal["ranking", "review-count", "new-open", "standard"]


@dataclass(frozen=True)
class RestaurantSearchPlan:
    area: str | None
    keyword: str | None
    cuisine: str | None
    required_genre: str | None = None


@dataclass(frozen=True)
class FoodSearchTerms:
    normalized_query: str | None
    keyword: str | None
    cuisine: str | None
    required_genre: str | None
    suggestions: SuggestionListOutput | None = None


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

    food_terms = await _resolve_food_search_terms(food_query)
    if is_nationwide and food_terms.required_genre is not None:
        search_area = "全国"

    search_result = await _search_restaurants_with_policy(
        area=search_area,
        keyword=food_terms.keyword,
        cuisine=food_terms.cuisine,
        required_genre=food_terms.required_genre,
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
    if food_terms.suggestions is not None and food_terms.suggestions.status == "error" and food_terms.suggestions.error:
        warnings.append(food_terms.suggestions.error.suggested_action)

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
            "cuisine": food_terms.cuisine,
            "keyword": food_terms.keyword,
            "required_genre": food_terms.required_genre,
            "used_area_suggestion": bool(area_suggestions and area_suggestions.items),
            "used_keyword_suggestion": (
                food_terms.cuisine is not None and food_terms.cuisine != food_terms.normalized_query
            ),
        },
        "area_suggestions": _model_to_json(area_suggestions) if area_suggestions is not None else None,
        "keyword_suggestions": _model_to_json(food_terms.suggestions) if food_terms.suggestions is not None else None,
        "display_items": _restaurant_display_items(search_result.items),
        "response_contract": _RESPONSE_CONTRACT,
        "search": _search_output_to_json(search_result),
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
    plan = _normalize_search_filters(area=area, keyword=keyword, cuisine=cuisine)
    result = await _search_restaurants_with_policy(
        area=plan.area,
        keyword=plan.keyword,
        cuisine=plan.cuisine,
        required_genre=plan.required_genre,
        sort=sort,
        limit=limit,
        page=page,
        reservation_date=reservation_date,
        reservation_time=reservation_time,
        party_size=party_size,
    )
    return _search_output_to_json(result)


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
                "the tool treats it as nationwide and never searches 日本 as a local area. "
                "It resolves ambiguous area text, maps supported cuisine terms, searches Gurume/Tabelog, "
                "and returns display_items plus structured restaurant results. "
                "For food terms whose Tabelog cuisine ranking is too broad, the tool may use keyword search "
                "with the 全国 area plus genre filtering, and will include that in normalized.required_genre "
                "and warnings. "
                "When answering, copy display_items rows exactly; never invent ratings or URLs, and never combine "
                "a restaurant name from one item with a URL, rating, area, or genre from another item."
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
                "For known broad cuisine filters, the tool may normalize to keyword search and filter returned "
                "restaurants by matching genres. "
                "For vague recommendation requests, prefer recommend_japanese_restaurants. "
                "Returns RestaurantSearchOutput fields plus display_items. "
                "When answering, copy display_items rows exactly; never invent ratings or URLs, and never combine "
                "a restaurant name from one item with a URL, rating, area, or genre from another item."
            ),
        ),
        Tool(
            get_japanese_restaurant_details,
            name="get_japanese_restaurant_details",
            description=(
                "Fetch detailed Tabelog data for one restaurant URL returned by Gurume search. "
                "At least one of fetch_reviews, fetch_menu, or fetch_courses must be true; do not call this "
                "only to repeat name, rating, area, genres, or URL fields already present in search results. "
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
    "壽喜燒": "すき焼き",
    "寿喜烧": "すき焼き",
    "壽喜焼": "すき焼き",
    "寿喜焼": "すき焼き",
    "sukiyaki": "すき焼き",
}

_KEYWORD_SEARCH_FOODS = {
    "すき焼き",
}

_MAX_GENRE_FILTER_PAGES = 3
_KEYWORD_AS_CUISINE_WARNING_FRAGMENT = "pass it as `cuisine`"

_RESPONSE_CONTRACT = (
    "Use display_items as the authoritative restaurant list. Copy each row exactly when presenting results. "
    "Do not rewrite ratings, URLs, names, genres, or areas from memory, and do not mix fields between rows."
)


def _is_nationwide_area_query(query: str) -> bool:
    return _compact_text(query) in _NATIONWIDE_AREA_QUERIES


def _normalize_search_filters(*, area: str | None, keyword: str | None, cuisine: str | None) -> RestaurantSearchPlan:
    normalized_cuisine = _normalize_food_query(cuisine) if cuisine else None
    normalized_keyword = _normalize_food_query(keyword) if keyword else None
    required_genre: str | None = None
    if normalized_cuisine is not None and _prefers_keyword_search(normalized_cuisine):
        if normalized_keyword is None:
            normalized_keyword = normalized_cuisine
        required_genre = normalized_cuisine
        normalized_cuisine = None
    if normalized_cuisine is None and normalized_keyword is not None:
        resolved_keyword_cuisine = _resolve_supported_cuisine(normalized_keyword)
        if resolved_keyword_cuisine is not None and not _prefers_keyword_search(resolved_keyword_cuisine):
            normalized_keyword = None
            normalized_cuisine = resolved_keyword_cuisine
        elif _prefers_keyword_search(normalized_keyword):
            required_genre = normalized_keyword
    normalized_area = _normalize_search_area(area, required_genre=required_genre)
    return RestaurantSearchPlan(
        area=normalized_area,
        keyword=normalized_keyword,
        cuisine=normalized_cuisine,
        required_genre=required_genre,
    )


def _normalize_search_area(area: str | None, *, required_genre: str | None) -> str | None:
    if area is None:
        return "全国" if required_genre is not None else None
    if _is_nationwide_area_query(area):
        return "全国" if required_genre is not None else None
    return area


async def _resolve_food_search_terms(food_query: str | None) -> FoodSearchTerms:
    if not food_query:
        return FoodSearchTerms(
            normalized_query=None,
            keyword=None,
            cuisine=None,
            required_genre=None,
        )
    normalized_query = _normalize_food_query(food_query)
    suggestions: SuggestionListOutput | None = None
    keyword: str | None = None
    cuisine: str | None = None
    if _prefers_keyword_search(normalized_query):
        keyword = normalized_query
    else:
        cuisine = _resolve_supported_cuisine(normalized_query)
    if cuisine is None and keyword is None:
        suggestions = await tabelog_get_keyword_suggestions(normalized_query)
        cuisine = _first_supported_cuisine(suggestions)
    if cuisine is not None and _prefers_keyword_search(cuisine):
        keyword = cuisine
        cuisine = None
    if cuisine is None:
        keyword = normalized_query
    required_genre = keyword if keyword is not None and _prefers_keyword_search(keyword) else None
    return FoodSearchTerms(
        normalized_query=normalized_query,
        keyword=keyword,
        cuisine=cuisine,
        required_genre=required_genre,
        suggestions=suggestions,
    )


async def _search_restaurants_with_policy(
    *,
    area: str | None,
    keyword: str | None,
    cuisine: str | None,
    required_genre: str | None,
    sort: GurumeSortOption,
    limit: int,
    page: int,
    reservation_date: str | None,
    reservation_time: str | None,
    party_size: int | None,
) -> RestaurantSearchOutput:
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
    if required_genre is None or result.status == "error":
        return result
    return await _collect_genre_filtered_search_results(
        first_result=result,
        area=area,
        keyword=keyword,
        cuisine=cuisine,
        required_genre=required_genre,
        sort=sort,
        limit=limit,
        page=page,
        reservation_date=reservation_date,
        reservation_time=reservation_time,
        party_size=party_size,
    )


async def _collect_genre_filtered_search_results(
    *,
    first_result: RestaurantSearchOutput,
    area: str | None,
    keyword: str | None,
    cuisine: str | None,
    required_genre: str,
    sort: GurumeSortOption,
    limit: int,
    page: int,
    reservation_date: str | None,
    reservation_time: str | None,
    party_size: int | None,
) -> RestaurantSearchOutput:
    filtered_items: list[RestaurantOutput] = []
    seen_urls: set[str] = set()
    discarded_count = 0
    warnings = _policy_warnings(first_result.warnings)
    last_result = first_result

    added_items, discarded_items = _filter_restaurants_by_genre(first_result.items, required_genre, seen_urls)
    filtered_items.extend(added_items)
    discarded_count += discarded_items

    next_page = page + 1
    max_page = page + _MAX_GENRE_FILTER_PAGES - 1
    while len(filtered_items) < limit and last_result.has_more and next_page <= max_page:
        last_result = await tabelog_search_restaurants(
            area=area,
            keyword=keyword,
            cuisine=cuisine,
            sort=sort,
            limit=limit,
            page=next_page,
            reservation_date=reservation_date,
            reservation_time=reservation_time,
            party_size=party_size,
        )
        warnings.extend(_policy_warnings(last_result.warnings))
        if last_result.status == "error":
            if last_result.error is not None:
                warnings.append(last_result.error.suggested_action)
            break
        added_items, discarded_items = _filter_restaurants_by_genre(last_result.items, required_genre, seen_urls)
        filtered_items.extend(added_items)
        discarded_count += discarded_items
        next_page += 1

    limited_items = filtered_items[:limit]
    _append_unique_warning(
        warnings,
        f"Used keyword search with genre filtering for {required_genre} because cuisine-only ranking can be too broad.",
    )
    if discarded_count:
        _append_unique_warning(
            warnings,
            f"Discarded {discarded_count} keyword search result(s) whose genres did not include {required_genre}.",
        )
    has_more = len(filtered_items) > limit or last_result.has_more
    status: Literal["success", "no_results", "error"] = "success" if limited_items else "no_results"
    return first_result.model_copy(
        update={
            "status": status,
            "items": limited_items,
            "returned_count": len(limited_items),
            "has_more": has_more,
            "warnings": _unique_strings(warnings),
        }
    )


def _filter_restaurants_by_genre(
    items: list[RestaurantOutput], required_genre: str, seen_urls: set[str]
) -> tuple[list[RestaurantOutput], int]:
    filtered_items: list[RestaurantOutput] = []
    discarded_count = 0
    for item in items:
        if not _restaurant_matches_genre(item, required_genre):
            discarded_count += 1
            continue
        item_url = str(item.url)
        if item_url in seen_urls:
            continue
        seen_urls.add(item_url)
        filtered_items.append(item)
    return filtered_items, discarded_count


def _restaurant_matches_genre(item: RestaurantOutput, required_genre: str) -> bool:
    required = _compact_text(required_genre)
    return any(required in _compact_text(genre) for genre in item.genres)


def _policy_warnings(warnings: list[str]) -> list[str]:
    return [warning for warning in warnings if _KEYWORD_AS_CUISINE_WARNING_FRAGMENT not in warning]


def _append_unique_warning(warnings: list[str], warning: str) -> None:
    if warning not in warnings:
        warnings.append(warning)


def _unique_strings(items: list[str]) -> list[str]:
    unique_items: list[str] = []
    for item in items:
        if item not in unique_items:
            unique_items.append(item)
    return unique_items


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


def _prefers_keyword_search(query: str) -> bool:
    return query in _KEYWORD_SEARCH_FOODS


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


def _search_output_to_json(model: RestaurantSearchOutput) -> dict[str, Any]:
    data = _model_to_json(model)
    data["display_items"] = _restaurant_display_items(model.items)
    data["response_contract"] = _RESPONSE_CONTRACT
    return data


def _restaurant_display_items(items: list[RestaurantOutput]) -> list[str]:
    return [_restaurant_display_item(rank, item) for rank, item in enumerate(items, start=1)]


def _restaurant_display_item(rank: int, item: RestaurantOutput) -> str:
    return (
        f"{rank}. {item.name} | rating: {_format_rating(item.rating)} | reviews: {item.review_count} | "
        f"area: {item.area or '-'} | genres: {_format_genres(item.genres)} | url: {item.url}"
    )


def _format_rating(rating: float | None) -> str:
    return f"{rating:.2f}" if rating is not None else "-"


def _format_genres(genres: list[str]) -> str:
    return "、".join(genres) if genres else "-"


def _model_to_json(model: BaseModel) -> dict[str, Any]:
    return cast(dict[str, Any], model.model_dump(mode="json"))
