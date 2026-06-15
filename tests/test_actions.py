from __future__ import annotations

import asyncio
from collections.abc import Sequence

import httpx
import pytest

from telegramagent.actions import ActionContent
from telegramagent.actions import ActionError
from telegramagent.actions import ActionRouter
from telegramagent.actions import ActionSettings
from telegramagent.actions import FetchedResponse
from telegramagent.actions import PendingActionStore
from telegramagent.actions import ProactiveActionTool
from telegramagent.actions import extract_url_context
from telegramagent.capabilities import Capability
from telegramagent.capabilities import CapabilityRegistry
from telegramagent.kabigon_tool import KabigonLoadError


class FakeAgent:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def reply(self, prompt: str, *, history: Sequence[tuple[str, str]]) -> str:
        self.prompts.append(prompt)
        return "整理完成"


class FailingAgent:
    async def reply(self, prompt: str, *, history: Sequence[tuple[str, str]]) -> str:
        raise httpx.ConnectError("LLM down")


class SlowTranscriptFetcher:
    async def fetch(self, video_id: str, *, languages: Sequence[str]) -> ActionContent:
        await asyncio.sleep(1)
        raise AssertionError("unreachable")


class FailingTranscriptFetcher:
    async def fetch(self, video_id: str, *, languages: Sequence[str]) -> ActionContent:
        raise RuntimeError("no transcript")


class FakeExternalLoader:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[str] = []

    async def fetch(self, url: str) -> ActionContent:
        self.calls.append(url)
        if self.fail:
            raise OSError("external loader failed")
        return ActionContent(title="external", source_url=url, body="外部 loader 內容", content_type="external_loader")


class FakeTranscriptFetcher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Sequence[str]]] = []

    async def fetch(self, video_id: str, *, languages: Sequence[str]) -> ActionContent:
        self.calls.append((video_id, languages))
        return ActionContent(
            title=f"video {video_id}",
            source_url=f"https://youtu.be/{video_id}",
            body="這是一段影片字幕。它會被拿來整理摘要。",
            content_type="youtube_transcript",
        )


def test_action_router_returns_structured_decisions_from_history_url() -> None:
    router = ActionRouter()
    pending = PendingActionStore()

    decision = router.route(
        "有字幕",
        chat_id=123,
        history=[("user", "https://www.youtube.com/watch?v=h_7fdZjUKE8"), ("assistant", "抓不到字幕")],
        pending=pending,
    )

    assert decision.kind == "execute"
    assert decision.action == "youtube_summary"
    assert decision.url == "https://www.youtube.com/watch?v=h_7fdZjUKE8"


@pytest.mark.asyncio
async def test_youtube_url_triggers_transcript_summary() -> None:
    agent = FakeAgent()
    fetcher = FakeTranscriptFetcher()
    tool = ProactiveActionTool(transcript_fetcher=fetcher)

    reply = await tool.handle("https://youtu.be/iG-hzh9roNw", chat_id=123, agent=agent, history=[])

    assert reply == "整理完成"
    assert fetcher.calls == [("iG-hzh9roNw", ("zh-Hant", "zh-TW", "zh", "ja", "en"))]
    assert "已經實際讀取到外部內容" in agent.prompts[0]
    assert "標題開頭必須剛好有一個 emoji" in agent.prompts[0]
    assert "最後一個 section 必須是收尾" in agent.prompts[0]
    assert "這是一段影片字幕" in agent.prompts[0]


@pytest.mark.asyncio
async def test_followup_go_uses_pending_youtube_url_without_asking_again() -> None:
    agent = FakeAgent()
    fetcher = FakeTranscriptFetcher()
    tool = ProactiveActionTool(transcript_fetcher=fetcher)

    await tool.handle("https://youtu.be/iG-hzh9roNw", chat_id=123, agent=agent, history=[])
    reply = await tool.handle("go", chat_id=123, agent=agent, history=[])

    assert reply == "整理完成"
    assert fetcher.calls == [
        ("iG-hzh9roNw", ("zh-Hant", "zh-TW", "zh", "ja", "en")),
        ("iG-hzh9roNw", ("zh-Hant", "zh-TW", "zh", "ja", "en")),
    ]


