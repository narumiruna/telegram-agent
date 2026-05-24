from __future__ import annotations

from pathlib import Path

from telegramagent.cli import _container_tools_from_settings
from telegramagent.settings import Settings


def test_container_tools_are_disabled_by_default_for_local_runs(tmp_path: Path) -> None:
    settings = Settings.model_validate({})
    tools, capability = _container_tools_from_settings(settings, project_root=tmp_path, in_container=False)

    assert tools == ()
    assert capability.available is False
    assert capability.reason == "disabled"


def test_container_tools_require_container_runtime_when_enabled(tmp_path: Path) -> None:
    settings = Settings.model_validate({"BOT_CONTAINER_TOOLS_ENABLED": True})
    tools, capability = _container_tools_from_settings(settings, project_root=tmp_path, in_container=False)

    assert tools == ()
    assert capability.available is False
    assert capability.reason == "not running in Docker/container"


def test_container_tools_register_requested_tools_when_enabled_in_container(tmp_path: Path) -> None:
    settings = Settings.model_validate({"BOT_CONTAINER_TOOLS_ENABLED": True, "BOT_CONTAINER_TOOLS_ROOT": "."})
    tools, capability = _container_tools_from_settings(settings, project_root=tmp_path, in_container=True)

    assert capability.available is True
    assert capability.reason == ""
    assert [tool.name for tool in tools] == ["bash", "edit", "find", "grep", "ls", "read", "write"]
