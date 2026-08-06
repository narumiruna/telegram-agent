from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import NotRequired
from typing import Protocol
from typing import TypedDict

from telegramagent.agent_runtime import AgentEventHandler
from telegramagent.agent_runtime import AgentSubmission
from telegramagent.images import GeneratedImage
from telegramagent.images import ImageAttachment
from telegramagent.url_context import UrlContext


class TelegramChat(TypedDict):
    id: int
    type: str


class TelegramUser(TypedDict, total=False):
    id: int
    is_bot: bool
    first_name: str
    username: str


class TelegramPhotoSize(TypedDict, total=False):
    file_id: str
    file_unique_id: str
    width: int
    height: int
    file_size: int


class TelegramDocument(TypedDict, total=False):
    file_id: str
    file_unique_id: str
    file_name: str
    mime_type: str
    file_size: int


class TelegramMessageEntity(TypedDict, total=False):
    type: str
    offset: int
    length: int
    url: str


class TelegramFile(TypedDict, total=False):
    file_id: str
    file_unique_id: str
    file_size: int
    file_path: str


TelegramMessage = TypedDict(
    "TelegramMessage",
    {
        "message_id": int,
        "chat": TelegramChat,
        "date": int,
        "text": str,
        "entities": list[TelegramMessageEntity],
        "caption": str,
        "caption_entities": list[TelegramMessageEntity],
        "photo": list[TelegramPhotoSize],
        "video": object,
        "document": TelegramDocument,
        "sticker": object,
        "voice": object,
        "audio": object,
        "animation": object,
        "video_note": object,
        "from": TelegramUser,
        "sender_chat": object,
        "reply_to_message": object,
    },
    total=False,
)


class TelegramUpdate(TypedDict):
    update_id: int
    message: NotRequired[TelegramMessage]


class Agent(Protocol):
    async def reply(
        self,
        prompt: str,
        *,
        history: Sequence[tuple[str, str]],
        images: Sequence[ImageAttachment] = (),
    ) -> str: ...


class AgentRuntimeGateway(Protocol):
    async def submit(
        self,
        chat_id: int,
        prompt: str,
        *,
        images: Sequence[ImageAttachment] = (),
        event_handler: AgentEventHandler | None = None,
    ) -> AgentSubmission: ...

    async def cancel(self, chat_id: int) -> bool: ...


class ImageGenerator(Protocol):
    async def generate(self, prompt: str) -> GeneratedImage: ...


class SkillTool(Protocol):
    async def handle(self, text: str, *, chat_id: int, user_id: int | None) -> str | None: ...


class ProactiveTool(Protocol):
    async def handle(
        self,
        text: str,
        *,
        chat_id: int,
        agent: Agent,
        history: Sequence[tuple[str, str]],
    ) -> str | None: ...


class TopicEndJudge(Protocol):
    async def should_end_topic(
        self,
        incoming_text: str,
        *,
        history: Sequence[tuple[str, str]],
        bot_reply_streak: int,
    ) -> bool: ...


class LongMessagePublisher(Protocol):
    async def publish(self, text: str) -> str: ...


class UrlContextLoader(Protocol):
    async def __call__(self, url: str) -> UrlContext: ...


class TelegramGateway(Protocol):
    async def get_me(self) -> dict[str, object]: ...

    async def get_updates(self, *, offset: int | None, poll_timeout: int = 30) -> list[TelegramUpdate]: ...

    async def get_file(self, file_id: str) -> TelegramFile: ...

    async def download_file(self, file_path: str) -> bytes: ...

    async def send_message(self, chat_id: int, text: str, *, reply_to_message_id: int | None = None) -> int | None: ...

    async def send_photo(
        self,
        chat_id: int,
        photo: bytes,
        *,
        caption: str | None = None,
        filename: str = "image.png",
        media_type: str = "image/png",
        reply_to_message_id: int | None = None,
    ) -> int | None: ...

    async def edit_message_text(self, chat_id: int, message_id: int, text: str) -> None: ...


@dataclass(frozen=True)
class TelegramImageRef:
    file_id: str
    media_type: str
    filename: str
    file_size: int | None = None


@dataclass(frozen=True)
class ReplyMessageContext:
    sender: str
    message_type: str
    content: str
    chat_id: int | None = None
    message_date: str | None = None
    message_id: int | None = None
    urls_found: tuple[str, ...] = ()
    url_contexts: tuple[UrlContext, ...] = ()