@pytest.mark.asyncio
async def test_followup_subtitle_and_kabigon_words_reuse_pending_youtube_url() -> None:
    agent = FakeAgent()
    fetcher = FakeTranscriptFetcher()
    tool = ProactiveActionTool(transcript_fetcher=fetcher)

    await tool.handle("https://www.youtube.com/watch?v=h_7fdZjUKE8", chat_id=123, agent=agent, history=[])
    await tool.handle("有字幕", chat_id=123, agent=agent, history=[])
    await tool.handle("你用 kabigon 抓抓看阿", chat_id=123, agent=agent, history=[])

    assert fetcher.calls == [
        ("h_7fdZjUKE8", ("zh-Hant", "zh-TW", "zh", "ja", "en")),
        ("h_7fdZjUKE8", ("zh-Hant", "zh-TW", "zh", "ja", "en")),
        ("h_7fdZjUKE8", ("zh-Hant", "zh-TW", "zh", "ja", "en")),
    ]


@pytest.mark.asyncio
async def test_youtube_fallback_uses_enabled_external_loader() -> None:
    agent = FakeAgent()
    external_loader = FakeExternalLoader()
    tool = ProactiveActionTool(
        capabilities=CapabilityRegistry([Capability("external_loader.kabigon", True, "test fallback")]),
        transcript_fetcher=FailingTranscriptFetcher(),
        external_loader=external_loader,
    )

    reply = await tool.handle("https://youtu.be/iG-hzh9roNw", chat_id=123, agent=agent, history=[])

    assert reply == "整理完成"
    assert external_loader.calls == ["https://youtu.be/iG-hzh9roNw"]
    assert "外部 loader 內容" in agent.prompts[0]


@pytest.mark.asyncio
async def test_youtube_fallback_reports_unavailable_external_loader_without_claiming_it_ran() -> None:
    agent = FakeAgent()
    tool = ProactiveActionTool(
        capabilities=CapabilityRegistry([Capability("external_loader.kabigon", False, "test fallback", "disabled")]),
        transcript_fetcher=FailingTranscriptFetcher(),
    )

    reply = await tool.handle("https://youtu.be/iG-hzh9roNw", chat_id=123, agent=agent, history=[])

    assert reply is not None
    assert "不是目前已啟用的 runtime capability" in reply
    assert agent.prompts == []


@pytest.mark.asyncio
async def test_youtube_transcript_fetch_is_bounded() -> None:
    agent = FakeAgent()
    tool = ProactiveActionTool(
        settings=ActionSettings(url_timeout_seconds=0.01),
        transcript_fetcher=SlowTranscriptFetcher(),
    )

    reply = await tool.handle("https://youtu.be/iG-hzh9roNw", chat_id=123, agent=agent, history=[])

    assert reply is not None
    assert "目前抓不到" in reply
    assert agent.prompts == []


@pytest.mark.asyncio
async def test_agent_failure_after_successful_fetch_returns_readable_error() -> None:
    fetcher = FakeTranscriptFetcher()
    tool = ProactiveActionTool(transcript_fetcher=fetcher)

    reply = await tool.handle("https://youtu.be/iG-hzh9roNw", chat_id=123, agent=FailingAgent(), history=[])

    assert reply == "AI 服務暫時無法使用, 請稍後再試。"


@pytest.mark.asyncio
async def test_invalid_youtube_url_is_not_remembered_for_followup() -> None:
    agent = FakeAgent()
    fetcher = FakeTranscriptFetcher()
    tool = ProactiveActionTool(transcript_fetcher=fetcher)

    first_reply = await tool.handle("https://www.youtube.com/@some-channel", chat_id=123, agent=agent, history=[])
    second_reply = await tool.handle("go", chat_id=123, agent=agent, history=[])

    assert first_reply is not None
    assert "YouTube 連結格式" in first_reply
    assert second_reply is None
    assert fetcher.calls == []


