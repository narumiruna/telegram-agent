from __future__ import annotations

import asyncio
import html
import re
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import replace
from datetime import UTC
from datetime import datetime
from typing import NotRequired
from typing import Protocol
from typing import TypedDict
from typing import cast

import httpx
from loguru import logger
from pydantic_ai.exceptions import AgentRunError

from telegramagent.actions import UrlContext
from telegramagent.actions import extract_url_context
from telegramagent.images import AgentReply
from telegramagent.images import GeneratedImage
from telegramagent.images import ImageAttachment
from telegramagent.images import as_telegram_photo
from telegramagent.session import SessionLog
from telegramagent.tasks import TaskQueue
from telegramagent.telegraph_pages import TelegraphPagePublisher
from telegramagent.telegraph_pages import TelegraphPublishError


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
    message_date: str | None = None
    message_id: int | None = None
    urls_found: tuple[str, ...] = ()
    url_contexts: tuple[UrlContext, ...] = ()


class TelegramImageError(RuntimeError):
    """Raised when a Telegram image cannot be safely downloaded for vision input."""


class TelegramApiError(RuntimeError):
    """Raised when Telegram Bot API returns an error response."""


class TelegramClient:
    def __init__(
        self,
        token: str,
        *,
        http_client: httpx.AsyncClient | None = None,
        telegraph_publisher: LongMessagePublisher | None = None,
        long_message_threshold: int = 1000,
    ) -> None:
        self.token = token
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.http_client = http_client
        self.telegraph_publisher = telegraph_publisher or TelegraphPagePublisher()
        self.long_message_threshold = long_message_threshold

    async def get_me(self) -> dict[str, object]:
        result = await self._request("getMe")
        if not isinstance(result, dict):
            raise TelegramApiError("Telegram getMe did not return an object")
        return cast(dict[str, object], result)

    async def get_updates(self, *, offset: int | None, poll_timeout: int = 30) -> list[TelegramUpdate]:
        payload: dict[str, object] = {
            "timeout": poll_timeout,
            "allowed_updates": ["message"],
        }
        if offset is not None:
            payload["offset"] = offset
        result = await self._request("getUpdates", payload)
        if not isinstance(result, list):
            raise TelegramApiError("Telegram getUpdates did not return a list")
        return cast(list[TelegramUpdate], result)

    async def get_file(self, file_id: str) -> TelegramFile:
        result = await self._request("getFile", {"file_id": file_id})
        if not isinstance(result, dict):
            raise TelegramApiError("Telegram getFile did not return an object")
        return cast(TelegramFile, result)

    async def download_file(self, file_path: str) -> bytes:
        if self.http_client is None:
            async with httpx.AsyncClient(timeout=60) as client:
                return await self._get_file_content(client, file_path)
        return await self._get_file_content(self.http_client, file_path)

    async def send_message(self, chat_id: int, text: str, *, reply_to_message_id: int | None = None) -> int | None:
        last_message_id: int | None = None
        outbound_text = await self._outbound_message_text(text)
        for chunk in _telegram_html_chunks(outbound_text):
            payload: dict[str, object] = {
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": _TELEGRAM_PARSE_MODE,
                "disable_web_page_preview": True,
            }
            if reply_to_message_id is not None:
                payload["reply_to_message_id"] = reply_to_message_id
            result = await self._request("sendMessage", payload)
            if isinstance(result, Mapping):
                result_mapping = cast(Mapping[str, object], result)
                message_id = result_mapping.get("message_id")
                if isinstance(message_id, int):
                    last_message_id = message_id
        return last_message_id

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
        payload: dict[str, object] = {"chat_id": chat_id}
        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = reply_to_message_id
        caption_chunks = _telegram_html_chunks(caption, limit=1024) if caption else []
        if caption_chunks:
            payload["caption"] = caption_chunks[0]
            payload["parse_mode"] = _TELEGRAM_PARSE_MODE

        result = await self._request_multipart(
            "sendPhoto",
            payload,
            files={"photo": (filename, photo, media_type)},
        )
        last_message_id: int | None = None
        if isinstance(result, Mapping):
            result_mapping = cast(Mapping[str, object], result)
            message_id = result_mapping.get("message_id")
            if isinstance(message_id, int):
                last_message_id = message_id
        for chunk in caption_chunks[1:]:
            last_message_id = await self.send_message(chat_id, chunk, reply_to_message_id=last_message_id)
        return last_message_id

    async def edit_message_text(self, chat_id: int, message_id: int, text: str) -> None:
        outbound_text = await self._outbound_message_text(text)
        chunks = _telegram_html_chunks(outbound_text)
        await self._request(
            "editMessageText",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": chunks[0],
                "parse_mode": _TELEGRAM_PARSE_MODE,
                "disable_web_page_preview": True,
            },
        )
        for chunk in chunks[1:]:
            await self.send_message(chat_id, chunk, reply_to_message_id=message_id)

    async def _outbound_message_text(self, text: str) -> str:
        sanitized = _sanitize_telegram_text(text)
        if len(sanitized) <= self.long_message_threshold:
            return text
        try:
            return await self.telegraph_publisher.publish(sanitized)
        except TelegraphPublishError:
            logger.exception("Failed to publish long Telegram message to Telegraph; falling back to Telegram chunks")
            return text

    async def _request(self, method: str, payload: dict[str, object] | None = None) -> object:
        if self.http_client is None:
            async with httpx.AsyncClient(timeout=60) as client:
                return await self._post(client, method, payload)
        return await self._post(self.http_client, method, payload)

    async def _request_multipart(
        self,
        method: str,
        payload: dict[str, object],
        *,
        files: Mapping[str, tuple[str, bytes, str]],
    ) -> object:
        if self.http_client is None:
            async with httpx.AsyncClient(timeout=60) as client:
                return await self._post_multipart(client, method, payload, files=files)
        return await self._post_multipart(self.http_client, method, payload, files=files)

    async def _post(self, client: httpx.AsyncClient, method: str, payload: dict[str, object] | None) -> object:
        response = await client.post(f"{self.base_url}/{method}", json=payload or {})
        return _telegram_result(response)

    async def _post_multipart(
        self,
        client: httpx.AsyncClient,
        method: str,
        payload: dict[str, object],
        *,
        files: Mapping[str, tuple[str, bytes, str]],
    ) -> object:
        response = await client.post(f"{self.base_url}/{method}", data=payload, files=files)
        return _telegram_result(response)

    async def _get_file_content(self, client: httpx.AsyncClient, file_path: str) -> bytes:
        response = await client.get(f"https://api.telegram.org/file/bot{self.token}/{file_path}")
        response.raise_for_status()
        return response.content


