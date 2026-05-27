from __future__ import annotations

import re
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Any
from typing import Protocol
from typing import cast

import httpx
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai.messages import BinaryContent
from pydantic_ai.messages import FilePart
from pydantic_ai.messages import ModelMessage
from pydantic_ai.messages import ModelRequest
from pydantic_ai.messages import ModelResponse
from pydantic_ai.messages import TextPart
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.messages import UserContent
from pydantic_ai.messages import UserPromptPart
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from telegramagent.context_files import ContextFile
from telegramagent.context_files import format_context_for_instructions
from telegramagent.images import AgentReply
from telegramagent.images import GeneratedImage
from telegramagent.images import ImageAttachment
from telegramagent.images import image_from_binary
from telegramagent.kabigon_tool import kabigon_load_url
from telegramagent.skills import AgentSkill
from telegramagent.skills import format_skills_for_instructions


class OpenAIChatClient:
    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.http_client = http_client

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def complete(self, messages: Sequence[dict[str, str]], *, temperature: float = 0.7) -> str:
        if not self.api_key:
            msg = "OpenAI-compatible API key is not configured"
            raise RuntimeError(msg)
        payload: dict[str, object] = {
            "model": self.model,
            "messages": list(messages),
            "temperature": temperature,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}

        if self.http_client is None:
            async with httpx.AsyncClient(timeout=60) as client:
                return await self._post_chat_completion(client, payload, headers)
        return await self._post_chat_completion(self.http_client, payload, headers)

    async def _post_chat_completion(
        self,
        client: httpx.AsyncClient,
        payload: Mapping[str, object],
        headers: Mapping[str, str],
    ) -> str:
        response = await client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            return "模型沒有回覆內容, 請稍後再試。"
        return content.strip()


class RunnableAgent(Protocol):
    def run(
        self,
        user_prompt: str | Sequence[UserContent],
        *,
        message_history: Sequence[ModelMessage] | None = None,
    ) -> Awaitable[object]: ...


AgentFactory = Callable[[str], RunnableAgent]


class ChatAgent:
    """Pydantic AI chat agent with Agent Skills instruction support."""

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        http_client: httpx.AsyncClient | None = None,
        skills: list[AgentSkill] | None = None,
        soul: ContextFile | None = None,
        memory: ContextFile | None = None,
        agent_factory: AgentFactory | None = None,
        capability_summary: str = "",
        kabigon_tool_timeout_seconds: float = 180.0,
        mcp_toolsets: Sequence[Any] = (),
        tools: Sequence[Any] = (),
    ) -> None:
        self.client = OpenAIChatClient(api_key=api_key, model=model, base_url=base_url, http_client=http_client)
        self.skills = skills or []
        self.soul = soul
        self.memory = memory
        self.capability_summary = capability_summary
        self.agent_factory = agent_factory
        self.kabigon_tool_timeout_seconds = kabigon_tool_timeout_seconds
        self.mcp_toolsets = tuple(mcp_toolsets)
        self.tools = tuple(tools)
        self.agent = self._create_agent(api_key=api_key, model=model, base_url=base_url, agent_factory=agent_factory)

    @property
    def is_configured(self) -> bool:
        return self.client.is_configured

    async def reply(
        self,
        prompt: str,
        *,
        history: Sequence[tuple[str, str]] = (),
        images: Sequence[ImageAttachment] = (),
    ) -> str:
        return (await self.reply_with_artifacts(prompt, history=history, images=images)).text

    async def reply_with_artifacts(
        self,
        prompt: str,
        *,
        history: Sequence[tuple[str, str]] = (),
        images: Sequence[ImageAttachment] = (),
    ) -> AgentReply:
        if not self.client.is_configured:
            if images:
                return AgentReply(
                    text=f"我有收到圖片，但目前還沒設定 OPENAI_API_KEY，所以不能讀圖。文字內容：\n\n{prompt}"
                )
            return AgentReply(text=f"我目前還沒設定 OPENAI_API_KEY, 所以先原樣回覆:\n\n{prompt}")

        result = await self.agent.run(_user_prompt(prompt, images), message_history=_message_history(history))
        output = getattr(result, "output", result)
        if not isinstance(output, str) or not output.strip():
            return AgentReply(text="模型沒有回覆內容, 請稍後再試。", images=tuple(_result_images(result)))
        return AgentReply(text=output.strip(), images=tuple(_result_images(result)))

    def reload_skills(self, skills: list[AgentSkill]) -> None:
        self.skills = skills
        self._rebuild_agent()

    def reload_context(self, *, soul: ContextFile | None = None, memory: ContextFile | None = None) -> None:
        if soul is not None:
            self.soul = soul
        if memory is not None:
            self.memory = memory
        self._rebuild_agent()

    def _rebuild_agent(self) -> None:
        self.agent = self._create_agent(
            api_key=self.client.api_key,
            model=self.client.model,
            base_url=self.client.base_url,
            agent_factory=self.agent_factory,
        )

    def _create_agent(
        self,
        *,
        api_key: str | None,
        model: str,
        base_url: str,
        agent_factory: AgentFactory | None,
    ) -> RunnableAgent:
        instructions = _chat_instructions(
            skills=self.skills, soul=self.soul, memory=self.memory, capability_summary=self.capability_summary
        )
        if agent_factory is not None:
            return agent_factory(instructions)
        provider = OpenAIProvider(base_url=base_url, api_key=api_key)
        pydantic_model = OpenAIChatModel(model, provider=provider)
        return PydanticAgent(
            pydantic_model,
            instructions=instructions,
            tools=[cast(Any, kabigon_load_url), *self.tools],
            toolsets=cast(Any, self.mcp_toolsets),
            tool_timeout=self.kabigon_tool_timeout_seconds,
        )


