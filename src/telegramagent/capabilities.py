from __future__ import annotations

import importlib.metadata
import shutil
from dataclasses import dataclass


@dataclass(frozen=True)
class Capability:
    name: str
    available: bool
    description: str
    reason: str = ""


class CapabilityRegistry:
    def __init__(self, capabilities: list[Capability] | None = None) -> None:
        self._capabilities = {capability.name: capability for capability in capabilities or default_capabilities()}

    def get(self, name: str) -> Capability:
        return self._capabilities.get(
            name, Capability(name=name, available=False, description="unknown", reason="not registered")
        )

    def is_available(self, name: str) -> bool:
        return self.get(name).available

    def set(self, capability: Capability) -> None:
        self._capabilities[capability.name] = capability

    def summary(self) -> str:
        lines = []
        for capability in sorted(self._capabilities.values(), key=lambda item: item.name):
            status = "available" if capability.available else f"unavailable: {capability.reason or 'disabled'}"
            lines.append(f"- {capability.name}: {status} — {capability.description}")
        return "\n".join(lines)


def default_capabilities() -> list[Capability]:
    kabigon_api_available = _package_available("kabigon")
    yfmcp_available = _package_available("yfmcp")
    gurume_available = _package_available("gurume")
    kabigon_path = shutil.which("kabigon") or shutil.which("uvx")
    yfmcp_path = shutil.which("yfmcp")
    gurume_path = shutil.which("gurume")
    return [
        Capability("web_fetch", True, "bounded HTTP(S) text/HTML fetching with SSRF guards"),
        Capability("youtube_transcript", True, "YouTube subtitle/transcript extraction with timeout"),
        Capability("file_events", True, "local file-backed immediate event dispatch"),
        Capability(
            "external_loader.kabigon",
            kabigon_api_available,
            "kabigon.api.load_url URL extraction fallback and Pydantic AI tool",
            "kabigon package not installed" if not kabigon_api_available else "",
        ),
        Capability(
            "external_command.kabigon",
            bool(kabigon_path and shutil.which("kabigon")),
            "host kabigon executable detection only; not used unless explicitly wired",
            "kabigon executable not found" if not shutil.which("kabigon") else "",
        ),
        Capability(
            "mcp.yfinance",
            yfmcp_available and yfmcp_path is not None,
            "Yahoo Finance market data MCP tools via yfmcp",
            _yfinance_reason(package_available=yfmcp_available, command_available=yfmcp_path is not None),
        ),
        Capability(
            "mcp.gurume",
            gurume_available and gurume_path is not None,
            "Japanese restaurant search MCP tools via gurume mcp",
            _gurume_reason(package_available=gurume_available, command_available=gurume_path is not None),
        ),
    ]


def _package_available(name: str) -> bool:
    try:
        importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return False
    return True


def _yfinance_reason(*, package_available: bool, command_available: bool) -> str:
    if not package_available:
        return "yfmcp package not installed"
    if not command_available:
        return "yfmcp executable not found"
    return ""


def _gurume_reason(*, package_available: bool, command_available: bool) -> str:
    if not package_available:
        return "gurume package not installed"
    if not command_available:
        return "gurume executable not found"
    return ""