class TelegramBot:
    def __init__(
        self,
        *,
        telegram: TelegramGateway,
        agent: Agent,
        whitelist: set[int] | None = None,
        bot_username: str | None = None,
        bot_user_id: int | None = None,
        max_consecutive_replies_to_bots: int = 1,
        group_passive_context_enabled: bool = True,
        topic_end_judge: TopicEndJudge | None = None,
        skill_tool: SkillTool | None = None,
        tools: Sequence[SkillTool] = (),
        proactive_tool: ProactiveTool | None = None,
        session_log: SessionLog | None = None,
        task_queue: TaskQueue | None = None,
        image_input_enabled: bool = True,
        image_max_bytes: int = 8_000_000,
        image_generator: ImageGenerator | None = None,
        url_context_extractor: UrlContextLoader | None = None,
    ) -> None:
        self.telegram = telegram
        self.agent = agent
        self.whitelist = whitelist or set()
        self.bot_username = bot_username
        self.bot_user_id = bot_user_id
        self.max_consecutive_replies_to_bots = max_consecutive_replies_to_bots
        self.group_passive_context_enabled = group_passive_context_enabled
        self.topic_end_judge = topic_end_judge
        self.skill_tool = skill_tool
        self.tools = list(tools)
        self.proactive_tool = proactive_tool
        self.session_log = session_log
        self.task_queue = task_queue
        self.image_input_enabled = image_input_enabled
        self.image_max_bytes = image_max_bytes
        self.image_generator = image_generator
        self.url_context_extractor = url_context_extractor or extract_url_context
        self.bot_reply_streaks: dict[int, int] = {}
        self.histories: dict[int, list[tuple[str, str]]] = {}

    async def run_forever(self) -> None:
        me = await self.telegram.get_me()
        username = me.get("username")
        user_id = me.get("id")
        self.bot_username = username if isinstance(username, str) else None
        self.bot_user_id = user_id if isinstance(user_id, int) else None
        logger.info("Telegram bot started as @{}", self.bot_username or "unknown")
        offset: int | None = None
        while True:
            try:
                updates = await self.telegram.get_updates(offset=offset)
                for update in updates:
                    offset = update["update_id"] + 1
                    await self.handle_update(update)
            except httpx.HTTPStatusError as exc:
                logger.warning(
                    "Telegram polling failed with HTTP status {}; retrying soon",
                    exc.response.status_code,
                )
                await asyncio.sleep(5)
            except (httpx.HTTPError, TelegramApiError) as exc:
                logger.warning("Telegram polling failed with {}; retrying soon", type(exc).__name__)
                await asyncio.sleep(5)

    async def handle_update(self, update: TelegramUpdate) -> None:
        message = update.get("message")
        if not message:
            return
        text = _message_text(message)
        image_ref = _message_image_ref(message)
        chat = message.get("chat")
        if (not text and image_ref is None) or not chat:
            return

        chat_id = chat["id"]
        sender = message.get("from")
        user_id = sender.get("id") if sender else None
        message_id = message.get("message_id")

        if not self._should_respond_to_message(chat=chat, message=message, text=text):
            self._record_passive_group_context(
                chat_id=chat_id,
                message=message,
                text=text,
                image_ref=image_ref,
                user_id=user_id,
            )
            logger.debug("Ignored unaddressed group message in chat_id={}", chat_id)
            return

        prompt = self._strip_bot_mention(text) if text else _DEFAULT_IMAGE_PROMPT
        if await self._should_end_bot_topic(chat_id=chat_id, sender=sender, prompt=prompt):
            logger.info("Topic-end judge stopped bot-to-bot reply loop in chat_id={} sender_id={}", chat_id, user_id)
            return

        if not self._is_allowed(chat_id=chat_id, user_id=user_id):
            logger.warning("Rejected message from unauthorized chat_id={} user_id={}", chat_id, user_id)
            await self.telegram.send_message(
                chat_id, "這個機器人目前沒有開放給你使用。", reply_to_message_id=message_id
            )
            return

        if await self._handle_image_generation_command(chat_id=chat_id, prompt=prompt, reply_to_message_id=message_id):
            return

        images = await self._message_images(chat_id=chat_id, image_ref=image_ref, reply_to_message_id=message_id)
        if images is None:
            return

        if (
            self.task_queue is not None
            and not images
            and not prompt.startswith("/")
            and _is_likely_long_running_action(prompt)
        ):
            background_task = asyncio.create_task(
                self.dispatch_synthetic_message(
                    chat_id=chat_id,
                    text=prompt,
                    reply_to_message_id=message_id,
                    reply_mode="edit-status",
                    synthetic=False,
                )
            )
            background_task.add_done_callback(_log_background_task_error)
            return

        reply_context = await self._reply_context_for_llm(message=message, text=text)
        reply = await self.build_response(chat_id, prompt, user_id=user_id, images=images, reply_context=reply_context)
        await self._send_agent_reply(chat_id, reply, reply_to_message_id=message_id)

    async def build_response(
        self,
        chat_id: int,
        text: str,
        *,
        user_id: int | None = None,
        images: Sequence[ImageAttachment] = (),
        reply_context: ReplyMessageContext | None = None,
    ) -> AgentReply:
        return await self._build_response(
            chat_id,
            text,
            user_id=user_id,
            allow_management=True,
            synthetic=False,
            images=images,
            reply_context=reply_context,
        )

    async def build_reply(
        self,
        chat_id: int,
        text: str,
        *,
        user_id: int | None = None,
        images: Sequence[ImageAttachment] = (),
    ) -> str:
        return (await self.build_response(chat_id, text, user_id=user_id, images=images)).text

    async def dispatch_synthetic_message(
        self,
        *,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
        reply_mode: str = "send",
        synthetic: bool = True,
    ) -> None:
        status_message_id: int | None = None
        if reply_mode == "edit-status":
            status_message_id = await self.telegram.send_message(
                chat_id,
                "處理中…",
                reply_to_message_id=reply_to_message_id,
            )

        async def action(_task: object) -> str:
            reply = await self._build_response(
                chat_id, text, user_id=None, allow_management=False, synthetic=synthetic, images=()
            )
            return reply.text

        if self.task_queue is not None:
            task = await self.task_queue.run(
                chat_id=chat_id,
                description=text[:120],
                action=action,
                priority="next",
                status_message_id=status_message_id,
            )
            reply = task.output if task.status == "completed" else task.error or "任務沒有完成。"
        else:
            reply = await action(object())

        if reply_mode == "edit-status" and status_message_id is not None:
            try:
                await self.telegram.edit_message_text(chat_id, status_message_id, reply)
            except httpx.HTTPError, TelegramApiError:
                logger.exception("Failed to edit synthetic event status message; sending a new message instead")
                await self.telegram.send_message(chat_id, reply, reply_to_message_id=reply_to_message_id)
            return
        await self.telegram.send_message(chat_id, reply, reply_to_message_id=reply_to_message_id)

    async def _build_response(
        self,
        chat_id: int,
        text: str,
        *,
        user_id: int | None,
        allow_management: bool,
        synthetic: bool,
        images: Sequence[ImageAttachment],
        reply_context: ReplyMessageContext | None = None,
    ) -> AgentReply:
        reply = await self._generate_response(
            chat_id,
            text,
            user_id=user_id,
            allow_management=allow_management,
            images=images,
            reply_context=reply_context,
        )
        if not _is_reset_command(text) and not (not allow_management and _is_management_command(text)):
            self._record_turn(
                chat_id,
                user_text=_history_user_text(
                    _history_text_with_reply_context(text, reply_context=reply_context), images=images
                ),
                assistant_text=reply.text,
                synthetic=synthetic,
            )
        return reply

    async def _generate_response(
        self,
        chat_id: int,
        text: str,
        *,
        user_id: int | None,
        allow_management: bool,
        images: Sequence[ImageAttachment],
        reply_context: ReplyMessageContext | None = None,
    ) -> AgentReply:
        if allow_management:
            for tool in self._management_tools():
                tool_reply = await tool.handle(text, chat_id=chat_id, user_id=user_id)
                if tool_reply is not None:
                    return AgentReply(text=tool_reply)

            command_reply = await self._handle_builtin_command(
                chat_id=chat_id, text=text, user_id=user_id, images=images, reply_context=reply_context
            )
            if command_reply is not None:
                return command_reply if isinstance(command_reply, AgentReply) else AgentReply(text=command_reply)
        elif _is_management_command(text):
            return AgentReply(text="Event 訊息不允許執行管理指令。")

        if not images:
            proactive_reply = await self._handle_proactive_action(chat_id=chat_id, text=text)
            if proactive_reply is not None:
                return AgentReply(text=proactive_reply)
        return await self._ask_agent_response(
            chat_id, _llm_prompt_with_reply_context(text.strip(), reply_context=reply_context), images=images
        )

    async def _reply_context_for_llm(self, *, message: TelegramMessage, text: str) -> ReplyMessageContext | None:
        if not self._mentions_bot(text):
            return None
        context = _reply_message_context(message)
        if context is None:
            return None
        urls_found = _reply_context_urls(message)
        url_contexts = []
        for url in urls_found:
            try:
                url_contexts.append(await self.url_context_extractor(url))
            except Exception as exc:  # noqa: BLE001 - URL enrichment must not break normal bot replies
                logger.warning(
                    "URL context extraction crashed for url={} with {}",
                    url,
                    type(exc).__name__,
                )
                url_contexts.append(_failed_url_context_from_exception(url, exc))
        context = replace(context, urls_found=tuple(urls_found), url_contexts=tuple(url_contexts))
        logger.debug(
            "Captured Telegram reply context chat_id={} message_id={} replied_message_id={} "
            "replied_type={} url_count={}",
            message.get("chat", {}).get("id"),
            message.get("message_id"),
            context.message_id,
            context.message_type,
            len(context.urls_found),
        )
        return context

    def _management_tools(self) -> list[SkillTool]:
        tools = [*self.tools]
        if self.skill_tool is not None:
            tools.insert(0, self.skill_tool)
        return tools

    async def _handle_proactive_action(self, *, chat_id: int, text: str) -> str | None:
        if self.proactive_tool is None:
            return None
        return await self.proactive_tool.handle(
            text.strip(), chat_id=chat_id, agent=self.agent, history=self._history(chat_id)
        )

    def _history(self, chat_id: int) -> list[tuple[str, str]]:
        if self.session_log is not None:
            return self.session_log.history(chat_id, limit=20)
        return self.histories.setdefault(chat_id, [])

    def _record_turn(self, chat_id: int, *, user_text: str, assistant_text: str, synthetic: bool = False) -> None:
        if self.session_log is not None:
            self.session_log.append_turn(
                chat_id, user_text=user_text, assistant_text=assistant_text, synthetic=synthetic
            )
            return
        history = self.histories.setdefault(chat_id, [])
        history.extend([("user", user_text), ("assistant", assistant_text)])
        del history[:-20]

    def _record_passive_group_context(
        self,
        *,
        chat_id: int,
        message: TelegramMessage,
        text: str,
        image_ref: TelegramImageRef | None,
        user_id: int | None,
    ) -> None:
        if not self.group_passive_context_enabled or not self._is_allowed(chat_id=chat_id, user_id=user_id):
            return
        if self.bot_user_id is not None and user_id == self.bot_user_id:
            return
        passive_text = _passive_group_history_text(message, text=text, image_ref=image_ref)
        if not passive_text:
            return
        message_id = message.get("message_id")
        if self.session_log is not None:
            self.session_log.append(
                chat_id,
                "user",
                text=passive_text,
                role="user",
                message_id=message_id,
                metadata={"passive_group_context": True},
            )
            return
        history = self.histories.setdefault(chat_id, [])
        history.append(("user", passive_text))
        del history[:-20]

    def _clear_history(self, chat_id: int) -> None:
        self.histories.pop(chat_id, None)
        if self.session_log is not None:
            self.session_log.clear_chat(chat_id)

    async def _handle_builtin_command(
        self,
        *,
        chat_id: int,
        text: str,
        user_id: int | None,
        images: Sequence[ImageAttachment],
        reply_context: ReplyMessageContext | None = None,
    ) -> str | AgentReply | None:
        command, _, argument = text.partition(" ")
        command_name = command.split("@", maxsplit=1)[0].lower()
        prompt = argument.strip()

        match command_name:
            case "/start":
                return _start_message()
            case "/help":
                return _help_message()
            case "/id":
                return f"chat_id: {chat_id}\nuser_id: {user_id if user_id is not None else 'unknown'}"
            case "/reset":
                self._clear_history(chat_id)
                return "已清除這個聊天室的對話記憶。"
            case "/ask":
                if not prompt and not images:
                    return "請在 /ask 後面加上你想問的內容。"
                return await self._ask_agent_response(
                    chat_id,
                    _llm_prompt_with_reply_context(prompt or _DEFAULT_IMAGE_PROMPT, reply_context=reply_context),
                    images=images,
                )
            case "/skills" | "/soul" | "/memory":
                return "這個 bot 尚未啟用這個管理功能。"
            case _ if text.startswith("/"):
                return "我不認識這個指令。輸入 /help 查看可用指令。"
            case _:
                return None

    async def _ask_agent(self, chat_id: int, prompt: str, *, images: Sequence[ImageAttachment] = ()) -> str:
        return (await self._ask_agent_response(chat_id, prompt, images=images)).text

    async def _ask_agent_response(
        self, chat_id: int, prompt: str, *, images: Sequence[ImageAttachment] = ()
    ) -> AgentReply:
        history = self._history(chat_id)
        try:
            rich_reply = getattr(self.agent, "reply_with_artifacts", None)
            if callable(rich_reply):
                return await rich_reply(prompt, history=history, images=images)
            if images:
                reply = await self.agent.reply(prompt, history=history, images=images)
            else:
                reply = await self.agent.reply(prompt, history=history)
        except httpx.HTTPError, AgentRunError:
            logger.exception("LLM request failed")
            if images:
                return AgentReply(text="AI 服務暫時無法處理這張圖片，可能是目前模型或 provider 不支援圖片理解。")
            return AgentReply(text="AI 服務暫時無法使用, 請稍後再試。")
        return AgentReply(text=reply)

    async def _send_agent_reply(
        self, chat_id: int, reply: AgentReply, *, reply_to_message_id: int | None = None
    ) -> None:
        parent_message_id = await self.telegram.send_message(
            chat_id, reply.text, reply_to_message_id=reply_to_message_id
        )
        for image in reply.images:
            photo = as_telegram_photo(image)
            try:
                await self.telegram.send_photo(
                    chat_id,
                    photo.data,
                    filename=photo.filename,
                    media_type=photo.media_type,
                    reply_to_message_id=parent_message_id or reply_to_message_id,
                )
            except httpx.HTTPError, TelegramApiError:
                logger.exception("Failed to send agent image artifact")
                await self.telegram.send_message(
                    chat_id,
                    "我有產生一張圖表，但目前無法透過 Telegram 傳送。",
                    reply_to_message_id=parent_message_id or reply_to_message_id,
                )

    async def _handle_image_generation_command(
        self, *, chat_id: int, prompt: str, reply_to_message_id: int | None
    ) -> bool:
        image_prompt = _image_generation_prompt(prompt)
        if image_prompt is None:
            return False
        if not image_prompt:
            await self.telegram.send_message(
                chat_id, "請在 /image 後面加上圖片描述。", reply_to_message_id=reply_to_message_id
            )
            return True
        await self._send_generated_image(chat_id=chat_id, prompt=image_prompt, reply_to_message_id=reply_to_message_id)
        return True

    async def _message_images(
        self, *, chat_id: int, image_ref: TelegramImageRef | None, reply_to_message_id: int | None
    ) -> list[ImageAttachment] | None:
        if image_ref is None:
            return []
        if not self.image_input_enabled:
            await self.telegram.send_message(
                chat_id, "圖片理解功能目前未啟用。", reply_to_message_id=reply_to_message_id
            )
            return None
        try:
            return [await self._download_image(image_ref)]
        except TelegramImageError as exc:
            await self.telegram.send_message(chat_id, str(exc), reply_to_message_id=reply_to_message_id)
        except httpx.HTTPError, TelegramApiError:
            logger.exception("Failed to download Telegram image")
            await self.telegram.send_message(
                chat_id,
                "我有收到圖片，但目前下載失敗，請稍後再試或改用較小的圖片。",
                reply_to_message_id=reply_to_message_id,
            )
        return None

    async def _download_image(self, image_ref: TelegramImageRef) -> ImageAttachment:
        if image_ref.file_size is not None and image_ref.file_size > self.image_max_bytes:
            raise TelegramImageError("這張圖片太大了，我先不讀取；請改傳較小的圖片。")

        file_info = await self.telegram.get_file(image_ref.file_id)
        file_size = file_info.get("file_size")
        if isinstance(file_size, int) and file_size > self.image_max_bytes:
            raise TelegramImageError("這張圖片太大了，我先不讀取；請改傳較小的圖片。")
        file_path = file_info.get("file_path")
        if not isinstance(file_path, str) or not file_path:
            raise TelegramImageError("我有收到圖片，但 Telegram 沒有提供可下載的檔案路徑。")

        data = await self.telegram.download_file(file_path)
        if len(data) > self.image_max_bytes:
            raise TelegramImageError("這張圖片太大了，我先不讀取；請改傳較小的圖片。")
        return ImageAttachment(data=data, media_type=image_ref.media_type, filename=image_ref.filename)

    async def _send_generated_image(self, *, chat_id: int, prompt: str, reply_to_message_id: int | None) -> None:
        if self.image_generator is None:
            await self.telegram.send_message(
                chat_id,
                "圖片生成功能目前未啟用；請設定 OPENAI_API_KEY 並啟用 BOT_IMAGE_GENERATION_ENABLED。",
                reply_to_message_id=reply_to_message_id,
            )
            return
        status_message_id = await self.telegram.send_message(
            chat_id,
            "產生圖片中…",
            reply_to_message_id=reply_to_message_id,
        )
        try:
            generated = await self.image_generator.generate(prompt)
            await self.telegram.send_photo(
                chat_id,
                generated.data,
                caption=_generated_image_caption(prompt),
                filename=generated.filename,
                media_type=generated.media_type,
                reply_to_message_id=reply_to_message_id,
            )
        except httpx.HTTPError, TelegramApiError, RuntimeError, ValueError:
            logger.exception("Image generation failed")
            await self._finish_image_status(
                chat_id=chat_id,
                status_message_id=status_message_id,
                fallback_reply_to_message_id=reply_to_message_id,
                text="圖片生成失敗；可能是模型或 OpenAI-compatible provider 不支援 /images/generations。",
            )
            return

        self._record_turn(chat_id, user_text=f"/image {prompt}", assistant_text="[已產生圖片]")
        await self._finish_image_status(
            chat_id=chat_id,
            status_message_id=status_message_id,
            fallback_reply_to_message_id=reply_to_message_id,
            text="圖片已產生。",
        )

    async def _finish_image_status(
        self,
        *,
        chat_id: int,
        status_message_id: int | None,
        fallback_reply_to_message_id: int | None,
        text: str,
    ) -> None:
        if status_message_id is None:
            await self.telegram.send_message(chat_id, text, reply_to_message_id=fallback_reply_to_message_id)
            return
        try:
            await self.telegram.edit_message_text(chat_id, status_message_id, text)
        except httpx.HTTPError, TelegramApiError:
            logger.exception("Failed to edit image generation status message; sending a new message instead")
            await self.telegram.send_message(chat_id, text, reply_to_message_id=fallback_reply_to_message_id)

    def _is_allowed(self, *, chat_id: int, user_id: int | None) -> bool:
        if not self.whitelist:
            return True
        return chat_id in self.whitelist or (user_id is not None and user_id in self.whitelist)

    async def _should_end_bot_topic(self, *, chat_id: int, sender: TelegramUser | None, prompt: str) -> bool:
        if not sender or not sender.get("is_bot"):
            self.bot_reply_streaks[chat_id] = 0
            return False
        if sender.get("id") == self.bot_user_id:
            return True

        streak = self.bot_reply_streaks.get(chat_id, 0)
        if self.topic_end_judge is not None:
            try:
                should_end = await self.topic_end_judge.should_end_topic(
                    prompt,
                    history=tuple(self._history(chat_id)),
                    bot_reply_streak=streak,
                )
            except httpx.HTTPError:
                logger.exception("Topic-end judge failed; falling back to reply streak guard")
            else:
                if should_end:
                    return True

        if streak >= self.max_consecutive_replies_to_bots:
            return True
        self.bot_reply_streaks[chat_id] = streak + 1
        return False

    def _should_respond_to_message(self, *, chat: TelegramChat, message: TelegramMessage, text: str) -> bool:
        if chat["type"] not in {"group", "supergroup"}:
            return True
        return self._mentions_bot(text) or self._is_reply_to_bot(message)

    def _mentions_bot(self, text: str) -> bool:
        if not self.bot_username:
            return False
        return f"@{self.bot_username.casefold()}" in text.casefold()

    def _is_reply_to_bot(self, message: TelegramMessage) -> bool:
        reply_to_message = message.get("reply_to_message")
        if not isinstance(reply_to_message, Mapping):
            return False
        reply_mapping = cast(Mapping[str, object], reply_to_message)
        reply_sender = reply_mapping.get("from")
        if not isinstance(reply_sender, Mapping):
            return False
        sender_mapping = cast(Mapping[str, object], reply_sender)
        sender_id = sender_mapping.get("id")
        if self.bot_user_id is not None and sender_id == self.bot_user_id:
            return True
        sender_username = sender_mapping.get("username")
        return (
            isinstance(sender_username, str)
            and self.bot_username is not None
            and sender_username.casefold() == self.bot_username.casefold()
        )

    def _strip_bot_mention(self, text: str) -> str:
        if not self.bot_username:
            return text
        mention_pattern = re.compile(rf"@{re.escape(self.bot_username)}\b", flags=re.IGNORECASE)
        return mention_pattern.sub("", text).strip()


