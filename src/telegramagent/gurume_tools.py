from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from collections.abc import Callable
from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from typing import Literal

from gurume.detail import RestaurantDetail
from gurume.detail import RestaurantDetailRequest
from gurume.genre_mapping import get_all_genres
from gurume.genre_mapping import get_genre_code
from gurume.restaurant import Restaurant
from gurume.restaurant import SortType
from gurume.search import SearchMeta
from gurume.search import SearchRequest
from gurume.search import SearchResponse
from gurume.search import SearchStatus
from gurume.suggest import AreaSuggestion
from gurume.suggest import KeywordSuggestion
from gurume.suggest import get_area_suggestions_async
from gurume.suggest import get_keyword_suggestions_async
from pydantic_ai import Tool

SortOption = Literal["ranking", "review-count", "new-open", "standard"]
SuggestionLoader = Callable[[str, float], Awaitable[list[AreaSuggestion] | list[KeywordSuggestion]]]

_SORT_MAP: dict[str, SortType] = {
    "ranking": SortType.RANKING,
    "review-count": SortType.REVIEW_COUNT,
    "new-open": SortType.NEW_OPEN,
    "standard": SortType.STANDARD,
}


@dataclass(frozen=True)
class GurumeToolConfig:
    timeout_seconds: float = 30.0
    max_results: int = 20


