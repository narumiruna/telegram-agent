from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import replace
from typing import cast

import httpx
from loguru import logger
from mcp.shared.exceptions import McpError
from pydantic_ai.exceptions import AgentRunError

from telegramagent.agent_runtime import AgentEvent
from telegramagent.images import AgentReply
from telegramagent.images import ImageAttachment
from telegramagent.images import as_telegram_photo
from telegramagent.session import SessionLog
from telegramagent.tasks import TaskQueue
from telegramagent.telegram_client import TelegramApiError
from telegramagent.telegram_client import TelegramClient
from telegramagent.telegram_messages import DEFAULT_IMAGE_PROMPT
from telegramagent.telegram_messages import IMAGE_COMMANDS
from telegramagent.telegram_messages import _failed_url_context_from_exception
from telegramagent.telegram_messages import _history_text_with_reply_context
from telegramagent.telegram_messages import _history_user_text
from telegramagent.telegram_messages import _image_generation_prompt
from telegramagent.telegram_messages import _llm_prompt_with_reply_context
from telegramagent.telegram_messages import _message_image_ref
from telegramagent.telegram_messages import _message_image_refs
from telegramagent.telegram_messages import _message_text
from telegramagent.telegram_messages import _passive_group_history_text
from telegramagent.telegram_messages import _reply_context_urls
from telegramagent.telegram_messages import _reply_message_context
from telegramagent.telegram_types import Agent
from telegramagent.telegram_types import AgentRuntimeGateway
from telegramagent.telegram_types import ImageGenerator
from telegramagent.telegram_types import ProactiveTool
from telegramagent.telegram_types import ReplyMessageContext
from telegramagent.telegram_types import SkillTool
from telegramagent.telegram_types import TelegramChat
from telegramagent.telegram_types import TelegramFile
from telegramagent.telegram_types import TelegramGateway
from telegramagent.telegram_types import TelegramImageRef
from telegramagent.telegram_types import TelegramMessage
from telegramagent.telegram_types import TelegramUpdate
from telegramagent.telegram_types import TelegramUser
from telegramagent.telegram_types import TopicEndJudge
from telegramagent.telegram_types import UrlContextLoader
from telegramagent.url_context import extract_url_context

__all__ = ["TelegramBot", "TelegramClient", "TelegramFile", "TelegramUpdate"]


class TelegramImageError(RuntimeError):
    """Raised when a Telegram image cannot be safely downloaded for vision input."""


class _TelegramAgentProgress:
    def __init__(
        self,
        *,
        telegram: TelegramGateway,
        chat_id: int,
        reply_to_message_id: int | None,
        edit_interval_seconds: float,
    ) -> None:
        self.telegram = telegram
        self.chat_id = chat_id
        self.reply_to_message_id = reply_to_message_id
        self.edit_interval_seconds = edit_interval_seconds
        self.message_id: int | None = None
        self.message_text = ""
        self.last_rendered = ""
        self.last_edit_at = 0.0

    async def handle(self, event: AgentEvent) -> None:
        if event.type == "agent_start":
            await self._ensure_message()
        elif event.type == "message_start":
            self.message_text = ""
        elif event.type == "message_delta":
            self.message_text += event.text
            await self._edit(self.message_text or "思考中…")
        elif event.type == "tool_start":
            await self._edit(f"{self.message_text}\n\n🔧 正在執行 {event.tool_name}…".strip(), force=True)
        elif event.type == "retry_scheduled":
            await self._edit("AI 服務暫時忙碌, 正在重試…", force=True)
        elif event.type == "compaction_start":
            await self._edit("正在整理較早的對話內容…", force=True)
        elif event.type == "agent_end":
            await self.finish(event.text)
        elif event.type == "cancelled":
            await self.finish(event.text or "已取消目前任務。")

    async def finish(self, text: str) -> None:
        if self.message_id is not None:
            await self._edit(text, force=True)

    async def _ensure_message(self) -> None:
        if self.message_id is not None:
            return
        try:
            self.message_id = await self.telegram.send_message(
                self.chat_id,
                "處理中…",
                reply_to_message_id=self.reply_to_message_id,
            )
        except _TELEGRAM_API_ERRORS as exc:
            logger.warning("Failed to send agent progress message with {}", type(exc).__name__)

    async def _edit(self, text: str, *, force: bool = False) -> None:
        await self._ensure_message()
        if self.message_id is None or not text or text == self.last_rendered:
            return
        now = time.monotonic()
        if not force and now - self.last_edit_at < self.edit_interval_seconds:
            return
        try:
            await self.telegram.edit_message_text(self.chat_id, self.message_id, text)
        except _TELEGRAM_API_ERRORS as exc:
            logger.warning("Failed to edit agent progress message with {}", type(exc).__name__)
            return
        self.last_rendered = text
        self.last_edit_at = now


