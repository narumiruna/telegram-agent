from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path

import httpx
import pytest
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData

from telegramagent.actions import ProactiveActionTool
from telegramagent.agent_runtime import AgentEvent
from telegramagent.agent_runtime import AgentSubmission
from telegramagent.context_files import ContextManagementTool
from telegramagent.context_files import load_context_file
from telegramagent.images import AgentReply
from telegramagent.images import GeneratedImage
from telegramagent.images import ImageAttachment
from telegramagent.session import SessionLog
from telegramagent.skills import SkillInstallResult
from telegramagent.skills import SkillManagementTool
from telegramagent.skills import format_skills_for_instructions
from telegramagent.skills import load_agent_skills
from telegramagent.tasks import TaskQueue
from telegramagent.telegram import TelegramBot
from tests.telegram_test_support import FakeAgent
from tests.telegram_test_support import FakeArtifactAgent
from tests.telegram_test_support import FakeCommandSkillInstaller
from tests.telegram_test_support import FakeImageGenerator
from tests.telegram_test_support import FakeProactiveTool
from tests.telegram_test_support import FakeSkillInstaller
from tests.telegram_test_support import FakeTelegram
from tests.telegram_test_support import FakeTranscriptFetcher
from tests.telegram_test_support import FakeVisionAgent
from tests.telegram_test_support import build_reply


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

    assert "Telegram AI 助理" in await build_reply(bot, 123, "/start", user_id=456)
    assert "/ask <問題>" in await build_reply(bot, 123, "/help", user_id=456)
    assert await build_reply(bot, 123, "/id", user_id=456) == "chat_id: 123\nuser_id: 456"

    bot.histories[123] = [("user", "hi")]
    assert await build_reply(bot, 123, "/reset", user_id=456) == "已清除這個聊天室的對話記憶。"
    assert 123 not in bot.histories


@pytest.mark.asyncio
async def test_runtime_streams_by_editing_one_telegram_message() -> None:
    class FakeRuntime:
        async def submit(self, chat_id, prompt, *, images=(), event_handler=None):
            del chat_id, prompt, images
            assert event_handler is not None
            await event_handler(AgentEvent("agent_start"))
            await event_handler(AgentEvent("message_start"))
            await event_handler(AgentEvent("message_delta", text="partial "))
            await event_handler(AgentEvent("message_delta", text="answer"))
            await event_handler(AgentEvent("agent_end", text="partial answer"))
            return AgentSubmission(kind="completed", reply=AgentReply(text="partial answer"))

        async def cancel(self, chat_id):
            del chat_id
            return False

        def clear_history(self, chat_id):
            del chat_id

    telegram = FakeTelegram()
    bot = TelegramBot(
        telegram=telegram,
        agent=FakeAgent(),
        agent_runtime=FakeRuntime(),
        progress_edit_interval_seconds=0,
    )

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 456},
                "text": "hello",
            },
        }
    )

    assert telegram.sent == [(123, "處理中…", 10)]
    assert telegram.edited[-1] == (123, 100, "partial answer")
    assert bot.histories == {}


@pytest.mark.asyncio
async def test_ask_command_uses_runtime_progress_stream() -> None:
    class FakeRuntime:
        async def submit(self, chat_id, prompt, *, images=(), event_handler=None):
            del chat_id, prompt, images
            assert event_handler is not None
            await event_handler(AgentEvent("agent_start"))
            await event_handler(AgentEvent("agent_end", text="answer"))
            return AgentSubmission(kind="completed", reply=AgentReply(text="answer"))

        async def cancel(self, chat_id):
            del chat_id
            return False

        def clear_history(self, chat_id):
            del chat_id

    telegram = FakeTelegram()
    bot = TelegramBot(telegram=telegram, agent=FakeAgent(), agent_runtime=FakeRuntime())

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 456},
                "text": "/ask hello",
            },
        }
    )

    assert telegram.sent == [(123, "處理中…", 10)]
    assert telegram.edited == [(123, 100, "answer")]