class GurumeToolRuntime:
    def __init__(self, config: GurumeToolConfig) -> None:
        self.config = config

    async def tabelog_search_restaurants(
        self,
        area: str | None = None,
        keyword: str | None = None,
        cuisine: str | None = None,
        sort: SortOption = "ranking",
        limit: int = 20,
        page: int = 1,
        reservation_date: str | None = None,
        reservation_time: str | None = None,
        party_size: int | None = None,
    ) -> dict[str, Any]:
        """Search Tabelog restaurants after validating ambiguous area and keyword inputs with suggestion tools."""
        normalized_area = _blank_to_none(area)
        normalized_keyword = _blank_to_none(keyword)
        normalized_cuisine = _blank_to_none(cuisine)
        normalized_reservation_date = _blank_to_none(reservation_date)
        normalized_reservation_time = _blank_to_none(reservation_time)

        validation_error = _validate_search_inputs(
            keyword=normalized_keyword,
            cuisine=normalized_cuisine,
            sort=sort,
            limit=limit,
            page=page,
            reservation_date=normalized_reservation_date,
            reservation_time=normalized_reservation_time,
            party_size=party_size,
        )
        if validation_error is not None:
            return _search_error_output(
                error=validation_error,
                area=normalized_area,
                keyword=normalized_keyword,
                cuisine=normalized_cuisine,
                sort=sort,
                limit=limit,
                page=page,
                reservation_date=normalized_reservation_date,
                reservation_time=normalized_reservation_time,
                party_size=party_size,
            )

        genre_code = None
        if normalized_cuisine is not None:
            genre_code = get_genre_code(normalized_cuisine)
            if genre_code is None:
                return _search_error_output(
                    error=_tool_error(
                        code="unsupported_cuisine",
                        message=f"Unsupported cuisine: {normalized_cuisine}",
                        retryable=False,
                        suggested_action="Call tabelog_list_cuisines or tabelog_get_keyword_suggestions first.",
                    ),
                    area=normalized_area,
                    keyword=normalized_keyword,
                    cuisine=normalized_cuisine,
                    sort=sort,
                    limit=limit,
                    page=page,
                    reservation_date=normalized_reservation_date,
                    reservation_time=normalized_reservation_time,
                    party_size=party_size,
                )

        capped_limit = min(limit, self.config.max_results)
        warnings = _search_warnings(
            area=normalized_area,
            keyword=normalized_keyword,
            cuisine=normalized_cuisine,
            requested_limit=limit,
            capped_limit=capped_limit,
            reservation_date=normalized_reservation_date,
        )

        request = SearchRequest(
            area=normalized_area,
            keyword=normalized_keyword,
            genre_code=genre_code,
            reservation_date=normalized_reservation_date,
            reservation_time=normalized_reservation_time,
            party_size=party_size,
            sort_type=_SORT_MAP[sort],
            page=page,
            max_pages=1,
            timeout=self.config.timeout_seconds,
        )

        try:
            response = await _await_with_timeout(request.search(), self.config.timeout_seconds)
        except TimeoutError:
            return _search_error_output(
                error=_tool_error(
                    code="timeout",
                    message=f"Tabelog search timed out after {self.config.timeout_seconds:g}s.",
                    retryable=True,
                    suggested_action="Retry with a narrower area, keyword, or cuisine.",
                ),
                area=normalized_area,
                keyword=normalized_keyword,
                cuisine=normalized_cuisine,
                sort=sort,
                limit=capped_limit,
                page=page,
                reservation_date=normalized_reservation_date,
                reservation_time=normalized_reservation_time,
                party_size=party_size,
                genre_code=genre_code,
                warnings=warnings,
            )
        except Exception as exc:  # noqa: BLE001
            return _search_error_output(
                error=_tool_error(
                    code="internal_error",
                    message="Tabelog search failed unexpectedly.",
                    retryable=True,
                    suggested_action="Retry the tool call. If it keeps failing, inspect the application logs.",
                    detail=str(exc),
                ),
                area=normalized_area,
                keyword=normalized_keyword,
                cuisine=normalized_cuisine,
                sort=sort,
                limit=capped_limit,
                page=page,
                reservation_date=normalized_reservation_date,
                reservation_time=normalized_reservation_time,
                party_size=party_size,
                genre_code=genre_code,
                warnings=warnings,
            )

        if response.status == SearchStatus.ERROR:
            return _search_error_output(
                error=_upstream_tool_error(
                    operation="search",
                    detail=response.error_message,
                    suggested_action="Validate area and cuisine inputs, then retry later.",
                ),
                area=normalized_area,
                keyword=normalized_keyword,
                cuisine=normalized_cuisine,
                sort=sort,
                limit=capped_limit,
                page=page,
                reservation_date=normalized_reservation_date,
                reservation_time=normalized_reservation_time,
                party_size=party_size,
                genre_code=genre_code,
                warnings=warnings,
            )

        items = [_restaurant_search_result_to_dict(restaurant) for restaurant in response.restaurants[:capped_limit]]
        return {
            "status": _search_status(response),
            "items": items,
            "returned_count": len(items),
            "limit": capped_limit,
            "has_more": bool(response.meta and response.meta.has_next_page),
            "meta": _search_meta_to_dict(response.meta),
            "applied_filters": _search_filters(
                area=normalized_area,
                keyword=normalized_keyword,
                cuisine=normalized_cuisine,
                genre_code=genre_code,
                sort=sort,
                page=page,
                reservation_date=normalized_reservation_date,
                reservation_time=normalized_reservation_time,
                party_size=party_size,
            ),
            "warnings": warnings,
        }

    async def tabelog_get_restaurant_details(
        self,
        restaurant_url: str,
        fetch_reviews: bool = True,
        fetch_menu: bool = True,
        fetch_courses: bool = True,
        max_review_pages: int = 1,
    ) -> dict[str, Any]:
        """Fetch Tabelog restaurant details, including reviews, menu items, and courses when requested."""
        if not restaurant_url or not restaurant_url.strip():
            return _detail_error_output(
                restaurant_url=restaurant_url,
                fetch_reviews=fetch_reviews,
                fetch_menu=fetch_menu,
                fetch_courses=fetch_courses,
                max_review_pages=max_review_pages,
                error=_tool_error(
                    code="invalid_parameters",
                    message="restaurant_url must not be empty.",
                    retryable=False,
                    suggested_action="Pass a Tabelog restaurant URL from search results.",
                ),
            )
        if not restaurant_url.startswith("https://tabelog.com/"):
            return _detail_error_output(
                restaurant_url=restaurant_url,
                fetch_reviews=fetch_reviews,
                fetch_menu=fetch_menu,
                fetch_courses=fetch_courses,
                max_review_pages=max_review_pages,
                error=_tool_error(
                    code="invalid_parameters",
                    message="restaurant_url must start with https://tabelog.com/.",
                    retryable=False,
                    suggested_action="Use a restaurant URL returned by tabelog_search_restaurants.",
                ),
            )
        if not any((fetch_reviews, fetch_menu, fetch_courses)):
            return _detail_error_output(
                restaurant_url=restaurant_url,
                fetch_reviews=fetch_reviews,
                fetch_menu=fetch_menu,
                fetch_courses=fetch_courses,
                max_review_pages=max_review_pages,
                error=_tool_error(
                    code="invalid_parameters",
                    message="At least one detail fetch option must be enabled.",
                    retryable=False,
                    suggested_action="Enable fetch_reviews, fetch_menu, or fetch_courses.",
                ),
            )
        if max_review_pages < 1:
            return _detail_error_output(
                restaurant_url=restaurant_url,
                fetch_reviews=fetch_reviews,
                fetch_menu=fetch_menu,
                fetch_courses=fetch_courses,
                max_review_pages=max_review_pages,
                error=_tool_error(
                    code="invalid_parameters",
                    message="max_review_pages must be greater than or equal to 1.",
                    retryable=False,
                    suggested_action="Use max_review_pages=1 or greater.",
                ),
            )

        request = RestaurantDetailRequest(
            restaurant_url=restaurant_url.strip(),
            fetch_reviews=fetch_reviews,
            fetch_menu=fetch_menu,
            fetch_courses=fetch_courses,
            max_review_pages=max_review_pages,
        )
        try:
            detail = await _await_with_timeout(request.fetch(), self.config.timeout_seconds)
        except TimeoutError:
            return _detail_error_output(
                restaurant_url=restaurant_url,
                fetch_reviews=fetch_reviews,
                fetch_menu=fetch_menu,
                fetch_courses=fetch_courses,
                max_review_pages=max_review_pages,
                error=_tool_error(
                    code="timeout",
                    message=f"Tabelog detail fetch timed out after {self.config.timeout_seconds:g}s.",
                    retryable=True,
                    suggested_action="Retry with fewer detail sections or try again later.",
                ),
            )
        except Exception as exc:  # noqa: BLE001
            return _detail_error_output(
                restaurant_url=restaurant_url,
                fetch_reviews=fetch_reviews,
                fetch_menu=fetch_menu,
                fetch_courses=fetch_courses,
                max_review_pages=max_review_pages,
                error=_upstream_tool_error(
                    operation="detail fetch",
                    detail=str(exc),
                    suggested_action="Verify the restaurant URL and retry later.",
                ),
            )
        return _restaurant_detail_to_dict(
            detail,
            fetch_reviews=fetch_reviews,
            fetch_menu=fetch_menu,
            fetch_courses=fetch_courses,
            max_review_pages=max_review_pages,
        )

    async def tabelog_list_cuisines(self) -> dict[str, Any]:
        """List cuisine names supported by Gurume for Tabelog cuisine filtering."""
        try:
            items = [
                {"name": cuisine, "code": code}
                for cuisine in get_all_genres()
                if (code := get_genre_code(cuisine)) is not None
            ]
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "error",
                "items": [],
                "returned_count": 0,
                "error": _tool_error(
                    code="internal_error",
                    message="Cuisine list retrieval failed unexpectedly.",
                    retryable=True,
                    suggested_action="Retry the tool call. If it keeps failing, inspect the application logs.",
                    detail=str(exc),
                ),
            }
        return {"status": "success", "items": items, "returned_count": len(items)}

    async def tabelog_get_area_suggestions(self, query: str) -> dict[str, Any]:
        """Get Tabelog area and station suggestions for an ambiguous user-provided location."""
        return await self._suggest(
            query=query,
            empty_message="query must not be empty.",
            suggested_action="Pass a non-empty area query string.",
            loader=get_area_suggestions_async,
        )

    async def tabelog_get_keyword_suggestions(self, query: str) -> dict[str, Any]:
        """Get Tabelog keyword suggestions for cuisine names, restaurant names, and search variants."""
        return await self._suggest(
            query=query,
            empty_message="query must not be empty.",
            suggested_action="Pass a non-empty keyword query string.",
            loader=get_keyword_suggestions_async,
        )

    async def _suggest(
        self,
        *,
        query: str,
        empty_message: str,
        suggested_action: str,
        loader: SuggestionLoader,
    ) -> dict[str, Any]:
        normalized_query = query.strip()
        if not normalized_query:
            return _suggestion_error_output(
                query=normalized_query,
                error=_tool_error(
                    code="invalid_parameters",
                    message=empty_message,
                    retryable=False,
                    suggested_action=suggested_action,
                ),
            )
        try:
            suggestions = await _await_with_timeout(
                loader(normalized_query, self.config.timeout_seconds),
                self.config.timeout_seconds,
            )
        except TimeoutError:
            return _suggestion_error_output(
                query=normalized_query,
                error=_tool_error(
                    code="timeout",
                    message=f"Tabelog suggestion lookup timed out after {self.config.timeout_seconds:g}s.",
                    retryable=True,
                    suggested_action="Retry with a shorter query or try again later.",
                ),
            )
        except Exception as exc:  # noqa: BLE001
            return _suggestion_error_output(
                query=normalized_query,
                error=_tool_error(
                    code="upstream_unavailable",
                    message="Tabelog suggestion lookup failed.",
                    retryable=True,
                    suggested_action="Retry later, or search directly with a broader query.",
                    detail=str(exc),
                ),
            )

        items = [asdict(suggestion) for suggestion in suggestions]
        return {"status": "success", "query": normalized_query, "items": items, "returned_count": len(items)}


