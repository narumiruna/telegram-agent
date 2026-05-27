from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic_ai.messages import BinaryContent
from pydantic_ai.messages import ModelRequest
from pydantic_ai.messages import ToolReturnPart

from telegramagent.actions import ActionContent
from telegramagent.actions import ProactiveActionTool
from telegramagent.actions import UrlContext
from telegramagent.context_files import ContextManagementTool
from telegramagent.context_files import load_context_file
from telegramagent.images import AgentReply
from telegramagent.images import GeneratedImage
from telegramagent.images import ImageAttachment
from telegramagent.llm import ChatAgent
from telegramagent.llm import TopicEndAgent
from telegramagent.session import SessionLog
from telegramagent.skills import AgentSkill
from telegramagent.skills import SkillInstaller
from telegramagent.skills import SkillInstallResult
from telegramagent.skills import SkillManagementTool
from telegramagent.skills import format_skills_for_instructions
from telegramagent.skills import load_agent_skills
from telegramagent.tasks import TaskQueue
from telegramagent.telegram import TelegramBot
from telegramagent.telegram import TelegramClient
from telegramagent.telegram import TelegramFile
from telegramagent.telegram import TelegramUpdate
from telegramagent.telegraph_pages import TelegraphPublishError
from telegramagent.telegraph_pages import _sanitize_telegraph_html
from telegramagent.telegraph_pages import format_telegraph_html
from telegramagent.telegraph_pages import telegraph_page_title


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, int | None]] = []
        self.sent_photos: list[tuple[int, bytes, str | None, str, str, int | None]] = []
        self.edited: list[tuple[int, int, str]] = []
        self.files: dict[str, TelegramFile] = {}
        self.file_contents: dict[str, bytes] = {}
        self.downloaded_paths: list[str] = []
        self.next_message_id = 100

    async def get_me(self) -> dict[str, object]:
        return {"username": "fakebot"}

    async def get_updates(self, *, offset: int | None, poll_timeout: int = 30) -> list[TelegramUpdate]:
        return []

    async def get_file(self, file_id: str) -> TelegramFile:
        return self.files.get(file_id, {"file_id": file_id, "file_path": f"photos/{file_id}.jpg"})

    async def download_file(self, file_path: str) -> bytes:
        self.downloaded_paths.append(file_path)
        return self.file_contents.get(file_path, b"image-bytes")

    async def send_message(self, chat_id: int, text: str, *, reply_to_message_id: int | None = None) -> int | None:
        self.sent.append((chat_id, text, reply_to_message_id))
        message_id = self.next_message_id
        self.next_message_id += 1
        return message_id

    async def send_photo(
        self,
        chat_id: int,
        photo: bytes,
        *,
        caption: str | None = None,
        filename: str = "image.png",
        media_type: str = "image/png",
        reply_to_message_id: int | None = None,
    ) -> int | None:
        self.sent_photos.append((chat_id, photo, caption, filename, media_type, reply_to_message_id))
        message_id = self.next_message_id
        self.next_message_id += 1
        return message_id

    async def edit_message_text(self, chat_id: int, message_id: int, text: str) -> None:
        self.edited.append((chat_id, message_id, text))


class FakeAgent:
    async def reply(
        self,
        prompt: str,
        *,
        history: Sequence[tuple[str, str]],
        images: Sequence[ImageAttachment] = (),
    ) -> str:
        if images:
            return f"AI: {prompt} ({len(history)}, images={len(images)})"
        return f"AI: {prompt} ({len(history)})"


class FakeArtifactAgent:
    def __init__(self, agent_reply: AgentReply) -> None:
        self.agent_reply = agent_reply
        self.calls: list[tuple[str, Sequence[tuple[str, str]], list[ImageAttachment]]] = []

    async def reply_with_artifacts(
        self,
        prompt: str,
        *,
        history: Sequence[tuple[str, str]],
        images: Sequence[ImageAttachment] = (),
    ) -> AgentReply:
        self.calls.append((prompt, [*history], [*images]))
        return self.agent_reply

    async def reply(
        self,
        prompt: str,
        *,
        history: Sequence[tuple[str, str]],
        images: Sequence[ImageAttachment] = (),
    ) -> str:
        return self.agent_reply.text


