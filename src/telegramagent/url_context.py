from __future__ import annotations

import asyncio
import html
import ipaddress
import re
import socket
import ssl
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from html.parser import HTMLParser
from typing import Literal
from typing import cast
from urllib.parse import urljoin
from urllib.parse import urlparse
from urllib.parse import urlunparse

import httpx

from telegramagent.kabigon_tool import KabigonLoadError
from telegramagent.kabigon_tool import load_url_with_kabigon

_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "www.youtu.be"}
_X_HOSTS = {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}
_REDDIT_HOSTS = {"reddit.com", "www.reddit.com", "old.reddit.com", "redd.it", "www.redd.it"}
_X_BLOCKER_PHRASES = (
    "javascript is not available",
    "javascript is disabled in this browser",
    "enable javascript or switch to a supported browser",
    "switch to a supported browser to continue using x.com",
    "continue using x.com",
)
_REDDIT_BLOCKER_PHRASES = (
    "reddit - please wait for verification",
    "please wait for verification",
    "verify you are a human",
    "verify you're a human",
)
_SENSITIVE_ERROR_RE = re.compile(r"(?i)\b(token|api[_-]?key|authorization|cookie|set-cookie|password|secret)=([^\s;]+)")
_BEARER_ERROR_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")


class ActionError(RuntimeError):
    pass


@dataclass(frozen=True)
class FetchedResponse:
    status_code: int
    headers: dict[str, str]
    content: bytes

    @property
    def text(self) -> str:
        content_type = self.headers.get("content-type", "")
        match = re.search(r"charset=([^;\s]+)", content_type, flags=re.IGNORECASE)
        encoding = match.group(1) if match is not None else "utf-8"
        return self.content.decode(encoding, errors="replace")


@dataclass(frozen=True)
class UrlContext:
    url: str
    final_url: str
    source_type: Literal["x_post", "webpage", "youtube", "unknown"]
    fetched_at: str
    extraction_status: Literal["success", "partial", "failed"]
    title: str | None = None
    author: str | None = None
    text: str | None = None
    description: str | None = None
    error: str | None = None


