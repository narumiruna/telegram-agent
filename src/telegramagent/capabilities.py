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
    try:
        importlib.metadata.version("kabigon")
    except importlib.metadata.PackageNotFoundError:
        kabigon_api_available = False
    else:
        kabigon_api_available = True
    kabigon_path = shutil.which("kabigon") or shutil.which("uvx")
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
    ]