class FakeVisionAgent:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Sequence[tuple[str, str]], list[ImageAttachment]]] = []

    async def reply(
        self,
        prompt: str,
        *,
        history: Sequence[tuple[str, str]],
        images: Sequence[ImageAttachment] = (),
    ) -> str:
        image_list = [*images]
        self.calls.append((prompt, [*history], image_list))
        return f"vision: {prompt} ({len(image_list)})"


class FakeImageGenerator:
    def __init__(self, image: GeneratedImage) -> None:
        self.image = image
        self.prompts: list[str] = []

    async def generate(self, prompt: str) -> GeneratedImage:
        self.prompts.append(prompt)
        return self.image


class FakeTelegraphPublisher:
    def __init__(self, url: str = "https://telegra.ph/long-reply", error: TelegraphPublishError | None = None) -> None:
        self.url = url
        self.error = error
        self.published: list[str] = []

    async def publish(self, text: str) -> str:
        self.published.append(text)
        if self.error is not None:
            raise self.error
        return self.url


class FakeRunResult:
    def __init__(self, output: str, messages: Sequence[object] = ()) -> None:
        self.output = output
        self.messages = list(messages)

    def new_messages(self) -> list[object]:
        return self.messages


class FakeRunnableAgent:
    def __init__(self, output: str = "回覆", messages: Sequence[object] = ()) -> None:
        self.output = output
        self.messages = [*messages]
        self.prompts: list[Any] = []
        self.message_history_lengths: list[int] = []

    async def run(self, user_prompt: Any, **kwargs: Any) -> FakeRunResult:
        self.prompts.append(user_prompt)
        self.message_history_lengths.append(len(kwargs.get("message_history") or []))
        return FakeRunResult(self.output, messages=self.messages)


class FakeCommandSkillInstaller(SkillInstaller):
    def __init__(self, project_root: Path, command_prefix: Sequence[str] = ("npx", "--yes", "skills@1.5.7")) -> None:
        super().__init__(project_root=project_root, command_prefix=command_prefix)
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


