from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest

from telegramagent.context_files import ContextManagementTool
from telegramagent.context_files import load_context_file
from telegramagent.llm import ChatAgent
from telegramagent.llm import TopicEndAgent
from telegramagent.skills import AgentSkill
from telegramagent.skills import SkillInstaller
from telegramagent.skills import SkillInstallResult
from telegramagent.skills import SkillManagementTool
from telegramagent.skills import format_skills_for_instructions
from telegramagent.skills import load_agent_skills
from telegramagent.telegram import TelegramBot
from telegramagent.telegram import TelegramClient
from telegramagent.telegram import TelegramUpdate


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, int | None]] = []

    async def get_me(self) -> dict[str, object]:
        return {"username": "fakebot"}

    async def get_updates(self, *, offset: int | None, poll_timeout: int = 30) -> list[TelegramUpdate]:
        return []

    async def send_message(self, chat_id: int, text: str, *, reply_to_message_id: int | None = None) -> None:
        self.sent.append((chat_id, text, reply_to_message_id))


class FakeAgent:
    async def reply(self, prompt: str, *, history: Sequence[tuple[str, str]]) -> str:
        return f"AI: {prompt} ({len(history)})"


class FakeRunResult:
    def __init__(self, output: str) -> None:
        self.output = output


class FakeRunnableAgent:
    def __init__(self, output: str = "回覆") -> None:
        self.output = output
        self.prompts: list[str] = []

    async def run(self, user_prompt: str) -> FakeRunResult:
        self.prompts.append(user_prompt)
        return FakeRunResult(self.output)


class FakeCommandSkillInstaller(SkillInstaller):
    def __init__(self, project_root: Path) -> None:
        super().__init__(project_root=project_root)
        self.commands: list[list[str]] = []

    async def _run(self, command: Sequence[str]) -> SkillInstallResult:
        command_list = [*command]
        self.commands.append(command_list)
        return SkillInstallResult(command=command_list, exit_code=0, output="ok")


class FakeSkillInstaller:
    def __init__(self, result: SkillInstallResult) -> None:
        self.result = result
        self.add_calls: list[str] = []
        self.list_calls = 0

    async def add(self, args: str) -> SkillInstallResult:
        self.add_calls.append(args)
        return self.result

    async def list(self) -> SkillInstallResult:
        self.list_calls += 1
        return self.result


class FakeProactiveTool:
    def __init__(self, replies: Sequence[str | None]) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[str, int, Sequence[tuple[str, str]]]] = []

    async def handle(
        self,
        text: str,
        *,
        chat_id: int,
        agent: object,
        history: Sequence[tuple[str, str]],
    ) -> str | None:
        self.calls.append((text, chat_id, [*history]))
        return self.replies.pop(0)


class FakeTopicEndJudge:
    def __init__(self, decisions: Sequence[bool]) -> None:
        self.decisions = list(decisions)
        self.calls: list[tuple[str, Sequence[tuple[str, str]], int]] = []

    async def should_end_topic(
        self,
        incoming_text: str,
        *,
        history: Sequence[tuple[str, str]],
        bot_reply_streak: int,
    ) -> bool:
        self.calls.append((incoming_text, history, bot_reply_streak))
        return self.decisions.pop(0)


def test_load_agent_skills_from_directory(tmp_path: Path) -> None:
    skill_dir = tmp_path / ".agents" / "skills" / "chat-style"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        "---\nname: chat-style\ndescription: Style guide\n---\n\n# Chat Style\n\n- 回答要短。\n",
        encoding="utf-8",
    )

    skills = load_agent_skills(tmp_path / ".agents" / "skills")

    assert [skill.name for skill in skills] == ["chat-style"]
    assert "回答要短" in format_skills_for_instructions(skills)


