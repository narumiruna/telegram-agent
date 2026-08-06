from __future__ import annotations

from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal
from typing import Protocol

from pydantic_ai.messages import ModelMessage

from telegramagent.images import AgentReply
from telegramagent.images import ImageAttachment

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
