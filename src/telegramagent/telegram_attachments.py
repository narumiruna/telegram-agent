from __future__ import annotations

from collections.abc import Sequence

import httpx
from loguru import logger

from telegramagent.documents import ConvertedDocument
from telegramagent.documents import DocumentConversionError
from telegramagent.images import ImageAttachment
from telegramagent.telegram_client import TelegramApiError
from telegramagent.telegram_client import TelegramDownloadTooLargeError
from telegramagent.telegram_types import DocumentConverter
from telegramagent.telegram_types import TelegramDocumentRef
from telegramagent.telegram_types import TelegramGateway
from telegramagent.telegram_types import TelegramImageRef

_TELEGRAM_API_ERRORS = (httpx.HTTPError, TelegramApiError)


class TelegramImageError(RuntimeError):
    """Raised when a Telegram image cannot be safely downloaded for vision input."""


class TelegramDocumentError(RuntimeError):
    """Raised when a Telegram document cannot be safely downloaded."""


async def load_message_attachments(
    *,
    telegram: TelegramGateway,
    chat_id: int,
    image_refs: Sequence[TelegramImageRef],
    document_refs: Sequence[TelegramDocumentRef],
    reply_to_message_id: int | None,
    image_enabled: bool,
    image_max_bytes: int,
    document_enabled: bool,
    document_max_bytes: int,
    document_converter: DocumentConverter | None,
) -> tuple[list[ImageAttachment], list[ConvertedDocument]] | None:
    images = await load_message_images(
        telegram=telegram,
        chat_id=chat_id,
        image_refs=image_refs,
        reply_to_message_id=reply_to_message_id,
        enabled=image_enabled,
        max_bytes=image_max_bytes,
    )
    if images is None:
        return None
    documents = await load_message_documents(
        telegram=telegram,
        chat_id=chat_id,
        document_refs=document_refs,
        reply_to_message_id=reply_to_message_id,
        enabled=document_enabled,
        max_bytes=document_max_bytes,
        converter=document_converter,
    )
    if documents is None:
        return None
    return images, documents


async def load_message_documents(
    *,
    telegram: TelegramGateway,
    chat_id: int,
    document_refs: Sequence[TelegramDocumentRef],
    reply_to_message_id: int | None,
    enabled: bool,
    max_bytes: int,
    converter: DocumentConverter | None,
) -> list[ConvertedDocument] | None:
    if not document_refs:
        return []
    if not enabled or converter is None:
        await telegram.send_message(chat_id, "文件閱讀功能目前未啟用。", reply_to_message_id=reply_to_message_id)
        return None
    try:
        return [
            await _download_document(
                telegram=telegram,
                document_ref=document_ref,
                max_bytes=max_bytes,
                converter=converter,
            )
            for document_ref in document_refs
        ]
    except TelegramDocumentError as exc:
        await telegram.send_message(chat_id, str(exc), reply_to_message_id=reply_to_message_id)
    except DocumentConversionError as exc:
        await telegram.send_message(
            chat_id,
            _document_conversion_error_message(exc.kind),
            reply_to_message_id=reply_to_message_id,
        )
    except _TELEGRAM_API_ERRORS as exc:
        logger.warning("Failed to download Telegram document with {}", type(exc).__name__)
        await telegram.send_message(
            chat_id,
            "我有收到文件，但目前下載失敗，請稍後再試。",
            reply_to_message_id=reply_to_message_id,
        )
    except Exception as exc:  # noqa: BLE001 - converter failures must not escape Telegram update handling
        logger.error("Telegram document conversion failed with {}", type(exc).__name__)
        await telegram.send_message(
            chat_id,
            "文件轉換失敗，請確認檔案完整且格式受支援後再試。",
            reply_to_message_id=reply_to_message_id,
        )
    return None