def _chat_instructions(
    *, skills: list[AgentSkill], soul: ContextFile | None, memory: ContextFile | None, capability_summary: str = ""
) -> str:
    sections = [
        "你是一個 Telegram 機器人助理。請用繁體中文簡潔、有幫助地回答；可以自然、克制地加入少量 emoji。"
        "對話歷史會以真正的 prior messages 提供；回覆前必須先檢查近期對話，"
        "如果使用者提到『剛剛那個』、『不是丟過了』或要求沿用前文 URL，不要要求重新貼連結。"
        "如果上一則助理訊息列出編號選項，而使用者只回覆 1、2、3 這類數字，"
        "必須把它當成選擇上一則訊息中相同編號的選項；不要重新編號、改寫選項或重問同一組問題。"
        "使用工具結果回答時，如果結果包含 display_items 或 response_contract，必須依照該 contract "
        "直接引用 display_items；不要自行重組店名、分數、地區與網址，避免把不同筆資料配錯。"
        "如果使用者要求你自動處理、讀取、整理或查詢，只有在工具結果或系統訊息明確提供內容時，"
        "才可以說你已經讀取或正在根據內容整理；如果沒有工具結果，不要假裝會在背景工作。"
        "如果使用者傳圖片且 runtime 已提供圖片內容，請直接根據圖片回答；若模型或供應商無法辨識圖片，請明確說明限制。"
        "Agent Skills 是操作說明，不代表你在 Telegram runtime 真的有該工具；"
        "只有 runtime capabilities、Pydantic AI tools 或已啟用 MCP toolsets 中列出的工具才是真的可執行。"
        "使用股票與金融資料時，明確說明僅供資訊參考，不構成投資建議。"
    ]
    soul_instructions = format_context_for_instructions(soul)
    if soul_instructions:
        sections.append(soul_instructions)
    memory_instructions = format_context_for_instructions(memory)
    if memory_instructions:
        sections.append(memory_instructions)
    if capability_summary:
        sections.append(f"Runtime capabilities:\n{capability_summary}")
    skill_instructions = format_skills_for_instructions(skills)
    if skill_instructions:
        sections.append(skill_instructions)
    return "\n\n".join(sections)


def _user_prompt(prompt: str, images: Sequence[ImageAttachment]) -> str | list[UserContent]:
    if not images:
        return prompt

    parts: list[UserContent] = [prompt.strip() or "請閱讀這張圖片，描述重點並回答使用者可能想知道的內容。"]
    for index, image in enumerate(images, start=1):
        parts.append(f"圖片 {index}: {image.filename}")
        parts.append(BinaryContent(data=image.data, media_type=image.media_type, identifier=image.filename))
    return parts


