from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from telegramagent.context_files import ContextManagementTool
from telegramagent.context_files import format_context_for_instructions
from telegramagent.context_files import load_context_file
from telegramagent.llm import ChatAgent
from telegramagent.settings import Settings
from telegramagent.skills import AgentSkill


class FakeRunResult:
    def __init__(self, output: str) -> None:
        self.output = output


def test_settings_include_soul_and_memory_defaults() -> None:
    settings = Settings()

    assert settings.bot_soul_path == Path("SOUL.md")
    assert settings.bot_soul_required is False
    assert settings.bot_soul_max_chars == 8000
    assert settings.bot_memory_path == Path("MEMORY.md")
    assert settings.bot_memory_required is False
    assert settings.bot_memory_max_chars == 12000


def test_settings_parse_soul_and_memory_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_SOUL_PATH", "persona/SOUL.md")
    monkeypatch.setenv("BOT_SOUL_REQUIRED", "true")
    monkeypatch.setenv("BOT_SOUL_MAX_CHARS", "123")
    monkeypatch.setenv("BOT_MEMORY_PATH", "persona/MEMORY.md")
    monkeypatch.setenv("BOT_MEMORY_REQUIRED", "true")
    monkeypatch.setenv("BOT_MEMORY_MAX_CHARS", "456")

    settings = Settings()

    assert settings.bot_soul_path == Path("persona/SOUL.md")
    assert settings.bot_soul_required is True
    assert settings.bot_soul_max_chars == 123
    assert settings.bot_memory_path == Path("persona/MEMORY.md")
    assert settings.bot_memory_required is True
    assert settings.bot_memory_max_chars == 456


class FakeRunnableAgent:
    def __init__(self, output: str = "ok") -> None:
        self.output = output

    async def run(self, user_prompt: str) -> FakeRunResult:
        return FakeRunResult(self.output)


def test_load_context_file_missing_optional(tmp_path: Path) -> None:
    context = load_context_file(tmp_path / "SOUL.md", label="SOUL.md", max_chars=100)

    assert context.exists is False
    assert context.loaded is False
    assert context.content == ""


def test_load_context_file_missing_required_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_context_file(tmp_path / "SOUL.md", label="SOUL.md", max_chars=100, required=True)


def test_load_context_file_truncates(tmp_path: Path) -> None:
    path = tmp_path / "MEMORY.md"
    path.write_text("abcdef", encoding="utf-8")

    context = load_context_file(path, label="MEMORY.md", max_chars=3)

    assert context.exists is True
    assert context.truncated is True
    assert context.original_chars == 6
    assert context.content == "abc"
    assert "truncated" in format_context_for_instructions(context)


def test_chat_agent_instruction_order_is_core_soul_memory_skills(tmp_path: Path) -> None:
    soul = load_context_file(_write(tmp_path / "SOUL.md", "# Soul\nvoice"), label="SOUL.md", max_chars=1000)
    memory = load_context_file(_write(tmp_path / "MEMORY.md", "# Memory\nfacts"), label="MEMORY.md", max_chars=1000)
    skill = AgentSkill(name="skill", description="desc", content="# Skill\nworkflow", path=tmp_path / "SKILL.md")
    captured: dict[str, str] = {}

    def factory(instructions: str) -> FakeRunnableAgent:
        captured["instructions"] = instructions
        return FakeRunnableAgent()

    ChatAgent(api_key="key", model="model", soul=soul, memory=memory, skills=[skill], agent_factory=factory)

    instructions = captured["instructions"]
    assert instructions.index("Telegram 機器人助理") < instructions.index("SOUL.md")
    assert instructions.index("SOUL.md") < instructions.index("MEMORY.md")
    assert instructions.index("MEMORY.md") < instructions.index("Skill: skill")


def test_chat_agent_reload_context_updates_instructions(tmp_path: Path) -> None:
    soul = load_context_file(_write(tmp_path / "SOUL.md", "old soul"), label="SOUL.md", max_chars=1000)
    updated_soul = load_context_file(_write(tmp_path / "SOUL.md", "new soul"), label="SOUL.md", max_chars=1000)
    captured: list[str] = []

    def factory(instructions: str) -> FakeRunnableAgent:
        captured.append(instructions)
        return FakeRunnableAgent()

    agent = ChatAgent(api_key="key", model="model", soul=soul, agent_factory=factory)
    agent.reload_context(soul=updated_soul)

    assert "old soul" in captured[0]
    assert "new soul" in captured[1]


def test_context_management_tool_show_reload_path_and_admin(tmp_path: Path) -> None:
    path = _write(tmp_path / "SOUL.md", "old soul")
    current = load_context_file(path, label="SOUL.md", max_chars=1000)

    async def reload_context():
        nonlocal current
        current = load_context_file(_write(path, "new soul"), label="SOUL.md", max_chars=1000)
        return current

    tool = ContextManagementTool(
        command_name="soul",
        display_name="SOUL.md",
        current_context=lambda: current,
        reload_context=reload_context,
        admins={456},
    )

    assert asyncio.run(tool.handle("/soul show", chat_id=123, user_id=999)) == "你沒有權限管理 SOUL.md。"
    show_reply = asyncio.run(tool.handle("/soul show", chat_id=123, user_id=456))
    path_reply = asyncio.run(tool.handle("/soul path", chat_id=123, user_id=456))
    reload_reply = asyncio.run(tool.handle("/soul reload", chat_id=123, user_id=456))
    updated_show_reply = asyncio.run(tool.handle("/soul show", chat_id=123, user_id=456))

    assert show_reply is not None and "old soul" in show_reply
    assert path_reply == str(path)
    assert reload_reply is not None and "已重新載入" in reload_reply
    assert updated_show_reply is not None and "new soul" in updated_show_reply


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path
