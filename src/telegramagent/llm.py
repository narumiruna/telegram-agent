from __future__ import annotations

import re
from collections.abc import Mapping
from collections.abc import Sequence

import httpx


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


class ChatAgent:
    """Small OpenAI-compatible chat client."""

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.client = OpenAIChatClient(api_key=api_key, model=model, base_url=base_url, http_client=http_client)

    @property
    def is_configured(self) -> bool:
        return self.client.is_configured

    async def reply(self, prompt: str, *, history: Sequence[tuple[str, str]] = ()) -> str:
        if not self.client.is_configured:
            return f"我目前還沒設定 OPENAI_API_KEY, 所以先原樣回覆:\n\n{prompt}"

        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": "你是一個 Telegram 機器人助理。請用繁體中文簡潔、有幫助地回答。",
            }
        ]
        for role, content in history[-10:]:
            if role in {"user", "assistant"} and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": prompt})

        return await self.client.complete(messages)


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