_DEFAULT_IMAGE_PROMPT = "請閱讀這張圖片，描述重點並回答使用者可能想知道的內容。"
_IMAGE_COMMANDS = {"/image", "/img", "/draw", "/畫圖"}


def _telegram_result(response: httpx.Response) -> object:
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise TelegramApiError("Telegram API returned a non-object response")
    if not data.get("ok"):
        description = data.get("description", "unknown Telegram API error")
        raise TelegramApiError(str(description))
    return data.get("result")


def _message_text(message: TelegramMessage) -> str:
    text = message.get("text")
    if isinstance(text, str) and text:
        return text
    caption = message.get("caption")
    if isinstance(caption, str) and caption:
        return caption
    return ""


def _message_image_ref(message: TelegramMessage) -> TelegramImageRef | None:
    photo_items = message.get("photo")
    if isinstance(photo_items, Sequence) and not isinstance(photo_items, str | bytes):
        photo_sizes = [
            item for item in photo_items if isinstance(item, Mapping) and isinstance(item.get("file_id"), str)
        ]
        if photo_sizes:
            largest = max(photo_sizes, key=_photo_sort_key)
            file_id = cast(str, largest["file_id"])
            return TelegramImageRef(
                file_id=file_id,
                media_type="image/jpeg",
                filename="telegram-photo.jpg",
                file_size=_optional_int(largest.get("file_size")),
            )

    document = message.get("document")
    if isinstance(document, Mapping):
        file_id = document.get("file_id")
        mime_type = document.get("mime_type")
        if isinstance(file_id, str) and isinstance(mime_type, str) and mime_type.startswith("image/"):
            filename = document.get("file_name")
            return TelegramImageRef(
                file_id=file_id,
                media_type=mime_type,
                filename=filename if isinstance(filename, str) and filename else "telegram-image",
                file_size=_optional_int(document.get("file_size")),
            )
    return None


