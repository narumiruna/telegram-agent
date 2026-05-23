from __future__ import annotations

import asyncio
import html
import ipaddress
import re
import socket
import ssl
import time
from collections.abc import Callable
from collections.abc import Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Literal
from typing import Protocol
from urllib.parse import parse_qs
from urllib.parse import urlparse
from urllib.parse import urlunparse

import httpx
from loguru import logger

from telegramagent.capabilities import CapabilityRegistry
from telegramagent.kabigon_tool import KabigonLoadError
from telegramagent.kabigon_tool import load_url_with_kabigon


@dataclass(frozen=True)
class ActionSettings:
    enabled: bool = True
    url_timeout_seconds: float = 15.0
    max_extracted_chars: int = 12000
    pending_ttl_seconds: int = 900
    allowed_schemes: frozenset[str] = frozenset({"http", "https"})
    youtube_languages: tuple[str, ...] = ("zh-Hant", "zh-TW", "zh", "ja", "en")
    external_loader_timeout_seconds: float = 180.0


@dataclass(frozen=True)
class PendingAction:
    kind: str
    url: str
    created_at: float


@dataclass(frozen=True)
class ActionContent:
    title: str
    source_url: str
    body: str
    content_type: str


@dataclass(frozen=True)
class ActionDecision:
    kind: Literal["answer", "execute", "ask", "confirm", "queue", "fallback_failed"]
    action: str = ""
    url: str = ""
    message: str = ""


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


class Agent(Protocol):
    async def reply(self, prompt: str, *, history: Sequence[tuple[str, str]]) -> str: ...


class TranscriptFetcher(Protocol):
    async def fetch(self, video_id: str, *, languages: Sequence[str]) -> ActionContent: ...


class ExternalLoader(Protocol):
    async def fetch(self, url: str) -> ActionContent: ...


class PendingActionStore:
    def __init__(self, *, ttl_seconds: int = 900, max_chats: int = 1000) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_chats = max_chats
        self._items: dict[int, PendingAction] = {}

    def remember(self, chat_id: int, *, kind: str, url: str) -> None:
        self._items[chat_id] = PendingAction(kind=kind, url=url, created_at=time.monotonic())
        if len(self._items) > self.max_chats:
            oldest_chat_id = min(self._items, key=lambda key: self._items[key].created_at)
            self._items.pop(oldest_chat_id, None)

    def get(self, chat_id: int) -> PendingAction | None:
        action = self._items.get(chat_id)
        if action is None:
            return None
        if time.monotonic() - action.created_at > self.ttl_seconds:
            self._items.pop(chat_id, None)
            return None
        return action

    def clear(self, chat_id: int) -> None:
        self._items.pop(chat_id, None)


class DefaultTranscriptFetcher:
    async def fetch(self, video_id: str, *, languages: Sequence[str]) -> ActionContent:
        return await asyncio.to_thread(_fetch_youtube_transcript, video_id, tuple(languages))


class KabigonExternalLoader:
    def __init__(self, *, timeout_seconds: float = 180.0, max_chars: int = 20000) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_chars = max_chars

    async def fetch(self, url: str) -> ActionContent:
        try:
            body = await load_url_with_kabigon(url, timeout_seconds=self.timeout_seconds, max_chars=self.max_chars)
        except KabigonLoadError as exc:
            raise ActionError(str(exc)) from exc
        return ActionContent(title="kabigon loaded content", source_url=url, body=body, content_type="kabigon_load_url")


class ActionRouter:
    def __init__(self, *, capabilities: CapabilityRegistry | None = None) -> None:
        self.capabilities = capabilities or CapabilityRegistry()

    def route(
        self,
        text: str,
        *,
        chat_id: int,
        history: Sequence[tuple[str, str]],
        pending: PendingActionStore,
    ) -> ActionDecision:
        url = _first_url(text)
        if url is not None:
            confirmation = _confirmation_required_reason(text)
            if confirmation is not None:
                return ActionDecision(kind="confirm", message=confirmation)
            if _is_youtube_url(url):
                if _youtube_video_id(url) is None:
                    pending.clear(chat_id)
                    return ActionDecision(
                        kind="ask",
                        message=(
                            "這個 YouTube 連結格式我讀不到，"
                            "請貼一般的 youtube.com/watch、youtube.com/shorts 或 youtu.be 連結。"
                        ),
                    )
                action = "youtube_summary"
            else:
                action = "url_summary"
            pending.remember(chat_id, kind=action, url=url)
            return ActionDecision(kind="execute", action=action, url=url)

        if _is_followup_trigger(text):
            action = pending.get(chat_id)
            if action is None:
                inferred_url = _latest_url_from_history(history)
                if inferred_url is None:
                    return ActionDecision(kind="answer")
                action_kind = "youtube_summary" if _is_youtube_url(inferred_url) else "url_summary"
                pending.remember(chat_id, kind=action_kind, url=inferred_url)
                action = pending.get(chat_id)
            if action is None:
                return ActionDecision(kind="answer")
            return ActionDecision(kind="execute", action=action.kind, url=action.url)

        return ActionDecision(kind="answer")


