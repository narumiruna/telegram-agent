from __future__ import annotations

from dataclasses import dataclass

import logfire
from loguru import logger


@dataclass(frozen=True)
class LogfireConfig:
    enabled: bool = True
    token: str | None = None
    environment: str | None = None
    service_name: str = "telegramagent"
    include_content: bool = False


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
    logfire.instrument_httpx(capture_headers=False, capture_request_body=False, capture_response_body=False)
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