def _photo_sort_key(photo: Mapping[str, object]) -> tuple[int, int]:
    file_size = _optional_int(photo.get("file_size")) or 0
    width = _optional_int(photo.get("width")) or 0
    height = _optional_int(photo.get("height")) or 0
    return file_size, width * height


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _image_generation_prompt(text: str) -> str | None:
    command, _, argument = text.strip().partition(" ")
    command_name = command.split("@", maxsplit=1)[0].lower()
    if command_name not in _IMAGE_COMMANDS:
        return None
    prompt = argument.strip()
    return prompt or ""


def _history_user_text(text: str, *, images: Sequence[ImageAttachment]) -> str:
    user_text = text.strip()
    if not images:
        return user_text
    image_names = ", ".join(image.filename for image in images)
    if user_text:
        return f"{user_text}\n[圖片: {image_names}]"
    return f"[圖片: {image_names}]"


def _history_text_with_reply_context(text: str, *, reply_context: ReplyMessageContext | None) -> str:
    if reply_context is None:
        return text
    return _llm_prompt_with_reply_context(text, reply_context=reply_context)


def _llm_prompt_with_reply_context(text: str, *, reply_context: ReplyMessageContext | None) -> str:
    if reply_context is None:
        return text
    lines = [
        "Replied message context:",
        f"Sender: {reply_context.sender}",
        f"Type: {reply_context.message_type}",
    ]
    if reply_context.message_date is not None:
        lines.append(f"Date: {reply_context.message_date}")
    lines.extend(
        [
            f"Content: {reply_context.content}",
        ]
    )
    if reply_context.urls_found:
        lines.extend(["URLs found:", *[f"- {url}" for url in reply_context.urls_found]])
    if reply_context.url_contexts:
        lines.extend(["", "Extracted URL context:"])
        for url_context in reply_context.url_contexts:
            lines.extend(_format_url_context(url_context))
    lines.extend(
        [
            "",
            "Current user message:",
            text.strip() or "（使用者只提及 bot，未提供額外文字。）",
            "",
            "Important instruction for the assistant:",
            "The user mentioned the bot while replying to the above message. "
            "Treat the replied message and extracted URL content as the primary object the user wants you to look at. "
            "If the current message only contains the bot mention and no explicit instruction, respond directly with a "
            "useful interpretation/commentary/summary of the replied content instead of asking what to do.",
        ]
    )
    return "\n".join(lines)


