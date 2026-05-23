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
