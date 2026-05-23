from __future__ import annotations

import asyncio
import html
import ipaddress
import re
import socket
import time
from collections.abc import Callable
from collections.abc import Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import parse_qs
from urllib.parse import urlparse

import httpx
from loguru import logger


@dataclass(frozen=True)
class ActionSettings:
    enabled: bool = True
    url_timeout_seconds: float = 15.0
    max_extracted_chars: int = 12000
    pending_ttl_seconds: int = 900
    allowed_schemes: frozenset[str] = frozenset({"http", "https"})
    youtube_languages: tuple[str, ...] = ("zh-Hant", "zh-TW", "zh", "ja", "en")


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


class Agent(Protocol):
    async def reply(self, prompt: str, *, history: Sequence[tuple[str, str]]) -> str: ...


class TranscriptFetcher(Protocol):
    async def fetch(self, video_id: str, *, languages: Sequence[str]) -> ActionContent: ...


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


class ProactiveActionTool:
    def __init__(
        self,
        *,
        settings: ActionSettings | None = None,
        pending: PendingActionStore | None = None,
        transcript_fetcher: TranscriptFetcher | None = None,
        http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self.settings = settings or ActionSettings()
        self.pending = pending or PendingActionStore(ttl_seconds=self.settings.pending_ttl_seconds)
        self.transcript_fetcher = transcript_fetcher or DefaultTranscriptFetcher()
        self.http_client_factory = http_client_factory

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

        url = _first_url(text)
        if url is not None:
            confirmation = _confirmation_required_reason(text)
            if confirmation is not None:
                return confirmation
            kind = "youtube_summary" if _is_youtube_url(url) else "url_summary"
            self.pending.remember(chat_id, kind=kind, url=url)
            return await self._execute(kind=kind, url=url, agent=agent, history=history)

        if _is_followup_trigger(text):
            pending = self.pending.get(chat_id)
            if pending is None:
                return None
            return await self._execute(kind=pending.kind, url=pending.url, agent=agent, history=history)

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
                content = await self._fetch_youtube(url)
            else:
                content = await self._fetch_url(url)
        except ActionError as exc:
            return str(exc)
        except (httpx.HTTPError, OSError) as exc:
            logger.warning("Proactive action failed with {}", type(exc).__name__)
            return "我有嘗試讀取內容，但目前抓不到。可能是網站阻擋、網路逾時，或影片沒有可用字幕。"

        prompt = _build_summary_prompt(content, max_chars=self.settings.max_extracted_chars)
        return await agent.reply(prompt, history=history)

    async def _fetch_youtube(self, url: str) -> ActionContent:
        video_id = _youtube_video_id(url)
        if video_id is None:
            raise ActionError("這個 YouTube 連結格式我讀不到，請貼一般的 youtube.com/watch 或 youtu.be 連結。")
        content = await self.transcript_fetcher.fetch(video_id, languages=self.settings.youtube_languages)
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
        await _assert_public_host(host)

        if self.http_client_factory is None:
            async with httpx.AsyncClient(timeout=self.settings.url_timeout_seconds, follow_redirects=False) as client:
                response = await client.get(url)
        else:
            async with self.http_client_factory() as client:
                response = await client.get(url)

        if 300 <= response.status_code < 400:
            raise ActionError("這個連結會重新導向。為了避免 SSRF/跳轉風險，我先不自動跟隨 redirect。")
        response.raise_for_status()
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
    r"^(go|開始|執行|繼續|做|自動做|你就自動做事|整理|摘要|好|好呀|ok|okay)\s*[.!！。]*$", re.IGNORECASE
)
_RISKY_ACTION_RE = re.compile(
    r"\b(delete|buy|purchase|send|deploy|login|sign\s*in|刪除|購買|下單|付款|發送|寄出|部署|登入|修改|提交)\b",
    re.IGNORECASE,
)


def _first_url(text: str) -> str | None:
    match = _URL_RE.search(text)
    if match is None:
        return None
    return match.group(0).rstrip(".,，。!！?)）]")


def _is_followup_trigger(text: str) -> bool:
    return _FOLLOWUP_RE.match(text.strip()) is not None


def _confirmation_required_reason(text: str) -> str | None:
    if _RISKY_ACTION_RE.search(text) is None:
        return None
    return (
        "這看起來可能需要登入、付款、送出資料或造成外部變更。"
        "請明確確認要我做哪個安全的讀取/整理動作；我不會自動執行有副作用的操作。"
    )


def _is_youtube_url(url: str) -> bool:
    return (urlparse(url).hostname or "").casefold() in _YOUTUBE_HOSTS


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


async def _assert_public_host(host: str) -> None:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ActionError("這個連結的主機名稱解析失敗，我沒辦法自動讀取。") from exc

    addresses = {info[4][0] for info in infos}
    if not addresses:
        raise ActionError("這個連結沒有解析到可用 IP，我沒辦法自動讀取。")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ActionError("基於安全限制，我不會自動讀取 localhost、私有網路或雲端 metadata 位址。")


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
