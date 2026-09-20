from __future__ import annotations

import pytest

from telegramagent.kabigon_tool import KabigonLoadError
from telegramagent.kabigon_tool import kabigon_load_url
from telegramagent.kabigon_tool import load_url_with_kabigon


@pytest.mark.asyncio
async def test_load_url_with_kabigon_calls_api_and_truncates(monkeypatch) -> None:
    async def allow_url(url: str) -> None:
        assert url == "https://example.com/video"

    async def fake_load(url: str) -> str:
        assert url == "https://example.com/video"
        return "abcdef"

    monkeypatch.setattr("telegramagent.kabigon_tool._assert_public_host", allow_url)
    monkeypatch.setattr("telegramagent.kabigon_tool._load_with_kabigon", fake_load)

    result = await load_url_with_kabigon("https://example.com/video", timeout_seconds=1, max_chars=3)

    assert result == "abc\n\n[truncated by telegramagent: 6 -> 3 chars]"


@pytest.mark.asyncio
async def test_load_url_with_kabigon_rejects_non_http_url() -> None:
    with pytest.raises(KabigonLoadError, match="http or https"):
        await load_url_with_kabigon("file:///etc/passwd")


@pytest.mark.asyncio
async def test_kabigon_tool_returns_safe_error_for_agent() -> None:
    result = await kabigon_load_url("file:///etc/passwd")

    assert result == "Error: kabigon tool only accepts http or https URLs"
