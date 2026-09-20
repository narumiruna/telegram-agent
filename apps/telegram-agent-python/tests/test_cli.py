from __future__ import annotations

import logging

from telegramagent.cli import _document_converter_from_settings
from telegramagent.cli import _mcp_toolsets_from_settings
from telegramagent.cli import configure_logging
from telegramagent.settings import Settings


def test_document_converter_from_settings_reports_enabled_capability() -> None:
    settings = Settings.model_validate(
        {
            "BOT_DOCUMENT_INPUT_ENABLED": True,
            "BOT_DOCUMENT_MAX_BYTES": 12345,
            "BOT_DOCUMENT_MAX_MARKDOWN_CHARS": 6789,
            "BOT_DOCUMENT_CONVERSION_TIMEOUT_SECONDS": 12.5,
            "BOT_DOCUMENT_MAX_CONCURRENT_CONVERSIONS": 3,
        }
    )

    converter, capability = _document_converter_from_settings(settings)

    assert converter is not None
    assert converter.max_markdown_chars == 6789
    assert converter.timeout_seconds == 12.5
    assert capability.name == "document_input.anydoc"
    assert capability.available is True
    assert capability.reason == ""


def test_document_converter_from_settings_reports_disabled_capability() -> None:
    settings = Settings.model_validate({"BOT_DOCUMENT_INPUT_ENABLED": False})

    converter, capability = _document_converter_from_settings(settings)

    assert converter is None
    assert capability.name == "document_input.anydoc"
    assert capability.available is False
    assert capability.reason == "disabled"


def test_mcp_toolsets_from_settings_registers_firecrawl_alongside_runtime_capability() -> None:
    settings = Settings.model_validate(
        {
            "BOT_YFINANCE_MCP_ENABLED": True,
            "BOT_YFINANCE_MCP_COMMAND": "python",
            "FIRECRAWL_API_KEY": "fc-test",
            "BOT_FIRECRAWL_MCP_ENABLED": True,
        }
    )

    toolsets, capabilities = _mcp_toolsets_from_settings(settings)

    assert [toolset.id for toolset in toolsets] == ["yfinance", "firecrawl"]
    firecrawl_capability = next(item for item in capabilities if item.name == "mcp.firecrawl")
    assert firecrawl_capability.available is True
    assert firecrawl_capability.reason == ""


def test_mcp_toolsets_from_settings_reports_missing_firecrawl_key() -> None:
    settings = Settings.model_validate(
        {
            "BOT_YFINANCE_MCP_ENABLED": False,
            "FIRECRAWL_API_KEY": None,
            "BOT_FIRECRAWL_MCP_ENABLED": True,
        }
    )

    toolsets, capabilities = _mcp_toolsets_from_settings(settings)

    assert toolsets == ()
    firecrawl_capability = next(item for item in capabilities if item.name == "mcp.firecrawl")
    assert firecrawl_capability.available is False
    assert firecrawl_capability.reason == "FIRECRAWL_API_KEY not configured"


def test_configure_logging_routes_stdlib_logging_to_loguru(capsys) -> None:
    root_logger = logging.getLogger()
    original_handlers = [*root_logger.handlers]
    original_level = root_logger.level

    try:
        configure_logging(verbose=True)

        logging.getLogger("kabigon.loader").debug("loaded via stdlib logging")

        captured = capsys.readouterr()
        assert "kabigon.loader: loaded via stdlib logging" in captured.err
    finally:
        root_logger.handlers = original_handlers
        root_logger.setLevel(original_level)
        logging.captureWarnings(False)


def test_configure_logging_redacts_sensitive_stdlib_log_values(capsys) -> None:
    root_logger = logging.getLogger()
    original_handlers = [*root_logger.handlers]
    original_level = root_logger.level

    try:
        configure_logging(verbose=True)

        logging.getLogger("kabigon.loader").debug(
            "GET https://api.telegram.org/bot123456:secret-token/getMe token=secret Bearer bearer-secret"
        )

        captured = capsys.readouterr()
        assert "/bot[redacted]/getMe" in captured.err
        assert "token=[redacted]" in captured.err
        assert "Bearer [redacted]" in captured.err
        assert "secret-token" not in captured.err
        assert "bearer-secret" not in captured.err
    finally:
        root_logger.handlers = original_handlers
        root_logger.setLevel(original_level)
        logging.captureWarnings(False)


def test_configure_logging_redacts_firecrawl_mcp_key_from_url(capsys) -> None:
    root_logger = logging.getLogger()
    original_handlers = [*root_logger.handlers]
    original_level = root_logger.level

    try:
        configure_logging(verbose=True)

        logging.getLogger("kabigon.loader").debug(
            "Request failed for url 'https://mcp.firecrawl.dev/fc-secret-key/v2/mcp'."
        )
        try:
            raise RuntimeError("POST https://mcp.firecrawl.dev/fc-secret-key/v2/mcp failed")
        except RuntimeError:
            logging.getLogger("kabigon.loader").exception("Firecrawl request failed")

        captured = capsys.readouterr()
        assert "https://mcp.firecrawl.dev/[redacted]/v2/mcp" in captured.err
        assert "fc-secret-key" not in captured.err
    finally:
        root_logger.handlers = original_handlers
        root_logger.setLevel(original_level)
        logging.captureWarnings(False)


def test_configure_logging_suppresses_openai_debug_payloads(capsys) -> None:
    root_logger = logging.getLogger()
    openai_logger = logging.getLogger("openai")
    original_handlers = [*root_logger.handlers]
    original_root_level = root_logger.level
    original_openai_level = openai_logger.level

    try:
        configure_logging(verbose=True)

        logging.getLogger("openai._base_client").debug("Request options with prompt body")

        captured = capsys.readouterr()
        assert "Request options with prompt body" not in captured.err
    finally:
        root_logger.handlers = original_handlers
        root_logger.setLevel(original_root_level)
        openai_logger.setLevel(original_openai_level)
        logging.captureWarnings(False)