@pytest.mark.asyncio
async def test_generic_url_fetch_extracts_html_text(monkeypatch: pytest.MonkeyPatch) -> None:
    async def allow_host(host: str) -> None:
        assert host == "example.com"

    monkeypatch.setattr("telegramagent.actions._assert_public_host", allow_host)

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://example.com/page"
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text=(
                "<html><title>Example</title><body><script>bad()</script>"
                "<h1>Hello</h1><p>Article text</p></body></html>"
            ),
        )

    tool = ProactiveActionTool(
        settings=ActionSettings(max_extracted_chars=1000),
        http_client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    agent = FakeAgent()

    reply = await tool.handle("整理 https://example.com/page", chat_id=123, agent=agent, history=[])

    assert reply == "整理完成"
    assert "Example" in agent.prompts[0]
    assert "Hello Article text" in agent.prompts[0]
    assert "bad()" not in agent.prompts[0]


@pytest.mark.asyncio
async def test_generic_url_uses_kabigon_fallback_when_builtin_fetch_cannot_extract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def allow_host(host: str) -> None:
        assert host == "example.com"

    monkeypatch.setattr("telegramagent.actions._assert_public_host", allow_host)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/json"}, json={"ok": True})

    external_loader = FakeExternalLoader()
    tool = ProactiveActionTool(
        http_client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        capabilities=CapabilityRegistry([Capability("external_loader.kabigon", True, "test fallback")]),
        external_loader=external_loader,
    )
    agent = FakeAgent()

    reply = await tool.handle("整理 https://example.com/data.json", chat_id=123, agent=agent, history=[])

    assert reply == "整理完成"
    assert external_loader.calls == ["https://example.com/data.json"]
    assert "外部 loader 內容" in agent.prompts[0]


@pytest.mark.asyncio
async def test_x_url_uses_kabigon_fallback_when_builtin_fetch_returns_browser_blocker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://x.com/IEObserve/status/2058190539988898008?s=20"

    async def allow_host(host: str) -> None:
        assert host == "x.com"

    monkeypatch.setattr("telegramagent.actions._assert_public_host", allow_host)

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == url
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text=(
                "<html><title>X</title><body>"
                "JavaScript is not available. "
                "Please enable JavaScript or switch to a supported browser to continue using x.com."
                "</body></html>"
            ),
        )

    external_loader = FakeExternalLoader()
    tool = ProactiveActionTool(
        http_client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        capabilities=CapabilityRegistry([Capability("external_loader.kabigon", True, "test fallback")]),
        external_loader=external_loader,
    )
    agent = FakeAgent()

    reply = await tool.handle(url, chat_id=123, agent=agent, history=[])

    assert reply == "整理完成"
    assert external_loader.calls == [url]
    assert "外部 loader 內容" in agent.prompts[0]
    assert "JavaScript is not available" not in agent.prompts[0]


@pytest.mark.asyncio
async def test_reddit_url_uses_kabigon_fallback_when_builtin_fetch_returns_verification_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://www.reddit.com/r/SonyAlpha/comments/1f9xt2k/regrets_upgrading_to_a7cii/"

    async def allow_host(host: str) -> None:
        assert host == "www.reddit.com"

    monkeypatch.setattr("telegramagent.actions._assert_public_host", allow_host)

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == url
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text=(
                "<html><title>Reddit - Please wait for verification</title>"
                "<body>Please wait for verification</body></html>"
            ),
        )

    external_loader = FakeExternalLoader()
    tool = ProactiveActionTool(
        http_client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        capabilities=CapabilityRegistry([Capability("external_loader.kabigon", True, "test fallback")]),
        external_loader=external_loader,
    )
    agent = FakeAgent()

    reply = await tool.handle(url, chat_id=123, agent=agent, history=[])

    assert reply == "整理完成"
    assert external_loader.calls == [url]
    assert "外部 loader 內容" in agent.prompts[0]
    assert "Please wait for verification" not in agent.prompts[0]


@pytest.mark.asyncio
async def test_generic_url_blocks_localhost() -> None:
    tool = ProactiveActionTool()
    agent = FakeAgent()

    reply = await tool.handle("http://localhost/admin", chat_id=123, agent=agent, history=[])

    assert reply is not None
    assert "localhost、私有網路" in reply
    assert agent.prompts == []


