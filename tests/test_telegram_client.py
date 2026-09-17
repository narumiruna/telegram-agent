from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from telegramagent.morsel import MorselNotConfiguredError
from telegramagent.morsel import MorselPublishError
from telegramagent.telegram import TelegramClient
from telegramagent.telegram_client import TelegramDownloadTooLargeError
from tests.telegram_test_support import FakeMorselPublisher


class TrackingByteStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.iterated = False

    async def __aiter__(self):
        self.iterated = True
        for chunk in self.chunks:
            yield chunk


@pytest.mark.asyncio
async def test_download_file_rejects_content_length_before_reading_body() -> None:
    stream = TrackingByteStream([b"too-large"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-length": "9"}, stream=stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        telegram = TelegramClient("token", http_client=client)
        with pytest.raises(TelegramDownloadTooLargeError):
            await telegram.download_file("documents/report.pdf", max_bytes=5)

    assert stream.iterated is False


@pytest.mark.asyncio
async def test_download_file_rejects_stream_that_crosses_limit_without_content_length() -> None:
    stream = TrackingByteStream([b"abc", b"def"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        telegram = TelegramClient("token", http_client=client)
        with pytest.raises(TelegramDownloadTooLargeError):
            await telegram.download_file("documents/report.pdf", max_bytes=5)

    assert stream.iterated is True


@pytest.mark.asyncio
async def test_download_file_returns_body_at_exact_limit() -> None:
    stream = TrackingByteStream([b"abc", b"de"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        telegram = TelegramClient("token", http_client=client)
        content = await telegram.download_file("documents/report.pdf", max_bytes=5)

    assert content == b"abcde"


@pytest.mark.asyncio
async def test_telegram_client_formats_commonmark_markdown_as_safe_html() -> None:
    payloads: list[dict[str, Any]] = []
    text = (
        "## 🌅 清晨趕車到新潟，旅程一開始就很有戲\r\n"
        "\n"
        "影片一開場就是早晨 6 點，主角其實有點遲到。\n"
        "\n"
        "## 🌸 櫻花、城跡、山景，把日本春天拍得很滿\n"
        "\n"
        "這一趟重點是 **櫻花** 和 **城堡遺跡**。\n"
        "\n"
        "URL: https://example.com/a_b?x=1&y=2\n"
        "\n"
        "特殊符號: _ * [ ] ( ) ~ ` > # + - = | { } . !\n"
        "`code`\n"
        "```text\ncode block > should be escaped\n```"
        "\x00\x08"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.read().decode()))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 99}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        telegram = TelegramClient("token", http_client=client)
        message_id = await telegram.send_message(123, text)

    assert message_id == 99
    assert payloads[0]["parse_mode"] == "HTML"
    assert "##" not in payloads[0]["text"]
    assert "**" not in payloads[0]["text"]
    assert "<b>🌅 清晨趕車到新潟，旅程一開始就很有戲</b>" in payloads[0]["text"]
    assert "<b>🌸 櫻花、城跡、山景，把日本春天拍得很滿</b>" in payloads[0]["text"]
    assert "這一趟重點是 <b>櫻花</b> 和 <b>城堡遺跡</b>。" in payloads[0]["text"]
    assert (
        '<a href="https://example.com/a_b?x=1&amp;y=2">https://example.com/a_b?x=1&amp;y=2</a>' in payloads[0]["text"]
    )
    assert "特殊符號: _ * [ ] ( ) ~ ` &gt; # + - = | { } . !" in payloads[0]["text"]
    assert "<code>code</code>" in payloads[0]["text"]
    assert "<pre>code block &gt; should be escaped\n</pre>" in payloads[0]["text"]
    assert "\x00" not in payloads[0]["text"]
    assert "\x08" not in payloads[0]["text"]


@pytest.mark.asyncio
async def test_telegram_client_publishes_messages_over_1000_chars_to_morsel() -> None:
    payloads: list[dict[str, Any]] = []
    publisher = FakeMorselPublisher(url="https://morsel.narumi.dev/#/s/share-token")
    text = "x" * 1001

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.read().decode()))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 99}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        telegram = TelegramClient("token", http_client=client, long_message_publisher=publisher)
        message_id = await telegram.send_message(123, text, reply_to_message_id=55)

    assert message_id == 99
    assert publisher.published == [text]
    expected_url = '<a href="https://morsel.narumi.dev/#/s/share-token">https://morsel.narumi.dev/#/s/share-token</a>'
    assert payloads == [
        {
            "chat_id": 123,
            "text": expected_url,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
            "reply_to_message_id": 55,
        }
    ]


@pytest.mark.asyncio
async def test_telegram_client_does_not_publish_messages_at_1000_chars() -> None:
    payloads: list[dict[str, Any]] = []
    publisher = FakeMorselPublisher()
    text = "x" * 1000

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.read().decode()))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 99}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        telegram = TelegramClient("token", http_client=client, long_message_publisher=publisher)
        message_id = await telegram.send_message(123, text)

    assert message_id == 99
    assert publisher.published == []
    assert payloads[0]["text"] == text