class FakeTranscriptFetcher:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def fetch(self, video_id: str, *, languages: Sequence[str]) -> ActionContent:
        self.calls.append(video_id)
        return ActionContent(
            title=f"video {video_id}",
            source_url=f"https://youtu.be/{video_id}",
            body="字幕內容",
            content_type="youtube_transcript",
        )


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

    assert await first_bot.build_reply(123, "https://youtu.be/video") == "AI: https://youtu.be/video (0)"

    second_proactive = FakeProactiveTool(["沿用前面的網址完成"])
    second_bot = TelegramBot(
        telegram=FakeTelegram(), agent=FakeAgent(), proactive_tool=second_proactive, session_log=session_log
    )

    assert await second_bot.build_reply(123, "有字幕") == "沿用前面的網址完成"
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

    first_reply = await first_bot.build_reply(123, "https://www.youtube.com/watch?v=h_7fdZjUKE8")

    assert "字幕內容" in first_reply

    second_fetcher = FakeTranscriptFetcher()
    second_bot = TelegramBot(
        telegram=FakeTelegram(),
        agent=FakeAgent(),
        proactive_tool=ProactiveActionTool(transcript_fetcher=second_fetcher),
        session_log=session_log,
    )

    await second_bot.build_reply(123, "你用 kabigon 抓抓看阿")

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
async def test_group_mention_reply_photo_downloads_replied_photo_for_vision() -> None:
    telegram = FakeTelegram()
    telegram.files["large"] = {"file_id": "large", "file_path": "photos/large.jpg", "file_size": 11}
    telegram.file_contents["photos/large.jpg"] = b"replied-large-image"
    agent = FakeArtifactAgent(AgentReply("ok"))
    bot = TelegramBot(telegram=telegram, agent=agent, bot_username="fakebot", bot_user_id=42)

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 11,
                "chat": {"id": -100, "type": "supergroup"},
                "from": {"id": 789, "username": "bob"},
                "reply_to_message": {
                    "message_id": 10,
                    "from": {"id": 456, "username": "alice"},
                    "photo": [
                        {"file_id": "small", "width": 100, "height": 100, "file_size": 30},
                        {"file_id": "large", "width": 800, "height": 600, "file_size": 11},
                    ],
                },
                "text": "@FakeBot 這張圖是什麼？",
            },
        }
    )

    assert telegram.downloaded_paths == ["photos/large.jpg"]
    assert telegram.sent == [(-100, "ok", 11)]
    prompt, history, images = agent.calls[0]
    assert history == []
    assert "Replied message context:\nSender: @alice\nType: photo" in prompt
    assert "Chat ID: -100" in prompt
    assert "Message ID: 10" in prompt
    assert "Content: 使用者回覆的是一則 photo 訊息，無文字內容" in prompt
    assert "Current user message:\n這張圖是什麼？" in prompt
    assert images == [
        ImageAttachment(data=b"replied-large-image", media_type="image/jpeg", filename="replied-telegram-photo.jpg")
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
async def test_proactive_tool_falls_back_to_agent_when_no_action_matches() -> None:
    proactive = FakeProactiveTool([None])
    bot = TelegramBot(telegram=FakeTelegram(), agent=FakeAgent(), proactive_tool=proactive)

    assert await bot.build_reply(123, "你好") == "AI: 你好 (0)"
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
async def test_group_plain_text_is_recorded_as_passive_context_without_reply() -> None:
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
    assert bot.histories[-100] == [("user", "[群組旁聽訊息 from user_id=456] 大家好")]


@pytest.mark.asyncio
async def test_group_passive_context_can_be_disabled() -> None:
    telegram = FakeTelegram()
    bot = TelegramBot(
        telegram=telegram,
        agent=FakeAgent(),
        bot_username="fakebot",
        bot_user_id=42,
        group_passive_context_enabled=False,
    )

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
    assert bot.histories == {}


@pytest.mark.asyncio
async def test_group_passive_context_is_used_when_bot_is_later_addressed() -> None:
    telegram = FakeTelegram()
    bot = TelegramBot(telegram=telegram, agent=FakeAgent(), bot_username="fakebot", bot_user_id=42)

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": -100, "type": "supergroup"},
                "from": {"id": 456, "username": "alice"},
                "text": "我想吃牛肉麵",
            },
        }
    )
    await bot.handle_update(
        {
            "update_id": 2,
            "message": {
                "message_id": 11,
                "chat": {"id": -100, "type": "supergroup"},
                "from": {"id": 789},
                "text": "@FakeBot 剛剛大家說什麼？",
            },
        }
    )

    assert telegram.sent == [(-100, "AI: 剛剛大家說什麼？ (1)", 11)]
    assert bot.histories[-100] == [
        ("user", "[群組旁聽訊息 from @alice] 我想吃牛肉麵"),
        ("user", "剛剛大家說什麼？"),
        ("assistant", "AI: 剛剛大家說什麼？ (1)"),
    ]


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
async def test_group_mention_reply_includes_replied_text_context_in_llm_prompt() -> None:
    telegram = FakeTelegram()
    agent = FakeArtifactAgent(AgentReply("ok"))
    bot = TelegramBot(telegram=telegram, agent=agent, bot_username="fakebot", bot_user_id=42)

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 11,
                "chat": {"id": -100, "type": "supergroup"},
                "from": {"id": 789, "username": "bob"},
                "reply_to_message": {
                    "message_id": 10,
                    "date": 1_764_000_000,
                    "from": {"id": 456, "username": "alice"},
                    "text": "我覺得 Gemini 目前 coding 強，但 agentic coding 還弱",
                },
                "text": "@FakeBot 你怎麼看？",
            },
        }
    )

    assert telegram.sent == [(-100, "ok", 11)]
    prompt = agent.calls[0][0]
    assert "Replied message context:\nSender: @alice\nType: text" in prompt
    assert "Date: 2025-11-24T16:00:00+00:00" in prompt
    assert "Content: 我覺得 Gemini 目前 coding 強，但 agentic coding 還弱" in prompt
    assert "Current user message:\n你怎麼看？" in prompt
    assert "Treat the replied message and extracted URL content as the primary object" in prompt