def build_gurume_tools(config: GurumeToolConfig) -> tuple[Tool[Any], ...]:
    runtime = GurumeToolRuntime(config)
    return (
        Tool(
            runtime.tabelog_search_restaurants,
            name="tabelog_search_restaurants",
            description=(
                "Search Tabelog restaurants with compact results. Before this tool, call "
                "tabelog_get_area_suggestions for ambiguous locations and tabelog_get_keyword_suggestions or "
                "tabelog_list_cuisines for cuisine-like keywords. Use tabelog_get_restaurant_details only after "
                "choosing a result URL."
            ),
        ),
        Tool(
            runtime.tabelog_get_restaurant_details,
            name="tabelog_get_restaurant_details",
            description="Fetch details for a Tabelog restaurant URL using Gurume's direct Python API.",
        ),
        Tool(
            runtime.tabelog_list_cuisines,
            name="tabelog_list_cuisines",
            description="List supported Tabelog cuisine filters.",
        ),
        Tool(
            runtime.tabelog_get_area_suggestions,
            name="tabelog_get_area_suggestions",
            description="Use before restaurant search to validate a user-provided Tabelog area or station query.",
        ),
        Tool(
            runtime.tabelog_get_keyword_suggestions,
            name="tabelog_get_keyword_suggestions",
            description="Use before restaurant search to validate cuisine-like keywords or restaurant-name queries.",
        ),
    )


