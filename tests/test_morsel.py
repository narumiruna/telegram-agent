from __future__ import annotations

import json
from typing import Any
from typing import cast

import httpx
import pytest

from telegramagent.morsel import MorselNotConfiguredError
from telegramagent.morsel import MorselPublisher
from telegramagent.morsel import MorselPublishError
from telegramagent.morsel import build_morsel_tools


@pytest.mark.asyncio
async def test_morsel_publisher_creates_markdown_share_with_first_configured_key() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            201,
            json={
                "id": "c60a64eb-a7fd-4f29-bff4-5fe153d58420",
                "share_url": "https://morsel.narumi.dev/s/share-token",
                "created_at": "2026-03-21T00:00:00Z",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        publisher = MorselPublisher(
            base_url="https://morsel.narumi.dev/",
            api_key=" first-key, second-key ",
            http_client=client,
        )
        share_url = await publisher.publish("# 標題\n\n```mermaid\ngraph LR\n```")

    assert share_url == "https://morsel.narumi.dev/s/share-token"
    assert len(requests) == 1
    assert requests[0].url == "https://morsel.narumi.dev/v1/shares"
    assert requests[0].headers["authorization"] == "Bearer first-key"
    assert json.loads(requests[0].content) == {
        "content": "# 標題\n\n```mermaid\ngraph LR\n```",
        "preview": True,
    }


@pytest.mark.asyncio
async def test_morsel_agent_tool_publishes_complete_markdown_and_returns_response_contract() -> None:
    markdown = '說明\n\n$$x^2$$\n\n```vega-lite\n{"data": {"values": []}}\n```'

    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content) == {"content": markdown, "preview": True}
        return httpx.Response(
            201,
            json={
                "id": "share-id",
                "share_url": "https://morsel.narumi.dev/s/rich-answer",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        publisher = MorselPublisher(api_key="test-key", http_client=client)
        tool = build_morsel_tools(publisher)[0]
        result = await cast(Any, tool.function)(markdown)

    assert tool.name == "publish_markdown_to_morsel"
    assert tool.sequential is True
    assert "Mermaid" in (tool.description or "")
    assert "Vega-Lite" in (tool.description or "")
    assert "fenced mermaid or vega-lite block" in (tool.description or "")
    assert "LaTeX" in (tool.description or "")
    assert result == {
        "status": "published",
        "share_url": "https://morsel.narumi.dev/s/rich-answer",
        "response_contract": (
            "Return the share_url to the user, with at most a brief introduction. Do not repeat the Markdown, "
            "Mermaid source, Vega-Lite source, or LaTeX source in the final response."
        ),
    }


@pytest.mark.asyncio
async def test_morsel_agent_tool_returns_honest_failure_when_not_configured() -> None:
    publisher = MorselPublisher(api_key=None)
    tool = build_morsel_tools(publisher)[0]

    result = await cast(Any, tool.function)("$$x$$")

    assert publisher.is_configured is False
    assert result["status"] == "error"
    assert "share_url" not in result
    assert "Do not claim" in result["response_contract"]


@pytest.mark.asyncio
async def test_morsel_agent_tool_contains_expected_publish_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"message": "try later"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        publisher = MorselPublisher(api_key="test-key", http_client=client)
        tool = build_morsel_tools(publisher)[0]
        result = await cast(Any, tool.function)("```mermaid\ngraph LR\n```")

    assert result["status"] == "error"
    assert result["error"] == "Morsel publishing is unavailable."
    assert "share_url" not in result


@pytest.mark.asyncio
async def test_morsel_publisher_requires_api_key_before_request() -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(201, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        publisher = MorselPublisher(api_key=None, http_client=client)
        with pytest.raises(MorselNotConfiguredError, match="MORSEL_API_KEY"):
            await publisher.publish("long reply")

    assert request_count == 0


@pytest.mark.asyncio
async def test_morsel_publisher_does_not_retry_failed_creation() -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(503, json={"code": "service_unavailable", "message": "try later"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        publisher = MorselPublisher(api_key="test-key", http_client=client)
        with pytest.raises(MorselPublishError, match="HTTP 503"):
            await publisher.publish("long reply")

    assert request_count == 1


@pytest.mark.asyncio
async def test_morsel_publisher_rejects_oversized_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, content=b"x" * 11)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        publisher = MorselPublisher(api_key="test-key", http_client=client, max_response_bytes=10)
        with pytest.raises(MorselPublishError, match="Failed to create Morsel share"):
            await publisher.publish("long reply")


@pytest.mark.parametrize(
    "url",
    [
        "http://morsel.example.com",
        "https://user@example.com",
        "https://example.com/path",
        "https://example.com/?query=yes",
        "https://example.com/#fragment",
    ],
)
def test_morsel_publisher_rejects_unsafe_or_non_origin_url(url: str) -> None:
    with pytest.raises(ValueError, match="MORSEL_URL"):
        MorselPublisher(base_url=url, api_key="test-key")


def test_morsel_publisher_allows_loopback_http() -> None:
    publisher = MorselPublisher(base_url="http://127.0.0.1:12647/", api_key="test-key")

    assert publisher.base_url == "http://127.0.0.1:12647"
