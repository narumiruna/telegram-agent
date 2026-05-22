from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx
import pytest

from telegramagent.llm import ChatAgent
from telegramagent.llm import TopicEndAgent
from telegramagent.telegram import TelegramBot
from telegramagent.telegram import TelegramClient
from telegramagent.telegram import TelegramUpdate


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, int | None]] = []

    async def get_me(self) -> dict[str, object]:
        return {"username": "fakebot"}

    async def get_updates(self, *, offset: int | None, poll_timeout: int = 30) -> list[TelegramUpdate]:
        return []

    async def send_message(self, chat_id: int, text: str, *, reply_to_message_id: int | None = None) -> None:
        self.sent.append((chat_id, text, reply_to_message_id))


class FakeAgent:
    async def reply(self, prompt: str, *, history: Sequence[tuple[str, str]]) -> str:
        return f"AI: {prompt} ({len(history)})"


class FakeTopicEndJudge:
    def __init__(self, decisions: Sequence[bool]) -> None:
        self.decisions = list(decisions)
        self.calls: list[tuple[str, Sequence[tuple[str, str]], int]] = []

    async def should_end_topic(
        self,
        incoming_text: str,
        *,
        history: Sequence[tuple[str, str]],
        bot_reply_streak: int,
    ) -> bool:
        self.calls.append((incoming_text, history, bot_reply_streak))
        return self.decisions.pop(0)


@pytest.mark.asyncio
async def test_start_help_id_and_reset_commands() -> None:
    bot = TelegramBot(telegram=FakeTelegram(), agent=FakeAgent())

    assert "Telegram AI 助理" in await bot.build_reply(123, "/start", user_id=456)
    assert "/ask <問題>" in await bot.build_reply(123, "/help", user_id=456)
    assert await bot.build_reply(123, "/id", user_id=456) == "chat_id: 123\nuser_id: 456"

    bot.histories[123] = [("user", "hi")]
    assert await bot.build_reply(123, "/reset", user_id=456) == "已清除這個聊天室的對話記憶。"
    assert 123 not in bot.histories


@pytest.mark.asyncio
async def test_plain_text_uses_agent_and_keeps_history() -> None:
    bot = TelegramBot(telegram=FakeTelegram(), agent=FakeAgent())

    assert await bot.build_reply(123, "你好") == "AI: 你好 (0)"
    assert await bot.build_reply(123, "/ask 第二題") == "AI: 第二題 (2)"


@pytest.mark.asyncio
async def test_group_plain_text_is_ignored_unless_addressed() -> None:
    telegram = FakeTelegram()
    bot = TelegramBot(telegram=telegram, agent=FakeAgent(), bot_username="fakebot", bot_user_id=42)

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": -100, "type": "supergroup"},
                "from": {"id": 456},
                "text": "大家好",
            },
        }
    )

    assert telegram.sent == []


@pytest.mark.asyncio
async def test_group_mention_addresses_bot_and_strips_mention() -> None:
    telegram = FakeTelegram()
    bot = TelegramBot(telegram=telegram, agent=FakeAgent(), bot_username="fakebot", bot_user_id=42)

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": -100, "type": "group"},
                "from": {"id": 456},
                "text": "@FakeBot 你好",
            },
        }
    )

    assert telegram.sent == [(-100, "AI: 你好 (0)", 10)]


@pytest.mark.asyncio
async def test_group_reply_to_bot_addresses_bot() -> None:
    telegram = FakeTelegram()
    bot = TelegramBot(telegram=telegram, agent=FakeAgent(), bot_username="fakebot", bot_user_id=42)

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 11,
                "chat": {"id": -100, "type": "supergroup"},
                "from": {"id": 456},
                "reply_to_message": {"message_id": 10, "from": {"id": 42, "username": "fakebot"}},
                "text": "繼續說",
            },
        }
    )

    assert telegram.sent == [(-100, "AI: 繼續說 (0)", 11)]


@pytest.mark.asyncio
async def test_topic_end_judge_can_stop_bot_reply_without_answering() -> None:
    telegram = FakeTelegram()
    judge = FakeTopicEndJudge([True])
    bot = TelegramBot(
        telegram=telegram,
        agent=FakeAgent(),
        bot_username="fakebot",
        bot_user_id=42,
        topic_end_judge=judge,
    )

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": -100, "type": "supergroup"},
                "from": {"id": 777, "is_bot": True, "username": "other_bot"},
                "reply_to_message": {"message_id": 9, "from": {"id": 42, "username": "fakebot"}},
                "text": "好的。",
            },
        }
    )

    assert telegram.sent == []
    assert judge.calls == [("好的。", (), 0)]