@pytest.mark.asyncio
async def test_group_mention_reply_includes_non_text_context_placeholder() -> None:
    telegram = FakeTelegram()
    agent = FakeArtifactAgent(AgentReply("ok"))
    bot = TelegramBot(telegram=telegram, agent=agent, bot_username="fakebot", bot_user_id=42)

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 11,
                "chat": {"id": -100, "type": "supergroup"},
                "from": {"id": 789, "username": "bob"},
                "reply_to_message": {
                    "message_id": 10,
                    "from": {"id": 456, "first_name": "Alice"},
                    "sticker": {"file_id": "sticker-1"},
                },
                "text": "@FakeBot 這是什麼？",
            },
        }
    )

    assert telegram.sent == [(-100, "ok", 11)]
    prompt = agent.calls[0][0]
    assert "Replied message context:\nSender: Alice\nType: sticker" in prompt
    assert "Content: 使用者回覆的是一則 sticker 訊息，無文字內容" in prompt
    assert "Current user message:\n這是什麼？" in prompt


@pytest.mark.asyncio
async def test_group_mention_reply_to_x_url_includes_extracted_url_context() -> None:
    telegram = FakeTelegram()
    agent = FakeArtifactAgent(AgentReply("ok"))
    extractor_calls: list[str] = []

    async def extract(url: str) -> UrlContext:
        extractor_calls.append(url)
        return UrlContext(
            url=url,
            final_url="https://x.com/IEObserve/status/2058190539988898008",
            source_type="x_post",
            fetched_at="2026-05-23T00:00:00+00:00",
            extraction_status="partial",
            title="IEObserve on X",
            author="@IEObserve",
            text="這是一則擷取到的 X 貼文摘要文字。",
        )

    bot = TelegramBot(
        telegram=telegram,
        agent=agent,
        bot_username="fakebot",
        bot_user_id=42,
        url_context_extractor=extract,
    )

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 11,
                "chat": {"id": -100, "type": "supergroup"},
                "from": {"id": 789, "username": "bob"},
                "reply_to_message": {
                    "message_id": 10,
                    "from": {"id": 456, "username": "alice"},
                    "text": "https://x.com/IEObserve/status/2058190539988898008?s=20",
                },
                "text": "@FakeBot",
            },
        }
    )

    assert telegram.sent == [(-100, "ok", 11)]
    assert extractor_calls == ["https://x.com/IEObserve/status/2058190539988898008?s=20"]
    prompt = agent.calls[0][0]
    assert "URLs found:\n- https://x.com/IEObserve/status/2058190539988898008?s=20" in prompt
    assert "Extracted URL context:" in prompt
    assert "Source type: x_post" in prompt
    assert "Extraction status: partial" in prompt
    assert "Title: IEObserve on X" in prompt
    assert "Author: @IEObserve" in prompt
    assert "Content:\n這是一則擷取到的 X 貼文摘要文字。" in prompt
    assert "Current user message:\n（使用者只提及 bot，未提供額外文字。）" in prompt
    assert "respond directly with a useful interpretation/commentary/summary" in prompt


@pytest.mark.asyncio
async def test_group_mention_reply_url_entities_prioritize_replied_urls() -> None:
    telegram = FakeTelegram()
    agent = FakeArtifactAgent(AgentReply("ok"))
    extractor_calls: list[str] = []

    async def extract(url: str) -> UrlContext:
        extractor_calls.append(url)
        return UrlContext(
            url=url,
            final_url=url,
            source_type="webpage",
            fetched_at="2026-05-23T00:00:00+00:00",
            extraction_status="success",
            title="頁面",
            text=f"擷取內容: {url}",
        )

    bot = TelegramBot(
        telegram=telegram,
        agent=agent,
        bot_username="fakebot",
        bot_user_id=42,
        url_context_extractor=extract,
    )

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 11,
                "chat": {"id": -100, "type": "supergroup"},
                "from": {"id": 789, "username": "bob"},
                "reply_to_message": {
                    "message_id": 10,
                    "from": {"id": 456, "username": "alice"},
                    "text": "看這篇",
                    "entities": [
                        {
                            "type": "text_link",
                            "offset": 0,
                            "length": 3,
                            "url": "https://example.com/replied",
                        }
                    ],
                },
                "text": "@FakeBot 再看 https://example.com/current",
                "entities": [
                    {"type": "mention", "offset": 0, "length": 8},
                    {"type": "url", "offset": 12, "length": 27},
                ],
            },
        }
    )

    assert telegram.sent == [(-100, "ok", 11)]
    assert extractor_calls == ["https://example.com/replied", "https://example.com/current"]
    prompt = agent.calls[0][0]
    assert prompt.index("- https://example.com/replied") < prompt.index("- https://example.com/current")