@pytest.mark.asyncio
async def test_start_help_id_and_reset_commands() -> None:
    bot = TelegramBot(telegram=FakeTelegram(), agent=FakeAgent())

    assert "Telegram AI 助理" in await bot.build_reply(123, "/start", user_id=456)
    assert "/ask <問題>" in await bot.build_reply(123, "/help", user_id=456)
    assert await bot.build_reply(123, "/id", user_id=456) == "chat_id: 123\nuser_id: 456"

    bot.histories[123] = [("user", "hi")]
    assert await bot.build_reply(123, "/reset", user_id=456) == "已清除這個聊天室的對話記憶。"
    assert 123 not in bot.histories


@pytest.mark.asyncio
async def test_plain_text_uses_agent_and_keeps_history() -> None:
    bot = TelegramBot(telegram=FakeTelegram(), agent=FakeAgent())

    assert await bot.build_reply(123, "你好") == "AI: 你好 (0)"
    assert await bot.build_reply(123, "/ask 第二題") == "AI: 第二題 (2)"


@pytest.mark.asyncio
async def test_proactive_tool_runs_before_generic_chat_and_updates_history() -> None:
    proactive = FakeProactiveTool(["主動整理完成"])
    bot = TelegramBot(telegram=FakeTelegram(), agent=FakeAgent(), proactive_tool=proactive)

    assert await bot.build_reply(123, "https://youtu.be/iG-hzh9roNw") == "主動整理完成"
    assert proactive.calls == [("https://youtu.be/iG-hzh9roNw", 123, [])]
    assert bot.histories[123] == [("user", "https://youtu.be/iG-hzh9roNw"), ("assistant", "主動整理完成")]


@pytest.mark.asyncio
async def test_proactive_tool_falls_back_to_agent_when_no_action_matches() -> None:
    proactive = FakeProactiveTool([None])
    bot = TelegramBot(telegram=FakeTelegram(), agent=FakeAgent(), proactive_tool=proactive)

    assert await bot.build_reply(123, "你好") == "AI: 你好 (0)"
    assert proactive.calls == [("你好", 123, [])]


@pytest.mark.asyncio
async def test_group_plain_text_is_ignored_unless_addressed() -> None:
    telegram = FakeTelegram()
    bot = TelegramBot(telegram=telegram, agent=FakeAgent(), bot_username="fakebot", bot_user_id=42)

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": -100, "type": "supergroup"},
                "from": {"id": 456},
                "text": "大家好",
            },
        }
    )

    assert telegram.sent == []


@pytest.mark.asyncio
async def test_group_mention_addresses_bot_and_strips_mention() -> None:
    telegram = FakeTelegram()
    bot = TelegramBot(telegram=telegram, agent=FakeAgent(), bot_username="fakebot", bot_user_id=42)

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": -100, "type": "group"},
                "from": {"id": 456},
                "text": "@FakeBot 你好",
            },
        }
    )

    assert telegram.sent == [(-100, "AI: 你好 (0)", 10)]


@pytest.mark.asyncio
async def test_group_reply_to_bot_addresses_bot() -> None:
    telegram = FakeTelegram()
    bot = TelegramBot(telegram=telegram, agent=FakeAgent(), bot_username="fakebot", bot_user_id=42)

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 11,
                "chat": {"id": -100, "type": "supergroup"},
                "from": {"id": 456},
                "reply_to_message": {"message_id": 10, "from": {"id": 42, "username": "fakebot"}},
                "text": "繼續說",
            },
        }
    )

    assert telegram.sent == [(-100, "AI: 繼續說 (0)", 11)]


@pytest.mark.asyncio
async def test_topic_end_judge_can_stop_bot_reply_without_answering() -> None:
    telegram = FakeTelegram()
    judge = FakeTopicEndJudge([True])
    bot = TelegramBot(
        telegram=telegram,
        agent=FakeAgent(),
        bot_username="fakebot",
        bot_user_id=42,
        topic_end_judge=judge,
    )

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": -100, "type": "supergroup"},
                "from": {"id": 777, "is_bot": True, "username": "other_bot"},
                "reply_to_message": {"message_id": 9, "from": {"id": 42, "username": "fakebot"}},
                "text": "好的。",
            },
        }
    )

    assert telegram.sent == []
    assert judge.calls == [("好的。", (), 0)]


