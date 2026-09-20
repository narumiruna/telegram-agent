from __future__ import annotations

import sys
from pathlib import Path

from telegramagent.capabilities import Capability
from telegramagent.cli import _guard_container_tools_for_otter
from telegramagent.cli import _otter_tools_from_settings
from telegramagent.settings import Settings


def _executable(tmp_path: Path) -> Path:
    command = tmp_path / "bin" / "otter"
    command.parent.mkdir()
    command.write_text(f"#!{sys.executable}\nprint('{{\"authenticated\": true}}')\n", encoding="utf-8")
    command.chmod(0o755)
    return command


def test_otter_tools_are_disabled_by_default(tmp_path: Path) -> None:
    tools, capability = _otter_tools_from_settings(Settings.model_validate({}), project_root=tmp_path)

    assert tools == ()
    assert capability.available is False
    assert capability.reason == "disabled"


def test_otter_tools_require_non_empty_whitelist(tmp_path: Path) -> None:
    settings = Settings.model_validate({"BOT_OTTER_TOOLS_ENABLED": True})

    tools, capability = _otter_tools_from_settings(settings, project_root=tmp_path)

    assert tools == ()
    assert capability.available is False
    assert capability.reason == "BOT_WHITELIST is empty"


def test_otter_tools_report_missing_command(tmp_path: Path) -> None:
    settings = Settings.model_validate(
        {
            "BOT_OTTER_TOOLS_ENABLED": True,
            "BOT_WHITELIST": "123",
            "BOT_OTTER_COMMAND": "missing-otter",
        }
    )

    tools, capability = _otter_tools_from_settings(settings, project_root=tmp_path)

    assert tools == ()
    assert capability.available is False
    assert capability.reason == "command not executable: missing-otter"


def test_otter_tools_reject_non_executable_command(tmp_path: Path) -> None:
    command = tmp_path / "otter"
    command.write_text("not executable", encoding="utf-8")
    settings = Settings.model_validate(
        {
            "BOT_OTTER_TOOLS_ENABLED": True,
            "BOT_WHITELIST": "123",
            "BOT_OTTER_COMMAND": str(command),
        }
    )

    tools, capability = _otter_tools_from_settings(settings, project_root=tmp_path)

    assert tools == ()
    assert capability.available is False
    assert capability.reason == f"command not executable: {command}"


def test_otter_tools_register_reviewed_actions_with_relative_command(tmp_path: Path) -> None:
    command = _executable(tmp_path)
    settings = Settings.model_validate(
        {
            "BOT_OTTER_TOOLS_ENABLED": True,
            "BOT_WHITELIST": "123",
            "BOT_OTTER_COMMAND": "bin/otter",
            "OTTER_TOKEN": "secret-token",
            "OTTER_CONFIG_PATH": ".state/otter.json",
        }
    )

    tools, capability = _otter_tools_from_settings(settings, project_root=tmp_path)

    assert capability.available is True
    assert capability.reason == ""
    assert len(tools) == 14
    runtime = tools[0].function.__self__.runtime
    assert runtime.config.command == str(command)
    assert runtime.config.token == "secret-token"
    assert runtime.config.config_path == tmp_path / ".state/otter.json"


def test_container_tools_are_removed_when_otter_is_enabled_or_credentials_are_configured(tmp_path: Path) -> None:
    original_tools = (object(),)
    original_capability = Capability("container_tools", True, "container tools")

    enabled_tools, enabled_capability = _guard_container_tools_for_otter(
        Settings.model_validate({"BOT_OTTER_TOOLS_ENABLED": True}), original_tools, original_capability
    )
    token_tools, token_capability = _guard_container_tools_for_otter(
        Settings.model_validate({"OTTER_TOKEN": "secret"}), original_tools, original_capability
    )
    config_tools, config_capability = _guard_container_tools_for_otter(
        Settings.model_validate({"OTTER_CONFIG_PATH": tmp_path / "credentials.json"}),
        original_tools,
        original_capability,
    )

    assert enabled_tools == token_tools == config_tools == ()
    assert enabled_capability.reason == "disabled while Otter tools or credentials are configured"
    assert token_capability.reason == enabled_capability.reason
    assert config_capability.reason == enabled_capability.reason


def test_container_tools_remain_available_without_otter_configuration() -> None:
    original_tools = (object(),)
    original_capability = Capability("container_tools", True, "container tools")

    tools, capability = _guard_container_tools_for_otter(
        Settings.model_validate({}), original_tools, original_capability
    )

    assert tools == original_tools
    assert capability is original_capability
