from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

import httpx
import logfire
from loguru import logger

_TELEGRAM_BOT_TOKEN_RE = re.compile(r"/bot\d+:[A-Za-z0-9_-]+")
_SENSITIVE_QUERY_VALUE_RE = re.compile(
    r"(?i)(^|[?&])((?:token|api[_-]?key|authorization|cookie|set-cookie|password|secret)=)[^&]+"
)


@dataclass(frozen=True)
class LogfireConfig:
    enabled: bool = True
    token: str | None = None
    environment: str | None = None
    service_name: str = "telegramagent"
    include_content: bool = False


class _Span(Protocol):
    def set_attribute(self, key: str, value: str) -> None: ...


class _HttpxRequest(Protocol):
    url: httpx.URL


def configure_logfire(config: LogfireConfig, *, verbose: bool = False) -> bool:
    """Configure Logfire tracing and log forwarding when a token is available."""
    if not config.enabled or not config.token:
        return False

    logfire.configure(
        token=config.token,
        send_to_logfire=True,
        service_name=config.service_name,
        environment=config.environment,
        console=False,
        inspect_arguments=False,
    )
    logfire.instrument_httpx(
        capture_headers=False,
        capture_request_body=False,
        capture_response_body=False,
        request_hook=_redact_httpx_request_span,
        async_request_hook=_redact_httpx_async_request_span,
    )
    logfire.instrument_pydantic_ai(include_content=config.include_content)
    logfire.instrument_mcp()

    logger.add(**logfire.loguru_handler(), level="DEBUG" if verbose else "INFO")
    logger.info(
        "Logfire configured for service={} environment={} include_content={}",
        config.service_name,
        config.environment or "default",
        config.include_content,
    )
    return True


def _redact_httpx_request_span(span: _Span, request: _HttpxRequest) -> None:
    _set_redacted_url_attributes(span, request.url)


async def _redact_httpx_async_request_span(span: _Span, request: _HttpxRequest) -> None:
    _set_redacted_url_attributes(span, request.url)


def _set_redacted_url_attributes(span: _Span, url: httpx.URL) -> None:
    raw_target = url.raw_path.decode("ascii", errors="ignore")
    query = url.query.decode("ascii", errors="ignore")

    span.set_attribute("http.url", _redact_url_text(str(url)))
    span.set_attribute("url.full", _redact_url_text(str(url)))
    span.set_attribute("http.target", _redact_url_text(raw_target))
    span.set_attribute("url.path", _redact_url_text(url.path))
    span.set_attribute("url.query", _redact_query_values(query))


def _redact_url_text(value: str) -> str:
    return _redact_query_values(_TELEGRAM_BOT_TOKEN_RE.sub("/bot[redacted]", value))


def _redact_query_values(value: str) -> str:
    return _SENSITIVE_QUERY_VALUE_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}[redacted]", value)