@pytest.mark.asyncio
async def test_topic_end_judge_can_continue_bot_reply() -> None:
    telegram = FakeTelegram()
    judge = FakeTopicEndJudge([False])
    bot = TelegramBot(
        telegram=telegram,
        agent=FakeAgent(),
        bot_username="fakebot",
        bot_user_id=42,
        topic_end_judge=judge,
    )

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": -100, "type": "supergroup"},
                "from": {"id": 777, "is_bot": True, "username": "other_bot"},
                "reply_to_message": {"message_id": 9, "from": {"id": 42, "username": "fakebot"}},
                "text": "請問下一步是什麼?",
            },
        }
    )

    assert telegram.sent == [(-100, "AI: 請問下一步是什麼? (0)", 10)]
    assert judge.calls == [("請問下一步是什麼?", (), 0)]


@pytest.mark.asyncio
async def test_bot_to_bot_loop_stops_after_one_reply_without_judge() -> None:
    telegram = FakeTelegram()
    bot = TelegramBot(telegram=telegram, agent=FakeAgent(), bot_username="fakebot", bot_user_id=42)

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": -100, "type": "supergroup"},
                "from": {"id": 777, "is_bot": True, "username": "other_bot"},
                "reply_to_message": {"message_id": 9, "from": {"id": 42, "username": "fakebot"}},
                "text": "好。",
            },
        }
    )
    await bot.handle_update(
        {
            "update_id": 2,
            "message": {
                "message_id": 11,
                "chat": {"id": -100, "type": "supergroup"},
                "from": {"id": 777, "is_bot": True, "username": "other_bot"},
                "reply_to_message": {"message_id": 10, "from": {"id": 42, "username": "fakebot"}},
                "text": "好的。",
            },
        }
    )

    assert telegram.sent == [(-100, "AI: 好。 (0)", 10)]


@pytest.mark.asyncio
async def test_human_message_resets_bot_to_bot_loop_guard() -> None:
    telegram = FakeTelegram()
    bot = TelegramBot(telegram=telegram, agent=FakeAgent(), bot_username="fakebot", bot_user_id=42)

    bot.bot_reply_streaks[-100] = 1
    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": -100, "type": "supergroup"},
                "from": {"id": 456, "is_bot": False},
                "text": "@fakebot 人類插話",
            },
        }
    )
    await bot.handle_update(
        {
            "update_id": 2,
            "message": {
                "message_id": 11,
                "chat": {"id": -100, "type": "supergroup"},
                "from": {"id": 777, "is_bot": True, "username": "other_bot"},
                "reply_to_message": {"message_id": 10, "from": {"id": 42, "username": "fakebot"}},
                "text": "好。",
            },
        }
    )

    assert telegram.sent == [(-100, "AI: 人類插話 (0)", 10), (-100, "AI: 好。 (2)", 11)]


@pytest.mark.asyncio
async def test_bot_to_bot_replies_can_be_fully_disabled() -> None:
    telegram = FakeTelegram()
    bot = TelegramBot(
        telegram=telegram,
        agent=FakeAgent(),
        bot_username="fakebot",
        bot_user_id=42,
        max_consecutive_replies_to_bots=0,
    )

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": -100, "type": "supergroup"},
                "from": {"id": 777, "is_bot": True, "username": "other_bot"},
                "reply_to_message": {"message_id": 9, "from": {"id": 42, "username": "fakebot"}},
                "text": "好。",
            },
        }
    )

    assert telegram.sent == []


