from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field
from typing import Literal
from typing import Protocol
from typing import cast

import httpx
from loguru import logger
from mcp.shared.exceptions import McpError
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai.messages import ModelRequest
from pydantic_ai.messages import UserPromptPart

from telegramagent.images import AgentReply
from telegramagent.images import ImageAttachment
from telegramagent.session import SessionLog

AgentEventType = Literal[
    "agent_start",
    "agent_end",
    "turn_start",
    "turn_end",
    "message_start",
    "message_delta",
    "message_end",
    "tool_start",
    "tool_end",
    "retry_scheduled",
    "compaction_start",
    "compaction_end",
    "cancelled",
]
SubmissionKind = Literal["completed", "steered", "cancelled"]


@dataclass(frozen=True)
class AgentEvent:
    type: AgentEventType
    text: str = ""
    tool_name: str = ""
    tool_call_id: str = ""
    data: Mapping[str, object] | None = None
    is_error: bool = False


AgentEventHandler = Callable[[AgentEvent], Awaitable[None] | None]


@dataclass(frozen=True)
class AgentRunOutput:
    reply: AgentReply
    new_messages: tuple[ModelMessage, ...]


@dataclass(frozen=True)
class AgentSubmission:
    kind: SubmissionKind
    reply: AgentReply


@dataclass(frozen=True)
class AgentRuntimeConfig:
    max_attempts: int = 3
    retry_base_delay_seconds: float = 1.0
    context_token_budget: int = 100_000
    compaction_trigger_ratio: float = 0.8
    chars_per_token: float = 4.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.retry_base_delay_seconds < 0:
            raise ValueError("retry_base_delay_seconds must not be negative")
        if self.context_token_budget < 1:
            raise ValueError("context_token_budget must be at least 1")
        if not 0 < self.compaction_trigger_ratio <= 1:
            raise ValueError("compaction_trigger_ratio must be between 0 and 1")
        if self.chars_per_token <= 0:
            raise ValueError("chars_per_token must be positive")


class AgentRunControl(Protocol):
    def steer(self, prompt: str, images: tuple[ImageAttachment, ...] = ()) -> None: ...


class AgentBackend(Protocol):
    async def run_streamed(
        self,
        prompt: str,
        *,
        message_history: tuple[ModelMessage, ...] = (),
        images: tuple[ImageAttachment, ...] = (),
        event_handler: AgentEventHandler | None = None,
        control_handler: Callable[[AgentRunControl], None] | None = None,
    ) -> AgentRunOutput: ...


class HistoryCompactor(Protocol):
    async def compact_history(self, messages: Sequence[ModelMessage]) -> str: ...


@dataclass
class _PendingSteering:
    prompt: str
    images: tuple[ImageAttachment, ...]


@dataclass
class _ChatSession:
    task: asyncio.Task[AgentSubmission] | None = None
    control: AgentRunControl | None = None
    pending: list[_PendingSteering] = field(default_factory=list)


