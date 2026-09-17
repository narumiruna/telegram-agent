from __future__ import annotations

import json
from collections.abc import AsyncIterator
from urllib.parse import urlsplit

import httpx

DEFAULT_MORSEL_URL = "https://morsel.narumi.dev/"
DEFAULT_MAX_RESPONSE_BYTES = 65_536


class MorselPublishError(RuntimeError):
    """Raised when a Morsel share cannot be created."""


class MorselNotConfiguredError(MorselPublishError):
    """Raised when Morsel publishing is disabled because no API key is configured."""


class MorselPublisher:
    def __init__(
        self,
        *,
        base_url: str = DEFAULT_MORSEL_URL,
        api_key: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 40.0,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        self.base_url = _validate_origin(base_url)
        self.api_key = _first_api_key(api_key)
        self.http_client = http_client
        self.timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 10.0))
        self.max_response_bytes = max_response_bytes

    async def publish(self, text: str) -> str:
        if not self.api_key:
            raise MorselNotConfiguredError("MORSEL_API_KEY is not configured")

        payload = json.dumps({"content": text}, ensure_ascii=False).encode()
        if self.http_client is not None:
            return await self._publish_with_client(self.http_client, payload)

        async with httpx.AsyncClient() as client:
            return await self._publish_with_client(client, payload)

    async def _publish_with_client(self, client: httpx.AsyncClient, payload: bytes) -> str:
        try:
            async with client.stream(
                "POST",
                f"{self.base_url}/v1/shares",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                content=payload,
                follow_redirects=False,
                timeout=self.timeout,
            ) as response:
                body = await _read_bounded(response.aiter_bytes(), limit=self.max_response_bytes)
                status_code = response.status_code
        except (httpx.HTTPError, MorselPublishError) as exc:
            raise MorselPublishError("Failed to create Morsel share") from exc

        if status_code != 201:
            raise MorselPublishError(f"Morsel share creation failed with HTTP {status_code}")

        try:
            metadata = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise MorselPublishError("Morsel returned invalid share metadata") from exc
        if not isinstance(metadata, dict):
            raise MorselPublishError("Morsel returned invalid share metadata")
        share_id = metadata.get("id")
        share_url = metadata.get("share_url")
        if not isinstance(share_id, str) or not share_id or not isinstance(share_url, str) or not share_url:
            raise MorselPublishError("Morsel returned invalid share metadata")
        return share_url


async def _read_bounded(chunks: AsyncIterator[bytes], *, limit: int) -> bytes:
    body = bytearray()
    async for chunk in chunks:
        body.extend(chunk)
        if len(body) > limit:
            raise MorselPublishError("Morsel response exceeded the size limit")
    return bytes(body)


def _first_api_key(configured_keys: str | None) -> str:
    if configured_keys is None:
        return ""
    api_key = next((key.strip() for key in configured_keys.split(",") if key.strip()), "")
    if any(ord(character) < 32 or ord(character) == 127 for character in api_key):
        raise ValueError("MORSEL_API_KEY contains control characters")
    return api_key


def _validate_origin(value: str) -> str:
    url = value.strip()
    if any(ord(character) < 32 or ord(character) == 127 for character in url):
        raise ValueError("MORSEL_URL contains control characters")
    try:
        parsed = urlsplit(url)
        valid = (
            parsed.scheme in {"http", "https"}
            and parsed.hostname is not None
            and parsed.username is None
            and parsed.password is None
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
        )
        _ = parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("MORSEL_URL must be an HTTP(S) origin without credentials, path, query, or fragment")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("MORSEL_URL must use HTTPS except for loopback development")
    return url.rstrip("/")