@pytest.mark.asyncio
async def test_polling_dispatches_different_chats_concurrently() -> None:
    class PollingTelegram(FakeTelegram):
        def __init__(self) -> None:
            super().__init__()
            self.polls = 0
            self.stop_polling = asyncio.Event()

        async def get_updates(self, *, offset, poll_timeout=30):
            del offset, poll_timeout
            self.polls += 1
            if self.polls == 1:
                return [
                    {
                        "update_id": 1,
                        "message": {
                            "message_id": 10,
                            "chat": {"id": 1, "type": "private"},
                            "from": {"id": 11},
                            "text": "one",
                        },
                    },
                    {
                        "update_id": 2,
                        "message": {
                            "message_id": 20,
                            "chat": {"id": 2, "type": "private"},
                            "from": {"id": 22},
                            "text": "two",
                        },
                    },
                ]
            await self.stop_polling.wait()
            return []

    class BlockingRuntime:
        def __init__(self) -> None:
            self.started: list[int] = []
            self.both_started = asyncio.Event()
            self.release = asyncio.Event()

        async def submit(self, chat_id, prompt, *, images=(), event_handler=None):
            del prompt, images, event_handler
            self.started.append(chat_id)
            if len(self.started) == 2:
                self.both_started.set()
            await self.release.wait()
            return AgentSubmission(kind="completed", reply=AgentReply(text=f"done {chat_id}"))

        async def cancel(self, chat_id):
            del chat_id
            return False

        def clear_history(self, chat_id):
            del chat_id

    telegram = PollingTelegram()
    runtime = BlockingRuntime()
    bot = TelegramBot(telegram=telegram, agent=FakeAgent(), agent_runtime=runtime)
    polling = asyncio.create_task(bot.run_forever())

    await asyncio.wait_for(runtime.both_started.wait(), timeout=1)
    assert set(runtime.started) == {1, 2}

    runtime.release.set()
    telegram.stop_polling.set()
    polling.cancel()
    with pytest.raises(asyncio.CancelledError):
        await polling
    await asyncio.gather(*bot._update_tasks)


@pytest.mark.asyncio
async def test_cancel_command_stops_active_runtime() -> None:
    class FakeRuntime:
        def __init__(self) -> None:
            self.cancelled: list[int] = []

        async def submit(self, chat_id, prompt, *, images=(), event_handler=None):
            raise AssertionError("submit should not be called")

        async def cancel(self, chat_id):
            self.cancelled.append(chat_id)
            return True

        def clear_history(self, chat_id):
            del chat_id

    runtime = FakeRuntime()
    bot = TelegramBot(telegram=FakeTelegram(), agent=FakeAgent(), agent_runtime=runtime)

    assert await build_reply(bot, 123, "/cancel") == "已取消目前任務。"
    assert runtime.cancelled == [123]


@pytest.mark.asyncio
async def test_plain_text_uses_agent_and_keeps_history() -> None:
    bot = TelegramBot(telegram=FakeTelegram(), agent=FakeAgent())

    assert await build_reply(bot, 123, "你好") == "AI: 你好 (0)"
    assert await build_reply(bot, 123, "/ask 第二題") == "AI: 第二題 (2)"


@pytest.mark.asyncio
async def test_mcp_failure_returns_safe_reply_instead_of_crashing_message_handling() -> None:
    class FailingMcpAgent:
        async def reply(
            self,
            prompt: str,
            *,
            history: Sequence[tuple[str, str]],
            images: Sequence[ImageAttachment] = (),
        ) -> str:
            del prompt, history, images
            raise McpError(ErrorData(code=-32603, message="MCP server unavailable"))

    bot = TelegramBot(telegram=FakeTelegram(), agent=FailingMcpAgent())

    assert await build_reply(bot, 123, "幫我搜尋") == "AI 服務暫時無法使用, 請稍後再試。"


