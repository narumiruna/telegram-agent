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

    def steer(self, prompt: str, images: tuple[ImageAttachment, ...] = ()) -> None:
        del images
        self.steering.append(prompt)


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
    ) -> AgentRunOutput:
        del images
        self.started.append(prompt)
        self.histories.append(message_history)
        control = FakeControl()
        self.controls.append(control)
        if control_handler is not None:
            control_handler(control)
        if event_handler is not None:
            await _maybe_await(event_handler(AgentEvent("agent_start")))
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


async def _maybe_await(value):
    if value is not None:
        await value


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
    active = asyncio.create_task(runtime.submit(1, "first"))
    await asyncio.sleep(0)
    await runtime.submit(1, "correction")

    assert await runtime.cancel(1) is True
    result = await active

    assert result.kind == "cancelled"
    assert backend.cancelled.is_set()
    assert runtime.pending_steering(1) == ()
    assert await runtime.cancel(1) is False


@pytest.mark.asyncio
async def test_transient_failures_retry_with_exponential_backoff(tmp_path: Path) -> None:
    backend = FakeBackend()
    backend.failures = [httpx.ConnectError("offline"), httpx.ReadTimeout("slow")]
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    runtime = AgentRuntime(
        backend=backend,
        sessions=SessionLog(tmp_path / "sessions"),
        config=AgentRuntimeConfig(max_attempts=3, retry_base_delay_seconds=0.25),
        sleep=fake_sleep,
    )
    events: list[AgentEvent] = []

    result = await runtime.submit(1, "retry", event_handler=events.append)

    assert result.kind == "completed"
    assert backend.started == ["retry", "retry", "retry"]
    assert delays == [0.25, 0.5]
    assert [event.type for event in events].count("retry_scheduled") == 2


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
        del event
        await runtime.emit(AgentEvent("tool_start", tool_name="read"))
        await runtime.emit(AgentEvent("tool_end", tool_name="read"))

    result = await runtime.submit(1, "tools", event_handler=emit_tool_events)

    assert result.kind == "completed"
    assert observed == ["before:read", "after:read"]
