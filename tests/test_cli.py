from __future__ import annotations

import logging

from telegramagent.cli import configure_logging


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