@pytest.mark.asyncio
async def test_proactive_tool_runs_before_generic_chat_and_updates_history() -> None:
    proactive = FakeProactiveTool(["主動整理完成"])
    bot = TelegramBot(telegram=FakeTelegram(), agent=FakeAgent(), proactive_tool=proactive)

    assert await build_reply(bot, 123, "https://youtu.be/iG-hzh9roNw") == "主動整理完成"
    assert proactive.calls == [("https://youtu.be/iG-hzh9roNw", 123, [])]
    assert bot.histories[123] == [("user", "https://youtu.be/iG-hzh9roNw"), ("assistant", "主動整理完成")]


@pytest.mark.asyncio
async def test_synthetic_message_blocks_management_commands() -> None:
    telegram = FakeTelegram()
    bot = TelegramBot(telegram=telegram, agent=FakeAgent())

    await bot.dispatch_synthetic_message(chat_id=123, text="/skills list", reply_to_message_id=55)

    assert telegram.sent == [(123, "Event 訊息不允許執行管理指令。", 55)]
    assert bot.histories == {}


@pytest.mark.asyncio
async def test_synthetic_message_edit_status_mode_edits_processing_message() -> None:
    telegram = FakeTelegram()
    proactive = FakeProactiveTool(["事件整理完成"])
    bot = TelegramBot(telegram=telegram, agent=FakeAgent(), proactive_tool=proactive)

    await bot.dispatch_synthetic_message(
        chat_id=123,
        text="[EVENT:job] https://example.com",
        reply_to_message_id=55,
        reply_mode="edit-status",
    )

    assert telegram.sent == [(123, "處理中…", 55)]
    assert telegram.edited == [(123, 100, "事件整理完成")]


@pytest.mark.asyncio
async def test_synthetic_message_edit_status_mode_falls_back_when_send_returns_no_message_id() -> None:
    class FakeTelegramNoMessageId(FakeTelegram):
        async def send_message(self, chat_id: int, text: str, *, reply_to_message_id: int | None = None) -> int | None:
            self.sent.append((chat_id, text, reply_to_message_id))
            return None

    telegram = FakeTelegramNoMessageId()
    proactive = FakeProactiveTool(["事件整理完成"])
    bot = TelegramBot(telegram=telegram, agent=FakeAgent(), proactive_tool=proactive)

    await bot.dispatch_synthetic_message(
        chat_id=123,
        text="[EVENT:job] https://example.com",
        reply_to_message_id=55,
        reply_mode="edit-status",
    )

    assert telegram.sent == [(123, "處理中…", 55), (123, "事件整理完成", 55)]
    assert telegram.edited == []


@pytest.mark.asyncio
async def test_synthetic_message_allows_proactive_and_generic_reply() -> None:
    telegram = FakeTelegram()
    proactive = FakeProactiveTool(["事件整理完成"])
    bot = TelegramBot(telegram=telegram, agent=FakeAgent(), proactive_tool=proactive)

    await bot.dispatch_synthetic_message(chat_id=123, text="[EVENT:job] https://example.com", reply_to_message_id=None)

    assert proactive.calls == [("[EVENT:job] https://example.com", 123, [])]
    assert telegram.sent == [(123, "事件整理完成", None)]
    assert bot.histories[123] == [("user", "[EVENT:job] https://example.com"), ("assistant", "事件整理完成")]


@pytest.mark.asyncio
async def test_session_log_restores_history_after_restart(tmp_path: Path) -> None:
    session_log = SessionLog(tmp_path / "sessions")
    first_bot = TelegramBot(telegram=FakeTelegram(), agent=FakeAgent(), session_log=session_log)

    assert await build_reply(first_bot, 123, "https://youtu.be/video") == "AI: https://youtu.be/video (0)"

    second_proactive = FakeProactiveTool(["沿用前面的網址完成"])
    second_bot = TelegramBot(
        telegram=FakeTelegram(), agent=FakeAgent(), proactive_tool=second_proactive, session_log=session_log
    )

    assert await build_reply(second_bot, 123, "有字幕") == "沿用前面的網址完成"
    assert second_proactive.calls == [
        ("有字幕", 123, [("user", "https://youtu.be/video"), ("assistant", "AI: https://youtu.be/video (0)")])
    ]


