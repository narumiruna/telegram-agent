from __future__ import annotations

import asyncio
import json
from typing import Any
from typing import cast

import httpx
import pytest
from loguru import logger

from telegramagent.morsel import MorselNotConfiguredError
from telegramagent.morsel import MorselPublisher
from telegramagent.morsel import MorselPublishError
from telegramagent.morsel import build_morsel_tools

CAPABILITY = "a" * 43
SHARE_URL = f"https://morsel.narumi.dev/s/{CAPABILITY}"


@pytest.mark.asyncio
async def test_morsel_publisher_creates_markdown_share_with_first_configured_key() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            201,
            json={
                "id": "c60a64eb-a7fd-4f29-bff4-5fe153d58420",
                "share_url": SHARE_URL,
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

    assert share_url == SHARE_URL
    assert len(requests) == 1
    assert requests[0].url == "https://morsel.narumi.dev/v1/shares"
    assert requests[0].headers["authorization"] == "Bearer first-key"
    assert json.loads(requests[0].content) == {
        "content": "# 標題\n\n```mermaid\ngraph LR\n```",
        "expires_in": 2_592_000,
        "preview": {
            "title": "標題",
            "description": "# 標題 ```mermaid graph LR ```",
        },
    }


@pytest.mark.asyncio
async def test_morsel_publisher_creates_telegram_instant_view_share_without_expiry() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json={"id": "share-id", "share_url": SHARE_URL})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        publisher = MorselPublisher(
            api_key="test-key",
            http_client=client,
            telegram_instant_view=True,
            telegram_instant_view_rhash="abc123def45678",
        )
        share_url = await publisher.publish("# Instant View\n\nFull article.")

    assert share_url == SHARE_URL + "?tg_rhash=abc123def45678"
    payload = json.loads(requests[0].content)
    assert payload["telegram_instant_view"] is True
    assert "expires_in" not in payload
    assert payload["preview"] == {
        "title": "Instant View",
        "description": "# Instant View Full article.",
    }


@pytest.mark.asyncio
async def test_morsel_agent_tool_publishes_complete_markdown_and_returns_response_contract() -> None:
    markdown = '說明\n\n$$x^2$$\n\n```vega-lite\n{"data": {"values": []}}\n```'

    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content) == {
            "content": markdown,
            "expires_in": 2_592_000,
            "preview": {
                "title": "說明",
                "description": '說明 $$x^2$$ ```vega-lite {"data": {"values": []}} ```',
            },
        }
        return httpx.Response(
            201,
            json={
                "id": "share-id",
                "share_url": SHARE_URL,
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
        "share_url": SHARE_URL,
        "response_contract": (
            "Return the share_url to the user, with at most a brief introduction. Do not repeat the Markdown, "
            "Mermaid source, Vega-Lite source, or LaTeX source in the final response."
        ),
    }


@pytest.mark.asyncio
async def test_morsel_publisher_normalizes_preview_metadata_to_api_limits() -> None:
    heading = "標" * 100
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            201,
            json={"id": "share-id", "share_url": SHARE_URL},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        publisher = MorselPublisher(api_key="test-key", http_client=client)
        await publisher.publish(f"# {heading}\n\n內容\x00包含控制字元")

    preview = json.loads(requests[0].content)["preview"]
    assert preview["title"] == "標" * 80
    assert len(preview["description"]) <= 200
    assert "\x00" not in preview["description"]
    assert "內容 包含控制字元" in preview["description"]


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


@pytest.mark.parametrize(
    "share_url",
    [
        SHARE_URL,
        f"https://morsel.narumi.dev/#/s/{CAPABILITY}",
        f"https://morsel.narumi.dev:443/s/{CAPABILITY}",
    ],
)
@pytest.mark.asyncio
async def test_morsel_publisher_accepts_documented_share_url_routes(share_url: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"id": "share-id", "share_url": share_url})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        publisher = MorselPublisher(api_key="test-key", http_client=client)

        assert await publisher.publish("content") == share_url


