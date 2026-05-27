from __future__ import annotations

import shlex
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import cast

from fastmcp.client.transports import StdioTransport
from pydantic_ai.mcp import MCPToolset


@dataclass(frozen=True)
class YFinanceMcpConfig:
    enabled: bool = True
    command: str = "yfmcp"
    args: tuple[str, ...] = ()
    init_timeout_seconds: float = 10.0
    read_timeout_seconds: float = 120.0


def parse_mcp_args(value: str | Sequence[str] | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(shlex.split(value))
    return tuple(value)


def command_available(command: str) -> bool:
    if not command:
        return False
    if Path(command).is_absolute() or "/" in command:
        return Path(command).exists()
    return shutil.which(command) is not None


def build_yfinance_mcp_toolsets(config: YFinanceMcpConfig) -> list[MCPToolset[Any]]:
    if not config.enabled or not command_available(config.command):
        return []
    transport = StdioTransport(command=config.command, args=list(config.args))
    return [
        MCPToolset(
            cast(Any, transport),
            id="yfinance",
            init_timeout=config.init_timeout_seconds,
            read_timeout=config.read_timeout_seconds,
        )
    ]