@pytest.mark.asyncio
async def test_session_log_restores_url_for_kabigon_followup_after_restart(tmp_path: Path) -> None:
    session_log = SessionLog(tmp_path / "sessions")
    first_fetcher = FakeTranscriptFetcher()
    first_bot = TelegramBot(
        telegram=FakeTelegram(),
        agent=FakeAgent(),
        proactive_tool=ProactiveActionTool(transcript_fetcher=first_fetcher),
        session_log=session_log,
    )

    first_reply = await build_reply(first_bot, 123, "https://www.youtube.com/watch?v=h_7fdZjUKE8")

    assert "字幕內容" in first_reply

    second_fetcher = FakeTranscriptFetcher()
    second_bot = TelegramBot(
        telegram=FakeTelegram(),
        agent=FakeAgent(),
        proactive_tool=ProactiveActionTool(transcript_fetcher=second_fetcher),
        session_log=session_log,
    )

    await build_reply(second_bot, 123, "你用 kabigon 抓抓看阿")

    assert first_fetcher.calls == ["h_7fdZjUKE8"]
    assert second_fetcher.calls == ["h_7fdZjUKE8"]


@pytest.mark.asyncio
async def test_handle_update_downloads_photo_and_passes_image_to_agent() -> None:
    telegram = FakeTelegram()
    telegram.files["large"] = {"file_id": "large", "file_path": "photos/large.jpg", "file_size": 11}
    telegram.file_contents["photos/large.jpg"] = b"large-image"
    agent = FakeVisionAgent()
    bot = TelegramBot(telegram=telegram, agent=agent)

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 456},
                "caption": "這張圖在幹嘛？",
                "photo": [
                    {"file_id": "small", "width": 100, "height": 100, "file_size": 3},
                    {"file_id": "large", "width": 800, "height": 600, "file_size": 11},
                ],
            },
        }
    )

    assert telegram.downloaded_paths == ["photos/large.jpg"]
    assert telegram.sent == [(123, "vision: 這張圖在幹嘛？ (1)", 10)]
    assert len(agent.calls) == 1
    prompt, history, images = agent.calls[0]
    assert prompt == "這張圖在幹嘛？"
    assert history == []
    assert images == [ImageAttachment(data=b"large-image", media_type="image/jpeg", filename="telegram-photo.jpg")]
    assert bot.histories[123] == [
        ("user", "這張圖在幹嘛？\n[圖片: telegram-photo.jpg]"),
        ("assistant", "vision: 這張圖在幹嘛？ (1)"),
    ]


@pytest.mark.asyncio
async def test_handle_update_rejects_oversized_photo_before_download() -> None:
    telegram = FakeTelegram()
    bot = TelegramBot(telegram=telegram, agent=FakeAgent(), image_max_bytes=5)

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 456},
                "caption": "看圖",
                "photo": [{"file_id": "large", "width": 800, "height": 600, "file_size": 6}],
            },
        }
    )

    assert telegram.downloaded_paths == []
    assert telegram.sent == [(123, "這張圖片太大了，我先不讀取；請改傳較小的圖片。", 10)]


