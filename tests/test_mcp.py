from __future__ import annotations

from fastmcp.client.transports import StreamableHttpTransport

from telegramagent.mcp import FirecrawlMcpConfig
from telegramagent.mcp import YFinanceMcpConfig
from telegramagent.mcp import build_firecrawl_mcp_toolsets
from telegramagent.mcp import build_yfinance_mcp_toolsets
from telegramagent.mcp import command_available
from telegramagent.mcp import parse_mcp_args


def test_parse_mcp_args_uses_shell_style_splitting() -> None:
    assert parse_mcp_args('--from yfmcp "yfmcp"') == ("--from", "yfmcp", "yfmcp")
    assert parse_mcp_args("") == ()


def test_command_available_checks_path_and_executable() -> None:
    assert command_available("/definitely/missing/yfmcp") is False


def test_build_yfinance_mcp_toolsets_skips_disabled_config() -> None:
    toolsets = build_yfinance_mcp_toolsets(YFinanceMcpConfig(enabled=False))

    assert toolsets == []


def test_build_yfinance_mcp_toolsets_creates_stdio_toolset_for_available_command() -> None:
    toolsets = build_yfinance_mcp_toolsets(YFinanceMcpConfig(command="python", args=("-m", "yfmcp")))

    assert len(toolsets) == 1


def test_build_firecrawl_mcp_toolsets_skips_disabled_or_missing_key() -> None:
    assert build_firecrawl_mcp_toolsets(FirecrawlMcpConfig(enabled=False, api_key="fc-test")) == []
    assert build_firecrawl_mcp_toolsets(FirecrawlMcpConfig(api_key=None)) == []
    assert build_firecrawl_mcp_toolsets(FirecrawlMcpConfig(api_key="  ")) == []


def test_build_firecrawl_mcp_toolsets_creates_streamable_http_toolset() -> None:
    toolsets = build_firecrawl_mcp_toolsets(
        FirecrawlMcpConfig(api_key="fc-test/key", init_timeout_seconds=4, read_timeout_seconds=30)
    )

    assert len(toolsets) == 1
    assert toolsets[0].id == "firecrawl"
    transport = toolsets[0].client.transport
    assert isinstance(transport, StreamableHttpTransport)
    assert transport.url == "https://mcp.firecrawl.dev/fc-test%2Fkey/v2/mcp"
    assert "fc-test/key" not in transport.url
