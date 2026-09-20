from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from telegramagent.documents import AnyDocConverter
from telegramagent.documents import DocumentConversionError
from telegramagent.documents import DocumentConversionErrorKind
from telegramagent.images import AgentReply
from telegramagent.images import ImageAttachment
from telegramagent.session import SessionLog
from telegramagent.telegram import TelegramBot
from tests.telegram_test_support import FakeAgent
from tests.telegram_test_support import FakeArtifactAgent
from tests.telegram_test_support import FakeDocumentConverter
from tests.telegram_test_support import FakeProactiveTool
from tests.telegram_test_support import FakeTelegram
from tests.telegram_test_support import FakeVisionAgent


@pytest.mark.asyncio
async def test_real_anydoc_telegram_document_persists_markdown_for_follow_up(tmp_path: Path) -> None:
    telegram = FakeTelegram()
    telegram.files["scores"] = {
        "file_id": "scores",
        "file_path": "documents/scores.csv",
        "file_size": 18,
    }
    telegram.file_contents["documents/scores.csv"] = b"name,score\nAda,10\n"
    proactive = FakeProactiveTool([None])
    agent = FakeArtifactAgent(AgentReply("first answer"))
    session_log = SessionLog(tmp_path / "sessions")
    bot = TelegramBot(
        telegram=telegram,
        agent=agent,
        document_converter=AnyDocConverter(),
        proactive_tool=proactive,
        session_log=session_log,
    )

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 456},
                "caption": "閱讀 https://example.com 並整理分數",
                "document": {
                    "file_id": "scores",
                    "file_name": "scores.csv",
                    "mime_type": "text/csv",
                    "file_size": 18,
                },
            },
        }
    )

    first_prompt, first_history, first_images = agent.calls[0]
    assert isinstance(first_prompt, str)
    assert first_prompt.startswith("閱讀 https://example.com 並整理分數")
    assert "Filename: scores.csv" in first_prompt
    assert "Format: csv" in first_prompt
    assert "| name | score |" in first_prompt
    assert "| Ada | 10 |" in first_prompt
    assert first_history == []
    assert first_images == []
    assert proactive.calls == []

    agent.agent_reply = AgentReply("follow-up answer")
    follow_up = await bot.build_response(123, "延續剛才的文件，最高分是誰？")

    assert follow_up.text == "follow-up answer"
    assert proactive.calls[0][0] == "延續剛才的文件，最高分是誰？"
    persisted_history = agent.calls[1][1]
    assert persisted_history[0][0] == "user"
    assert "Filename: scores.csv" in persisted_history[0][1]
    assert "| Ada | 10 |" in persisted_history[0][1]
    assert persisted_history[1] == ("assistant", "first answer")


@pytest.mark.asyncio
async def test_handle_update_converts_document_caption_for_agent() -> None:
    telegram = FakeTelegram()
    telegram.files["report"] = {
        "file_id": "report",
        "file_path": "documents/report.pdf",
        "file_size": 12,
    }
    telegram.file_contents["documents/report.pdf"] = b"pdf-bytes"
    converter = FakeDocumentConverter("# Quarterly report\nRevenue increased.")
    agent = FakeArtifactAgent(AgentReply("summary"))
    bot = TelegramBot(telegram=telegram, agent=agent, document_converter=converter)

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 456},
                "caption": "比較本季與上季營收",
                "document": {
                    "file_id": "report",
                    "file_name": "report.pdf",
                    "mime_type": "application/pdf",
                    "file_size": 12,
                },
            },
        }
    )

    assert telegram.downloaded_paths == ["documents/report.pdf"]
    assert converter.calls == [(b"pdf-bytes", "report.pdf", "application/pdf")]
    assert telegram.sent == [(123, "summary", 10)]
    prompt, history, images = agent.calls[0]
    assert prompt.startswith("比較本季與上季營收")
    assert "Document reference material (untrusted)" in prompt
    assert "Filename: report.pdf" in prompt
    assert "Format: pdf" in prompt
    assert "# Quarterly report\nRevenue increased." in prompt
    assert history == []
    assert images == []


