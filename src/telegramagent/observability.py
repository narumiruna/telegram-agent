from __future__ import annotations

import os
from dataclasses import dataclass
from importlib import import_module
from typing import Protocol

import httpx
import logfire
from loguru import logger

_TELEGRAM_BOT_TOKEN_MARKER = "/bot"
_TELEGRAM_API_EXCLUDED_URL_PATTERN = r"https://api\.telegram\.org/bot.*"
_OPENAI_AGENTS_DISABLE_TRACING_ENV = "OPENAI_AGENTS_DISABLE_TRACING"
_OTEL_HTTPX_EXCLUDED_URLS_ENV = "OTEL_PYTHON_HTTPX_EXCLUDED_URLS"
_REDACTED_QUERY_VALUE = "[redacted]"


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

    _disable_openai_agents_default_tracing()
    _ensure_httpx_excluded_url(_TELEGRAM_API_EXCLUDED_URL_PATTERN)

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


def _disable_openai_agents_default_tracing() -> None:
    os.environ.setdefault(_OPENAI_AGENTS_DISABLE_TRACING_ENV, "1")
    if not _env_flag_enabled(os.environ.get(_OPENAI_AGENTS_DISABLE_TRACING_ENV)):
        return
    try:
        agents_module = import_module("agents")
    except ImportError:
        return
    set_tracing_disabled = getattr(agents_module, "set_tracing_disabled", None)
    if callable(set_tracing_disabled):
        set_tracing_disabled(True)


def _ensure_httpx_excluded_url(pattern: str) -> None:
    existing = os.environ.get(_OTEL_HTTPX_EXCLUDED_URLS_ENV, "")
    patterns = [entry.strip() for entry in existing.split(",") if entry.strip()]
    if pattern not in patterns:
        patterns.append(pattern)
    os.environ[_OTEL_HTTPX_EXCLUDED_URLS_ENV] = ",".join(patterns)


def _env_flag_enabled(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().casefold() not in {"", "0", "false", "no", "off"}


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
    safe_value = _redact_telegram_bot_token(value)
    path, separator, query = safe_value.partition("?")
    if not separator:
        return path
    return f"{path}?{_redact_query_values(query)}"


def _redact_query_values(value: str) -> str:
    if not value:
        return ""
    redacted_parts = []
    for part in value.split("&"):
        key, separator, _raw_value = part.partition("=")
        redacted_parts.append(f"{key}{separator}{_REDACTED_QUERY_VALUE}" if separator else key)
    return "&".join(redacted_parts)


def _redact_telegram_bot_token(value: str) -> str:
    marker_index = value.find(_TELEGRAM_BOT_TOKEN_MARKER)
    if marker_index == -1:
        return value
    token_start = marker_index + len(_TELEGRAM_BOT_TOKEN_MARKER)
    token_end_candidates = [
        index for index in (value.find("/", token_start), value.find("?", token_start)) if index != -1
    ]
    token_end = min(token_end_candidates) if token_end_candidates else len(value)
    return f"{value[:marker_index]}/bot[redacted]{value[token_end:]}"
