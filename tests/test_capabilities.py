from __future__ import annotations

from telegramagent.capabilities import Capability
from telegramagent.capabilities import CapabilityRegistry


def test_capability_registry_reports_available_and_unavailable_tools() -> None:
    registry = CapabilityRegistry(
        [
            Capability("web_fetch", True, "fetch web pages"),
            Capability("external_loader.kabigon", False, "kabigon fallback", "not configured"),
        ]
    )

    assert registry.is_available("web_fetch") is True
    assert registry.is_available("external_loader.kabigon") is False
    assert registry.is_available("missing") is False
    assert "external_loader.kabigon: unavailable: not configured" in registry.summary()


def test_capability_registry_can_enable_explicit_runtime_capability() -> None:
    registry = CapabilityRegistry([])

    registry.set(Capability("external_loader.kabigon", True, "wired test loader"))

    assert registry.is_available("external_loader.kabigon") is True