async def _await_with_timeout[T](awaitable: Awaitable[T], timeout_seconds: float) -> T:
    return await asyncio.wait_for(awaitable, timeout=timeout_seconds)


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _validate_search_inputs(
    *,
    keyword: str | None,
    cuisine: str | None,
    sort: str,
    limit: int,
    page: int,
    reservation_date: str | None,
    reservation_time: str | None,
    party_size: int | None,
) -> dict[str, Any] | None:
    if sort not in _SORT_MAP:
        return _tool_error(
            code="invalid_parameters",
            message=f"sort must be one of: {', '.join(_SORT_MAP)}.",
            retryable=False,
            suggested_action="Use ranking, review-count, new-open, or standard.",
        )
    if limit < 1:
        return _tool_error(
            code="invalid_parameters",
            message="limit must be greater than or equal to 1.",
            retryable=False,
            suggested_action="Use limit=1 or greater.",
        )
    if page < 1:
        return _tool_error(
            code="invalid_parameters",
            message="page must be greater than or equal to 1.",
            retryable=False,
            suggested_action="Use page=1 or greater.",
        )
    if keyword is not None and cuisine is not None:
        return _tool_error(
            code="invalid_parameters",
            message="keyword and cuisine cannot be used together.",
            retryable=False,
            suggested_action="Use keyword for restaurant-name matching or cuisine for supported cuisine filters.",
        )
    if reservation_date is not None and reservation_time is None:
        return _tool_error(
            code="invalid_parameters",
            message="reservation_time is required when using reservation_date.",
            retryable=False,
            suggested_action="Provide reservation_date as YYYYMMDD and reservation_time as HHMM together.",
        )
    if reservation_time is not None and reservation_date is None:
        return _tool_error(
            code="invalid_parameters",
            message="reservation_date is required when using reservation_time.",
            retryable=False,
            suggested_action="Provide reservation_date as YYYYMMDD and reservation_time as HHMM together.",
        )
    if party_size is not None and (reservation_date is None or reservation_time is None):
        return _tool_error(
            code="invalid_parameters",
            message="party_size requires reservation_date and reservation_time.",
            retryable=False,
            suggested_action="Provide party_size only together with reservation date and time filters.",
        )
    return None