async def _download_document(
    *,
    telegram: TelegramGateway,
    document_ref: TelegramDocumentRef,
    max_bytes: int,
    converter: DocumentConverter,
) -> ConvertedDocument:
    if document_ref.file_size is not None and document_ref.file_size > max_bytes:
        raise TelegramDocumentError("這份文件太大了，我先不讀取；請改傳較小的文件。")

    file_info = await telegram.get_file(document_ref.file_id)
    file_size = file_info.get("file_size")
    if isinstance(file_size, int) and file_size > max_bytes:
        raise TelegramDocumentError("這份文件太大了，我先不讀取；請改傳較小的文件。")
    file_path = file_info.get("file_path")
    if not isinstance(file_path, str) or not file_path:
        raise TelegramDocumentError("我有收到文件，但 Telegram 沒有提供可下載的檔案路徑。")

    try:
        data = await telegram.download_file(file_path, max_bytes=max_bytes)
    except TelegramDownloadTooLargeError as exc:
        raise TelegramDocumentError("這份文件太大了，我先不讀取；請改傳較小的文件。") from exc
    if len(data) > max_bytes:
        raise TelegramDocumentError("這份文件太大了，我先不讀取；請改傳較小的文件。")
    return await converter.convert(data, filename=document_ref.filename, media_type=document_ref.media_type)


async def load_message_images(
    *,
    telegram: TelegramGateway,
    chat_id: int,
    image_refs: Sequence[TelegramImageRef],
    reply_to_message_id: int | None,
    enabled: bool,
    max_bytes: int,
) -> list[ImageAttachment] | None:
    if not image_refs:
        return []
    if not enabled:
        await telegram.send_message(chat_id, "圖片理解功能目前未啟用。", reply_to_message_id=reply_to_message_id)
        return None
    try:
        return [
            await _download_image(telegram=telegram, image_ref=image_ref, max_bytes=max_bytes)
            for image_ref in image_refs
        ]
    except TelegramImageError as exc:
        await telegram.send_message(chat_id, str(exc), reply_to_message_id=reply_to_message_id)
    except _TELEGRAM_API_ERRORS:
        logger.exception("Failed to download Telegram image")
        await telegram.send_message(
            chat_id,
            "我有收到圖片，但目前下載失敗，請稍後再試或改用較小的圖片。",
            reply_to_message_id=reply_to_message_id,
        )
    return None


async def _download_image(
    *,
    telegram: TelegramGateway,
    image_ref: TelegramImageRef,
    max_bytes: int,
) -> ImageAttachment:
    if image_ref.file_size is not None and image_ref.file_size > max_bytes:
        raise TelegramImageError("這張圖片太大了，我先不讀取；請改傳較小的圖片。")

    file_info = await telegram.get_file(image_ref.file_id)
    file_size = file_info.get("file_size")
    if isinstance(file_size, int) and file_size > max_bytes:
        raise TelegramImageError("這張圖片太大了，我先不讀取；請改傳較小的圖片。")
    file_path = file_info.get("file_path")
    if not isinstance(file_path, str) or not file_path:
        raise TelegramImageError("我有收到圖片，但 Telegram 沒有提供可下載的檔案路徑。")

    try:
        data = await telegram.download_file(file_path, max_bytes=max_bytes)
    except TelegramDownloadTooLargeError as exc:
        raise TelegramImageError("這張圖片太大了，我先不讀取；請改傳較小的圖片。") from exc
    if len(data) > max_bytes:
        raise TelegramImageError("這張圖片太大了，我先不讀取；請改傳較小的圖片。")
    return ImageAttachment(data=data, media_type=image_ref.media_type, filename=image_ref.filename)


def _document_conversion_error_message(kind: str) -> str:
    messages = {
        "unsupported": "這個文件格式目前無法讀取；掃描版或只有圖片的 PDF 也不支援。",
        "encrypted": "這份文件有密碼或已加密，請先解除保護後再傳送。",
        "malformed": "這份文件似乎已損壞或缺少必要內容，無法讀取。",
        "resource_limit": "這份文件的結構太複雜，已超過安全轉換限制。",
        "empty": "這份文件沒有轉換出可讀文字。",
        "timeout": "文件轉換逾時，請改傳較小或較簡單的文件。",
    }
    return messages.get(kind, "文件轉換失敗，請確認檔案完整且格式受支援後再試。")