class ProactiveActionTool:
    def __init__(
        self,
        *,
        settings: ActionSettings | None = None,
        pending: PendingActionStore | None = None,
        transcript_fetcher: TranscriptFetcher | None = None,
        http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
        capabilities: CapabilityRegistry | None = None,
        external_loader: ExternalLoader | None = None,
        router: ActionRouter | None = None,
    ) -> None:
        self.settings = settings or ActionSettings()
        self.pending = pending or PendingActionStore(ttl_seconds=self.settings.pending_ttl_seconds)
        self.transcript_fetcher = transcript_fetcher or DefaultTranscriptFetcher()
        self.http_client_factory = http_client_factory
        self.capabilities = capabilities or CapabilityRegistry()
        self.external_loader = external_loader
        self.router = router or ActionRouter(capabilities=self.capabilities)

    async def handle(
        self,
        text: str,
        *,
        chat_id: int,
        agent: Agent,
        history: Sequence[tuple[str, str]],
    ) -> str | None:
        if not self.settings.enabled:
            return None

        decision = self.router.route(text, chat_id=chat_id, history=history, pending=self.pending)
        if decision.kind == "answer":
            return None
        if decision.kind in {"ask", "confirm", "fallback_failed"}:
            return decision.message
        if decision.kind in {"execute", "queue"}:
            return await self._execute(kind=decision.action, url=decision.url, agent=agent, history=history)
        return None

    async def _execute(
        self,
        *,
        kind: str,
        url: str,
        agent: Agent,
        history: Sequence[tuple[str, str]],
    ) -> str:
        try:
            if kind == "youtube_summary":
                content = await self._fetch_youtube_with_fallback(url)
            else:
                content = await self._fetch_url_with_fallback(url)
        except ActionError as exc:
            return str(exc)
        except (httpx.HTTPError, OSError, TimeoutError) as exc:
            logger.warning("Proactive action failed with {}", type(exc).__name__)
            return "我有嘗試讀取內容，但目前抓不到。可能是網站阻擋、網路逾時，或影片沒有可用字幕。"

        prompt = _build_summary_prompt(content, max_chars=self.settings.max_extracted_chars)
        try:
            return await agent.reply(prompt, history=history)
        except httpx.HTTPError:
            logger.exception("LLM request failed after proactive action")
            return "AI 服務暫時無法使用, 請稍後再試。"

    async def _fetch_youtube_with_fallback(self, url: str) -> ActionContent:
        try:
            return await self._fetch_youtube(url)
        except ActionError as exc:
            try:
                return await self._fetch_external_loader(url, primary_error=exc)
            except ActionError as fallback_exc:
                if self._external_loader_enabled():
                    raise fallback_exc from exc
                raise ActionError(
                    f"{exc}\n我已保留前面的 YouTube 連結，但外部 loader（例如 kabigon）"
                    "不是目前已啟用的 runtime capability。如果你有字幕文字，可以直接貼上，我會接著整理。"
                ) from exc

    async def _fetch_url_with_fallback(self, url: str) -> ActionContent:
        try:
            return await self._fetch_url(url)
        except ActionError as exc:
            if not self._should_try_external_loader_after_action_error(exc):
                raise
            return await self._fetch_external_loader(url, primary_error=exc)
        except (httpx.HTTPError, OSError, TimeoutError) as exc:
            if not self._external_loader_enabled():
                raise
            return await self._fetch_external_loader(url, primary_error=exc)

    async def _fetch_external_loader(self, url: str, *, primary_error: BaseException) -> ActionContent:
        if not self._external_loader_enabled() or self.external_loader is None:
            if isinstance(primary_error, ActionError):
                raise primary_error
            raise ActionError("我有嘗試讀取內容，但目前抓不到。可能是網站阻擋或網路逾時。") from primary_error
        try:
            return await asyncio.wait_for(
                self.external_loader.fetch(url), timeout=self.settings.external_loader_timeout_seconds
            )
        except (ActionError, OSError, TimeoutError) as fallback_exc:
            raise ActionError(
                "我有嘗試用內建讀取與 kabigon 讀取，但目前都抓不到。"
                f"內建讀取失敗原因：{_readable_error(primary_error)}；"
                f"kabigon 失敗原因：{_readable_error(fallback_exc)}。"
                "可能是網站阻擋、需要登入/paywall、需要 Playwright browser assets，或網路逾時。"
            ) from fallback_exc

    def _external_loader_enabled(self) -> bool:
        return self.capabilities.is_available("external_loader.kabigon") and self.external_loader is not None

    @staticmethod
    def _should_try_external_loader_after_action_error(exc: ActionError) -> bool:
        message = str(exc)
        hard_stop_fragments = (
            "只能讀取 http 或 https",
            "沒有有效主機名稱",
            "localhost、私有網路",
            "不自動跟隨 redirect",
        )
        return not any(fragment in message for fragment in hard_stop_fragments)

    async def _fetch_youtube(self, url: str) -> ActionContent:
        video_id = _youtube_video_id(url)
        if video_id is None:
            raise ActionError("這個 YouTube 連結格式我讀不到，請貼一般的 youtube.com/watch 或 youtu.be 連結。")
        try:
            content = await asyncio.wait_for(
                self.transcript_fetcher.fetch(video_id, languages=self.settings.youtube_languages),
                timeout=self.settings.url_timeout_seconds,
            )
        except Exception as exc:
            raise ActionError(
                "我有找到 YouTube 影片，但目前抓不到可用字幕；可能是字幕關閉、影片受限，或 YouTube 擋住伺服器 IP。"
            ) from exc
        if len(content.body) > self.settings.max_extracted_chars:
            return ActionContent(
                title=content.title,
                source_url=content.source_url,
                body=content.body[: self.settings.max_extracted_chars],
                content_type=content.content_type,
            )
        return content

    async def _fetch_url(self, url: str) -> ActionContent:
        parsed = urlparse(url)
        if parsed.scheme.casefold() not in self.settings.allowed_schemes:
            raise ActionError("我只能讀取 http 或 https 連結，其他協定先不自動處理。")
        host = parsed.hostname
        if host is None:
            raise ActionError("這個連結沒有有效主機名稱，我沒辦法自動讀取。")
        if self.http_client_factory is None:
            response = await _fetch_public_url(
                url,
                timeout_seconds=self.settings.url_timeout_seconds,
                max_bytes=self.settings.max_extracted_chars * 8,
            )
        else:
            await _assert_public_host(host)
            async with self.http_client_factory() as client:
                httpx_response = await client.get(url)
            response = FetchedResponse(
                status_code=httpx_response.status_code,
                headers={key.casefold(): value for key, value in httpx_response.headers.items()},
                content=httpx_response.content,
            )

        if 300 <= response.status_code < 400:
            raise ActionError("這個連結會重新導向。為了避免 SSRF/跳轉風險，我先不自動跟隨 redirect。")
        if response.status_code >= 400:
            raise ActionError(f"這個連結回傳 HTTP {response.status_code}，我目前讀不到內容。")
        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type and "text/plain" not in content_type:
            raise ActionError("這個連結不是可摘要的文字或 HTML 內容，我先不自動讀取。")
        if len(response.content) > self.settings.max_extracted_chars * 8:
            raise ActionError("這個頁面太大了，我先不自動讀取，避免 Telegram bot 卡住。")

        raw_text = response.text
        text = _html_to_text(raw_text) if "text/html" in content_type else raw_text
        text = _collapse_whitespace(html.unescape(text))[: self.settings.max_extracted_chars]
        title = _html_title(raw_text) or parsed.netloc
        if not text:
            raise ActionError("這個頁面沒有讀到可摘要的文字內容。")
        return ActionContent(title=title, source_url=url, body=text, content_type="web_page")


