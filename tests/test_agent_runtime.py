from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path

import httpx
import pytest
from pydantic_ai.messages import ModelMessage
from pydantic_ai.messages import ModelRequest
from pydantic_ai.messages import ModelResponse
from pydantic_ai.messages import TextPart
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.messages import UserPromptPart

from telegramagent.agent_runtime import AgentEvent
from telegramagent.agent_runtime import AgentRunOutput
from telegramagent.agent_runtime import AgentRuntime
from telegramagent.agent_runtime import AgentRuntimeConfig
from telegramagent.images import AgentReply
from telegramagent.images import ImageAttachment
from telegramagent.session import SessionLog


class FakeControl:
    def __init__(self) -> None:
        self.steering: list[str] = []
        self.followups: list[str] = []
        self.followup_images: list[tuple[ImageAttachment, ...]] = []

    def steer(self, prompt: str, images: tuple[ImageAttachment, ...] = ()) -> None:
        del images
        self.steering.append(prompt)

    def follow_up(self, prompt: str, images: tuple[ImageAttachment, ...] = ()) -> None:
        self.followups.append(prompt)
        self.followup_images.append(images)


class FakeBackend:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.histories: list[tuple[ModelMessage, ...]] = []
        self.controls: list[FakeControl] = []
        self.release = asyncio.Event()
        self.wait = False
        self.failures: list[Exception] = []
        self.cancelled = asyncio.Event()

    async def run_streamed(
        self,
        prompt: str,
        *,
        message_history: tuple[ModelMessage, ...] = (),
        images: tuple[ImageAttachment, ...] = (),
        event_handler=None,
        control_handler=None,
        history_processor=None,
    ) -> AgentRunOutput:
        del images, event_handler
        self.started.append(prompt)
        request_messages = [*message_history, ModelRequest(parts=[UserPromptPart(content=prompt)])]
        processed_history = (
            await history_processor(request_messages) if history_processor is not None else request_messages
        )
        self.histories.append(tuple(processed_history[:-1]))
        control = FakeControl()
        self.controls.append(control)
        if control_handler is not None:
            control_handler(control)
        if self.failures:
            raise self.failures.pop(0)
        try:
            if self.wait:
                await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        messages = (
            ModelRequest(parts=[UserPromptPart(content=prompt)]),
            ModelResponse(parts=[TextPart(content=f"answer: {prompt}")]),
        )
        return AgentRunOutput(reply=AgentReply(text=f"answer: {prompt}"), new_messages=messages)


class FakeCompactor:
    def __init__(self, summary: str = "summary") -> None:
        self.summary = summary
        self.calls: list[Sequence[ModelMessage]] = []

    async def compact_history(self, messages: Sequence[ModelMessage]) -> str:
        self.calls.append(messages)
        return self.summary


@pytest.mark.asyncio
async def test_same_chat_message_steers_active_run(tmp_path: Path) -> None:
    backend = FakeBackend()
    backend.wait = True
    runtime = AgentRuntime(backend=backend, sessions=SessionLog(tmp_path / "sessions"))

    active = asyncio.create_task(runtime.submit(1, "first"))
    await asyncio.sleep(0)
    steered = await runtime.submit(1, "correction")

    assert steered.kind == "steered"
    assert backend.controls[0].steering == ["correction"]
    backend.release.set()
    completed = await active
    assert completed.kind == "completed"


@pytest.mark.asyncio
async def test_same_chat_message_can_wait_until_active_run_is_idle(tmp_path: Path) -> None:
    backend = FakeBackend()
    backend.wait = True
    runtime = AgentRuntime(backend=backend, sessions=SessionLog(tmp_path / "sessions"))

    active = asyncio.create_task(runtime.submit(1, "first"))
    await asyncio.sleep(0)
    image = ImageAttachment(data=b"image", media_type="image/png", filename="queued.png")
    queued = await runtime.submit(1, "later", images=[image], intent="follow_up")
    second = await runtime.submit(1, "last", intent="follow_up")

    assert queued.kind == "followed_up"
    assert second.kind == "followed_up"
    assert queued.reply.text == "已將新訊息排在目前任務完成後處理。"
    assert backend.controls[0].followups == ["later", "last"]
    assert backend.controls[0].followup_images == [(image,), ()]
    backend.release.set()
    assert (await active).kind == "completed"


@pytest.mark.asyncio
async def test_different_chats_run_concurrently(tmp_path: Path) -> None:
    backend = FakeBackend()
    backend.wait = True
    runtime = AgentRuntime(backend=backend, sessions=SessionLog(tmp_path / "sessions"))

    first = asyncio.create_task(runtime.submit(1, "one"))
    second = asyncio.create_task(runtime.submit(2, "two"))
    await asyncio.sleep(0)

    assert set(backend.started) == {"one", "two"}
    backend.release.set()
    assert {result.kind for result in await asyncio.gather(first, second)} == {"completed"}