@pytest.mark.asyncio
async def test_image_command_sends_generated_photo() -> None:
    telegram = FakeTelegram()
    generator = FakeImageGenerator(GeneratedImage(data=b"png", media_type="image/png", filename="cat.png"))
    bot = TelegramBot(telegram=telegram, agent=FakeAgent(), image_generator=generator)

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 456},
                "text": "/image 一隻橘貓在鍵盤上睡覺",
            },
        }
    )

    assert generator.prompts == ["一隻橘貓在鍵盤上睡覺"]
    assert telegram.sent == [(123, "產生圖片中…", 10)]
    assert telegram.sent_photos == [
        (123, b"png", "已根據提示產生圖片：\n一隻橘貓在鍵盤上睡覺", "cat.png", "image/png", 10)
    ]
    assert telegram.edited == [(123, 100, "圖片已產生。")]
    assert bot.histories[123] == [("user", "/image 一隻橘貓在鍵盤上睡覺"), ("assistant", "[已產生圖片]")]


@pytest.mark.asyncio
async def test_image_command_requires_generator() -> None:
    telegram = FakeTelegram()
    bot = TelegramBot(telegram=telegram, agent=FakeAgent())

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 456},
                "text": "/image 一隻貓",
            },
        }
    )

    assert telegram.sent == [
        (123, "圖片生成功能目前未啟用；請設定 OPENAI_API_KEY 並啟用 BOT_IMAGE_GENERATION_ENABLED。", 10)
    ]
    assert telegram.sent_photos == []


@pytest.mark.asyncio
async def test_handle_update_routes_long_action_through_background_task_queue() -> None:
    telegram = FakeTelegram()
    proactive = FakeProactiveTool(["背景整理完成"])
    bot = TelegramBot(telegram=telegram, agent=FakeAgent(), proactive_tool=proactive, task_queue=TaskQueue())

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 456},
                "text": "https://example.com",
            },
        }
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert telegram.sent == [(123, "處理中…", 10)]
    assert telegram.edited == [(123, 100, "背景整理完成")]
    assert bot.task_queue is not None
    assert [task.status for task in bot.task_queue.list_records(chat_id=123)] == ["completed"]


@pytest.mark.asyncio
async def test_background_link_reply_survives_session_log_append_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    telegram = FakeTelegram()
    proactive = FakeProactiveTool(["背景整理完成"])
    session_log = SessionLog(tmp_path / "sessions")

    def fail_append_turn(chat_id: int, *, user_text: str, assistant_text: str, synthetic: bool = False) -> None:
        raise FileExistsError(17, "File exists", ".telegramagent")

    monkeypatch.setattr(session_log, "append_turn", fail_append_turn)
    bot = TelegramBot(
        telegram=telegram,
        agent=FakeAgent(),
        proactive_tool=proactive,
        session_log=session_log,
        task_queue=TaskQueue(),
    )

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 456},
                "text": "https://example.com",
            },
        }
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert telegram.edited == [(123, 100, "背景整理完成")]
    assert bot.task_queue is not None
    assert [task.status for task in bot.task_queue.list_records(chat_id=123)] == ["completed"]
    assert bot.histories[123] == [("user", "https://example.com"), ("assistant", "背景整理完成")]


@pytest.mark.asyncio
async def test_background_task_failure_uses_generic_user_reply() -> None:
    class ExplodingProactiveTool:
        async def handle(
            self,
            text: str,
            *,
            chat_id: int,
            agent: object,
            history: Sequence[tuple[str, str]],
        ) -> str | None:
            raise FileExistsError(17, "File exists", ".telegramagent")

    telegram = FakeTelegram()
    bot = TelegramBot(
        telegram=telegram,
        agent=FakeAgent(),
        proactive_tool=ExplodingProactiveTool(),
        task_queue=TaskQueue(),
    )

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 456},
                "text": "https://example.com",
            },
        }
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert telegram.edited == [(123, 100, "任務執行時遇到內部錯誤，沒有完成；請稍後再試。")]
    assert ".telegramagent" not in telegram.edited[0][2]