def _format_url_context(context: UrlContext) -> list[str]:
    lines = [
        f"URL: {context.url}",
        f"Final URL: {context.final_url}",
        f"Source type: {context.source_type}",
        f"Extraction status: {context.extraction_status}",
        f"Fetched at: {context.fetched_at}",
    ]
    if context.title:
        lines.append(f"Title: {context.title}")
    if context.author:
        lines.append(f"Author: {context.author}")
    if context.description:
        lines.append(f"Description: {context.description}")
    if context.error:
        lines.append(f"Error: {context.error}")
    content = context.text or context.description
    if content:
        lines.extend(["Content:", _truncate_context_text(content)])
    else:
        lines.extend(["Content:", "（沒有擷取到可讀內容。）"])
    lines.append("")
    return lines


def _truncate_context_text(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}…"


def _reply_message_context(message: TelegramMessage) -> ReplyMessageContext | None:
    reply_to_message = message.get("reply_to_message")
    if not isinstance(reply_to_message, Mapping):
        return None
    reply_mapping = cast(Mapping[str, object], reply_to_message)
    message_type = _telegram_message_content_type(reply_mapping)
    return ReplyMessageContext(
        sender=_telegram_message_sender(reply_mapping),
        message_type=message_type,
        content=_telegram_message_content(reply_mapping, message_type=message_type),
        message_date=_telegram_message_date(reply_mapping.get("date")),
        message_id=_optional_int(reply_mapping.get("message_id")),
    )