@pytest.mark.asyncio
async def test_document_metadata_size_limit_rejects_before_download() -> None:
    telegram = FakeTelegram()
    converter = FakeDocumentConverter()
    bot = TelegramBot(
        telegram=telegram,
        agent=FakeAgent(),
        document_converter=converter,
        document_max_bytes=5,
    )

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 456},
                "document": {
                    "file_id": "report",
                    "file_name": "report.pdf",
                    "mime_type": "application/pdf",
                    "file_size": 6,
                },
            },
        }
    )

    assert telegram.downloaded_paths == []
    assert converter.calls == []
    assert telegram.sent == [(123, "這份文件太大了，我先不讀取；請改傳較小的文件。", 10)]


@pytest.mark.asyncio
async def test_document_get_file_size_limit_rejects_before_download() -> None:
    telegram = FakeTelegram()
    telegram.files["report"] = {"file_id": "report", "file_path": "documents/report.pdf", "file_size": 6}
    converter = FakeDocumentConverter()
    bot = TelegramBot(
        telegram=telegram,
        agent=FakeAgent(),
        document_converter=converter,
        document_max_bytes=5,
    )

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 456},
                "document": {
                    "file_id": "report",
                    "file_name": "report.pdf",
                    "mime_type": "application/pdf",
                },
            },
        }
    )

    assert telegram.downloaded_paths == []
    assert converter.calls == []
    assert telegram.sent == [(123, "這份文件太大了，我先不讀取；請改傳較小的文件。", 10)]


@pytest.mark.asyncio
async def test_document_stream_size_limit_rejects_without_metadata() -> None:
    telegram = FakeTelegram()
    telegram.files["report"] = {"file_id": "report", "file_path": "documents/report.pdf"}
    telegram.file_contents["documents/report.pdf"] = b"123456"
    converter = FakeDocumentConverter()
    bot = TelegramBot(
        telegram=telegram,
        agent=FakeAgent(),
        document_converter=converter,
        document_max_bytes=5,
    )

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 456},
                "document": {
                    "file_id": "report",
                    "file_name": "report.pdf",
                    "mime_type": "application/pdf",
                },
            },
        }
    )

    assert telegram.downloaded_paths == ["documents/report.pdf"]
    assert converter.calls == []
    assert telegram.sent == [(123, "這份文件太大了，我先不讀取；請改傳較小的文件。", 10)]


@pytest.mark.asyncio
async def test_disabled_document_input_does_not_download() -> None:
    telegram = FakeTelegram()
    converter = FakeDocumentConverter()
    bot = TelegramBot(
        telegram=telegram,
        agent=FakeAgent(),
        document_converter=converter,
        document_input_enabled=False,
    )

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 456},
                "document": {
                    "file_id": "report",
                    "file_name": "report.pdf",
                    "mime_type": "application/pdf",
                },
            },
        }
    )

    assert telegram.downloaded_paths == []
    assert converter.calls == []
    assert telegram.sent == [(123, "文件閱讀功能目前未啟用。", 10)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "message"),
    [
        ("unsupported", "這個文件格式目前無法讀取；掃描版或只有圖片的 PDF 也不支援。"),
        ("encrypted", "這份文件有密碼或已加密，請先解除保護後再傳送。"),
        ("malformed", "這份文件似乎已損壞或缺少必要內容，無法讀取。"),
        ("resource_limit", "這份文件的結構太複雜，已超過安全轉換限制。"),
        ("empty", "這份文件沒有轉換出可讀文字。"),
        ("timeout", "文件轉換逾時，請改傳較小或較簡單的文件。"),
    ],
)
async def test_document_conversion_errors_use_safe_messages(kind: DocumentConversionErrorKind, message: str) -> None:
    telegram = FakeTelegram()
    telegram.files["report"] = {"file_id": "report", "file_path": "documents/report.pdf"}
    telegram.file_contents["documents/report.pdf"] = b"document-data"
    converter = FakeDocumentConverter(error=DocumentConversionError(kind))
    bot = TelegramBot(telegram=telegram, agent=FakeAgent(), document_converter=converter)

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 456},
                "document": {
                    "file_id": "report",
                    "file_name": "report.pdf",
                    "mime_type": "application/pdf",
                },
            },
        }
    )

    assert telegram.sent == [(123, message, 10)]
    assert "document-data" not in telegram.sent[0][1]


