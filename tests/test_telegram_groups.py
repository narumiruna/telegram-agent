from __future__ import annotations

import pytest

from telegramagent.images import AgentReply
from telegramagent.images import ImageAttachment
from telegramagent.telegram import TelegramBot
from telegramagent.url_context import UrlContext
from tests.telegram_test_support import FakeAgent
from tests.telegram_test_support import FakeArtifactAgent
from tests.telegram_test_support import FakeTelegram
from tests.telegram_test_support import FakeTopicEndJudge


@pytest.mark.asyncio
async def test_group_mention_reply_photo_downloads_replied_photo_for_vision() -> None:
    telegram = FakeTelegram()
    telegram.files["large"] = {"file_id": "large", "file_path": "photos/large.jpg", "file_size": 11}
    telegram.file_contents["photos/large.jpg"] = b"replied-large-image"
    agent = FakeArtifactAgent(AgentReply("ok"))
    bot = TelegramBot(telegram=telegram, agent=agent, bot_username="fakebot", bot_user_id=42)

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 11,
                "chat": {"id": -100, "type": "supergroup"},
                "from": {"id": 789, "username": "bob"},
                "reply_to_message": {
                    "message_id": 10,
                    "from": {"id": 456, "username": "alice"},
                    "photo": [
                        {"file_id": "small", "width": 100, "height": 100, "file_size": 30},
                        {"file_id": "large", "width": 800, "height": 600, "file_size": 11},
                    ],
                },
                "text": "@FakeBot 這張圖是什麼？",
            },
        }
    )

    assert telegram.downloaded_paths == ["photos/large.jpg"]
    assert telegram.sent == [(-100, "ok", 11)]
    prompt, history, images = agent.calls[0]
    assert history == []
    assert "Replied message context:\nSender: @alice\nType: photo" in prompt
    assert "Chat ID: -100" in prompt
    assert "Message ID: 10" in prompt
    assert "Content: 使用者回覆的是一則 photo 訊息，無文字內容" in prompt
    assert "Current user message:\n這張圖是什麼？" in prompt
    assert images == [
        ImageAttachment(data=b"replied-large-image", media_type="image/jpeg", filename="replied-telegram-photo.jpg")
    ]


@pytest.mark.asyncio
async def test_group_plain_text_is_recorded_as_passive_context_without_reply() -> None:
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
    assert bot.histories[-100] == [("user", "[群組旁聽訊息 from user_id=456] 大家好")]


@pytest.mark.asyncio
async def test_group_passive_context_can_be_disabled() -> None:
    telegram = FakeTelegram()
    bot = TelegramBot(
        telegram=telegram,
        agent=FakeAgent(),
        bot_username="fakebot",
        bot_user_id=42,
        group_passive_context_enabled=False,
    )

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
    assert bot.histories == {}


@pytest.mark.asyncio
async def test_group_passive_context_is_used_when_bot_is_later_addressed() -> None:
    telegram = FakeTelegram()
    bot = TelegramBot(telegram=telegram, agent=FakeAgent(), bot_username="fakebot", bot_user_id=42)

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": -100, "type": "supergroup"},
                "from": {"id": 456, "username": "alice"},
                "text": "我想吃牛肉麵",
            },
        }
    )
    await bot.handle_update(
        {
            "update_id": 2,
            "message": {
                "message_id": 11,
                "chat": {"id": -100, "type": "supergroup"},
                "from": {"id": 789},
                "text": "@FakeBot 剛剛大家說什麼？",
            },
        }
    )

    assert telegram.sent == [(-100, "AI: 剛剛大家說什麼？ (1)", 11)]
    assert bot.histories[-100] == [
        ("user", "[群組旁聽訊息 from @alice] 我想吃牛肉麵"),
        ("user", "剛剛大家說什麼？"),
        ("assistant", "AI: 剛剛大家說什麼？ (1)"),
    ]


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
async def test_group_mention_reply_includes_replied_text_context_in_llm_prompt() -> None:
    telegram = FakeTelegram()
    agent = FakeArtifactAgent(AgentReply("ok"))
    bot = TelegramBot(telegram=telegram, agent=agent, bot_username="fakebot", bot_user_id=42)

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 11,
                "chat": {"id": -100, "type": "supergroup"},
                "from": {"id": 789, "username": "bob"},
                "reply_to_message": {
                    "message_id": 10,
                    "date": 1_764_000_000,
                    "from": {"id": 456, "username": "alice"},
                    "text": "我覺得 Gemini 目前 coding 強，但 agentic coding 還弱",
                },
                "text": "@FakeBot 你怎麼看？",
            },
        }
    )

    assert telegram.sent == [(-100, "ok", 11)]
    prompt = agent.calls[0][0]
    assert "Replied message context:\nSender: @alice\nType: text" in prompt
    assert "Date: 2025-11-24T16:00:00+00:00" in prompt
    assert "Content: 我覺得 Gemini 目前 coding 強，但 agentic coding 還弱" in prompt
    assert "Current user message:\n你怎麼看？" in prompt
    assert "Treat the replied message and extracted URL content as the primary object" in prompt


@pytest.mark.asyncio
async def test_group_mention_reply_includes_non_text_context_placeholder() -> None:
    telegram = FakeTelegram()
    agent = FakeArtifactAgent(AgentReply("ok"))
    bot = TelegramBot(telegram=telegram, agent=agent, bot_username="fakebot", bot_user_id=42)

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 11,
                "chat": {"id": -100, "type": "supergroup"},
                "from": {"id": 789, "username": "bob"},
                "reply_to_message": {
                    "message_id": 10,
                    "from": {"id": 456, "first_name": "Alice"},
                    "sticker": {"file_id": "sticker-1"},
                },
                "text": "@FakeBot 這是什麼？",
            },
        }
    )

    assert telegram.sent == [(-100, "ok", 11)]
    prompt = agent.calls[0][0]
    assert "Replied message context:\nSender: Alice\nType: sticker" in prompt
    assert "Content: 使用者回覆的是一則 sticker 訊息，無文字內容" in prompt
    assert "Current user message:\n這是什麼？" in prompt