@pytest.mark.asyncio
async def test_cancel_stops_active_run_and_clears_steering(tmp_path: Path) -> None:
    backend = FakeBackend()
    backend.wait = True
    runtime = AgentRuntime(backend=backend, sessions=SessionLog(tmp_path / "sessions"))
    events: list[AgentEvent] = []
    active = asyncio.create_task(runtime.submit(1, "first", event_handler=events.append))
    await asyncio.sleep(0)
    await runtime.submit(1, "correction")

    assert await runtime.cancel(1) is True
    result = await active

    assert result.kind == "cancelled"
    assert backend.cancelled.is_set()
    assert [event.type for event in events] == ["agent_start", "cancelled"]
    assert runtime.pending_steering(1) == ()
    assert await runtime.cancel(1) is False


@pytest.mark.asyncio
async def test_runtime_never_replays_a_complete_backend_run(tmp_path: Path) -> None:
    backend = FakeBackend()
    backend.failures = [httpx.ConnectError("offline")]
    runtime = AgentRuntime(backend=backend, sessions=SessionLog(tmp_path / "sessions"))

    with pytest.raises(httpx.ConnectError, match="offline"):
        await runtime.submit(1, "retry")

    assert backend.started == ["retry"]


@pytest.mark.asyncio
async def test_runtime_owns_one_balanced_success_lifecycle(tmp_path: Path) -> None:
    runtime = AgentRuntime(backend=FakeBackend(), sessions=SessionLog(tmp_path / "sessions"))
    events: list[AgentEvent] = []

    result = await runtime.submit(1, "hello", event_handler=events.append)

    assert result.kind == "completed"
    assert [event.type for event in events].count("agent_start") == 1
    assert [event.type for event in events].count("agent_end") == 1
    assert events[0].type == "agent_start"
    assert events[-1].type == "agent_end"


@pytest.mark.asyncio
async def test_runtime_emits_one_failure_terminal_event(tmp_path: Path) -> None:
    backend = FakeBackend()
    backend.failures = [ValueError("bad request")]
    runtime = AgentRuntime(backend=backend, sessions=SessionLog(tmp_path / "sessions"))
    events: list[AgentEvent] = []

    with pytest.raises(ValueError, match="bad request"):
        await runtime.submit(1, "fail", event_handler=events.append)

    assert [event.type for event in events] == ["agent_start", "agent_error"]


@pytest.mark.asyncio
async def test_permanent_failure_is_not_retried(tmp_path: Path) -> None:
    backend = FakeBackend()
    backend.failures = [ValueError("bad request")]
    runtime = AgentRuntime(backend=backend, sessions=SessionLog(tmp_path / "sessions"))

    with pytest.raises(ValueError, match="bad request"):
        await runtime.submit(1, "fail")

    assert backend.started == ["fail"]


@pytest.mark.asyncio
async def test_context_is_compacted_before_run_and_recorded(tmp_path: Path) -> None:
    sessions = SessionLog(tmp_path / "sessions")
    sessions.append_turn(1, user_text="u" * 200, assistant_text="a" * 200)
    backend = FakeBackend()
    compactor = FakeCompactor("short summary")
    runtime = AgentRuntime(
        backend=backend,
        sessions=sessions,
        compactor=compactor,
        config=AgentRuntimeConfig(context_token_budget=50, compaction_trigger_ratio=0.5, chars_per_token=1),
    )

    result = await runtime.submit(1, "new")

    assert result.kind == "completed"
    assert len(compactor.calls) == 1
    summary_request = backend.histories[0][0]
    assert isinstance(summary_request, ModelRequest)
    assert "short summary" in str(summary_request.parts[0].content)
    assert any(record.type == "compaction" for record in sessions.records(1))


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["empty", "error"])
async def test_compaction_generation_failure_preserves_original_context(tmp_path: Path, mode: str) -> None:
    class FailingCompactor:
        async def compact_history(self, messages):
            del messages
            if mode == "error":
                raise RuntimeError("summary unavailable")
            return ""

    sessions = SessionLog(tmp_path / "sessions")
    sessions.append_turn(1, user_text="u" * 200, assistant_text="a" * 200)
    backend = FakeBackend()
    events: list[AgentEvent] = []
    runtime = AgentRuntime(
        backend=backend,
        sessions=sessions,
        compactor=FailingCompactor(),
        config=AgentRuntimeConfig(context_token_budget=50, compaction_trigger_ratio=1, chars_per_token=1),
    )

    result = await runtime.submit(1, "new", event_handler=events.append)

    assert result.kind == "completed"
    assert len(backend.histories[0]) == 2
    assert not any(record.type == "compaction" for record in sessions.records(1))
    assert any(event.type == "compaction_end" and event.is_error for event in events)