class AgentRuntime:
    """Owns pi-style execution state while delegating model/tool work to Pydantic AI."""

    def __init__(
        self,
        *,
        backend: AgentBackend,
        sessions: SessionLog,
        compactor: HistoryCompactor | None = None,
        config: AgentRuntimeConfig | None = None,
        before_tool: AgentEventHandler | None = None,
        after_tool: AgentEventHandler | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.backend = backend
        self.sessions = sessions
        self.compactor = compactor
        self.config = config or AgentRuntimeConfig()
        self.before_tool = before_tool
        self.after_tool = after_tool
        self.sleep = sleep
        self._chat_sessions: dict[int, _ChatSession] = {}
        self._volatile_history: dict[int, list[ModelMessage]] = {}
        self._listeners: set[AgentEventHandler] = set()
        self._lock = asyncio.Lock()

    async def submit(
        self,
        chat_id: int,
        prompt: str,
        *,
        images: Sequence[ImageAttachment] = (),
        event_handler: AgentEventHandler | None = None,
    ) -> AgentSubmission:
        image_tuple = tuple(images)
        async with self._lock:
            session = self._chat_sessions.setdefault(chat_id, _ChatSession())
            if session.task is not None and not session.task.done():
                steering = _PendingSteering(prompt, image_tuple)
                if session.control is None:
                    session.pending.append(steering)
                else:
                    session.control.steer(prompt, image_tuple)
                return AgentSubmission(kind="steered", reply=AgentReply(text="已將新訊息加入目前任務。"))

            current_task = asyncio.current_task()
            if current_task is None:  # pragma: no cover - submit always runs in an event loop task
                raise RuntimeError("agent submission requires an active asyncio task")
            session.task = cast(asyncio.Task[AgentSubmission], current_task)
        return await self._execute(chat_id, prompt, images=image_tuple, event_handler=event_handler)

    async def cancel(self, chat_id: int) -> bool:
        async with self._lock:
            session = self._chat_sessions.get(chat_id)
            if session is None or session.task is None or session.task.done():
                return False
            session.pending.clear()
            session.control = None
            task = session.task
            task.cancel()
        await task
        return True

    def clear_history(self, chat_id: int) -> None:
        self._volatile_history.pop(chat_id, None)
        self.sessions.clear_chat(chat_id)

    def pending_steering(self, chat_id: int) -> tuple[str, ...]:
        session = self._chat_sessions.get(chat_id)
        if session is None:
            return ()
        return tuple(item.prompt for item in session.pending)

    def is_running(self, chat_id: int) -> bool:
        session = self._chat_sessions.get(chat_id)
        return bool(session and session.task and not session.task.done())

    def subscribe(self, listener: AgentEventHandler) -> Callable[[], None]:
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    async def emit(self, event: AgentEvent) -> None:
        await self._dispatch(event, None)

    async def _execute(
        self,
        chat_id: int,
        prompt: str,
        *,
        images: tuple[ImageAttachment, ...],
        event_handler: AgentEventHandler | None,
    ) -> AgentSubmission:
        current_task = asyncio.current_task()
        try:
            history = await self._compact_if_needed(chat_id, event_handler)
            output = await self._run_with_retry(
                chat_id,
                prompt,
                history=history,
                images=images,
                event_handler=event_handler,
            )
            self._record_output(chat_id, output.new_messages)
            return AgentSubmission(kind="completed", reply=output.reply)
        except asyncio.CancelledError:
            await self._dispatch(AgentEvent("cancelled", text="任務已取消。"), event_handler)
            return AgentSubmission(kind="cancelled", reply=AgentReply(text="已取消目前任務。"))
        finally:
            async with self._lock:
                session = self._chat_sessions.get(chat_id)
                if session is not None and session.task is current_task:
                    session.task = None
                    session.control = None
                    session.pending.clear()

    async def _run_with_retry(
        self,
        chat_id: int,
        prompt: str,
        *,
        history: tuple[ModelMessage, ...],
        images: tuple[ImageAttachment, ...],
        event_handler: AgentEventHandler | None,
    ) -> AgentRunOutput:
        for attempt in range(1, self.config.max_attempts + 1):
            try:
                return await self.backend.run_streamed(
                    prompt,
                    message_history=history,
                    images=images,
                    event_handler=lambda event: self._dispatch(event, event_handler),
                    control_handler=lambda control: self._bind_control(chat_id, control),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._clear_control(chat_id)
                if attempt >= self.config.max_attempts or not _is_transient_error(exc):
                    raise
                delay = self.config.retry_base_delay_seconds * (2 ** (attempt - 1))
                await self._dispatch(
                    AgentEvent(
                        "retry_scheduled",
                        text="暫時性錯誤, 準備重試。",
                        data={"attempt": attempt + 1, "delay_seconds": delay},
                    ),
                    event_handler,
                )
                await self.sleep(delay)
        raise RuntimeError("retry loop ended unexpectedly")  # pragma: no cover

    def _bind_control(self, chat_id: int, control: AgentRunControl) -> None:
        session = self._chat_sessions.setdefault(chat_id, _ChatSession())
        session.control = control
        pending = tuple(session.pending)
        session.pending.clear()
        for steering in pending:
            control.steer(steering.prompt, steering.images)

    def _clear_control(self, chat_id: int) -> None:
        session = self._chat_sessions.get(chat_id)
        if session is not None:
            session.control = None

    def _record_output(self, chat_id: int, new_messages: Sequence[ModelMessage]) -> None:
        volatile = self._volatile_history.get(chat_id, [])
        messages = [*volatile, *new_messages]
        try:
            self.sessions.append_messages(chat_id, messages)
        except Exception as exc:  # noqa: BLE001 - completed replies must survive persistence failures
            logger.warning(
                "Session append failed for chat_id={} with {}; retaining volatile history",
                chat_id,
                type(exc).__name__,
            )
            self._volatile_history[chat_id] = messages
        else:
            self._volatile_history.pop(chat_id, None)

    async def _compact_if_needed(
        self, chat_id: int, event_handler: AgentEventHandler | None
    ) -> tuple[ModelMessage, ...]:
        history = (*self.sessions.model_history(chat_id), *self._volatile_history.get(chat_id, ()))
        if self.compactor is None or not _needs_compaction(history, self.config):
            return history

        await self._dispatch(AgentEvent("compaction_start", data={"source_message_count": len(history)}), event_handler)
        try:
            summary = (await self.compactor.compact_history(history)).strip()
            if not summary:
                raise ValueError("compactor returned an empty summary")
        except Exception as exc:  # noqa: BLE001 - compaction failure must preserve the usable original context
            logger.warning("Context compaction failed with {}; retaining original history", type(exc).__name__)
            await self._dispatch(
                AgentEvent("compaction_end", text="對話摘要失敗, 沿用原始上下文。", is_error=True), event_handler
            )
            return history

        summary_messages: tuple[ModelMessage, ...] = (
            ModelRequest(parts=[UserPromptPart(content=f"Earlier conversation summary:\n{summary}")]),
        )
        try:
            self.sessions.append_compaction(chat_id, summary_messages, source_message_count=len(history))
        except Exception as exc:  # noqa: BLE001 - retain original context when compaction cannot be persisted
            logger.warning("Compaction record failed with {}; retaining original history", type(exc).__name__)
            await self._dispatch(
                AgentEvent("compaction_end", text="對話摘要無法儲存, 沿用原始上下文。", is_error=True),
                event_handler,
            )
            return tuple(history)
        self._volatile_history.pop(chat_id, None)
        await self._dispatch(AgentEvent("compaction_end", data={"source_message_count": len(history)}), event_handler)
        return summary_messages

    async def _dispatch(self, event: AgentEvent, event_handler: AgentEventHandler | None) -> None:
        if event.type == "tool_start":
            await _safe_notify(self.before_tool, event)
        await _safe_notify(event_handler, event)
        for listener in tuple(self._listeners):
            await _safe_notify(listener, event)
        if event.type == "tool_end":
            await _safe_notify(self.after_tool, event)


def _needs_compaction(messages: Sequence[ModelMessage], config: AgentRuntimeConfig) -> bool:
    if not messages:
        return False
    serialized_chars = len(ModelMessagesTypeAdapter.dump_json(list(messages)))
    estimated_tokens = serialized_chars / config.chars_per_token
    return estimated_tokens >= config.context_token_budget * config.compaction_trigger_ratio


async def _safe_notify(handler: AgentEventHandler | None, event: AgentEvent) -> None:
    if handler is None:
        return
    try:
        result = handler(event)
        if inspect.isawaitable(result):
            await result
    except Exception as exc:  # noqa: BLE001 - lifecycle observers must not break execution
        logger.warning("Agent event handler failed for type={} with {}", event.type, type(exc).__name__)


def _is_transient_error(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, ModelHTTPError):
        return exc.status_code == 429 or exc.status_code >= 500
    if isinstance(exc, ModelAPIError):
        return True
    if isinstance(exc, McpError):
        return exc.error.code in {-32603, -32000, -32001, -32002}
    return False
