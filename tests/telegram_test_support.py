from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from telegramagent.actions import ActionContent
from telegramagent.images import AgentReply
from telegramagent.images import GeneratedImage
from telegramagent.images import ImageAttachment
from telegramagent.skills import SkillInstaller
from telegramagent.skills import SkillInstallResult
from telegramagent.telegram import TelegramBot
from telegramagent.telegram import TelegramFile
from telegramagent.telegram import TelegramUpdate
from telegramagent.telegraph_pages import TelegraphPublishError


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


async def build_reply(bot: TelegramBot, chat_id: int, text: str, *, user_id: int | None = None) -> str:
    return (await bot.build_response(chat_id, text, user_id=user_id)).text


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
