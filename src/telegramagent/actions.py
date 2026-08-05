from __future__ import annotations

import asyncio
import html
import re
import time
from collections.abc import Callable
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal
from typing import Protocol
from urllib.parse import parse_qs
from urllib.parse import urljoin
from urllib.parse import urlparse

import httpx
from loguru import logger
from mcp.shared.exceptions import McpError
from pydantic_ai.exceptions import AgentRunError

from telegramagent.capabilities import CapabilityRegistry
from telegramagent.kabigon_tool import KabigonLoadError
from telegramagent.kabigon_tool import load_url_with_kabigon
from telegramagent.url_context import _YOUTUBE_HOSTS
from telegramagent.url_context import ActionError
from telegramagent.url_context import FetchedResponse
from telegramagent.url_context import UrlContext
from telegramagent.url_context import _assert_public_host
from telegramagent.url_context import _blocker_error_for_url
from telegramagent.url_context import _collapse_whitespace
from telegramagent.url_context import _fetch_public_url_follow_redirects
from telegramagent.url_context import _html_title
from telegramagent.url_context import _html_to_text
from telegramagent.url_context import _is_x_status_url
from telegramagent.url_context import _is_youtube_url
from telegramagent.url_context import _looks_like_x_blocker_page
from telegramagent.url_context import _readable_error
from telegramagent.url_context import extract_url_context

__all__ = ["ActionError", "FetchedResponse", "UrlContext", "extract_url_context"]


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
        if _is_x_status_url(url) and _looks_like_x_blocker_page(body):
            raise ActionError("kabigon 讀到的是 X 的 JavaScript/browser unsupported 頁面，不是貼文內容。")
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
        except httpx.HTTPError, AgentRunError, McpError:
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
            "重新導向但沒有提供 Location",
            "重新導向到不支援或無效",
            "重新導向太多次",
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
            final_url, response = await _fetch_public_url_follow_redirects(
                url,
                timeout_seconds=self.settings.url_timeout_seconds,
                max_bytes=self.settings.max_extracted_chars * 8,
            )
        else:
            final_url, response = await self._fetch_url_with_http_client_follow_redirects(url)

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
        final_parsed = urlparse(final_url)
        title = _html_title(raw_text) or final_parsed.netloc or parsed.netloc
        blocker_error = _blocker_error_for_url(url, text, title=title) or _blocker_error_for_url(
            final_url, text, title=title
        )
        if blocker_error is not None:
            raise ActionError(blocker_error)
        if not text:
            raise ActionError("這個頁面沒有讀到可摘要的文字內容。")
        return ActionContent(title=title, source_url=final_url, body=text, content_type="web_page")

    async def _fetch_url_with_http_client_follow_redirects(
        self, url: str, *, max_redirects: int = 3
    ) -> tuple[str, FetchedResponse]:
        http_client_factory = self.http_client_factory
        if http_client_factory is None:
            raise ActionError("HTTP client factory is not configured.")

        current_url = url
        async with http_client_factory() as client:
            for _redirect_count in range(max_redirects + 1):
                parsed = urlparse(current_url)
                if parsed.scheme.casefold() not in self.settings.allowed_schemes or parsed.hostname is None:
                    raise ActionError("這個連結重新導向到不支援或無效的 URL。")
                await _assert_public_host(parsed.hostname)

                httpx_response = await client.get(current_url, follow_redirects=False)
                response = FetchedResponse(
                    status_code=httpx_response.status_code,
                    headers={key.casefold(): value for key, value in httpx_response.headers.items()},
                    content=httpx_response.content,
                )
                if not 300 <= response.status_code < 400:
                    return current_url, response

                location = response.headers.get("location")
                if not location:
                    raise ActionError("這個連結重新導向但沒有提供 Location header。")
                next_url = urljoin(current_url, location)
                next_parsed = urlparse(next_url)
                if next_parsed.scheme.casefold() not in self.settings.allowed_schemes or next_parsed.hostname is None:
                    raise ActionError("這個連結重新導向到不支援或無效的 URL。")
                await _assert_public_host(next_parsed.hostname)
                current_url = next_url

        raise ActionError("這個連結重新導向太多次，我先不自動讀取。")


_URL_RE = re.compile(r"https?://[^\s<>()]+", flags=re.IGNORECASE)
_FOLLOWUP_RE = re.compile(
    r"^(go|開始|執行|繼續|做|自動做|你就自動做事|整理|摘要|好|好呀|ok|okay|有字幕|抓抓看|抓字幕|用\s*kabigon.*)\s*[.!！。]*$",
    re.IGNORECASE,
)
_RISKY_ACTION_RE = re.compile(
    r"(?:\b(?:delete|buy|purchase|send|deploy|login|sign\s*in)\b|刪除|購買|下單|付款|發送|寄出|部署|登入|修改|提交)",
    re.IGNORECASE,
)


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
        "輸出成 3 到 5 個連貫 section。每個 section 標題都要具體、使用台灣繁體中文，"
        "且標題開頭必須剛好有一個 emoji。每個 section 內文可以有一段或多段，"
        "section 之間轉折要自然，整體要像同一篇 cohesive post。"
        "最後一個 section 必須是收尾，只能重述前面已提過的重點，不要加入新資訊。\n\n"
        f"來源標題: {content.title}\n"
        f"來源網址: {content.source_url}\n"
        f"內容類型: {content.content_type}\n\n"
        f"已擷取內容:\n{body}"
    )