def _reply_context_urls(message: TelegramMessage) -> tuple[str, ...]:
    reply_to_message = message.get("reply_to_message")
    urls: list[str] = []
    if isinstance(reply_to_message, Mapping):
        urls.extend(_urls_from_telegram_message(cast(Mapping[str, object], reply_to_message)))
    urls.extend(_urls_from_telegram_message(cast(Mapping[str, object], message)))
    return tuple(_dedupe_urls(urls)[:3])


def _urls_from_telegram_message(message: Mapping[str, object]) -> list[str]:
    urls: list[str] = []
    text = message.get("text")
    if isinstance(text, str) and text:
        urls.extend(_urls_from_text_and_entities(text, message.get("entities")))
    caption = message.get("caption")
    if isinstance(caption, str) and caption:
        urls.extend(_urls_from_text_and_entities(caption, message.get("caption_entities")))
    return urls


def _urls_from_text_and_entities(text: str, entities: object) -> list[str]:
    urls = _urls_from_text(text)
    if isinstance(entities, Sequence) and not isinstance(entities, str | bytes):
        for entity in entities:
            if not isinstance(entity, Mapping):
                continue
            entity_mapping = cast(Mapping[str, object], entity)
            entity_type = entity_mapping.get("type")
            if entity_type == "text_link":
                url = entity_mapping.get("url")
                if isinstance(url, str):
                    urls.append(_trim_url(url))
            elif entity_type == "url":
                offset = entity_mapping.get("offset")
                length = entity_mapping.get("length")
                if isinstance(offset, int) and isinstance(length, int):
                    urls.append(_trim_url(_telegram_entity_text(text, offset=offset, length=length)))
    return [url for url in urls if url]


