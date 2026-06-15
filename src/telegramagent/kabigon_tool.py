from __future__ import annotations

import asyncio
import ipaddress
import socket
import warnings
from urllib.parse import urlparse


class KabigonLoadError(RuntimeError):
    pass


async def load_url_with_kabigon(url: str, *, timeout_seconds: float = 180.0, max_chars: int = 20000) -> str:
    _validate_http_url(url)
    await _assert_public_host(url)
    try:
        content = await asyncio.wait_for(_load_with_kabigon(url), timeout=timeout_seconds)
    except TimeoutError as exc:
        raise KabigonLoadError("kabigon load_url timed out") from exc
    except Exception as exc:
        raise KabigonLoadError(f"kabigon load_url failed: {type(exc).__name__}") from exc
    if not isinstance(content, str) or not content.strip():
        raise KabigonLoadError("kabigon returned no content")
    content = content.strip()
    if len(content) > max_chars:
        return f"{content[:max_chars]}\n\n[truncated by telegramagent: {len(content)} -> {max_chars} chars]"
    return content


async def kabigon_load_url(url: str) -> str:
    """Load URL content as text or markdown using kabigon.api.load_url.

    Use this for supported public HTTP(S) URLs when runtime needs richer extraction
    than the built-in URL fetcher, including YouTube, articles, PDFs, social posts,
    and audio/video pages. Localhost and private network URLs are blocked.
    """
    try:
        return await load_url_with_kabigon(url)
    except KabigonLoadError as exc:
        return f"Error: {exc}"


async def _load_with_kabigon(url: str) -> str:
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message='Field name "json" in "MonitorPage.*" shadows an attribute in parent "BaseModel"',
            category=UserWarning,
        )
        from kabigon.api import load_url

    return await load_url(url)


def _validate_http_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme.casefold() not in {"http", "https"}:
        raise KabigonLoadError("kabigon tool only accepts http or https URLs")
    if parsed.hostname is None:
        raise KabigonLoadError("URL has no valid hostname")


async def _assert_public_host(url: str) -> None:
    host = urlparse(url).hostname
    if host is None:
        raise KabigonLoadError("URL has no valid hostname")
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise KabigonLoadError("hostname resolution failed") from exc
    addresses = {info[4][0] for info in infos}
    if not addresses:
        raise KabigonLoadError("hostname resolved to no addresses")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise KabigonLoadError("private, localhost, link-local, and metadata IPs are blocked")
