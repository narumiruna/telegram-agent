from __future__ import annotations

import os
import re

import httpx

from telegramagent.observability import LogfireConfig
from telegramagent.observability import _redact_httpx_async_request_span
from telegramagent.observability import _redact_httpx_request_span
from telegramagent.observability import configure_logfire


class FakeLogfire:
    def __init__(self) -> None:
        self.calls = []

    def configure(self, **kwargs) -> None:
        self.calls.append(("configure", kwargs))

    def instrument_httpx(self, **kwargs) -> None:
        self.calls.append(("instrument_httpx", kwargs))

    def instrument_pydantic_ai(self, **kwargs) -> None:
        self.calls.append(("instrument_pydantic_ai", kwargs))

    def instrument_mcp(self) -> None:
        self.calls.append(("instrument_mcp", {}))

    def loguru_handler(self):
        self.calls.append(("loguru_handler", {}))
        return {"sink": "fake-sink", "format": "{message}"}


class FakeLogger:
    def __init__(self) -> None:
        self.add_calls = []
        self.info_calls = []

    def add(self, **kwargs) -> None:
        self.add_calls.append(kwargs)

    def info(self, *args) -> None:
        self.info_calls.append(args)


def test_configure_logfire_noops_without_token(monkeypatch) -> None:
    fake_logfire = FakeLogfire()
    monkeypatch.setattr("telegramagent.observability.logfire", fake_logfire)

    assert configure_logfire(LogfireConfig(token=None)) is False

    assert fake_logfire.calls == []


def test_configure_logfire_sets_integrations(monkeypatch) -> None:
    fake_logfire = FakeLogfire()
    fake_logger = FakeLogger()
    monkeypatch.delenv("OPENAI_AGENTS_DISABLE_TRACING", raising=False)
    monkeypatch.delenv("OTEL_PYTHON_HTTPX_EXCLUDED_URLS", raising=False)
    monkeypatch.setattr("telegramagent.observability.logfire", fake_logfire)
    monkeypatch.setattr("telegramagent.observability.logger", fake_logger)

    enabled = configure_logfire(
        LogfireConfig(token="secret-token", environment="prod", service_name="bot", include_content=True),
        verbose=True,
    )

    assert enabled is True
    assert [name for name, _ in fake_logfire.calls] == [
        "configure",
        "instrument_httpx",
        "instrument_pydantic_ai",
        "instrument_mcp",
        "loguru_handler",
    ]
    assert fake_logfire.calls[0][1] == {
        "token": "secret-token",
        "send_to_logfire": True,
        "service_name": "bot",
        "environment": "prod",
        "console": False,
        "inspect_arguments": False,
        "scrubbing": fake_logfire.calls[0][1]["scrubbing"],
    }
    scrub_patterns = fake_logfire.calls[0][1]["scrubbing"].extra_patterns
    assert scrub_patterns is not None
    assert any(
        re.search(pattern, "request to https://mcp.firecrawl.dev/fc-secret/key/v2/mcp failed")
        for pattern in scrub_patterns
    )
    assert fake_logfire.calls[1][1] == {
        "capture_headers": False,
        "capture_request_body": False,
        "capture_response_body": False,
        "request_hook": _redact_httpx_request_span,
        "async_request_hook": _redact_httpx_async_request_span,
    }
    assert fake_logfire.calls[2][1] == {"include_content": True}
    assert fake_logger.add_calls == [{"sink": "fake-sink", "format": "{message}", "level": "DEBUG"}]
    assert fake_logger.info_calls
    assert "api\\.telegram\\.org/bot" in os.environ["OTEL_PYTHON_HTTPX_EXCLUDED_URLS"]
    assert os.environ["OPENAI_AGENTS_DISABLE_TRACING"] == "1"


def test_configure_logfire_preserves_existing_httpx_excluded_urls(monkeypatch) -> None:
    fake_logfire = FakeLogfire()
    fake_logger = FakeLogger()
    monkeypatch.setenv("OTEL_PYTHON_HTTPX_EXCLUDED_URLS", "https://example.invalid/.*")
    monkeypatch.setattr("telegramagent.observability.logfire", fake_logfire)
    monkeypatch.setattr("telegramagent.observability.logger", fake_logger)

    assert configure_logfire(LogfireConfig(token="secret-token")) is True

    excluded_urls = os.environ["OTEL_PYTHON_HTTPX_EXCLUDED_URLS"].split(",")
    assert excluded_urls == ["https://example.invalid/.*", "https://api\\.telegram\\.org/bot.*"]


class FakeSpan:
    def __init__(self) -> None:
        self.attributes = {}

    def set_attribute(self, key: str, value: str) -> None:
        self.attributes[key] = value


class FakeRequest:
    def __init__(self, url: str) -> None:
        self.url = httpx.URL(url)


def test_redact_httpx_request_span_removes_telegram_token_and_query_values() -> None:
    span = FakeSpan()

    _redact_httpx_request_span(
        span,
        FakeRequest("https://api.telegram.org/bot123456:secret-token/getUpdates?token=secret&safe=yes"),
    )

    assert (
        span.attributes["http.url"]
        == "https://api.telegram.org/bot[redacted]/getUpdates?token=[redacted]&safe=[redacted]"
    )
    assert span.attributes["url.full"] == span.attributes["http.url"]
    assert span.attributes["http.target"] == "/bot[redacted]/getUpdates?token=[redacted]&safe=[redacted]"
    assert span.attributes["url.path"] == "/bot[redacted]/getUpdates"
    assert span.attributes["url.query"] == "token=[redacted]&safe=[redacted]"
    assert "secret-token" not in str(span.attributes)
    assert "token=secret" not in str(span.attributes)
    assert "safe=yes" not in str(span.attributes)


def test_redact_httpx_request_span_removes_firecrawl_mcp_key() -> None:
    span = FakeSpan()

    _redact_httpx_request_span(
        span,
        FakeRequest("https://mcp.firecrawl.dev/fc-secret%2Fkey/v2/mcp"),
    )

    assert span.attributes["http.url"] == "https://mcp.firecrawl.dev/[redacted]/v2/mcp"
    assert span.attributes["url.full"] == span.attributes["http.url"]
    assert span.attributes["http.target"] == "/[redacted]/v2/mcp"
    assert span.attributes["url.path"] == "/[redacted]/v2/mcp"
    assert "fc-secret" not in str(span.attributes)
    assert "%2Fkey" not in str(span.attributes)


def test_redact_httpx_request_span_removes_non_secret_query_values() -> None:
    span = FakeSpan()

    _redact_httpx_request_span(
        span,
        FakeRequest("https://example.com/search?sort=ranking&page=1&q=%E4%B8%89%E9%87%8D%E7%B8%A3"),
    )

    assert span.attributes["http.url"] == "https://example.com/search?sort=[redacted]&page=[redacted]&q=[redacted]"
    assert span.attributes["http.target"] == "/search?sort=[redacted]&page=[redacted]&q=[redacted]"
    assert span.attributes["url.path"] == "/search"
    assert span.attributes["url.query"] == "sort=[redacted]&page=[redacted]&q=[redacted]"
    assert "%E4%B8%89" not in str(span.attributes)