@pytest.mark.parametrize(
    "share_url",
    [
        f"https://evil.example/s/{CAPABILITY}",
        f"https://user@morsel.narumi.dev/s/{CAPABILITY}",
        f"https://morsel.narumi.dev/share/{CAPABILITY}",
        "https://morsel.narumi.dev/s/short",
        f"https://morsel.narumi.dev/s/{CAPABILITY}?track=1",
        f"https://morsel.narumi.dev:0/s/{CAPABILITY}",
        f"https://morsel.narumi.dev/s/{CAPABILITY}#/s/{CAPABILITY}",
        f"\nhttps://morsel.narumi.dev/s/{CAPABILITY}",
    ],
)
@pytest.mark.asyncio
async def test_morsel_publisher_rejects_untrusted_share_urls(share_url: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"id": "share-id", "share_url": share_url})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        publisher = MorselPublisher(api_key="test-key", http_client=client)

        with pytest.raises(MorselPublishError, match="invalid share URL") as exc_info:
            await publisher.publish("content")

    assert exc_info.value.category == "invalid_response"


@pytest.mark.asyncio
async def test_morsel_publisher_applies_configured_expiry_and_timeout() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json={"id": "share-id", "share_url": SHARE_URL})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        publisher = MorselPublisher(
            api_key="test-key",
            http_client=client,
            expires_in_seconds=3600,
            timeout_seconds=8.5,
        )
        await publisher.publish("content")

    assert json.loads(requests[0].content)["expires_in"] == 3600
    assert publisher.timeout.read == 8.5
    assert publisher.timeout.connect == 8.5


@pytest.mark.asyncio
async def test_morsel_publisher_enforces_timeout_across_complete_response_stream() -> None:
    class HangingByteStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"id":"share-id",'
            await asyncio.Event().wait()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, stream=HangingByteStream())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        publisher = MorselPublisher(api_key="test-key", http_client=client, timeout_seconds=0.01)

        with pytest.raises(MorselPublishError, match="Failed to create Morsel share") as exc_info:
            await publisher.publish("content")

    assert exc_info.value.category == "transport_timeout"


@pytest.mark.parametrize(
    ("expires_in_seconds", "timeout_seconds"),
    [
        (0, 12.0),
        (315_360_001, 12.0),
        (2_592_000, 0.0),
        (2_592_000, float("inf")),
    ],
)
def test_morsel_publisher_rejects_invalid_lifecycle_values(expires_in_seconds: int, timeout_seconds: float) -> None:
    with pytest.raises(ValueError):
        MorselPublisher(
            api_key="test-key",
            expires_in_seconds=expires_in_seconds,
            timeout_seconds=timeout_seconds,
        )


def test_morsel_publisher_rejects_invalid_telegram_rhash() -> None:
    with pytest.raises(ValueError, match="TELEGRAM_INSTANT_VIEW_RHASH"):
        MorselPublisher(api_key="test-key", telegram_instant_view_rhash="invalid&hash")


@pytest.mark.asyncio
async def test_morsel_logs_metadata_without_content_credentials_or_capability() -> None:
    content = "private-answer-marker"
    api_key = "private-key-marker"
    records: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"id": "share-id", "share_url": SHARE_URL})

    sink_id = logger.add(records.append, format="{message}")
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            publisher = MorselPublisher(api_key=api_key, http_client=client)
            tool = build_morsel_tools(publisher)[0]
            await cast(Any, tool.function)(content)
    finally:
        logger.remove(sink_id)

    rendered = "".join(records)
    assert "reason=rich" in rendered
    assert "outcome=success" in rendered
    assert "content_chars=21" in rendered
    assert content not in rendered
    assert api_key not in rendered
    assert CAPABILITY not in rendered
    assert SHARE_URL not in rendered
