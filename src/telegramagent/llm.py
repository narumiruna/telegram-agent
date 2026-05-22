from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Sequence

import httpx


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
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.http_client = http_client

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def reply(self, prompt: str, *, history: Sequence[tuple[str, str]] = ()) -> str:
        if not self.api_key:
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

        payload: dict[str, object] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.7,
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