@pytest.mark.asyncio
async def test_topic_end_judge_can_continue_bot_reply() -> None:
    telegram = FakeTelegram()
    judge = FakeTopicEndJudge([False])
    bot = TelegramBot(
        telegram=telegram,
        agent=FakeAgent(),
        bot_username="fakebot",
        bot_user_id=42,
        topic_end_judge=judge,
    )

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": -100, "type": "supergroup"},
                "from": {"id": 777, "is_bot": True, "username": "other_bot"},
                "reply_to_message": {"message_id": 9, "from": {"id": 42, "username": "fakebot"}},
                "text": "請問下一步是什麼?",
            },
        }
    )

    assert telegram.sent == [(-100, "AI: 請問下一步是什麼? (0)", 10)]
    assert judge.calls == [("請問下一步是什麼?", (), 0)]


@pytest.mark.asyncio
async def test_bot_to_bot_loop_stops_after_one_reply_without_judge() -> None:
    telegram = FakeTelegram()
    bot = TelegramBot(telegram=telegram, agent=FakeAgent(), bot_username="fakebot", bot_user_id=42)

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": -100, "type": "supergroup"},
                "from": {"id": 777, "is_bot": True, "username": "other_bot"},
                "reply_to_message": {"message_id": 9, "from": {"id": 42, "username": "fakebot"}},
                "text": "好。",
            },
        }
    )
    await bot.handle_update(
        {
            "update_id": 2,
            "message": {
                "message_id": 11,
                "chat": {"id": -100, "type": "supergroup"},
                "from": {"id": 777, "is_bot": True, "username": "other_bot"},
                "reply_to_message": {"message_id": 10, "from": {"id": 42, "username": "fakebot"}},
                "text": "好的。",
            },
        }
    )

    assert telegram.sent == [(-100, "AI: 好。 (0)", 10)]


@pytest.mark.asyncio
async def test_human_message_resets_bot_to_bot_loop_guard() -> None:
    telegram = FakeTelegram()
    bot = TelegramBot(telegram=telegram, agent=FakeAgent(), bot_username="fakebot", bot_user_id=42)

    bot.bot_reply_streaks[-100] = 1
    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": -100, "type": "supergroup"},
                "from": {"id": 456, "is_bot": False},
                "text": "@fakebot 人類插話",
            },
        }
    )
    await bot.handle_update(
        {
            "update_id": 2,
            "message": {
                "message_id": 11,
                "chat": {"id": -100, "type": "supergroup"},
                "from": {"id": 777, "is_bot": True, "username": "other_bot"},
                "reply_to_message": {"message_id": 10, "from": {"id": 42, "username": "fakebot"}},
                "text": "好。",
            },
        }
    )

    assert telegram.sent == [(-100, "AI: 人類插話 (0)", 10), (-100, "AI: 好。 (2)", 11)]


@pytest.mark.asyncio
async def test_bot_to_bot_replies_can_be_fully_disabled() -> None:
    telegram = FakeTelegram()
    bot = TelegramBot(
        telegram=telegram,
        agent=FakeAgent(),
        bot_username="fakebot",
        bot_user_id=42,
        max_consecutive_replies_to_bots=0,
    )

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": -100, "type": "supergroup"},
                "from": {"id": 777, "is_bot": True, "username": "other_bot"},
                "reply_to_message": {"message_id": 9, "from": {"id": 42, "username": "fakebot"}},
                "text": "好。",
            },
        }
    )

    assert telegram.sent == []


@pytest.mark.asyncio
async def test_whitelist_rejects_unauthorized_message() -> None:
    telegram = FakeTelegram()
    bot = TelegramBot(telegram=telegram, agent=FakeAgent(), whitelist={999})

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 456},
                "text": "hi",
            },
        }
    )

    assert telegram.sent == [(123, "這個機器人目前沒有開放給你使用。", 10)]


@pytest.mark.asyncio
async def test_telegram_client_calls_bot_api() -> None:
    requests: list[tuple[str, dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((str(request.url), dict(request.headers)))
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [{"update_id": 1}]})
        return httpx.Response(200, json={"ok": True, "result": {}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        telegram = TelegramClient("token", http_client=client)
        updates = await telegram.get_updates(offset=2, poll_timeout=1)
        await telegram.send_message(123, "hello")

    assert updates == [{"update_id": 1}]
    assert requests[0][0] == "https://api.telegram.org/bottoken/getUpdates"
    assert requests[1][0] == "https://api.telegram.org/bottoken/sendMessage"


@pytest.mark.asyncio
async def test_chat_agent_uses_openai_compatible_api() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["Authorization"]
        captured["json"] = request.read().decode()
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "  回覆  "}}]},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        agent = ChatAgent(api_key="key", model="model", base_url="https://example.test/v1/", http_client=client)
        reply = await agent.reply("問題")

    assert reply == "回覆"
    assert captured["url"] == "https://example.test/v1/chat/completions"
    assert captured["authorization"] == "Bearer key"
    assert '"model":"model"' in captured["json"].replace(" ", "")


@pytest.mark.asyncio
async def test_chat_agent_falls_back_without_api_key() -> None:
    agent = ChatAgent(api_key=None, model="model")

    reply = await agent.reply("問題")

    assert "OPENAI_API_KEY" in reply
    assert "問題" in reply


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