async def extract_url_context(
    url: str,
    *,
    timeout_seconds: float = 15.0,
    max_chars: int = 6000,
    max_bytes: int = 80_000,
) -> UrlContext:
    fetched_at = datetime.now(UTC).isoformat()
    source_type = _source_type_for_url(url)
    try:
        final_url, response = await _fetch_public_url_follow_redirects(
            url, timeout_seconds=timeout_seconds, max_bytes=max_bytes
        )
        return _url_context_from_response(
            url,
            final_url=final_url,
            response=response,
            source_type=source_type,
            fetched_at=fetched_at,
            max_chars=max_chars,
        )
    except (ActionError, httpx.HTTPError, OSError, TimeoutError) as primary_exc:
        try:
            body = await load_url_with_kabigon(url, timeout_seconds=timeout_seconds, max_chars=max_chars)
            if source_type == "x_post" and _looks_like_x_blocker_page(body):
                raise KabigonLoadError("kabigon 讀到的是 X 的 JavaScript/browser unsupported 頁面，不是貼文內容。")
        except KabigonLoadError as fallback_exc:
            return _failed_url_context(
                url,
                source_type=source_type,
                fetched_at=fetched_at,
                primary_error=primary_exc,
                fallback_error=fallback_exc,
            )
        return _url_context_from_text(
            url,
            source_type=source_type,
            fetched_at=fetched_at,
            text=body,
            extraction_status="success",
        )


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg", "nav", "footer", "header"}:
            self.skip_depth += 1
        if tag in {"p", "br", "div", "section", "article", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg", "nav", "footer", "header"} and self.skip_depth > 0:
            self.skip_depth -= 1
        if tag in {"p", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_depth == 0 and data.strip():
            self.parts.append(data)


def _readable_error(exc: BaseException) -> str:
    text = str(exc).strip()
    if not text:
        text = type(exc).__name__
    text = _collapse_whitespace(text)
    text = _redact_error_text(text)
    if len(text) > 180:
        return f"{text[:180]}…"
    return text


def _redact_error_text(text: str) -> str:
    text = _SENSITIVE_ERROR_RE.sub(lambda match: f"{match.group(1)}=[redacted]", text)
    return _BEARER_ERROR_RE.sub("Bearer [redacted]", text)


def _is_youtube_url(url: str) -> bool:
    return (urlparse(url).hostname or "").casefold() in _YOUTUBE_HOSTS


def _source_type_for_url(url: str) -> Literal["x_post", "webpage", "youtube", "unknown"]:
    if _is_x_status_url(url):
        return "x_post"
    if _is_youtube_url(url):
        return "youtube"
    parsed = urlparse(url)
    if parsed.scheme.casefold() in {"http", "https"} and parsed.hostname:
        return "webpage"
    return "unknown"


def _is_reddit_url(url: str) -> bool:
    return (urlparse(url).hostname or "").casefold() in _REDDIT_HOSTS


def _is_x_status_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    path_parts = [part for part in parsed.path.split("/") if part]
    return host in _X_HOSTS and len(path_parts) >= 3 and path_parts[1] == "status"


def _x_status_parts(url: str) -> tuple[str, str] | None:
    if not _is_x_status_url(url):
        return None
    path_parts = [part for part in urlparse(url).path.split("/") if part]
    return path_parts[0], path_parts[2]


def _looks_like_x_blocker_page(
    text: str | None,
    *,
    title: str | None = None,
    description: str | None = None,
) -> bool:
    combined = " ".join(part for part in (title, description, text) if part).casefold()
    return bool(combined) and any(phrase in combined for phrase in _X_BLOCKER_PHRASES)


def _looks_like_reddit_blocker_page(
    text: str | None,
    *,
    title: str | None = None,
    description: str | None = None,
) -> bool:
    combined = " ".join(part for part in (title, description, text) if part).casefold()
    return bool(combined) and any(phrase in combined for phrase in _REDDIT_BLOCKER_PHRASES)


def _blocker_error_for_url(url: str, text: str | None, *, title: str | None = None) -> str | None:
    if _is_x_status_url(url) and _looks_like_x_blocker_page(text, title=title):
        return "X 回傳的是 JavaScript/browser unsupported 頁面，不是貼文內容。"
    if _is_reddit_url(url) and _looks_like_reddit_blocker_page(text, title=title):
        return "Reddit 回傳的是驗證/反機器人頁面，不是貼文內容。"
    return None


async def _fetch_public_url(url: str, *, timeout_seconds: float, max_bytes: int) -> FetchedResponse:
    parsed = urlparse(url)
    host = parsed.hostname
    if host is None:
        raise ActionError("這個連結沒有有效主機名稱，我沒辦法自動讀取。")
    addresses = await _resolve_public_addresses(host)
    address = addresses[0]
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    target = urlunparse(("", "", parsed.path or "/", parsed.params, parsed.query, ""))
    host_header = host if parsed.port is None else f"{host}:{parsed.port}"
    request = (
        f"GET {target} HTTP/1.1\r\n"
        f"Host: {host_header}\r\n"
        "User-Agent: telegram-agent/0.1\r\n"
        "Accept: text/html,text/plain;q=0.9,*/*;q=0.1\r\n"
        "Accept-Encoding: identity\r\n"
        "Connection: close\r\n\r\n"
    ).encode()

    ssl_context = ssl.create_default_context() if parsed.scheme == "https" else None
    try:
        async with asyncio.timeout(timeout_seconds):
            reader, writer = await asyncio.open_connection(
                address,
                port,
                ssl=ssl_context,
                server_hostname=host if ssl_context is not None else None,
            )
            try:
                writer.write(request)
                await writer.drain()
                raw_headers = await reader.readuntil(b"\r\n\r\n")
                status_code, headers = _parse_response_headers(raw_headers)
                body = await _read_limited_body(reader, headers=headers, max_bytes=max_bytes)
            finally:
                writer.close()
                await writer.wait_closed()
    except asyncio.LimitOverrunError as exc:
        raise ActionError("這個頁面的 HTTP headers 太大了，我先不自動讀取。") from exc
    except asyncio.IncompleteReadError as exc:
        raise ActionError("這個連結回應不完整，我目前讀不到內容。") from exc

    return FetchedResponse(status_code=status_code, headers=headers, content=body)


async def _fetch_public_url_follow_redirects(
    url: str, *, timeout_seconds: float, max_bytes: int, max_redirects: int = 3
) -> tuple[str, FetchedResponse]:
    current_url = url
    for _redirect_count in range(max_redirects + 1):
        response = await _fetch_public_url(current_url, timeout_seconds=timeout_seconds, max_bytes=max_bytes)
        if not 300 <= response.status_code < 400:
            return current_url, response

        location = response.headers.get("location")
        if not location:
            raise ActionError("這個連結重新導向但沒有提供 Location header。")
        next_url = urljoin(current_url, location)
        parsed = urlparse(next_url)
        if parsed.scheme.casefold() not in {"http", "https"} or parsed.hostname is None:
            raise ActionError("這個連結重新導向到不支援或無效的 URL。")
        await _assert_public_host(parsed.hostname)
        current_url = next_url

    raise ActionError("這個連結重新導向太多次，我先不自動讀取。")


async def _assert_public_host(host: str) -> None:
    await _resolve_public_addresses(host)


async def _resolve_public_addresses(host: str) -> list[str]:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ActionError("這個連結的主機名稱解析失敗，我沒辦法自動讀取。") from exc

    addresses = sorted({cast(str, info[4][0]) for info in infos})
    if not addresses:
        raise ActionError("這個連結沒有解析到可用 IP，我沒辦法自動讀取。")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ActionError("基於安全限制，我不會自動讀取 localhost、私有網路或雲端 metadata 位址。")
    return addresses


def _parse_response_headers(raw_headers: bytes) -> tuple[int, dict[str, str]]:
    header_text = raw_headers.decode("iso-8859-1")
    lines = header_text.split("\r\n")
    status_parts = lines[0].split(maxsplit=2)
    if len(status_parts) < 2 or not status_parts[1].isdigit():
        raise ActionError("這個連結回傳了無效 HTTP 回應，我目前讀不到內容。")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line or ":" not in line:
            continue
        key, value = line.split(":", maxsplit=1)
        headers[key.strip().casefold()] = value.strip()
    return int(status_parts[1]), headers


async def _read_limited_body(reader: asyncio.StreamReader, *, headers: dict[str, str], max_bytes: int) -> bytes:
    if headers.get("transfer-encoding", "").casefold() == "chunked":
        return await _read_chunked_body(reader, max_bytes=max_bytes)

    content_length = headers.get("content-length")
    if content_length is not None and content_length.isdigit() and int(content_length) > max_bytes:
        raise ActionError("這個頁面太大了，我先不自動讀取，避免 Telegram bot 卡住。")

    body = bytearray()
    while True:
        chunk = await reader.read(min(8192, max_bytes + 1 - len(body)))
        if not chunk:
            break
        body.extend(chunk)
        if len(body) > max_bytes:
            raise ActionError("這個頁面太大了，我先不自動讀取，避免 Telegram bot 卡住。")
    return bytes(body)


async def _read_chunked_body(reader: asyncio.StreamReader, *, max_bytes: int) -> bytes:
    body = bytearray()
    while True:
        size_line = await reader.readline()
        size_text = size_line.split(b";", maxsplit=1)[0].strip()
        try:
            size = int(size_text, 16)
        except ValueError as exc:
            raise ActionError("這個連結回傳了無效 chunked 回應，我目前讀不到內容。") from exc
        if size == 0:
            break
        if len(body) + size > max_bytes:
            raise ActionError("這個頁面太大了，我先不自動讀取，避免 Telegram bot 卡住。")
        body.extend(await reader.readexactly(size))
        await reader.readexactly(2)
    return bytes(body)


def _html_to_text(raw_html: str) -> str:
    parser = _TextExtractor()
    parser.feed(raw_html)
    return " ".join(parser.parts)


def _html_title(raw_html: str) -> str | None:
    match = re.search(r"<title[^>]*>(.*?)</title>", raw_html, flags=re.IGNORECASE | re.DOTALL)
    if match is None:
        return None
    return _collapse_whitespace(html.unescape(match.group(1))) or None


class _HTMLMetadataExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title_parts: list[str] = []
        self.in_title = False
        self.meta: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self.in_title = True
            return
        if tag != "meta":
            return
        values = {key.casefold(): value for key, value in attrs if value is not None}
        content = values.get("content")
        if not content:
            return
        key = values.get("property") or values.get("name")
        if key:
            self.meta[key.casefold()] = _collapse_whitespace(html.unescape(content))

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)

    @property
    def title(self) -> str | None:
        return _collapse_whitespace(html.unescape(" ".join(self.title_parts))) or None


def _html_metadata(raw_html: str) -> _HTMLMetadataExtractor:
    parser = _HTMLMetadataExtractor()
    parser.feed(raw_html)
    return parser


def _url_context_from_response(
    url: str,
    *,
    final_url: str,
    response: FetchedResponse,
    source_type: Literal["x_post", "webpage", "youtube", "unknown"],
    fetched_at: str,
    max_chars: int,
) -> UrlContext:
    if response.status_code >= 400:
        raise ActionError(f"這個連結回傳 HTTP {response.status_code}，我目前讀不到內容。")
    content_type = response.headers.get("content-type", "")
    if "text/html" not in content_type and "text/plain" not in content_type:
        raise ActionError("這個連結不是可讀取的文字或 HTML 內容。")

    raw_text = response.text
    title: str | None = None
    description: str | None = None
    author: str | None = None
    if "text/html" in content_type:
        metadata = _html_metadata(raw_text)
        title = metadata.meta.get("og:title") or metadata.meta.get("twitter:title") or metadata.title
        description = (
            metadata.meta.get("og:description")
            or metadata.meta.get("twitter:description")
            or metadata.meta.get("description")
        )
        author = metadata.meta.get("article:author") or metadata.meta.get("author")
        text = _collapse_whitespace(html.unescape(_html_to_text(raw_text)))[:max_chars]
    else:
        text = _collapse_whitespace(html.unescape(raw_text))[:max_chars]

    if source_type == "x_post" and _looks_like_x_blocker_page(text, title=title, description=description):
        raise ActionError("X 回傳的是 JavaScript/browser unsupported 頁面，不是貼文內容。")
    if (_is_reddit_url(url) or _is_reddit_url(final_url)) and _looks_like_reddit_blocker_page(
        text, title=title, description=description
    ):
        raise ActionError("Reddit 回傳的是驗證/反機器人頁面，不是貼文內容。")

    if source_type == "x_post" and author is None:
        x_parts = _x_status_parts(final_url) or _x_status_parts(url)
        if x_parts is not None:
            author = f"@{x_parts[0]}"

    if not text and not description and not title:
        raise ActionError("這個頁面沒有讀到可用內容。")

    status: Literal["success", "partial"] = "success" if text else "partial"
    return UrlContext(
        url=url,
        final_url=final_url,
        source_type=source_type,
        fetched_at=fetched_at,
        extraction_status=status,
        title=title,
        author=author,
        text=text or None,
        description=description,
    )


def _url_context_from_text(
    url: str,
    *,
    source_type: Literal["x_post", "webpage", "youtube", "unknown"],
    fetched_at: str,
    text: str,
    extraction_status: Literal["success", "partial"],
    error: str | None = None,
) -> UrlContext:
    x_parts = _x_status_parts(url)
    author = f"@{x_parts[0]}" if x_parts is not None else None
    return UrlContext(
        url=url,
        final_url=url,
        source_type=source_type,
        fetched_at=fetched_at,
        extraction_status=extraction_status,
        author=author,
        text=text[:6000],
        error=error,
    )


def _failed_url_context(
    url: str,
    *,
    source_type: Literal["x_post", "webpage", "youtube", "unknown"],
    fetched_at: str,
    primary_error: BaseException,
    fallback_error: BaseException,
) -> UrlContext:
    error = f"built-in fetch: {_readable_error(primary_error)}; kabigon: {_readable_error(fallback_error)}"
    if source_type != "x_post":
        return UrlContext(
            url=url,
            final_url=url,
            source_type=source_type,
            fetched_at=fetched_at,
            extraction_status="failed",
            error=error,
        )

    x_parts = _x_status_parts(url)
    if x_parts is None:
        return UrlContext(
            url=url,
            final_url=url,
            source_type=source_type,
            fetched_at=fetched_at,
            extraction_status="failed",
            error=error,
        )
    username, status_id = x_parts
    return UrlContext(
        url=url,
        final_url=url,
        source_type=source_type,
        fetched_at=fetched_at,
        extraction_status="partial",
        author=f"@{username}",
        text=f"X/Twitter status URL by @{username}, status id {status_id}. Full post text was not extracted.",
        error=error,
    )


def _collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