def _search_status(response: SearchResponse) -> str:
    if response.status == SearchStatus.NO_RESULTS:
        return "no_results"
    return "success"


def _search_meta_to_dict(meta: SearchMeta | None) -> dict[str, Any] | None:
    if meta is None:
        return None
    return {
        "total_count": meta.total_count,
        "current_page": meta.current_page,
        "results_per_page": meta.results_per_page,
        "total_pages": meta.total_pages,
        "has_next_page": meta.has_next_page,
        "has_prev_page": meta.has_prev_page,
        "search_time": _serialize_value(meta.search_time),
    }


def _search_filters(
    *,
    area: str | None,
    keyword: str | None,
    cuisine: str | None,
    genre_code: str | None,
    sort: str,
    page: int,
    reservation_date: str | None,
    reservation_time: str | None,
    party_size: int | None,
) -> dict[str, Any]:
    return {
        "area": area,
        "keyword": keyword,
        "cuisine": cuisine,
        "genre_code": genre_code,
        "sort": sort,
        "page": page,
        "reservation_date": reservation_date,
        "reservation_time": reservation_time,
        "party_size": party_size,
    }


def _search_warnings(
    *,
    area: str | None,
    keyword: str | None,
    cuisine: str | None,
    requested_limit: int,
    capped_limit: int,
    reservation_date: str | None,
) -> list[str]:
    warnings = []
    if requested_limit > capped_limit:
        warnings.append(
            f"Result limit was capped by BOT_GURUME_TOOLS_MAX_RESULTS: {requested_limit} -> {capped_limit}."
        )
    if area is not None:
        warnings.append("Use tabelog_get_area_suggestions first when an area name is ambiguous.")
    if keyword is not None and cuisine is None:
        warnings.append("Use tabelog_get_keyword_suggestions first when the keyword might be a cuisine type.")
    if reservation_date is not None:
        warnings.append("Reservation availability comes from Tabelog and may change.")
    return warnings