class ActionError(RuntimeError):
    pass


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip_depth += 1
        if tag in {"p", "br", "div", "section", "article", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self.skip_depth > 0:
            self.skip_depth -= 1
        if tag in {"p", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_depth == 0 and data.strip():
            self.parts.append(data)


_URL_RE = re.compile(r"https?://[^\s<>()]+", flags=re.IGNORECASE)
_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "www.youtu.be"}
_FOLLOWUP_RE = re.compile(
    r"^(go|開始|執行|繼續|做|自動做|你就自動做事|整理|摘要|好|好呀|ok|okay|有字幕|抓抓看|抓字幕|用\s*kabigon.*)\s*[.!！。]*$",
    re.IGNORECASE,
)
_RISKY_ACTION_RE = re.compile(
    r"(?:\b(?:delete|buy|purchase|send|deploy|login|sign\s*in)\b|刪除|購買|下單|付款|發送|寄出|部署|登入|修改|提交)",
    re.IGNORECASE,
)


def _readable_error(exc: BaseException) -> str:
    text = str(exc).strip()
    if not text:
        text = type(exc).__name__
    text = _collapse_whitespace(text)
    if len(text) > 180:
        return f"{text[:180]}…"
    return text


def _first_url(text: str) -> str | None:
    match = _URL_RE.search(text)
    if match is None:
        return None
    return match.group(0).rstrip(".,，。!！?)）]")


def _is_followup_trigger(text: str) -> bool:
    stripped = text.strip()
    lowered = stripped.casefold()
    return (
        _FOLLOWUP_RE.match(stripped) is not None or "kabigon" in lowered or "抓字幕" in stripped or "抓抓看" in stripped
    )


def _confirmation_required_reason(text: str) -> str | None:
    if _RISKY_ACTION_RE.search(text) is None:
        return None
    return (
        "這看起來可能需要登入、付款、送出資料或造成外部變更。"
        "請明確確認要我做哪個安全的讀取/整理動作；我不會自動執行有副作用的操作。"
    )


def _is_youtube_url(url: str) -> bool:
    return (urlparse(url).hostname or "").casefold() in _YOUTUBE_HOSTS


def _latest_url_from_history(history: Sequence[tuple[str, str]]) -> str | None:
    for _role, content in reversed(history):
        url = _first_url(content)
        if url is not None:
            return url
    return None


def _youtube_video_id(url: str) -> str | None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    if host not in _YOUTUBE_HOSTS:
        return None
    if host.endswith("youtu.be"):
        video_id = parsed.path.strip("/").split("/", maxsplit=1)[0]
        return video_id or None
    query_video_id = parse_qs(parsed.query).get("v", [None])[0]
    if query_video_id:
        return query_video_id
    if parsed.path.startswith("/shorts/") or parsed.path.startswith("/embed/"):
        return parsed.path.strip("/").split("/", maxsplit=1)[1]
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


async def _assert_public_host(host: str) -> None:
    await _resolve_public_addresses(host)


async def _resolve_public_addresses(host: str) -> list[str]:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ActionError("這個連結的主機名稱解析失敗，我沒辦法自動讀取。") from exc

    addresses = sorted({info[4][0] for info in infos})
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


def _collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _fetch_youtube_transcript(video_id: str, languages: tuple[str, ...]) -> ActionContent:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        from youtube_transcript_api.formatters import TextFormatter
    except ImportError as exc:  # pragma: no cover - dependency is declared in pyproject
        raise ActionError("YouTube 字幕工具尚未安裝，暫時不能自動整理影片。") from exc

    try:
        api = YouTubeTranscriptApi()
        transcript = api.fetch(video_id, languages=list(languages))
        body = TextFormatter().format_transcript(transcript)
    except Exception as exc:
        logger.warning("YouTube transcript fetch failed for video_id={} with {}", video_id, type(exc).__name__)
        raise ActionError(
            "我有找到 YouTube 影片，但目前抓不到可用字幕；可能是字幕關閉、影片受限，或 YouTube 擋住伺服器 IP。"
        ) from exc

    body = _collapse_whitespace(body)
    if not body:
        raise ActionError("我有找到 YouTube 影片，但字幕內容是空的。")
    return ActionContent(
        title=f"YouTube video {video_id}",
        source_url=f"https://youtu.be/{video_id}",
        body=body,
        content_type="youtube_transcript",
    )


def _build_summary_prompt(content: ActionContent, *, max_chars: int) -> str:
    body = content.body[:max_chars]
    return (
        "你已經實際讀取到外部內容。請根據下方工具結果，用台灣繁體中文主動整理。\n"
        "不要說你還沒讀到內容；如果內容不足，直接說明限制。\n"
        "輸出格式：1) 一句話總結 2) 重點條列 3) 如果是影片/文章，列出值得注意的細節。\n\n"
        f"來源標題: {content.title}\n"
        f"來源網址: {content.source_url}\n"
        f"內容類型: {content.content_type}\n\n"
        f"已擷取內容:\n{body}"
    )