@pytest.mark.asyncio
async def test_non_html_response_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    async def allow_host(host: str) -> None:
        assert host == "example.com"

    monkeypatch.setattr("telegramagent.actions._assert_public_host", allow_host)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/json"}, json={"ok": True})

    tool = ProactiveActionTool(http_client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    agent = FakeAgent()

    reply = await tool.handle("https://example.com/data.json", chat_id=123, agent=agent, history=[])

    assert reply is not None
    assert "不是可摘要的文字或 HTML" in reply
    assert agent.prompts == []


@pytest.mark.asyncio
async def test_oversized_response_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    async def allow_host(host: str) -> None:
        assert host == "example.com"

    monkeypatch.setattr("telegramagent.actions._assert_public_host", allow_host)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="x" * 1000)

    tool = ProactiveActionTool(
        settings=ActionSettings(max_extracted_chars=100),
        http_client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    agent = FakeAgent()

    reply = await tool.handle("https://example.com/large.txt", chat_id=123, agent=agent, history=[])

    assert reply is not None
    assert "頁面太大" in reply
    assert agent.prompts == []


@pytest.mark.asyncio
async def test_timeout_returns_readable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def allow_host(host: str) -> None:
        assert host == "example.com"

    monkeypatch.setattr("telegramagent.actions._assert_public_host", allow_host)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout")

    tool = ProactiveActionTool(http_client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    agent = FakeAgent()

    reply = await tool.handle("https://example.com/slow", chat_id=123, agent=agent, history=[])

    assert reply is not None
    assert "目前抓不到" in reply
    assert agent.prompts == []


@pytest.mark.asyncio
async def test_invalid_youtube_url_returns_readable_error() -> None:
    tool = ProactiveActionTool()
    agent = FakeAgent()

    reply = await tool.handle("https://www.youtube.com/@some-channel", chat_id=123, agent=agent, history=[])

    assert reply is not None
    assert "YouTube 連結格式" in reply
    assert agent.prompts == []


@pytest.mark.asyncio
async def test_risky_action_requires_confirmation() -> None:
    tool = ProactiveActionTool()
    agent = FakeAgent()

    english_reply = await tool.handle("delete https://example.com/account", chat_id=123, agent=agent, history=[])
    chinese_reply = await tool.handle("請幫我付款 https://example.com/checkout", chat_id=123, agent=agent, history=[])

    assert english_reply is not None
    assert "不會自動執行有副作用" in english_reply
    assert chinese_reply is not None
    assert "不會自動執行有副作用" in chinese_reply
    assert agent.prompts == []


@pytest.mark.asyncio
async def test_redirect_is_not_followed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def allow_host(host: str) -> None:
        assert host == "example.com"

    monkeypatch.setattr("telegramagent.actions._assert_public_host", allow_host)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://localhost/admin"})

    tool = ProactiveActionTool(http_client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    agent = FakeAgent()

    reply = await tool.handle("https://example.com/redirect", chat_id=123, agent=agent, history=[])

    assert reply is not None
    assert "不自動跟隨 redirect" in reply
    assert agent.prompts == []


@pytest.mark.asyncio
async def test_non_actionable_text_returns_none() -> None:
    tool = ProactiveActionTool()
    agent = FakeAgent()

    assert await tool.handle("今天好累", chat_id=123, agent=agent, history=[]) is None


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

    monkeypatch.setattr("telegramagent.actions._fetch_public_url_follow_redirects", fetch)

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

    monkeypatch.setattr("telegramagent.actions._fetch_public_url_follow_redirects", fetch)
    monkeypatch.setattr("telegramagent.actions.load_url_with_kabigon", load)

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

    monkeypatch.setattr("telegramagent.actions._fetch_public_url_follow_redirects", fetch)
    monkeypatch.setattr("telegramagent.actions.load_url_with_kabigon", load)

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

    monkeypatch.setattr("telegramagent.actions._fetch_public_url_follow_redirects", fetch)
    monkeypatch.setattr("telegramagent.actions.load_url_with_kabigon", load)

    context = await extract_url_context("https://example.com/slow")

    assert context.source_type == "webpage"
    assert context.extraction_status == "failed"
    assert context.error == "built-in fetch: timeout; kabigon: kabigon timeout"
