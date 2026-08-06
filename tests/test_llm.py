from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai import Tool
from pydantic_ai.messages import BinaryContent
from pydantic_ai.messages import ModelRequest
from pydantic_ai.messages import ModelResponse
from pydantic_ai.messages import TextPart
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.messages import UserPromptPart
from pydantic_ai.models.test import TestModel

from telegramagent.agent_runtime import AgentEvent
from telegramagent.images import GeneratedImage
from telegramagent.images import ImageAttachment
from telegramagent.llm import ChatAgent
from telegramagent.llm import TopicEndAgent
from telegramagent.skills import AgentSkill
from tests.telegram_test_support import FakeRunnableAgent
from tests.telegram_test_support import FakeRunResult


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
    assert "直接引用 display_items" in captured["instructions"]
    assert runnable.prompts == ["問題"]
    assert runnable.message_history_lengths == [2]


@pytest.mark.asyncio
async def test_chat_agent_streams_normalized_lifecycle_events_and_messages() -> None:
    pydantic_agent = PydanticAgent(TestModel(custom_output_text="streamed answer"))
    agent = ChatAgent(api_key="key", model="model", agent_factory=lambda _instructions: pydantic_agent)
    events: list[AgentEvent] = []

    result = await agent.run_streamed("question", event_handler=events.append)

    assert result.reply.text == "streamed answer"
    assert events[0].type == "agent_start"
    assert events[-1].type == "agent_end"
    assert "".join(event.text for event in events if event.type == "message_delta") == "streamed answer"
    assert result.new_messages


@pytest.mark.asyncio
async def test_chat_agent_streams_tool_lifecycle_events() -> None:
    async def lookup_weather(city: str) -> str:
        return f"sunny in {city}"

    pydantic_agent = PydanticAgent(
        TestModel(call_tools=["lookup_weather"]), tools=[Tool(lookup_weather, takes_ctx=False)]
    )
    agent = ChatAgent(api_key="key", model="model", agent_factory=lambda _instructions: pydantic_agent)
    events: list[AgentEvent] = []

    await agent.run_streamed("weather", event_handler=events.append)

    tool_events = [event for event in events if event.type in {"tool_start", "tool_end"}]
    assert [event.type for event in tool_events] == ["tool_start", "tool_end"]
    assert {event.tool_name for event in tool_events} == {"lookup_weather"}


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
async def test_chat_agent_compacts_structured_history_without_tools() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read().decode()
        return httpx.Response(200, json={"choices": [{"message": {"content": "compact summary"}}]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        agent = ChatAgent(api_key="key", model="model", base_url="https://example.test/v1", http_client=client)
        summary = await agent.compact_history(
            [
                ModelRequest(parts=[UserPromptPart(content="old question")]),
                ModelResponse(parts=[TextPart(content="old answer")]),
            ]
        )

    assert summary == "compact summary"
    assert "old question" in captured["body"]
    assert "summar" in captured["body"].lower()


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
