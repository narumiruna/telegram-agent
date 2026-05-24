from __future__ import annotations

from telegramagent.mcp import GurumeMcpConfig
from telegramagent.mcp import YFinanceMcpConfig
from telegramagent.mcp import build_gurume_mcp_toolsets
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


def test_build_gurume_mcp_toolsets_skips_disabled_config() -> None:
    toolsets = build_gurume_mcp_toolsets(GurumeMcpConfig(enabled=False))

    assert toolsets == []


def test_build_gurume_mcp_toolsets_creates_stdio_toolset_for_available_command() -> None:
    toolsets = build_gurume_mcp_toolsets(GurumeMcpConfig(enabled=True, command="python", args=("-m", "gurume", "mcp")))

    assert len(toolsets) == 1
