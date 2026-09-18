from __future__ import annotations

import asyncio
import json
import math
import re
import unicodedata
from collections.abc import AsyncIterator
from time import monotonic
from urllib.parse import SplitResult
from urllib.parse import urlencode
from urllib.parse import urlsplit

import httpx
from loguru import logger
from pydantic_ai import Tool

DEFAULT_MORSEL_URL = "https://morsel.narumi.dev/"
DEFAULT_MAX_RESPONSE_BYTES = 65_536
MAX_PREVIEW_SOURCE_BYTES = 4_096
MAX_PREVIEW_TITLE_CHARS = 80
MAX_PREVIEW_DESCRIPTION_CHARS = 200
MIN_EXPIRES_IN_SECONDS = 1
MAX_EXPIRES_IN_SECONDS = 315_360_000
_SHARE_CAPABILITY_RE = re.compile(r"[A-Za-z0-9_-]{43}")
_TELEGRAM_RHASH_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")


class MorselPublishError(RuntimeError):
    """Raised when a Morsel share cannot be created."""

    def __init__(self, message: str, *, category: str = "publish_error") -> None:
        super().__init__(message)
        self.category = category


class MorselNotConfiguredError(MorselPublishError):
    """Raised when Morsel publishing is disabled because no API key is configured."""

    def __init__(self, message: str) -> None:
        super().__init__(message, category="not_configured")


class MorselPublisher:
    def __init__(
        self,
        *,
        base_url: str = DEFAULT_MORSEL_URL,
        api_key: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 12.0,
        expires_in_seconds: int = 2_592_000,
        telegram_instant_view: bool = False,
        telegram_instant_view_rhash: str | None = None,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("Morsel timeout must be finite and positive")
        if not MIN_EXPIRES_IN_SECONDS <= expires_in_seconds <= MAX_EXPIRES_IN_SECONDS:
            raise ValueError(
                f"Morsel share expiry must be between {MIN_EXPIRES_IN_SECONDS} and {MAX_EXPIRES_IN_SECONDS} seconds"
            )
        self.base_url = _validate_origin(base_url)
        self.api_key = _first_api_key(api_key)
        self.http_client = http_client
        self.timeout_seconds = timeout_seconds
        self.timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 10.0))
        self.expires_in_seconds = expires_in_seconds
        self.telegram_instant_view = telegram_instant_view
        self.telegram_instant_view_rhash = _validate_telegram_rhash(telegram_instant_view_rhash)
        self.max_response_bytes = max_response_bytes

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def publish(self, text: str) -> str:
        if not self.api_key:
            raise MorselNotConfiguredError("MORSEL_API_KEY is not configured")

        payload_data = {
            "content": text,
            "preview": _preview_metadata(text),
        }
        if self.telegram_instant_view:
            payload_data["telegram_instant_view"] = True
        else:
            payload_data["expires_in"] = self.expires_in_seconds
        payload = json.dumps(payload_data, ensure_ascii=False).encode()
        started_at = monotonic()
        try:
            async with asyncio.timeout(self.timeout_seconds):
                if self.http_client is not None:
                    share_url = await self._publish_with_client(self.http_client, payload)
                else:
                    async with httpx.AsyncClient() as client:
                        share_url = await self._publish_with_client(client, payload)
        except TimeoutError as exc:
            publish_error = MorselPublishError("Failed to create Morsel share", category="transport_timeout")
            self._log_failure(text, started_at=started_at, category=publish_error.category)
            raise publish_error from exc
        except MorselPublishError as exc:
            self._log_failure(text, started_at=started_at, category=exc.category)
            raise

        logger.info(
            "Morsel publication outcome=success content_chars={} content_bytes={} elapsed_ms={}",
            len(text),
            len(text.encode()),
            round((monotonic() - started_at) * 1000),
        )
        if self.telegram_instant_view and self.telegram_instant_view_rhash:
            return _telegram_instant_view_url(share_url, rhash=self.telegram_instant_view_rhash)
        return share_url

    @staticmethod
    def _log_failure(text: str, *, started_at: float, category: str) -> None:
        logger.warning(
            "Morsel publication outcome=failure content_chars={} content_bytes={} elapsed_ms={} error_category={}",
            len(text),
            len(text.encode()),
            round((monotonic() - started_at) * 1000),
            category,
        )

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
        except httpx.TimeoutException as exc:
            raise MorselPublishError("Failed to create Morsel share", category="transport_timeout") from exc
        except httpx.HTTPError as exc:
            raise MorselPublishError("Failed to create Morsel share", category="transport_error") from exc
        except MorselPublishError as exc:
            raise MorselPublishError("Failed to create Morsel share", category=exc.category) from exc

        if status_code != 201:
            raise MorselPublishError(
                f"Morsel share creation failed with HTTP {status_code}", category=f"http_{status_code}"
            )

        try:
            metadata = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise MorselPublishError("Morsel returned invalid share metadata", category="invalid_response") from exc
        if not isinstance(metadata, dict):
            raise MorselPublishError("Morsel returned invalid share metadata", category="invalid_response")
        share_id = metadata.get("id")
        share_url = metadata.get("share_url")
        if not isinstance(share_id, str) or not share_id or not isinstance(share_url, str) or not share_url:
            raise MorselPublishError("Morsel returned invalid share metadata", category="invalid_response")
        return _validate_share_url(share_url, base_url=self.base_url)