_TELEGRAM_API_ERRORS = (httpx.HTTPError, TelegramApiError)
_LLM_REQUEST_ERRORS = (httpx.HTTPError, AgentRunError, McpError)
_IMAGE_GENERATION_ERRORS = (httpx.HTTPError, TelegramApiError, RuntimeError, ValueError)
_BACKGROUND_TASK_ERRORS = (asyncio.CancelledError, httpx.HTTPError, TelegramApiError)


class TelegramBot:
    def __init__(
        self,
        *,
        telegram: TelegramGateway,
        agent: Agent,
        agent_runtime: AgentRuntimeGateway | None = None,
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
        progress_edit_interval_seconds: float = 0.5,
    ) -> None:
        self.telegram = telegram
        self.agent = agent
        self.agent_runtime = agent_runtime
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
        self.progress_edit_interval_seconds = progress_edit_interval_seconds
        self.bot_reply_streaks: dict[int, int] = {}
        self.histories: dict[int, list[tuple[str, str]]] = {}
        self._update_tasks: set[asyncio.Task[None]] = set()

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
                    task = asyncio.create_task(self.handle_update(update))
                    self._update_tasks.add(task)
                    task.add_done_callback(self._update_tasks.discard)
                    task.add_done_callback(_log_background_task_error)
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
        image_refs = _message_image_refs(message)
        chat = message.get("chat")
        if (not text and not image_refs) or not chat:
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

        prompt = self._strip_bot_mention(text) if text else DEFAULT_IMAGE_PROMPT
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

        images = await self._message_images(chat_id=chat_id, image_refs=image_refs, reply_to_message_id=message_id)
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
        progress = (
            _TelegramAgentProgress(
                telegram=self.telegram,
                chat_id=chat_id,
                reply_to_message_id=message_id,
                edit_interval_seconds=self.progress_edit_interval_seconds,
            )
            if self.agent_runtime is not None
            else None
        )
        reply = await self.build_response(
            chat_id,
            prompt,
            user_id=user_id,
            images=images,
            reply_context=reply_context,
            progress=progress,
        )
        if progress is not None:
            await progress.finish(reply.text)
        await self._send_agent_reply(
            chat_id,
            reply,
            reply_to_message_id=message_id,
            existing_message_id=progress.message_id if progress is not None else None,
        )

    async def build_response(
        self,
        chat_id: int,
        text: str,
        *,
        user_id: int | None = None,
        images: Sequence[ImageAttachment] = (),
        reply_context: ReplyMessageContext | None = None,
        progress: _TelegramAgentProgress | None = None,
    ) -> AgentReply:
        return await self._build_response(
            chat_id,
            text,
            user_id=user_id,
            allow_management=True,
            synthetic=False,
            images=images,
            reply_context=reply_context,
            progress=progress,
        )

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
            try:
                status_message_id = await self.telegram.send_message(
                    chat_id,
                    "處理中…",
                    reply_to_message_id=reply_to_message_id,
                )
            except _TELEGRAM_API_ERRORS as exc:
                logger.warning(
                    "Failed to send synthetic status message with {}; skipping background task",
                    type(exc).__name__,
                )
                return

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
            if task.status == "completed":
                reply = task.output
            else:
                logger.warning("Telegram background task {} failed with status={}", task.id, task.status)
                reply = _task_failure_reply(task.status)
        else:
            reply = await action(object())

        if reply_mode == "edit-status" and status_message_id is not None:
            try:
                await self.telegram.edit_message_text(chat_id, status_message_id, reply)
            except _TELEGRAM_API_ERRORS as exc:
                logger.warning(
                    "Failed to edit synthetic event status message with {}; sending a new message instead",
                    type(exc).__name__,
                )
                try:
                    await self.telegram.send_message(chat_id, reply, reply_to_message_id=reply_to_message_id)
                except _TELEGRAM_API_ERRORS as send_exc:
                    logger.warning("Failed to send synthetic fallback reply with {}", type(send_exc).__name__)
            return
        try:
            await self.telegram.send_message(chat_id, reply, reply_to_message_id=reply_to_message_id)
        except _TELEGRAM_API_ERRORS as exc:
            logger.warning("Failed to send synthetic reply with {}", type(exc).__name__)

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
        progress: _TelegramAgentProgress | None = None,
    ) -> AgentReply:
        reply = await self._generate_response(
            chat_id,
            text,
            user_id=user_id,
            allow_management=allow_management,
            images=images,
            reply_context=reply_context,
            progress=progress,
        )
        if (
            not reply.session_recorded
            and not _is_reset_command(text)
            and not (not allow_management and _is_management_command(text))
        ):
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
        progress: _TelegramAgentProgress | None = None,
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
            chat_id,
            _llm_prompt_with_reply_context(text.strip(), reply_context=reply_context),
            images=images,
            progress=progress,
        )

    async def _reply_context_for_llm(self, *, message: TelegramMessage, text: str) -> ReplyMessageContext | None:
        if not self._should_include_reply_context(message=message, text=text):
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
        memory_history = self.histories.get(chat_id)
        if self.session_log is not None:
            try:
                durable_history = self.session_log.history(chat_id, limit=20)
            except Exception as exc:  # noqa: BLE001 - broken durable history must not block replies
                logger.warning(
                    "Session log history failed for chat_id={} with {}; using in-memory history",
                    chat_id,
                    type(exc).__name__,
                )
            else:
                if memory_history:
                    return [*durable_history, *memory_history][-20:]
                return durable_history
        return self.histories.setdefault(chat_id, [])

    def _record_turn(self, chat_id: int, *, user_text: str, assistant_text: str, synthetic: bool = False) -> None:
        if self.session_log is not None:
            try:
                self.session_log.append_turn(
                    chat_id, user_text=user_text, assistant_text=assistant_text, synthetic=synthetic
                )
            except Exception as exc:  # noqa: BLE001 - keep the user reply even if durable history fails
                logger.warning(
                    "Session log append failed for chat_id={} with {}; falling back to in-memory history",
                    chat_id,
                    type(exc).__name__,
                )
            else:
                return
        self._append_in_memory_history(chat_id, ("user", user_text), ("assistant", assistant_text))

    def _append_in_memory_history(self, chat_id: int, *turns: tuple[str, str]) -> None:
        history = self.histories.setdefault(chat_id, [])
        history.extend(turns)
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
            try:
                self.session_log.append(
                    chat_id,
                    "user",
                    text=passive_text,
                    role="user",
                    message_id=message_id,
                    metadata={"passive_group_context": True},
                )
            except Exception as exc:  # noqa: BLE001 - passive context should not break message handling
                logger.warning(
                    "Session log passive append failed for chat_id={} with {}; falling back to in-memory history",
                    chat_id,
                    type(exc).__name__,
                )
            else:
                return
        self._append_in_memory_history(chat_id, ("user", passive_text))

    def _clear_history(self, chat_id: int) -> None:
        self.histories.pop(chat_id, None)
        if self.session_log is not None:
            try:
                self.session_log.clear_chat(chat_id)
            except Exception as exc:  # noqa: BLE001 - reset should still clear in-memory fallback history
                logger.warning("Session log clear failed for chat_id={} with {}", chat_id, type(exc).__name__)

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
            case "/cancel":
                return await self._cancel_active_run(chat_id)
            case "/ask":
                if not prompt and not images:
                    return "請在 /ask 後面加上你想問的內容。"
                return await self._ask_agent_response(
                    chat_id,
                    _llm_prompt_with_reply_context(prompt or DEFAULT_IMAGE_PROMPT, reply_context=reply_context),
                    images=images,
                )
            case "/skills" | "/soul":
                return "這個 bot 尚未啟用這個管理功能。"
            case _ if text.startswith("/"):
                return "我不認識這個指令。輸入 /help 查看可用指令。"
            case _:
                return None

    async def _cancel_active_run(self, chat_id: int) -> str:
        if self.agent_runtime is None or not await self.agent_runtime.cancel(chat_id):
            return "目前沒有執行中的任務。"
        return "已取消目前任務。"

    async def _ask_agent(self, chat_id: int, prompt: str, *, images: Sequence[ImageAttachment] = ()) -> str:
        return (await self._ask_agent_response(chat_id, prompt, images=images)).text

    async def _ask_agent_response(
        self,
        chat_id: int,
        prompt: str,
        *,
        images: Sequence[ImageAttachment] = (),
        progress: _TelegramAgentProgress | None = None,
    ) -> AgentReply:
        try:
            if self.agent_runtime is not None:
                submission = await self.agent_runtime.submit(
                    chat_id,
                    prompt,
                    images=images,
                    event_handler=progress.handle if progress is not None else None,
                )
                return replace(submission.reply, session_recorded=True)

            history = self._history(chat_id)
            rich_reply = getattr(self.agent, "reply_with_artifacts", None)
            if callable(rich_reply):
                return await rich_reply(prompt, history=history, images=images)
            if images:
                reply = await self.agent.reply(prompt, history=history, images=images)
            else:
                reply = await self.agent.reply(prompt, history=history)
        except _LLM_REQUEST_ERRORS:
            logger.exception("LLM request failed")
            if images:
                return AgentReply(text="AI 服務暫時無法處理這張圖片，可能是目前模型或 provider 不支援圖片理解。")
            return AgentReply(text="AI 服務暫時無法使用, 請稍後再試。")
        return AgentReply(text=reply)

    async def _send_agent_reply(
        self,
        chat_id: int,
        reply: AgentReply,
        *,
        reply_to_message_id: int | None = None,
        existing_message_id: int | None = None,
    ) -> None:
        parent_message_id = existing_message_id
        if parent_message_id is None:
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
            except _TELEGRAM_API_ERRORS:
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
        self, *, chat_id: int, image_refs: Sequence[TelegramImageRef], reply_to_message_id: int | None
    ) -> list[ImageAttachment] | None:
        if not image_refs:
            return []
        if not self.image_input_enabled:
            await self.telegram.send_message(
                chat_id, "圖片理解功能目前未啟用。", reply_to_message_id=reply_to_message_id
            )
            return None
        try:
            return [await self._download_image(image_ref) for image_ref in image_refs]
        except TelegramImageError as exc:
            await self.telegram.send_message(chat_id, str(exc), reply_to_message_id=reply_to_message_id)
        except _TELEGRAM_API_ERRORS:
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
        except _IMAGE_GENERATION_ERRORS:
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
        except _TELEGRAM_API_ERRORS:
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

    def _should_include_reply_context(self, *, message: TelegramMessage, text: str) -> bool:
        if not isinstance(message.get("reply_to_message"), Mapping):
            return False
        if self._mentions_bot(text):
            return True
        chat = message.get("chat")
        return bool(chat and chat.get("type") == "private")


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
            "/cancel - 取消目前執行中的任務",
            "/ask <問題> - 詢問 AI 助理",
            "/image <描述> - 產生圖片（需要 provider 支援 /images/generations）",
            "/skills add <package> - 使用 npx skills add 安裝 Agent Skills",
            "/skills list - 列出已安裝 Agent Skills",
            "/soul show|reload|path - 管理 SOUL.md",
            "/events list|show|cancel|reload - 管理 immediate events",
            "/tasks list|show|cancel - 管理 proactive tasks",
            "也可以直接傳一般文字給我。",
        ]
    )


def _log_background_task_error(task: asyncio.Task[None]) -> None:
    try:
        task.result()
    except _BACKGROUND_TASK_ERRORS:
        logger.exception("Background Telegram task failed")


def _task_failure_reply(status: str) -> str:
    if status == "cancelled":
        return "任務已取消。"
    return "任務執行時遇到內部錯誤，沒有完成；請稍後再試。"


def _is_management_command(text: str) -> bool:
    parts = text.strip().split(maxsplit=1)
    if not parts:
        return False
    command = parts[0].split("@", maxsplit=1)[0].lower()
    return command in {
        "/skills",
        "/soul",
        "/events",
        "/tasks",
        "/start",
        "/help",
        "/id",
        "/reset",
        "/cancel",
        "/ask",
        *IMAGE_COMMANDS,
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