@pytest.mark.asyncio
async def test_telegram_client_edits_long_messages_to_morsel_url() -> None:
    payloads: list[dict[str, Any]] = []
    publisher = FakeMorselPublisher(url="https://morsel.narumi.dev/#/s/status")

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.read().decode()))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 99}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        telegram = TelegramClient("token", http_client=client, long_message_publisher=publisher)
        await telegram.edit_message_text(123, 99, "x" * 1001)

    assert publisher.published == ["x" * 1001]
    expected_url = '<a href="https://morsel.narumi.dev/#/s/status">https://morsel.narumi.dev/#/s/status</a>'
    assert payloads == [
        {
            "chat_id": 123,
            "message_id": 99,
            "text": expected_url,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }
    ]


@pytest.mark.asyncio
async def test_telegram_client_falls_back_to_chunks_when_morsel_publish_fails() -> None:
    payloads: list[dict[str, Any]] = []
    publisher = FakeMorselPublisher(error=MorselPublishError("no share"))
    text = "x" * 4100

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.read().decode()))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 99}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        telegram = TelegramClient("token", http_client=client, long_message_publisher=publisher)
        await telegram.send_message(123, text)

    assert publisher.published == [text]
    assert [payload["text"] for payload in payloads] == ["x" * 4096, "x" * 4]


@pytest.mark.asyncio
async def test_telegram_client_silently_chunks_long_messages_when_morsel_is_not_configured(monkeypatch) -> None:
    payloads: list[dict[str, Any]] = []
    publisher = FakeMorselPublisher(error=MorselNotConfiguredError("MORSEL_API_KEY is not configured"))
    text = "x" * 4100

    class NoExceptionLogger:
        def exception(self, *args, **kwargs) -> None:
            pytest.fail(f"unexpected exception log: {args}, {kwargs}")

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.read().decode()))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 99}})

    monkeypatch.setattr("telegramagent.telegram_client.logger", NoExceptionLogger())
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        telegram = TelegramClient("token", http_client=client, long_message_publisher=publisher)
        await telegram.send_message(123, text)

    assert publisher.published == [text]
    assert [payload["text"] for payload in payloads] == ["x" * 4096, "x" * 4]


@pytest.mark.asyncio
async def test_telegram_client_calls_bot_api() -> None:
    requests: list[tuple[str, dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((str(request.url), dict(request.headers)))
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [{"update_id": 1}]})
        if request.url.path.endswith("/sendMessage"):
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 99}})
        return httpx.Response(200, json={"ok": True, "result": {}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        telegram = TelegramClient("token", http_client=client)
        updates = await telegram.get_updates(offset=2, poll_timeout=1)
        message_id = await telegram.send_message(123, "hello")
        await telegram.edit_message_text(123, 99, "done")

    assert updates == [{"update_id": 1}]
    assert message_id == 99
    assert requests[0][0] == "https://api.telegram.org/bottoken/getUpdates"
    assert requests[1][0] == "https://api.telegram.org/bottoken/sendMessage"
    assert requests[2][0] == "https://api.telegram.org/bottoken/editMessageText"
