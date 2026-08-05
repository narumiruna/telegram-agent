from __future__ import annotations

import pytest

from telegramagent.kabigon_tool import KabigonLoadError
from telegramagent.url_context import ActionError
from telegramagent.url_context import FetchedResponse
from telegramagent.url_context import extract_url_context


@pytest.mark.asyncio
async def test_extract_url_context_reads_webpage_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fetch(url: str, *, timeout_seconds: float, max_bytes: int, max_redirects: int = 3):
        assert url == "https://example.com/article"
        assert timeout_seconds == 15.0
        assert max_bytes == 80_000
        assert max_redirects == 3
        return (
            "https://example.com/final",
            FetchedResponse(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                content=(
                    b"<html><head>"
                    b"<title>Fallback title</title>"
                    b'<meta property="og:title" content="OG title">'
                    b'<meta name="description" content="Meta description">'
                    b"</head><body>"
                    b"<nav>navigation</nav><h1>Article heading</h1><p>Main text</p><footer>footer</footer>"
                    b"</body></html>"
                ),
            ),
        )

    monkeypatch.setattr("telegramagent.url_context._fetch_public_url_follow_redirects", fetch)

    context = await extract_url_context("https://example.com/article")

    assert context.url == "https://example.com/article"
    assert context.final_url == "https://example.com/final"
    assert context.source_type == "webpage"
    assert context.extraction_status == "success"
    assert context.title == "OG title"
    assert context.description == "Meta description"
    assert context.text is not None
    assert "Article heading Main text" in context.text
    assert "navigation" not in context.text
    assert "footer" not in context.text


@pytest.mark.asyncio
async def test_extract_url_context_uses_kabigon_when_x_fetch_returns_browser_blocker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://x.com/IEObserve/status/2058190539988898008?s=20"

    async def fetch(url: str, *, timeout_seconds: float, max_bytes: int, max_redirects: int = 3):
        return (
            url,
            FetchedResponse(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                content=(
                    b"<html><title>X</title><body>"
                    b"JavaScript is not available. "
                    b"Please enable JavaScript or switch to a supported browser to continue using x.com."
                    b"</body></html>"
                ),
            ),
        )

    async def load(url: str, *, timeout_seconds: float, max_chars: int) -> str:
        return "這是 kabigon 擷取到的 X 貼文內容。"

    monkeypatch.setattr("telegramagent.url_context._fetch_public_url_follow_redirects", fetch)
    monkeypatch.setattr("telegramagent.url_context.load_url_with_kabigon", load)

    context = await extract_url_context(url)

    assert context.source_type == "x_post"
    assert context.extraction_status == "success"
    assert context.author == "@IEObserve"
    assert context.text == "這是 kabigon 擷取到的 X 貼文內容。"


@pytest.mark.asyncio
async def test_extract_url_context_returns_partial_for_x_status_when_fetchers_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fetch(url: str, *, timeout_seconds: float, max_bytes: int, max_redirects: int = 3):
        raise ActionError("network failed token=secret-value")

    async def load(url: str, *, timeout_seconds: float, max_chars: int) -> str:
        raise KabigonLoadError("kabigon failed Bearer secret-token")

    monkeypatch.setattr("telegramagent.url_context._fetch_public_url_follow_redirects", fetch)
    monkeypatch.setattr("telegramagent.url_context.load_url_with_kabigon", load)

    context = await extract_url_context("https://x.com/IEObserve/status/2058190539988898008?s=20")

    assert context.source_type == "x_post"
    assert context.extraction_status == "partial"
    assert context.author == "@IEObserve"
    assert context.text is not None
    assert "status id 2058190539988898008" in context.text
    assert context.error is not None
    assert "token=[redacted]" in context.error
    assert "Bearer [redacted]" in context.error
    assert "secret-value" not in context.error
    assert "secret-token" not in context.error


@pytest.mark.asyncio
async def test_extract_url_context_returns_failed_for_unreadable_webpage(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fetch(url: str, *, timeout_seconds: float, max_bytes: int, max_redirects: int = 3):
        raise ActionError("timeout")

    async def load(url: str, *, timeout_seconds: float, max_chars: int) -> str:
        raise KabigonLoadError("kabigon timeout")

    monkeypatch.setattr("telegramagent.url_context._fetch_public_url_follow_redirects", fetch)
    monkeypatch.setattr("telegramagent.url_context.load_url_with_kabigon", load)

    context = await extract_url_context("https://example.com/slow")

    assert context.source_type == "webpage"
    assert context.extraction_status == "failed"
    assert context.error == "built-in fetch: timeout; kabigon: kabigon timeout"
