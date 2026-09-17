from __future__ import annotations

import json

import httpx
import pytest

from telegramagent.morsel import MorselPublisher
from telegramagent.morsel import MorselPublishError


@pytest.mark.asyncio
async def test_morsel_publisher_creates_markdown_share_with_first_configured_key() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            201,
            json={
                "id": "c60a64eb-a7fd-4f29-bff4-5fe153d58420",
                "share_url": "https://morsel.narumi.dev/#/s/share-token",
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

    assert share_url == "https://morsel.narumi.dev/#/s/share-token"
    assert len(requests) == 1
    assert requests[0].url == "https://morsel.narumi.dev/v1/shares"
    assert requests[0].headers["authorization"] == "Bearer first-key"
    assert json.loads(requests[0].content) == {"content": "# 標題\n\n```mermaid\ngraph LR\n```"}


@pytest.mark.asyncio
async def test_morsel_publisher_requires_api_key_before_request() -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(201, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        publisher = MorselPublisher(api_key=None, http_client=client)
        with pytest.raises(MorselPublishError, match="MORSEL_API_KEY"):
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