@pytest.mark.asyncio
async def test_group_mention_reply_photo_caption_includes_caption_context() -> None:
    telegram = FakeTelegram()
    telegram.files["photo-1"] = {"file_id": "photo-1", "file_path": "photos/photo-1.jpg", "file_size": 9}
    telegram.file_contents["photos/photo-1.jpg"] = b"caption-image"
    agent = FakeArtifactAgent(AgentReply("ok"))
    bot = TelegramBot(telegram=telegram, agent=agent, bot_username="fakebot", bot_user_id=42)

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 11,
                "chat": {"id": -100, "type": "supergroup"},
                "from": {"id": 789, "username": "bob"},
                "reply_to_message": {
                    "message_id": 10,
                    "from": {"id": 456, "first_name": "Alice"},
                    "photo": [{"file_id": "photo-1", "width": 100, "height": 100}],
                    "caption": "這張圖在講模型比較",
                },
                "text": "@FakeBot",
            },
        }
    )

    assert telegram.downloaded_paths == ["photos/photo-1.jpg"]
    assert telegram.sent == [(-100, "ok", 11)]
    prompt, _history, images = agent.calls[0]
    assert "Type: photo" in prompt
    assert "Content: 使用者回覆的是一則 photo 訊息，caption: 這張圖在講模型比較" in prompt
    assert "Current user message:\n（使用者只提及 bot，未提供額外文字。）" in prompt
    assert images == [
        ImageAttachment(data=b"caption-image", media_type="image/jpeg", filename="replied-telegram-photo.jpg")
    ]


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


@pytest.mark.asyncio
async def test_telegram_client_formats_commonmark_markdown_as_safe_html() -> None:
    payloads: list[dict[str, Any]] = []
    text = (
        "## 🌅 清晨趕車到新潟，旅程一開始就很有戲\r\n"
        "\n"
        "影片一開場就是早晨 6 點，主角其實有點遲到。\n"
        "\n"
        "## 🌸 櫻花、城跡、山景，把日本春天拍得很滿\n"
        "\n"
        "這一趟重點是 **櫻花** 和 **城堡遺跡**。\n"
        "\n"
        "URL: https://example.com/a_b?x=1&y=2\n"
        "\n"
        "特殊符號: _ * [ ] ( ) ~ ` > # + - = | { } . !\n"
        "`code`\n"
        "```text\ncode block > should be escaped\n```"
        "\x00\x08"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.read().decode()))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 99}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        telegram = TelegramClient("token", http_client=client)
        message_id = await telegram.send_message(123, text)

    assert message_id == 99
    assert payloads[0]["parse_mode"] == "HTML"
    assert "##" not in payloads[0]["text"]
    assert "**" not in payloads[0]["text"]
    assert "<b>🌅 清晨趕車到新潟，旅程一開始就很有戲</b>" in payloads[0]["text"]
    assert "<b>🌸 櫻花、城跡、山景，把日本春天拍得很滿</b>" in payloads[0]["text"]
    assert "這一趟重點是 <b>櫻花</b> 和 <b>城堡遺跡</b>。" in payloads[0]["text"]
    assert (
        '<a href="https://example.com/a_b?x=1&amp;y=2">https://example.com/a_b?x=1&amp;y=2</a>' in payloads[0]["text"]
    )
    assert "特殊符號: _ * [ ] ( ) ~ ` &gt; # + - = | { } . !" in payloads[0]["text"]
    assert "<code>code</code>" in payloads[0]["text"]
    assert "<pre>code block &gt; should be escaped\n</pre>" in payloads[0]["text"]
    assert "\x00" not in payloads[0]["text"]
    assert "\x08" not in payloads[0]["text"]