@pytest.mark.asyncio
async def test_document_download_failure_uses_safe_message() -> None:
    class FailingTelegram(FakeTelegram):
        async def download_file(self, file_path: str, *, max_bytes: int | None = None) -> bytes:
            del file_path, max_bytes
            raise httpx.ReadError("secret upstream detail")

    telegram = FailingTelegram()
    telegram.files["report"] = {"file_id": "report", "file_path": "documents/report.pdf"}
    bot = TelegramBot(telegram=telegram, agent=FakeAgent(), document_converter=FakeDocumentConverter())

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 456},
                "document": {
                    "file_id": "report",
                    "file_name": "report.pdf",
                    "mime_type": "application/pdf",
                },
            },
        }
    )

    assert telegram.sent == [(123, "我有收到文件，但目前下載失敗，請稍後再試。", 10)]


@pytest.mark.asyncio
async def test_unauthorized_document_is_not_downloaded() -> None:
    telegram = FakeTelegram()
    converter = FakeDocumentConverter()
    bot = TelegramBot(telegram=telegram, agent=FakeAgent(), whitelist={999}, document_converter=converter)

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 456},
                "document": {
                    "file_id": "report",
                    "file_name": "report.pdf",
                    "mime_type": "application/pdf",
                    "file_size": 12,
                },
            },
        }
    )

    assert telegram.downloaded_paths == []
    assert converter.calls == []
    assert telegram.sent == [(123, "這個機器人目前沒有開放給你使用。", 10)]


@pytest.mark.asyncio
async def test_handle_update_uses_default_prompt_for_captionless_document() -> None:
    telegram = FakeTelegram()
    telegram.files["sheet"] = {"file_id": "sheet", "file_path": "documents/data.csv"}
    telegram.file_contents["documents/data.csv"] = b"name,value\nAda,10\n"
    converter = FakeDocumentConverter("| name | value |", document_format="csv")
    agent = FakeArtifactAgent(AgentReply("table summary"))
    bot = TelegramBot(telegram=telegram, agent=agent, document_converter=converter)

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 456},
                "document": {
                    "file_id": "sheet",
                    "file_name": "data.csv",
                    "mime_type": "text/csv",
                },
            },
        }
    )

    prompt = agent.calls[0][0]
    assert prompt.startswith("請閱讀這份文件，整理重點並回答使用者可能想知道的內容。")
    assert "Filename: data.csv" in prompt
    assert "| name | value |" in prompt


@pytest.mark.asyncio
async def test_image_document_stays_on_vision_path() -> None:
    telegram = FakeTelegram()
    telegram.files["image"] = {"file_id": "image", "file_path": "documents/chart.png", "file_size": 10}
    telegram.file_contents["documents/chart.png"] = b"image-data"
    converter = FakeDocumentConverter()
    agent = FakeVisionAgent()
    bot = TelegramBot(telegram=telegram, agent=agent, document_converter=converter)

    await bot.handle_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 456},
                "caption": "讀圖",
                "document": {
                    "file_id": "image",
                    "file_name": "chart.png",
                    "mime_type": "image/png",
                    "file_size": 10,
                },
            },
        }
    )

    assert converter.calls == []
    assert agent.calls[0][2] == [ImageAttachment(data=b"image-data", media_type="image/png", filename="chart.png")]