def _urls_from_text(text: str) -> list[str]:
    return [_trim_url(match.group(0)) for match in _PLAIN_URL_RE.finditer(text)]


def _trim_url(url: str) -> str:
    return url.strip().rstrip(".,，。!！?)）]}>")


def _telegram_entity_text(text: str, *, offset: int, length: int) -> str:
    encoded = text.encode("utf-16-le")
    start = max(offset, 0) * 2
    end = max(offset + length, offset) * 2
    return encoded[start:end].decode("utf-16-le", errors="ignore")


def _dedupe_urls(urls: Sequence[str]) -> list[str]:
    seen = set()
    deduped = []
    for url in urls:
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append(url)
    return deduped


def _failed_url_context_from_exception(url: str, exc: BaseException) -> UrlContext:
    return UrlContext(
        url=url,
        final_url=url,
        source_type="unknown",
        fetched_at=datetime.now(UTC).isoformat(),
        extraction_status="failed",
        error=f"{type(exc).__name__}: {_safe_error_summary(str(exc))}",
    )


def _safe_error_summary(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(
        r"(?i)\b(token|api[_-]?key|authorization|cookie|set-cookie|password|secret)=([^\s;]+)",
        lambda match: f"{match.group(1)}=[redacted]",
        text,
    )
    text = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [redacted]", text)
    if not text:
        return "unknown error"
    if len(text) > 180:
        return f"{text[:180]}…"
    return text


def _telegram_message_sender(message: Mapping[str, object]) -> str:
    sender = message.get("from")
    if isinstance(sender, Mapping):
        return _telegram_actor_name(cast(Mapping[str, object], sender), fallback_prefix="user_id")

    sender_chat = message.get("sender_chat")
    if isinstance(sender_chat, Mapping):
        return _telegram_actor_name(cast(Mapping[str, object], sender_chat), fallback_prefix="chat_id")

    return "unknown"


def _telegram_actor_name(actor: Mapping[str, object], *, fallback_prefix: str) -> str:
    username = actor.get("username")
    if isinstance(username, str) and username:
        return f"@{username}"

    first_name = actor.get("first_name")
    last_name = actor.get("last_name")
    name_parts = [part for part in (first_name, last_name) if isinstance(part, str) and part]
    if name_parts:
        return " ".join(name_parts)

    title = actor.get("title")
    if isinstance(title, str) and title:
        return title

    actor_id = actor.get("id")
    if isinstance(actor_id, int):
        return f"{fallback_prefix}={actor_id}"

    return "unknown"


def _telegram_message_content_type(message: Mapping[str, object]) -> str:
    text = message.get("text")
    if isinstance(text, str) and text:
        return "text"
    for content_type in (
        "photo",
        "video",
        "document",
        "sticker",
        "voice",
        "audio",
        "animation",
        "video_note",
    ):
        if message.get(content_type) is not None:
            return content_type
    if isinstance(message.get("caption"), str):
        return "caption"
    return "unknown"


def _telegram_message_content(message: Mapping[str, object], *, message_type: str) -> str:
    text = message.get("text")
    if isinstance(text, str) and text:
        return text

    caption = message.get("caption")
    if isinstance(caption, str) and caption:
        if message_type == "caption":
            return caption
        return f"使用者回覆的是一則 {message_type} 訊息，caption: {caption}"

    if message_type == "unknown":
        return "無法取得被回覆訊息內容"
    return f"使用者回覆的是一則 {message_type} 訊息，無文字內容"


def _telegram_message_date(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    try:
        return datetime.fromtimestamp(value, tz=UTC).isoformat()
    except OSError, OverflowError, ValueError:
        return None


def _passive_group_history_text(message: TelegramMessage, *, text: str, image_ref: TelegramImageRef | None) -> str:
    body_parts: list[str] = []
    if text.strip():
        body_parts.append(text.strip())
    if image_ref is not None:
        body_parts.append(f"[圖片: {image_ref.filename}; 未讀取圖片內容]")
    body = "\n".join(body_parts).strip()
    if not body:
        return ""
    return f"[群組旁聽訊息 from {_sender_label(message)}] {body}"


def _sender_label(message: TelegramMessage) -> str:
    sender = message.get("from")
    if not sender:
        return "unknown"
    username = sender.get("username")
    if username:
        return f"@{username}"
    sender_id = sender.get("id")
    if sender_id is not None:
        return f"user_id={sender_id}"
    first_name = sender.get("first_name")
    return first_name or "unknown"


def _generated_image_caption(prompt: str) -> str:
    if len(prompt) <= 900:
        return f"已根據提示產生圖片：\n{prompt}"
    return f"已根據提示產生圖片：\n{prompt[:900]}…"


def _start_message() -> str:
    return "你好! 我是 Telegram AI 助理。直接傳訊息給我, 或用 /ask 問問題。"


def _help_message() -> str:
    return "\n".join(
        [
            "可用指令:",
            "/start - 顯示簡介",
            "/help - 顯示說明",
            "/id - 顯示 chat/user ID, 方便設定白名單",
            "/reset - 清除這個聊天室的對話記憶",
            "/ask <問題> - 詢問 AI 助理",
            "/image <描述> - 產生圖片（需要 provider 支援 /images/generations）",
            "/skills add <package> - 使用 npx skills add 安裝 Agent Skills",
            "/skills list - 列出已安裝 Agent Skills",
            "/soul show|reload|path - 管理 SOUL.md",
            "/memory show|reload|path - 管理 MEMORY.md",
            "/events list|show|cancel|reload - 管理 immediate events",
            "/tasks list|show|cancel - 管理 proactive tasks",
            "也可以直接傳一般文字給我。",
        ]
    )


def _log_background_task_error(task: asyncio.Task[None]) -> None:
    try:
        task.result()
    except asyncio.CancelledError, httpx.HTTPError, TelegramApiError:
        logger.exception("Background Telegram task failed")


def _is_management_command(text: str) -> bool:
    parts = text.strip().split(maxsplit=1)
    if not parts:
        return False
    command = parts[0].split("@", maxsplit=1)[0].lower()
    return command in {
        "/skills",
        "/soul",
        "/memory",
        "/events",
        "/tasks",
        "/start",
        "/help",
        "/id",
        "/reset",
        "/ask",
        *_IMAGE_COMMANDS,
    }


def _is_reset_command(text: str) -> bool:
    parts = text.strip().split(maxsplit=1)
    if not parts:
        return False
    return parts[0].split("@", maxsplit=1)[0].lower() == "/reset"


def _is_likely_long_running_action(text: str) -> bool:
    normalized = text.strip().casefold()
    return (
        "http://" in normalized
        or "https://" in normalized
        or normalized in {"go", "ok", "okay", "有字幕", "抓字幕", "抓抓看", "你就自動做事"}
        or "kabigon" in normalized
    )


_TELEGRAM_PARSE_MODE = "HTML"
_FENCED_CODE_RE = re.compile(r"```(?:([^\n`]*)\n)?([\s\S]*?)```")
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+)$")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", flags=re.DOTALL)
_PLAIN_URL_RE = re.compile(r"https?://[^\s<>()]+", flags=re.IGNORECASE)