def test_telegraph_html_formats_markdown_and_sanitizes_supported_tags() -> None:
    text = (
        "# Page title\n\n"
        "This is **bold** with `code` and <script>plain text</script>.\n\n"
        "```html\n<div>escaped code</div>\n```"
    )

    assert telegraph_page_title(text) == "Page title"
    rendered = format_telegraph_html(text)

    assert "<h3>Page title</h3>" in rendered
    assert "This is <b>bold</b> with <code>code</code>" in rendered
    assert "&lt;script&gt;plain text&lt;/script&gt;" in rendered
    assert "<pre>&lt;div&gt;escaped code&lt;/div&gt;\n</pre>" in rendered


def test_telegraph_html_sanitizer_remaps_and_escapes_unsupported_html() -> None:
    rendered = _sanitize_telegraph_html(
        '<h1>Title</h1><del>Gone</del><span class="x">No</span><a href="https://example.com" rel="x">Link</a>'
    )

    assert rendered == (
        '<h3>Title</h3><s>Gone</s>&lt;span class="x"&gt;No&lt;/span&gt;<a href="https://example.com">Link</a>'
    )


@pytest.mark.asyncio
async def test_telegram_client_publishes_messages_over_1000_chars_to_telegraph() -> None:
    payloads: list[dict[str, Any]] = []
    publisher = FakeTelegraphPublisher(url="https://telegra.ph/福岡-05-27")
    text = "x" * 1001

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.read().decode()))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 99}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        telegram = TelegramClient("token", http_client=client, telegraph_publisher=publisher)
        message_id = await telegram.send_message(123, text, reply_to_message_id=55)

    assert message_id == 99
    assert publisher.published == [text]
    expected_url = '<a href="https://telegra.ph/%E7%A6%8F%E5%B2%A1-05-27">https://telegra.ph/福岡-05-27</a>'
    assert payloads == [
        {
            "chat_id": 123,
            "text": expected_url,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
            "reply_to_message_id": 55,
        }
    ]


@pytest.mark.asyncio
async def test_telegram_client_does_not_publish_messages_at_1000_chars() -> None:
    payloads: list[dict[str, Any]] = []
    publisher = FakeTelegraphPublisher()
    text = "x" * 1000

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.read().decode()))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 99}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        telegram = TelegramClient("token", http_client=client, telegraph_publisher=publisher)
        message_id = await telegram.send_message(123, text)

    assert message_id == 99
    assert publisher.published == []
    assert payloads[0]["text"] == text


@pytest.mark.asyncio
async def test_telegram_client_edits_long_messages_to_telegraph_url() -> None:
    payloads: list[dict[str, Any]] = []
    publisher = FakeTelegraphPublisher(url="https://telegra.ph/status")

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.read().decode()))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 99}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        telegram = TelegramClient("token", http_client=client, telegraph_publisher=publisher)
        await telegram.edit_message_text(123, 99, "x" * 1001)

    assert publisher.published == ["x" * 1001]
    assert payloads == [
        {
            "chat_id": 123,
            "message_id": 99,
            "text": '<a href="https://telegra.ph/status">https://telegra.ph/status</a>',
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }
    ]


@pytest.mark.asyncio
async def test_telegram_client_falls_back_to_chunks_when_telegraph_publish_fails() -> None:
    payloads: list[dict[str, Any]] = []
    publisher = FakeTelegraphPublisher(error=TelegraphPublishError("no page"))
    text = "x" * 4100

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.read().decode()))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 99}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        telegram = TelegramClient("token", http_client=client, telegraph_publisher=publisher)
        await telegram.send_message(123, text)

    assert publisher.published == [text]
    assert [payload["text"] for payload in payloads] == ["x" * 4096, "x" * 4]


