from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from collections.abc import Sequence
from typing import NotRequired
from typing import Protocol
from typing import TypedDict
from typing import cast

import httpx
from loguru import logger


class TelegramChat(TypedDict):
    id: int
    type: str


class TelegramUser(TypedDict, total=False):
    id: int
    is_bot: bool
    first_name: str
    username: str


TelegramMessage = TypedDict(
    "TelegramMessage",
    {"message_id": int, "chat": TelegramChat, "text": str, "from": TelegramUser, "reply_to_message": object},
    total=False,
)


class TelegramUpdate(TypedDict):
    update_id: int
    message: NotRequired[TelegramMessage]


class Agent(Protocol):
    async def reply(self, prompt: str, *, history: Sequence[tuple[str, str]]) -> str: ...


class SkillTool(Protocol):
    async def handle(self, text: str, *, chat_id: int, user_id: int | None) -> str | None: ...


class TopicEndJudge(Protocol):
    async def should_end_topic(
        self,
        incoming_text: str,
        *,
        history: Sequence[tuple[str, str]],
        bot_reply_streak: int,
    ) -> bool: ...


class TelegramGateway(Protocol):
    async def get_me(self) -> dict[str, object]: ...

    async def get_updates(self, *, offset: int | None, poll_timeout: int = 30) -> list[TelegramUpdate]: ...

    async def send_message(self, chat_id: int, text: str, *, reply_to_message_id: int | None = None) -> None: ...


class TelegramApiError(RuntimeError):
    """Raised when Telegram Bot API returns an error response."""


class TelegramClient:
    def __init__(self, token: str, *, http_client: httpx.AsyncClient | None = None) -> None:
        self.token = token
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.http_client = http_client

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

    async def send_message(self, chat_id: int, text: str, *, reply_to_message_id: int | None = None) -> None:
        for chunk in _chunk_text(text):
            payload: dict[str, object] = {
                "chat_id": chat_id,
                "text": chunk,
                "disable_web_page_preview": True,
            }
            if reply_to_message_id is not None:
                payload["reply_to_message_id"] = reply_to_message_id
            await self._request("sendMessage", payload)

    async def _request(self, method: str, payload: dict[str, object] | None = None) -> object:
        if self.http_client is None:
            async with httpx.AsyncClient(timeout=60) as client:
                return await self._post(client, method, payload)
        return await self._post(self.http_client, method, payload)

    async def _post(self, client: httpx.AsyncClient, method: str, payload: dict[str, object] | None) -> object:
        response = await client.post(f"{self.base_url}/{method}", json=payload or {})
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise TelegramApiError("Telegram API returned a non-object response")
        if not data.get("ok"):
            description = data.get("description", "unknown Telegram API error")
            raise TelegramApiError(str(description))
        return data.get("result")


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
        topic_end_judge: TopicEndJudge | None = None,
        skill_tool: SkillTool | None = None,
        tools: Sequence[SkillTool] = (),
    ) -> None:
        self.telegram = telegram
        self.agent = agent
        self.whitelist = whitelist or set()
        self.bot_username = bot_username
        self.bot_user_id = bot_user_id
        self.max_consecutive_replies_to_bots = max_consecutive_replies_to_bots
        self.topic_end_judge = topic_end_judge
        self.skill_tool = skill_tool
        self.tools = list(tools)
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
        text = message.get("text")
        chat = message.get("chat")
        if not text or not chat:
            return

        chat_id = chat["id"]
        sender = message.get("from")
        user_id = sender.get("id") if sender else None
        message_id = message.get("message_id")

        if not self._should_respond_to_message(chat=chat, message=message, text=text):
            logger.debug("Ignored unaddressed group message in chat_id={}", chat_id)
            return

        prompt = self._strip_bot_mention(text)
        if await self._should_end_bot_topic(chat_id=chat_id, sender=sender, prompt=prompt):
            logger.info("Topic-end judge stopped bot-to-bot reply loop in chat_id={} sender_id={}", chat_id, user_id)
            return

        if not self._is_allowed(chat_id=chat_id, user_id=user_id):
            logger.warning("Rejected message from unauthorized chat_id={} user_id={}", chat_id, user_id)
            await self.telegram.send_message(
                chat_id, "這個機器人目前沒有開放給你使用。", reply_to_message_id=message_id
            )
            return

        reply = await self.build_reply(chat_id, prompt, user_id=user_id)
        await self.telegram.send_message(chat_id, reply, reply_to_message_id=message_id)

    async def build_reply(self, chat_id: int, text: str, *, user_id: int | None = None) -> str:
        for tool in self._management_tools():
            tool_reply = await tool.handle(text, chat_id=chat_id, user_id=user_id)
            if tool_reply is not None:
                return tool_reply

        command_reply = await self._handle_builtin_command(chat_id=chat_id, text=text, user_id=user_id)
        if command_reply is not None:
            return command_reply
        return await self._ask_agent(chat_id, text.strip())

    def _management_tools(self) -> list[SkillTool]:
        tools = [*self.tools]
        if self.skill_tool is not None:
            tools.insert(0, self.skill_tool)
        return tools

    async def _handle_builtin_command(self, *, chat_id: int, text: str, user_id: int | None) -> str | None:
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
                self.histories.pop(chat_id, None)
                return "已清除這個聊天室的對話記憶。"
            case "/ask":
                if not prompt:
                    return "請在 /ask 後面加上你想問的內容。"
                return await self._ask_agent(chat_id, prompt)
            case "/skills" | "/soul" | "/memory":
                return "這個 bot 尚未啟用這個管理功能。"
            case _ if text.startswith("/"):
                return "我不認識這個指令。輸入 /help 查看可用指令。"
            case _:
                return None

    async def _ask_agent(self, chat_id: int, prompt: str) -> str:
        history = self.histories.setdefault(chat_id, [])
        try:
            reply = await self.agent.reply(prompt, history=history)
        except httpx.HTTPError:
            logger.exception("LLM request failed")
            return "AI 服務暫時無法使用, 請稍後再試。"
        history.extend([("user", prompt), ("assistant", reply)])
        del history[:-20]
        return reply

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
                    history=self.histories.get(chat_id, ()),
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
        return mention_pattern.sub("", text).strip() or text


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
            "/skills add <package> - 使用 npx skills add 安裝 Agent Skills",
            "/skills list - 列出已安裝 Agent Skills",
            "/soul show|reload|path - 管理 SOUL.md",
            "/memory show|reload|path - 管理 MEMORY.md",
            "也可以直接傳一般文字給我。",
        ]
    )


def _chunk_text(text: str, limit: int = 4096) -> list[str]:
    if not text:
        return [" "]
    return [text[index : index + limit] for index in range(0, len(text), limit)]
