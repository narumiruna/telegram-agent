from __future__ import annotations

from collections.abc import Mapping
from typing import cast

import httpx
from loguru import logger

from telegramagent.telegram_rendering import TELEGRAM_PARSE_MODE
from telegramagent.telegram_rendering import sanitize_telegram_text
from telegramagent.telegram_rendering import telegram_html_chunks
from telegramagent.telegram_types import LongMessagePublisher
from telegramagent.telegram_types import TelegramFile
from telegramagent.telegram_types import TelegramUpdate
from telegramagent.telegraph_pages import TelegraphPagePublisher
from telegramagent.telegraph_pages import TelegraphPublishError


class TelegramApiError(RuntimeError):
    """Raised when Telegram Bot API returns an error response."""


class TelegramDownloadTooLargeError(TelegramApiError):
    """Raised before a Telegram file download can exceed its byte limit."""


class TelegramClient:
    def __init__(
        self,
        token: str,
        *,
        http_client: httpx.AsyncClient | None = None,
        telegraph_publisher: LongMessagePublisher | None = None,
        long_message_threshold: int = 1000,
    ) -> None:
        self.token = token
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.http_client = http_client
        self.telegraph_publisher = telegraph_publisher or TelegraphPagePublisher()
        self.long_message_threshold = long_message_threshold

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

    async def get_file(self, file_id: str) -> TelegramFile:
        result = await self._request("getFile", {"file_id": file_id})
        if not isinstance(result, dict):
            raise TelegramApiError("Telegram getFile did not return an object")
        return cast(TelegramFile, result)

    async def download_file(self, file_path: str, *, max_bytes: int | None = None) -> bytes:
        if max_bytes is not None and max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        if self.http_client is None:
            async with httpx.AsyncClient(timeout=60) as client:
                return await self._get_file_content(client, file_path, max_bytes=max_bytes)
        return await self._get_file_content(self.http_client, file_path, max_bytes=max_bytes)

    async def send_message(self, chat_id: int, text: str, *, reply_to_message_id: int | None = None) -> int | None:
        last_message_id: int | None = None
        outbound_text = await self._outbound_message_text(text)
        for chunk in telegram_html_chunks(outbound_text):
            payload: dict[str, object] = {
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": TELEGRAM_PARSE_MODE,
                "disable_web_page_preview": False,
            }
            if reply_to_message_id is not None:
                payload["reply_to_message_id"] = reply_to_message_id
            result = await self._request("sendMessage", payload)
            if isinstance(result, Mapping):
                result_mapping = cast(Mapping[str, object], result)
                message_id = result_mapping.get("message_id")
                if isinstance(message_id, int):
                    last_message_id = message_id
        return last_message_id

    async def send_photo(
        self,
        chat_id: int,
        photo: bytes,
        *,
        caption: str | None = None,
        filename: str = "image.png",
        media_type: str = "image/png",
        reply_to_message_id: int | None = None,
    ) -> int | None:
        payload: dict[str, object] = {"chat_id": chat_id}
        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = reply_to_message_id
        caption_chunks = telegram_html_chunks(caption, limit=1024) if caption else []
        if caption_chunks:
            payload["caption"] = caption_chunks[0]
            payload["parse_mode"] = TELEGRAM_PARSE_MODE

        result = await self._request_multipart(
            "sendPhoto",
            payload,
            files={"photo": (filename, photo, media_type)},
        )
        last_message_id: int | None = None
        if isinstance(result, Mapping):
            result_mapping = cast(Mapping[str, object], result)
            message_id = result_mapping.get("message_id")
            if isinstance(message_id, int):
                last_message_id = message_id
        for chunk in caption_chunks[1:]:
            last_message_id = await self.send_message(chat_id, chunk, reply_to_message_id=last_message_id)
        return last_message_id

    async def edit_message_text(self, chat_id: int, message_id: int, text: str) -> None:
        outbound_text = await self._outbound_message_text(text)
        chunks = telegram_html_chunks(outbound_text)
        await self._request(
            "editMessageText",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": chunks[0],
                "parse_mode": TELEGRAM_PARSE_MODE,
                "disable_web_page_preview": False,
            },
        )
        for chunk in chunks[1:]:
            await self.send_message(chat_id, chunk, reply_to_message_id=message_id)

    async def _outbound_message_text(self, text: str) -> str:
        sanitized = sanitize_telegram_text(text)
        if len(sanitized) <= self.long_message_threshold:
            return text
        try:
            return await self.telegraph_publisher.publish(sanitized)
        except TelegraphPublishError:
            logger.exception("Failed to publish long Telegram message to Telegraph; falling back to Telegram chunks")
            return text

    async def _request(self, method: str, payload: dict[str, object] | None = None) -> object:
        if self.http_client is None:
            async with httpx.AsyncClient(timeout=60) as client:
                return await self._post(client, method, payload)
        return await self._post(self.http_client, method, payload)

    async def _request_multipart(
        self,
        method: str,
        payload: dict[str, object],
        *,
        files: Mapping[str, tuple[str, bytes, str]],
    ) -> object:
        if self.http_client is None:
            async with httpx.AsyncClient(timeout=60) as client:
                return await self._post_multipart(client, method, payload, files=files)
        return await self._post_multipart(self.http_client, method, payload, files=files)

    async def _post(self, client: httpx.AsyncClient, method: str, payload: dict[str, object] | None) -> object:
        response = await client.post(f"{self.base_url}/{method}", json=payload or {})
        return _telegram_result(response)

    async def _post_multipart(
        self,
        client: httpx.AsyncClient,
        method: str,
        payload: dict[str, object],
        *,
        files: Mapping[str, tuple[str, bytes, str]],
    ) -> object:
        response = await client.post(f"{self.base_url}/{method}", data=payload, files=files)
        return _telegram_result(response)

    async def _get_file_content(
        self,
        client: httpx.AsyncClient,
        file_path: str,
        *,
        max_bytes: int | None,
    ) -> bytes:
        async with client.stream("GET", f"https://api.telegram.org/file/bot{self.token}/{file_path}") as response:
            response.raise_for_status()
            content_length = response.headers.get("content-length")
            if max_bytes is not None and content_length is not None:
                try:
                    declared_bytes = int(content_length)
                except ValueError:
                    declared_bytes = -1
                if declared_bytes > max_bytes:
                    raise TelegramDownloadTooLargeError("Telegram file exceeds the configured byte limit")

            content = bytearray()
            async for chunk in response.aiter_bytes():
                if max_bytes is not None and len(content) + len(chunk) > max_bytes:
                    raise TelegramDownloadTooLargeError("Telegram file exceeds the configured byte limit")
                content.extend(chunk)
            return bytes(content)


def _telegram_result(response: httpx.Response) -> object:
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise TelegramApiError("Telegram API returned a non-object response")
    if not data.get("ok"):
        description = data.get("description", "unknown Telegram API error")
        raise TelegramApiError(str(description))
    return data.get("result")
