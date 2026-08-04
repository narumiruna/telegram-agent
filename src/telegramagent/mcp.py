from __future__ import annotations

import re
import shlex
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import cast
from urllib.parse import quote

from fastmcp.client.transports import StdioTransport
from fastmcp.client.transports import StreamableHttpTransport
from mcp.shared.exceptions import McpError
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.mcp import CallToolFunc
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.mcp import ToolResult

_FIRECRAWL_MCP_URL_TEMPLATE = "https://mcp.firecrawl.dev/{api_key}/v2/mcp"
_FIRECRAWL_SEARCH_TOOL_NAME = "firecrawl_search"
_INVALID_PARAMS_ERROR_CODE = -32602
FIRECRAWL_MCP_LOGFIRE_SCRUB_PATTERN = r"https://mcp\.firecrawl\.dev/[^?#\s]+?/v2/mcp"
_FIRECRAWL_MCP_CREDENTIAL_RE = re.compile(
    r"(?P<prefix>https://mcp\.firecrawl\.dev/|(?<!\S)/)"
    r"(?P<credential>[^?#\s]+?)"
    r"(?P<suffix>/v2/mcp(?:-search)?)"
)


@dataclass(frozen=True)
class FirecrawlMcpConfig:
    enabled: bool = True
    api_key: str | None = None
    init_timeout_seconds: float = 10.0
    read_timeout_seconds: float = 120.0


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


def redact_firecrawl_mcp_url(value: str) -> str:
    return _FIRECRAWL_MCP_CREDENTIAL_RE.sub(
        lambda match: f"{match.group('prefix')}[redacted]{match.group('suffix')}", value
    )


async def retry_invalid_mcp_tool_arguments(
    ctx: RunContext[Any],
    call_tool: CallToolFunc,
    name: str,
    tool_args: dict[str, Any],
) -> ToolResult:
    """Normalize known argument representations and return other validation errors for retry."""
    del ctx
    normalized_args = _normalize_mcp_tool_arguments(name, tool_args)
    try:
        return await call_tool(name, normalized_args)
    except McpError as exc:
        if exc.error.code != _INVALID_PARAMS_ERROR_CODE:
            raise
        raise ModelRetry(f"MCP tool {name!r} rejected its arguments: {exc}") from exc


def _normalize_mcp_tool_arguments(name: str, tool_args: dict[str, Any]) -> dict[str, Any]:
    sources = tool_args.get("sources")
    if name == _FIRECRAWL_SEARCH_TOOL_NAME and isinstance(sources, dict):
        return {**tool_args, "sources": [sources]}
    return tool_args


def build_firecrawl_mcp_toolsets(config: FirecrawlMcpConfig) -> list[MCPToolset[Any]]:
    api_key = config.api_key.strip() if config.api_key else ""
    if not config.enabled or not api_key:
        return []

    url = _FIRECRAWL_MCP_URL_TEMPLATE.format(api_key=quote(api_key, safe=""))
    transport = StreamableHttpTransport(url=url)
    return [
        MCPToolset(
            cast(Any, transport),
            id="firecrawl",
            process_tool_call=retry_invalid_mcp_tool_arguments,
            init_timeout=config.init_timeout_seconds,
            read_timeout=config.read_timeout_seconds,
        )
    ]


def build_yfinance_mcp_toolsets(config: YFinanceMcpConfig) -> list[MCPToolset[Any]]:
    if not config.enabled or not command_available(config.command):
        return []
    transport = StdioTransport(command=config.command, args=list(config.args))
    return [
        MCPToolset(
            cast(Any, transport),
            id="yfinance",
            process_tool_call=retry_invalid_mcp_tool_arguments,
            init_timeout=config.init_timeout_seconds,
            read_timeout=config.read_timeout_seconds,
        )
    ]