def build_morsel_tools(publisher: MorselPublisher) -> tuple[Tool[None], ...]:
    async def publish_markdown_to_morsel(content: str) -> dict[str, str]:
        """Publish a complete Markdown answer to Morsel for rich rendering."""
        logger.info("Morsel routing reason=rich content_chars={} content_bytes={}", len(content), len(content.encode()))
        try:
            share_url = await publisher.publish(content)
        except MorselPublishError as exc:
            logger.warning("Morsel routing reason=rich outcome=fallback error_category={}", exc.category)
            return {
                "status": "error",
                "error": "Morsel publishing is unavailable.",
                "response_contract": (
                    "Do not claim the content was published. Answer in Telegram-readable plain text without raw "
                    "Mermaid, Vega-Lite, or LaTeX markup, and briefly disclose that rich rendering is unavailable."
                ),
            }
        return {
            "status": "published",
            "share_url": share_url,
            "response_contract": (
                "Return the share_url to the user, with at most a brief introduction. Do not repeat the Markdown, "
                "Mermaid source, Vega-Lite source, or LaTeX source in the final response."
            ),
        }

    return (
        Tool(
            publish_markdown_to_morsel,
            takes_ctx=False,
            name="publish_markdown_to_morsel",
            description=(
                "Publish the complete Markdown answer to Morsel so Mermaid diagrams, Vega-Lite charts, and LaTeX "
                "math render correctly. Use this whenever the planned answer contains a fenced mermaid or vega-lite "
                "block, or LaTeX delimited by $...$ or $$...$$. Pass the complete answer, including all prose and rich "
                "markup, exactly once."
            ),
            sequential=True,
        ),
    )


def _preview_metadata(text: str) -> dict[str, str]:
    source = text.encode()[:MAX_PREVIEW_SOURCE_BYTES].decode(errors="ignore")
    description = _plain_single_line(source) or "Shared with Morsel."
    title = "Morsel"
    for line in source.split("\n"):
        candidate = _plain_single_line(line.strip().lstrip("#>*+-`_~ "))
        if candidate:
            title = candidate
            break
    return {
        "title": title[:MAX_PREVIEW_TITLE_CHARS],
        "description": description[:MAX_PREVIEW_DESCRIPTION_CHARS],
    }


def _plain_single_line(value: str) -> str:
    safe_value = "".join(
        " " if unicodedata.category(character) in {"Cc", "Zl", "Zp"} else character for character in value
    )
    return " ".join(safe_value.split())


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


def _validate_telegram_rhash(value: str | None) -> str:
    rhash = value.strip() if value is not None else ""
    if rhash and not _TELEGRAM_RHASH_RE.fullmatch(rhash):
        raise ValueError("MORSEL_TELEGRAM_INSTANT_VIEW_RHASH must contain 1-128 URL-safe characters")
    return rhash


def _telegram_instant_view_url(share_url: str, *, rhash: str) -> str:
    return "https://t.me/iv?" + urlencode({"url": share_url, "rhash": rhash})


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


def _validate_share_url(value: str, *, base_url: str) -> str:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise MorselPublishError("Morsel returned an invalid share URL", category="invalid_response")
    try:
        share = urlsplit(value)
        base = urlsplit(base_url)
        valid_origin = (
            share.username is None and share.password is None and _normalized_origin(share) == _normalized_origin(base)
        )
        path_capability = share.path.removeprefix("/s/") if share.path.startswith("/s/") else ""
        fragment_capability = share.fragment.removeprefix("/s/") if share.fragment.startswith("/s/") else ""
        valid_path_route = (
            bool(_SHARE_CAPABILITY_RE.fullmatch(path_capability)) and not share.query and not share.fragment
        )
        valid_fragment_route = (
            share.path in {"", "/"} and not share.query and bool(_SHARE_CAPABILITY_RE.fullmatch(fragment_capability))
        )
    except ValueError:
        valid_origin = False
        valid_path_route = False
        valid_fragment_route = False
    if not valid_origin or not (valid_path_route or valid_fragment_route):
        raise MorselPublishError("Morsel returned an invalid share URL", category="invalid_response")
    return value


def _normalized_origin(parsed: SplitResult) -> tuple[str, str | None, int | None]:
    default_port = 443 if parsed.scheme == "https" else 80 if parsed.scheme == "http" else None
    port = parsed.port
    return parsed.scheme, parsed.hostname, default_port if port is None else port