@pytest.mark.asyncio
async def test_telegram_client_calls_bot_api() -> None:
    requests: list[tuple[str, dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((str(request.url), dict(request.headers)))
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [{"update_id": 1}]})
        if request.url.path.endswith("/sendMessage"):
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 99}})
        return httpx.Response(200, json={"ok": True, "result": {}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        telegram = TelegramClient("token", http_client=client)
        updates = await telegram.get_updates(offset=2, poll_timeout=1)
        message_id = await telegram.send_message(123, "hello")
        await telegram.edit_message_text(123, 99, "done")

    assert updates == [{"update_id": 1}]
    assert message_id == 99
    assert requests[0][0] == "https://api.telegram.org/bottoken/getUpdates"
    assert requests[1][0] == "https://api.telegram.org/bottoken/sendMessage"
    assert requests[2][0] == "https://api.telegram.org/bottoken/editMessageText"


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
    assert "自然、克制地加入少量 emoji" in captured["instructions"]
    assert "必須把它當成選擇上一則訊息中相同編號的選項" in captured["instructions"]
    assert runnable.prompts == ["問題"]
    assert runnable.message_history_lengths == [2]


@pytest.mark.asyncio
async def test_chat_agent_passes_images_as_binary_user_content() -> None:
    runnable = FakeRunnableAgent("已看圖")

    def factory(instructions: str) -> FakeRunnableAgent:
        return runnable

    agent = ChatAgent(api_key="key", model="model", agent_factory=factory)
    reply = await agent.reply(
        "請描述圖片",
        images=[ImageAttachment(data=b"image-bytes", media_type="image/png", filename="sample.png")],
    )

    assert reply == "已看圖"
    prompt = runnable.prompts[0]
    assert isinstance(prompt, list)
    assert prompt[0] == "請描述圖片"
    assert prompt[1] == "圖片 1: sample.png"
    assert prompt[2].data == b"image-bytes"
    assert prompt[2].media_type == "image/png"


@pytest.mark.asyncio
async def test_chat_agent_extracts_tool_return_images_as_artifacts() -> None:
    tool_result = ModelRequest(
        parts=[
            ToolReturnPart(
                tool_name="yfinance_get_price_history",
                content=BinaryContent(data=b"webp", media_type="image/webp"),
            )
        ]
    )
    runnable = FakeRunnableAgent("這是圖表", messages=[tool_result])

    def factory(instructions: str) -> FakeRunnableAgent:
        return runnable

    agent = ChatAgent(api_key="key", model="model", agent_factory=factory)
    reply = await agent.reply_with_artifacts("畫 AAPL 股價圖")

    assert reply.text == "這是圖表"
    assert reply.images == (
        GeneratedImage(data=b"webp", media_type="image/webp", filename="yfinance_get_price_history.webp"),
    )


@pytest.mark.asyncio
async def test_chat_agent_injects_runtime_capabilities_into_pydantic_instructions() -> None:
    captured: dict[str, str] = {}

    def factory(instructions: str) -> FakeRunnableAgent:
        captured["instructions"] = instructions
        return FakeRunnableAgent()

    agent = ChatAgent(
        api_key="key", model="model", capability_summary="- external_loader.kabigon: unavailable", agent_factory=factory
    )
    await agent.reply("問題")

    assert "Runtime capabilities" in captured["instructions"]
    assert "external_loader.kabigon: unavailable" in captured["instructions"]
    assert (
        "只有 runtime capabilities、Pydantic AI tools 或已啟用 MCP toolsets 中列出的工具才是真的可執行"
        in captured["instructions"]
    )
    assert "不構成投資建議" in captured["instructions"]


@pytest.mark.asyncio
async def test_chat_agent_registers_kabigon_load_url_tool_and_mcp_toolsets(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    sentinel_tool = object()
    sentinel_toolset = object()

    class FakePydanticAgent:
        def __init__(
            self,
            model: object,
            *,
            instructions: str,
            tools: Sequence[object],
            toolsets: Sequence[object],
            tool_timeout: int,
        ) -> None:
            captured["tools"] = tools
            captured["toolsets"] = toolsets
            captured["tool_timeout"] = tool_timeout

        async def run(self, user_prompt: str, **kwargs: Any) -> FakeRunResult:
            return FakeRunResult("ok")

    monkeypatch.setattr("telegramagent.llm.PydanticAgent", FakePydanticAgent)

    agent = ChatAgent(api_key="key", model="model", mcp_toolsets=[sentinel_toolset], tools=[sentinel_tool])
    reply = await agent.reply("問題")

    assert reply == "ok"
    assert getattr(captured["tools"][0], "__name__", "") == "kabigon_load_url"
    assert captured["tools"][1] is sentinel_tool
    assert captured["toolsets"] == (sentinel_toolset,)
    assert captured["tool_timeout"] == 180


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