def _result_images(result: object) -> list[GeneratedImage]:
    new_messages = getattr(result, "new_messages", None)
    if not callable(new_messages):
        return []
    images: list[GeneratedImage] = []
    for message in new_messages():
        if isinstance(message, ModelRequest):
            images.extend(_request_images(message))
        elif isinstance(message, ModelResponse):
            images.extend(_response_images(message))
    return images


def _request_images(message: ModelRequest) -> list[GeneratedImage]:
    return [
        image_from_binary(file.data, media_type=file.media_type, filename_prefix=_safe_filename_prefix(part.tool_name))
        for part in message.parts
        if isinstance(part, ToolReturnPart)
        for file in part.files
        if isinstance(file, BinaryContent) and file.is_image
    ]


def _response_images(message: ModelResponse) -> list[GeneratedImage]:
    return [
        image_from_binary(part.content.data, media_type=part.content.media_type)
        for part in message.parts
        if isinstance(part, FilePart) and part.content.is_image
    ]


def _safe_filename_prefix(value: str) -> str:
    prefix = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-._")
    return prefix or "tool-image"


def _message_history(history: Sequence[tuple[str, str]]) -> list[ModelMessage]:
    messages: list[ModelMessage] = []
    for role, content in history[-20:]:
        if not content:
            continue
        if role == "user":
            messages.append(ModelRequest(parts=[UserPromptPart(content=content)]))
        elif role == "assistant":
            messages.append(ModelResponse(parts=[TextPart(content=content)]))
    return messages


class TopicEndAgent:
    """Decides whether a bot-to-bot thread should stop without replying."""

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.client = OpenAIChatClient(api_key=api_key, model=model, base_url=base_url, http_client=http_client)

    async def should_end_topic(
        self,
        incoming_text: str,
        *,
        history: Sequence[tuple[str, str]],
        bot_reply_streak: int,
    ) -> bool:
        if _looks_like_closing_loop(incoming_text, history=history, bot_reply_streak=bot_reply_streak):
            return True
        if not self.client.is_configured:
            return False

        recent = "\n".join(f"{role}: {content}" for role, content in history[-8:]) or "(no history)"
        messages = [
            {
                "role": "system",
                "content": (
                    "你是 Telegram bot 對話迴圈終止判斷 agent。"
                    "任務: 判斷我方 bot 是否應該不要回覆這則來自另一個 bot 的訊息, "
                    "以避免兩個 bot 無限互相回覆。"
                    "如果對話已經只是禮貌收尾、重複確認、短句互相附和、沒有新資訊或像 '好的/好/了解' 來回, 回覆 END。"
                    "如果對方 bot 提出明確新問題、提供需要處理的新資訊、或人類顯然期待繼續處理, 回覆 CONTINUE。"
                    "只能輸出 END 或 CONTINUE。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"目前連續回覆 bot 次數: {bot_reply_streak}\n"
                    f"近期對話:\n{recent}\n\n"
                    f"新的 bot 訊息:\n{incoming_text}\n\n"
                    "是否結束話題?"
                ),
            },
        ]
        decision = await self.client.complete(messages, temperature=0)
        return decision.strip().upper().startswith("END")


def _looks_like_closing_loop(
    incoming_text: str,
    *,
    history: Sequence[tuple[str, str]],
    bot_reply_streak: int,
) -> bool:
    normalized = re.sub(r"[\s。.!\uFF01?\uFF1F~\uFF5E、\uFF0C,]+", "", incoming_text.casefold())
    closing_tokens = {
        "好",
        "好的",
        "了解",
        "瞭解",
        "收到",
        "ok",
        "okay",
        "感謝",
        "謝謝",
        "沒問題",
        "是的",
        "嗯",
    }
    if normalized in closing_tokens:
        return True
    recent_assistant = [content for role, content in history[-4:] if role == "assistant"]
    return (
        bot_reply_streak > 0
        and bool(recent_assistant)
        and normalized
        in {re.sub(r"[\s。.!\uFF01?\uFF1F~\uFF5E、\uFF0C,]+", "", content.casefold()) for content in recent_assistant}
    )