@pytest.mark.asyncio
async def test_context_tool_runs_before_builtin_commands(tmp_path: Path) -> None:
    context = load_context_file(_write(tmp_path / "MEMORY.md", "memory text"), label="MEMORY.md", max_chars=1000)

    async def reload_context():
        return context

    bot = TelegramBot(
        telegram=FakeTelegram(),
        agent=FakeAgent(),
        tools=[
            ContextManagementTool(
                command_name="memory",
                display_name="MEMORY.md",
                current_context=lambda: context,
                reload_context=reload_context,
                admins={456},
            )
        ],
    )

    assert "memory text" in await bot.build_reply(123, "/memory show", user_id=456)
    assert await bot.build_reply(123, "/memory show", user_id=999) == "你沒有權限管理 MEMORY.md。"


@pytest.mark.asyncio
async def test_skills_add_runs_installer_and_reloads() -> None:
    installer = FakeSkillInstaller(SkillInstallResult(command=["npx"], exit_code=0, output="installed"))
    reload_count = 0

    async def reload_skills() -> int:
        nonlocal reload_count
        reload_count += 1
        return 2

    bot = TelegramBot(
        telegram=FakeTelegram(),
        agent=FakeAgent(),
        skill_tool=SkillManagementTool(installer=installer, skill_admins={456}, reload_skills=reload_skills),
    )

    reply = await bot.build_reply(123, "/skills add owner/repo --skill chat-style", user_id=456)

    assert installer.add_calls == ["owner/repo --skill chat-style"]
    assert reload_count == 1
    assert "已重新載入 2 個 skill" in reply
    assert "installed" in reply


@pytest.mark.asyncio
async def test_natural_language_skills_install_request_runs_installer() -> None:
    installer = FakeSkillInstaller(SkillInstallResult(command=["npx"], exit_code=0, output="installed"))
    bot = TelegramBot(
        telegram=FakeTelegram(),
        agent=FakeAgent(),
        skill_tool=SkillManagementTool(installer=installer, skill_admins={456}),
    )

    reply = await bot.build_reply(123, "安裝 narumiruna/skills 的 skills 所有", user_id=456)

    assert installer.add_calls == ["narumiruna/skills --skill *"]
    assert "已重新載入" not in reply
    assert "Skill 安裝失敗" not in reply


@pytest.mark.asyncio
async def test_skills_add_requires_admin() -> None:
    installer = FakeSkillInstaller(SkillInstallResult(command=["npx"], exit_code=0, output="installed"))
    bot = TelegramBot(
        telegram=FakeTelegram(),
        agent=FakeAgent(),
        skill_tool=SkillManagementTool(installer=installer, skill_admins={999}),
    )

    reply = await bot.build_reply(123, "/skills add owner/repo", user_id=456)

    assert reply == "你沒有權限管理 Agent Skills。"
    assert installer.add_calls == []


def test_skill_installer_builds_non_interactive_npx_add_command(tmp_path: Path) -> None:
    installer = FakeCommandSkillInstaller(project_root=tmp_path)

    result = asyncio.run(installer.add("owner/repo --skill chat-style"))

    assert result.ok
    assert installer.commands == [
        [
            "npx",
            "skills",
            "add",
            "owner/repo",
            "--skill",
            "chat-style",
            "--agent",
            "universal",
            "--yes",
            "--copy",
        ]
    ]


def test_skill_installer_installs_all_skills_only_for_universal_agent(tmp_path: Path) -> None:
    installer = FakeCommandSkillInstaller(project_root=tmp_path)

    result = asyncio.run(installer.add("owner/repo --all"))

    assert result.ok
    assert installer.commands == [
        ["npx", "skills", "add", "owner/repo", "--skill", "*", "--agent", "universal", "--yes", "--copy"]
    ]


def test_skill_tool_skips_when_all_skills_already_exist() -> None:
    installer = FakeSkillInstaller(SkillInstallResult(command=["npx"], exit_code=0, output="installed"))
    tool = SkillManagementTool(
        installer=installer,
        skill_admins={456},
        installed_skill_names=lambda: {"python", "writing-plans"},
    )

    reply = asyncio.run(tool.handle("/skills add owner/repo --all", chat_id=123, user_id=456))

    assert reply == "目前已安裝 2 個 skill, 略過安裝。若要重裝請加 --force。"
    assert installer.add_calls == []