@pytest.mark.asyncio
async def test_background_status_send_failure_does_not_log_task_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingStatusTelegram(FakeTelegram):
        async def send_message(
            self,
            chat_id: int,
            text: str,
            *,
            reply_to_message_id: int | None = None,
        ) -> int | None:
            raise httpx.ConnectError("network unavailable")

    class FakeLogger:
        def __init__(self) -> None:
            self.warning_calls: list[tuple[object, ...]] = []
            self.exception_calls: list[tuple[object, ...]] = []

        def debug(self, *args: object) -> None:
            pass

        def info(self, *args: object) -> None:
            pass

        def warning(self, *args: object) -> None:
            self.warning_calls.append(args)

        def exception(self, *args: object) -> None:
            self.exception_calls.append(args)

    fake_logger = FakeLogger()
    monkeypatch.setattr("telegramagent.telegram.logger", fake_logger)
    bot = TelegramBot(
        telegram=FailingStatusTelegram(),
        agent=FakeAgent(),
        proactive_tool=FakeProactiveTool(["背景整理完成"]),
        task_queue=TaskQueue(),
    )

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 456},
                "text": "https://example.com",
            },
        }
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert fake_logger.exception_calls == []
    assert fake_logger.warning_calls


@pytest.mark.asyncio
async def test_proactive_tool_falls_back_to_agent_when_no_action_matches() -> None:
    proactive = FakeProactiveTool([None])
    bot = TelegramBot(telegram=FakeTelegram(), agent=FakeAgent(), proactive_tool=proactive)

    assert await build_reply(bot, 123, "你好") == "AI: 你好 (0)"
    assert proactive.calls == [("你好", 123, [])]


@pytest.mark.asyncio
async def test_handle_update_sends_agent_image_artifacts_after_text_reply() -> None:
    telegram = FakeTelegram()
    agent = FakeArtifactAgent(
        AgentReply(
            text="這是股價圖。", images=(GeneratedImage(data=b"webp", media_type="image/webp", filename="chart.webp"),)
        )
    )
    bot = TelegramBot(telegram=telegram, agent=agent)

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 456},
                "text": "畫 AAPL 股價圖",
            },
        }
    )

    assert telegram.sent == [(123, "這是股價圖。", 10)]
    assert telegram.sent_photos == [(123, b"webp", None, "chart.webp", "image/webp", 100)]
    assert bot.histories[123] == [("user", "畫 AAPL 股價圖"), ("assistant", "這是股價圖。")]


@pytest.mark.asyncio
async def test_context_tool_runs_before_builtin_commands(tmp_path: Path) -> None:
    context = load_context_file(_write(tmp_path / "SOUL.md", "soul text"), label="SOUL.md", max_chars=1000)

    async def reload_context():
        return context

    bot = TelegramBot(
        telegram=FakeTelegram(),
        agent=FakeAgent(),
        tools=[
            ContextManagementTool(
                command_name="soul",
                display_name="SOUL.md",
                current_context=lambda: context,
                reload_context=reload_context,
                admins={456},
            )
        ],
    )

    assert "soul text" in await build_reply(bot, 123, "/soul show", user_id=456)
    assert await build_reply(bot, 123, "/soul show", user_id=999) == "你沒有權限管理 SOUL.md。"


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

    reply = await build_reply(bot, 123, "/skills add owner/repo --skill chat-style", user_id=456)

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

    reply = await build_reply(bot, 123, "安裝 narumiruna/skills 的 skills 所有", user_id=456)

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

    reply = await build_reply(bot, 123, "/skills add owner/repo", user_id=456)

    assert reply == "你沒有權限管理 Agent Skills。"
    assert installer.add_calls == []


def test_skill_installer_builds_non_interactive_npx_add_command(tmp_path: Path) -> None:
    installer = FakeCommandSkillInstaller(project_root=tmp_path)

    result = asyncio.run(installer.add("owner/repo --skill chat-style"))

    assert result.ok
    assert installer.commands == [
        [
            "npx",
            "--yes",
            "skills@1.5.7",
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
        [
            "npx",
            "--yes",
            "skills@1.5.7",
            "add",
            "owner/repo",
            "--skill",
            "*",
            "--agent",
            "universal",
            "--yes",
            "--copy",
        ]
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


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path