@pytest.mark.asyncio
async def test_group_mention_reply_to_x_url_includes_extracted_url_context() -> None:
    telegram = FakeTelegram()
    agent = FakeArtifactAgent(AgentReply("ok"))
    extractor_calls: list[str] = []

    async def extract(url: str) -> UrlContext:
        extractor_calls.append(url)
        return UrlContext(
            url=url,
            final_url="https://x.com/IEObserve/status/2058190539988898008",
            source_type="x_post",
            fetched_at="2026-05-23T00:00:00+00:00",
            extraction_status="partial",
            title="IEObserve on X",
            author="@IEObserve",
            text="這是一則擷取到的 X 貼文摘要文字。",
        )

    bot = TelegramBot(
        telegram=telegram,
        agent=agent,
        bot_username="fakebot",
        bot_user_id=42,
        url_context_extractor=extract,
    )

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 11,
                "chat": {"id": -100, "type": "supergroup"},
                "from": {"id": 789, "username": "bob"},
                "reply_to_message": {
                    "message_id": 10,
                    "from": {"id": 456, "username": "alice"},
                    "text": "https://x.com/IEObserve/status/2058190539988898008?s=20",
                },
                "text": "@FakeBot",
            },
        }
    )

    assert telegram.sent == [(-100, "ok", 11)]
    assert extractor_calls == ["https://x.com/IEObserve/status/2058190539988898008?s=20"]
    prompt = agent.calls[0][0]
    assert "URLs found:\n- https://x.com/IEObserve/status/2058190539988898008?s=20" in prompt
    assert "Extracted URL context:" in prompt
    assert "Source type: x_post" in prompt
    assert "Extraction status: partial" in prompt
    assert "Title: IEObserve on X" in prompt
    assert "Author: @IEObserve" in prompt
    assert "Content:\n這是一則擷取到的 X 貼文摘要文字。" in prompt
    assert "Current user message:\n（使用者只提及 bot，未提供額外文字。）" in prompt
    assert "respond directly with a useful interpretation/commentary/summary" in prompt


@pytest.mark.asyncio
async def test_group_mention_reply_url_entities_prioritize_replied_urls() -> None:
    telegram = FakeTelegram()
    agent = FakeArtifactAgent(AgentReply("ok"))
    extractor_calls: list[str] = []

    async def extract(url: str) -> UrlContext:
        extractor_calls.append(url)
        return UrlContext(
            url=url,
            final_url=url,
            source_type="webpage",
            fetched_at="2026-05-23T00:00:00+00:00",
            extraction_status="success",
            title="頁面",
            text=f"擷取內容: {url}",
        )

    bot = TelegramBot(
        telegram=telegram,
        agent=agent,
        bot_username="fakebot",
        bot_user_id=42,
        url_context_extractor=extract,
    )

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 11,
                "chat": {"id": -100, "type": "supergroup"},
                "from": {"id": 789, "username": "bob"},
                "reply_to_message": {
                    "message_id": 10,
                    "from": {"id": 456, "username": "alice"},
                    "text": "看這篇",
                    "entities": [
                        {
                            "type": "text_link",
                            "offset": 0,
                            "length": 3,
                            "url": "https://example.com/replied",
                        }
                    ],
                },
                "text": "@FakeBot 再看 https://example.com/current",
                "entities": [
                    {"type": "mention", "offset": 0, "length": 8},
                    {"type": "url", "offset": 12, "length": 27},
                ],
            },
        }
    )

    assert telegram.sent == [(-100, "ok", 11)]
    assert extractor_calls == ["https://example.com/replied", "https://example.com/current"]
    prompt = agent.calls[0][0]
    assert prompt.index("- https://example.com/replied") < prompt.index("- https://example.com/current")


@pytest.mark.asyncio
async def test_group_mention_reply_photo_caption_includes_caption_context() -> None:
    telegram = FakeTelegram()
    telegram.files["photo-1"] = {"file_id": "photo-1", "file_path": "photos/photo-1.jpg", "file_size": 9}
    telegram.file_contents["photos/photo-1.jpg"] = b"caption-image"
    agent = FakeArtifactAgent(AgentReply("ok"))
    bot = TelegramBot(telegram=telegram, agent=agent, bot_username="fakebot", bot_user_id=42)

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 11,
                "chat": {"id": -100, "type": "supergroup"},
                "from": {"id": 789, "username": "bob"},
                "reply_to_message": {
                    "message_id": 10,
                    "from": {"id": 456, "first_name": "Alice"},
                    "photo": [{"file_id": "photo-1", "width": 100, "height": 100}],
                    "caption": "這張圖在講模型比較",
                },
                "text": "@FakeBot",
            },
        }
    )

    assert telegram.downloaded_paths == ["photos/photo-1.jpg"]
    assert telegram.sent == [(-100, "ok", 11)]
    prompt, _history, images = agent.calls[0]
    assert "Type: photo" in prompt
    assert "Content: 使用者回覆的是一則 photo 訊息，caption: 這張圖在講模型比較" in prompt
    assert "Current user message:\n（使用者只提及 bot，未提供額外文字。）" in prompt
    assert images == [
        ImageAttachment(data=b"caption-image", media_type="image/jpeg", filename="replied-telegram-photo.jpg")
    ]


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