@pytest.mark.asyncio
async def test_whitelist_rejects_unauthorized_message() -> None:
    telegram = FakeTelegram()
    bot = TelegramBot(telegram=telegram, agent=FakeAgent(), whitelist={999})

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 456},
                "text": "hi",
            },
        }
    )

    assert telegram.sent == [(123, "這個機器人目前沒有開放給你使用。", 10)]


@pytest.mark.asyncio
async def test_telegram_client_calls_bot_api() -> None:
    requests: list[tuple[str, dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((str(request.url), dict(request.headers)))
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [{"update_id": 1}]})
        return httpx.Response(200, json={"ok": True, "result": {}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        telegram = TelegramClient("token", http_client=client)
        updates = await telegram.get_updates(offset=2, poll_timeout=1)
        await telegram.send_message(123, "hello")

    assert updates == [{"update_id": 1}]
    assert requests[0][0] == "https://api.telegram.org/bottoken/getUpdates"
    assert requests[1][0] == "https://api.telegram.org/bottoken/sendMessage"


@pytest.mark.asyncio
async def test_chat_agent_uses_pydantic_agent_with_history() -> None:
    runnable = FakeRunnableAgent("  回覆  ")
    captured: dict[str, str] = {}

    def factory(instructions: str) -> FakeRunnableAgent:
        captured["instructions"] = instructions
        return runnable

    agent = ChatAgent(api_key="key", model="model", agent_factory=factory)
    reply = await agent.reply("問題", history=[("user", "前題"), ("assistant", "前答")])

    assert reply == "回覆"
    assert "Telegram 機器人助理" in captured["instructions"]
    assert runnable.prompts == ["近期對話:\nuser: 前題\nassistant: 前答\n\n使用者新訊息:\n問題"]


@pytest.mark.asyncio
async def test_chat_agent_injects_agent_skills_into_pydantic_instructions() -> None:
    skill = AgentSkill(
        name="chat-style",
        description="Style guide",
        content="---\nname: chat-style\ndescription: Style guide\n---\n\n# Chat Style\n\n- 回答要短。",
        path=Path(".agents/skills/chat-style/SKILL.md"),
    )
    captured: dict[str, str] = {}

    def factory(instructions: str) -> FakeRunnableAgent:
        captured["instructions"] = instructions
        return FakeRunnableAgent()

    agent = ChatAgent(api_key="key", model="model", skills=[skill], agent_factory=factory)
    await agent.reply("問題")

    assert "Skill: chat-style" in captured["instructions"]
    assert "回答要短" in captured["instructions"]


@pytest.mark.asyncio
async def test_chat_agent_falls_back_without_api_key() -> None:
    agent = ChatAgent(api_key=None, model="model")

    reply = await agent.reply("問題")

    assert "OPENAI_API_KEY" in reply
    assert "問題" in reply


@pytest.mark.asyncio
async def test_topic_end_agent_stops_obvious_closing_loop_without_api_call() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"choices": [{"message": {"content": "CONTINUE"}}]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        judge = TopicEndAgent(api_key="key", model="model", base_url="https://example.test/v1", http_client=client)
        should_end = await judge.should_end_topic("好的。", history=[], bot_reply_streak=0)

    assert should_end is True
    assert called is False


@pytest.mark.asyncio
async def test_topic_end_agent_uses_model_for_non_obvious_bot_message() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read().decode()
        return httpx.Response(200, json={"choices": [{"message": {"content": "END"}}]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        judge = TopicEndAgent(api_key="key", model="model", base_url="https://example.test/v1", http_client=client)
        should_end = await judge.should_end_topic(
            "我已經完成整理。",
            history=[("assistant", "好的, 我來整理。")],
            bot_reply_streak=1,
        )

    assert should_end is True
    assert "Telegram bot" in captured["body"]
    assert "我已經完成整理" in captured["body"]


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path