def _search_error_output(
    *,
    error: dict[str, Any],
    area: str | None,
    keyword: str | None,
    cuisine: str | None,
    sort: str,
    limit: int,
    page: int,
    reservation_date: str | None,
    reservation_time: str | None,
    party_size: int | None,
    genre_code: str | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "status": "error",
        "items": [],
        "returned_count": 0,
        "limit": limit,
        "has_more": False,
        "meta": None,
        "applied_filters": _search_filters(
            area=area,
            keyword=keyword,
            cuisine=cuisine,
            genre_code=genre_code,
            sort=sort,
            page=page,
            reservation_date=reservation_date,
            reservation_time=reservation_time,
            party_size=party_size,
        ),
        "warnings": warnings or [],
        "error": error,
    }


def _restaurant_to_dict(restaurant: Restaurant) -> dict[str, Any]:
    return {key: _serialize_value(value) for key, value in asdict(restaurant).items()}


def _restaurant_search_result_to_dict(restaurant: Restaurant) -> dict[str, Any]:
    return {
        "name": restaurant.name,
        "rating": restaurant.rating,
        "review_count": restaurant.review_count,
        "area": restaurant.area,
        "genres": restaurant.genres,
        "url": restaurant.url,
        "lunch_price": restaurant.lunch_price,
        "dinner_price": restaurant.dinner_price,
    }


def _restaurant_detail_to_dict(
    detail: RestaurantDetail,
    *,
    fetch_reviews: bool,
    fetch_menu: bool,
    fetch_courses: bool,
    max_review_pages: int,
) -> dict[str, Any]:
    restaurant = _restaurant_to_dict(detail.restaurant)
    return {
        "status": "success",
        "restaurant": restaurant,
        "restaurant_url": detail.restaurant.url,
        "address": detail.restaurant.address,
        "station": detail.restaurant.station,
        "phone": detail.restaurant.phone,
        "business_hours": detail.restaurant.business_hours,
        "closed_days": detail.restaurant.closed_days,
        "reservation_url": detail.restaurant.reservation_url,
        "review_count": len(detail.reviews),
        "menu_item_count": len(detail.menu_items),
        "course_count": len(detail.courses),
        "fetch_reviews": fetch_reviews,
        "fetch_menu": fetch_menu,
        "fetch_courses": fetch_courses,
        "max_review_pages": max_review_pages,
        "reviews": [asdict(review) for review in detail.reviews],
        "menu_items": [asdict(item) for item in detail.menu_items],
        "courses": [asdict(course) for course in detail.courses],
    }


def _detail_error_output(
    *,
    restaurant_url: str,
    fetch_reviews: bool,
    fetch_menu: bool,
    fetch_courses: bool,
    max_review_pages: int,
    error: dict[str, Any],
) -> dict[str, Any]:
    return {
        "status": "error",
        "restaurant": None,
        "restaurant_url": restaurant_url,
        "fetch_reviews": fetch_reviews,
        "fetch_menu": fetch_menu,
        "fetch_courses": fetch_courses,
        "max_review_pages": max_review_pages,
        "reviews": [],
        "menu_items": [],
        "courses": [],
        "error": error,
    }


def _suggestion_error_output(*, query: str, error: dict[str, Any]) -> dict[str, Any]:
    return {"status": "error", "query": query, "items": [], "returned_count": 0, "error": error}


def _tool_error(
    *,
    code: str,
    message: str,
    retryable: bool,
    suggested_action: str,
    detail: str | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "retryable": retryable,
        "suggested_action": suggested_action,
        "detail": detail,
    }


def _upstream_tool_error(*, operation: str, detail: str | None, suggested_action: str) -> dict[str, Any]:
    if detail and ("403" in detail or "Forbidden" in detail):
        return _tool_error(
            code="upstream_forbidden",
            message=f"Tabelog {operation} was blocked with HTTP 403.",
            retryable=False,
            suggested_action="Tell the user Tabelog blocked this server-side request; do not retry immediately.",
            detail=detail,
        )
    return _tool_error(
        code="upstream_unavailable",
        message=f"Tabelog {operation} returned an error response.",
        retryable=True,
        suggested_action=suggested_action,
        detail=detail,
    )


def _serialize_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    return value
