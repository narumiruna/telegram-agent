from __future__ import annotations

import asyncio
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from typing import Protocol
from typing import cast

import anydoc

DOCUMENT_TRUNCATION_MARKER = "\n\n[文件內容因長度限制已截斷]"
_WHITESPACE = re.compile(r"\s+")
_MAX_FILENAME_CHARS = 255
_MAX_MEDIA_TYPE_CHARS = 200

DocumentConversionErrorKind = Literal[
    "unsupported",
    "malformed",
    "encrypted",
    "resource_limit",
    "empty",
    "timeout",
]


class AnyDocEngine(Protocol):
    def format_from_bytes(self, data: bytes) -> str | None: ...

    def format_from_extension(self, extension: str) -> str | None: ...

    def to_markdown_bytes(self, data: bytes, format: str | None = None) -> str: ...


@dataclass(frozen=True)
class ConvertedDocument:
    filename: str
    media_type: str
    format: str | None
    markdown: str
    truncated: bool = False


class DocumentConversionError(RuntimeError):
    def __init__(self, kind: DocumentConversionErrorKind) -> None:
        super().__init__(kind)
        self.kind = kind


class AnyDocConverter:
    def __init__(
        self,
        *,
        engine: AnyDocEngine | None = None,
        max_markdown_chars: int = 50_000,
        timeout_seconds: float = 30.0,
        max_concurrent: int = 2,
    ) -> None:
        if max_markdown_chars < 1:
            raise ValueError("max_markdown_chars must be positive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be positive")
        self._engine = engine if engine is not None else cast(AnyDocEngine, anydoc)
        self.max_markdown_chars = max_markdown_chars
        self.timeout_seconds = timeout_seconds
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def convert(self, data: bytes, *, filename: str, media_type: str) -> ConvertedDocument:
        safe_filename = _safe_metadata(filename, fallback="telegram-document", limit=_MAX_FILENAME_CHARS)
        safe_media_type = _safe_metadata(
            media_type,
            fallback="application/octet-stream",
            limit=_MAX_MEDIA_TYPE_CHARS,
        )

        await self._semaphore.acquire()
        worker = asyncio.create_task(asyncio.to_thread(self._convert_sync, data, safe_filename))
        worker.add_done_callback(self._worker_done)
        try:
            async with asyncio.timeout(self.timeout_seconds):
                markdown, document_format = await asyncio.shield(worker)
        except TimeoutError as exc:
            raise DocumentConversionError("timeout") from exc
        except anydoc.UnsupportedError as exc:
            raise DocumentConversionError("unsupported") from exc
        except anydoc.EncryptedError as exc:
            raise DocumentConversionError("encrypted") from exc
        except anydoc.ResourceLimitError as exc:
            raise DocumentConversionError("resource_limit") from exc
        except (anydoc.MalformedError, anydoc.MissingPartError) as exc:
            raise DocumentConversionError("malformed") from exc
        except ValueError as exc:
            raise DocumentConversionError("unsupported") from exc

        if not isinstance(markdown, str) or not markdown.strip():
            raise DocumentConversionError("empty")
        markdown = markdown.strip()
        truncated = len(markdown) > self.max_markdown_chars
        if truncated:
            markdown = f"{markdown[: self.max_markdown_chars]}{DOCUMENT_TRUNCATION_MARKER}"
        return ConvertedDocument(
            filename=safe_filename,
            media_type=safe_media_type,
            format=document_format,
            markdown=markdown,
            truncated=truncated,
        )

    def _worker_done(self, worker: asyncio.Task[tuple[str, str | None]]) -> None:
        self._semaphore.release()
        if not worker.cancelled():
            worker.exception()

    def _convert_sync(self, data: bytes, filename: str) -> tuple[str, str | None]:
        document_format = self._engine.format_from_bytes(data)
        if document_format is None:
            extension = Path(filename).suffix.casefold()
            if extension:
                document_format = self._engine.format_from_extension(extension)
        if document_format is None:
            markdown = self._engine.to_markdown_bytes(data)
        else:
            markdown = self._engine.to_markdown_bytes(data, document_format)
        return markdown, document_format


def _safe_metadata(value: str, *, fallback: str, limit: int) -> str:
    cleaned = "".join(" " if unicodedata.category(character) in {"Cc", "Cf"} else character for character in value)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    return (cleaned or fallback)[:limit]