@pytest.mark.asyncio
async def test_compaction_write_failure_preserves_original_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions = SessionLog(tmp_path / "sessions")
    sessions.append_turn(1, user_text="u" * 200, assistant_text="a" * 200)
    backend = FakeBackend()
    events: list[AgentEvent] = []
    runtime = AgentRuntime(
        backend=backend,
        sessions=sessions,
        compactor=FakeCompactor(),
        config=AgentRuntimeConfig(context_token_budget=50, compaction_trigger_ratio=1, chars_per_token=1),
    )

    def fail_compaction(*args, **kwargs):
        del args, kwargs
        raise OSError("disk unavailable")

    monkeypatch.setattr(sessions, "append_compaction", fail_compaction)

    result = await runtime.submit(1, "new", event_handler=events.append)

    assert result.kind == "completed"
    assert len(backend.histories[0]) == 2
    assert not any(record.type == "compaction" for record in sessions.records(1))
    assert any(event.type == "compaction_end" and event.is_error for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize(("budget", "expected_compactions"), [(300, 2), (1000, 1)])
async def test_context_compacts_inside_multi_turn_run_without_splitting_tool_pair(
    tmp_path: Path, budget: int, expected_compactions: int
) -> None:
    class MultiTurnBackend:
        def __init__(self) -> None:
            self.second_request: list[ModelMessage] = []

        async def run_streamed(
            self,
            prompt,
            *,
            message_history=(),
            images=(),
            event_handler=None,
            control_handler=None,
            history_processor=None,
        ) -> AgentRunOutput:
            del images, event_handler, control_handler
            user = ModelRequest(parts=[UserPromptPart(content=prompt)])
            tool_call = ModelResponse(parts=[ToolCallPart(tool_name="mutate", args={}, tool_call_id="call-1")])
            tool_result = ModelRequest(
                parts=[ToolReturnPart(tool_name="mutate", content="x" * 1000, tool_call_id="call-1")]
            )
            final = ModelResponse(parts=[TextPart(content="done")])
            assert history_processor is not None
            await history_processor([*message_history, user])
            self.second_request = await history_processor([*message_history, user, tool_call, tool_result])
            return AgentRunOutput(
                reply=AgentReply(text="done"),
                new_messages=(user, tool_call, tool_result, final),
            )

    sessions = SessionLog(tmp_path / "sessions")
    sessions.append_turn(1, user_text="old", assistant_text="context")
    backend = MultiTurnBackend()
    compactor = FakeCompactor("summary including current request")
    runtime = AgentRuntime(
        backend=backend,
        sessions=sessions,
        compactor=compactor,
        config=AgentRuntimeConfig(context_token_budget=budget, compaction_trigger_ratio=1, chars_per_token=1),
    )

    result = await runtime.submit(1, "new request")

    assert result.kind == "completed"
    assert len(compactor.calls) == expected_compactions
    assert len(backend.second_request) == 3
    assert "summary including current request" in str(backend.second_request[0])
    assert isinstance(backend.second_request[1], ModelResponse)
    assert isinstance(backend.second_request[2], ModelRequest)
    restored = sessions.model_history(1)
    assert len(restored) == 4
    assert "summary including current request" in str(restored[0])
    assert isinstance(restored[1].parts[0], ToolCallPart)
    assert isinstance(restored[2].parts[0], ToolReturnPart)
    assert restored[1].parts[0].tool_call_id == restored[2].parts[0].tool_call_id == "call-1"


@pytest.mark.asyncio
async def test_clear_history_removes_durable_and_volatile_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions = SessionLog(tmp_path / "sessions")
    backend = FakeBackend()
    runtime = AgentRuntime(backend=backend, sessions=sessions)
    original_append = sessions.append_messages
    fail_writes = True

    def sometimes_fail(*args, **kwargs):
        if fail_writes:
            raise OSError("disk unavailable")
        return original_append(*args, **kwargs)

    monkeypatch.setattr(sessions, "append_messages", sometimes_fail)
    await runtime.submit(1, "first")

    runtime.clear_history(1)
    fail_writes = False
    await runtime.submit(1, "second")

    assert backend.histories[1] == ()


@pytest.mark.asyncio
async def test_completed_reply_survives_session_write_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sessions = SessionLog(tmp_path / "sessions")
    backend = FakeBackend()
    runtime = AgentRuntime(backend=backend, sessions=sessions)

    def fail_append(*args, **kwargs) -> None:
        del args, kwargs
        raise OSError("disk unavailable")

    monkeypatch.setattr(sessions, "append_messages", fail_append)

    first = await runtime.submit(1, "first")
    second = await runtime.submit(1, "second")

    assert first.kind == "completed"
    assert second.kind == "completed"
    assert len(backend.histories[1]) == 2


@pytest.mark.asyncio
async def test_tool_hooks_observe_events_without_breaking_run(tmp_path: Path) -> None:
    backend = FakeBackend()
    observed: list[str] = []

    async def before_tool(event: AgentEvent) -> None:
        observed.append(f"before:{event.tool_name}")

    async def after_tool(event: AgentEvent) -> None:
        observed.append(f"after:{event.tool_name}")

    runtime = AgentRuntime(
        backend=backend,
        sessions=SessionLog(tmp_path / "sessions"),
        before_tool=before_tool,
        after_tool=after_tool,
    )

    async def emit_tool_events(event: AgentEvent) -> None:
        if event.type == "agent_start":
            await runtime.emit(AgentEvent("tool_start", tool_name="read"))
            await runtime.emit(AgentEvent("tool_end", tool_name="read"))

    result = await runtime.submit(1, "tools", event_handler=emit_tool_events)

    assert result.kind == "completed"
    assert observed == ["before:read", "after:read"]