def _telegram_html_chunks(text: str, *, limit: int = 4096) -> list[str]:
    sanitized = _sanitize_telegram_text(text)
    return [_format_for_telegram(chunk) for chunk in _chunk_text(sanitized, limit=limit)]


def _sanitize_telegram_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(character for character in normalized if _is_allowed_telegram_text_character(character))


def _format_for_telegram(text: str) -> str:
    parts: list[str] = []
    cursor = 0
    for match in _FENCED_CODE_RE.finditer(text):
        parts.append(_format_inline_telegram_html(text[cursor : match.start()]))
        parts.append(f"<pre>{html.escape(match.group(2), quote=False)}</pre>")
        cursor = match.end()
    parts.append(_format_inline_telegram_html(text[cursor:]))
    return "".join(parts)


def _format_inline_telegram_html(text: str) -> str:
    return "".join(_format_inline_telegram_line(line) for line in text.splitlines(keepends=True))


def _format_inline_telegram_line(line: str) -> str:
    content = line.removesuffix("\n")
    newline = "\n" if content != line else ""
    heading_match = _HEADING_RE.match(content)
    if heading_match is None:
        return _format_inline_markdown_html(content, convert_bold=True) + newline

    heading_text = heading_match.group(2).strip()
    return f"<b>{_format_inline_markdown_html(heading_text, convert_bold=False)}</b>{newline}"


def _format_inline_markdown_html(text: str, *, convert_bold: bool) -> str:
    parts: list[str] = []
    cursor = 0
    for match in _INLINE_CODE_RE.finditer(text):
        parts.append(_format_markdown_text_html(text[cursor : match.start()], convert_bold=convert_bold))
        parts.append(f"<code>{html.escape(match.group(1), quote=False)}</code>")
        cursor = match.end()
    parts.append(_format_markdown_text_html(text[cursor:], convert_bold=convert_bold))
    return "".join(parts)


def _format_markdown_text_html(text: str, *, convert_bold: bool) -> str:
    escaped = html.escape(text, quote=False)
    replacement = (lambda match: f"<b>{match.group(1)}</b>") if convert_bold else (lambda match: match.group(1))
    return _BOLD_RE.sub(replacement, escaped)


def _is_allowed_telegram_text_character(character: str) -> bool:
    codepoint = ord(character)
    return character in {"\n", "\t"} or (codepoint >= 0x20 and not 0xD800 <= codepoint <= 0xDFFF)


def _chunk_text(text: str, limit: int = 4096) -> list[str]:
    if not text:
        return [" "]
    return [text[index : index + limit] for index in range(0, len(text), limit)]
