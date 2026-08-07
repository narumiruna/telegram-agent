from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass

import anydoc
import pytest

from telegramagent.documents import DOCUMENT_TRUNCATION_MARKER
from telegramagent.documents import AnyDocConverter
from telegramagent.documents import DocumentConversionError


@dataclass
class FakeAnyDocEngine:
    detected_format: str | None = None
    extension_format: str | None = None
    markdown: str = "# Document"
    error: Exception | None = None

    def __post_init__(self) -> None:
        self.extension_calls: list[str] = []
        self.convert_calls: list[tuple[bytes, str | None]] = []

    def format_from_bytes(self, data: bytes) -> str | None:
        return self.detected_format

    def format_from_extension(self, extension: str) -> str | None:
        self.extension_calls.append(extension)
        return self.extension_format

    def to_markdown_bytes(self, data: bytes, format: str | None = None) -> str:
        self.convert_calls.append((data, format))
        if self.error is not None:
            raise self.error
        return self.markdown


@pytest.mark.asyncio
async def test_converter_prefers_content_detected_format() -> None:
    engine = FakeAnyDocEngine(detected_format="pdf", extension_format="csv")
    converter = AnyDocConverter(engine=engine)

    converted = await converter.convert(b"%PDF", filename="report.csv", media_type="application/pdf")

    assert converted.format == "pdf"
    assert converted.filename == "report.csv"
    assert engine.extension_calls == []
    assert engine.convert_calls == [(b"%PDF", "pdf")]


@pytest.mark.asyncio
async def test_converter_uses_extension_hint_for_signatureless_csv() -> None:
    engine = FakeAnyDocEngine(extension_format="csv", markdown="| name |\n| --- |\n| Ada |")
    converter = AnyDocConverter(engine=engine)

    converted = await converter.convert(b"name\nAda\n", filename="people.CSV", media_type="text/csv")

    assert converted.format == "csv"
    assert "Ada" in converted.markdown
    assert engine.extension_calls == [".csv"]
    assert engine.convert_calls == [(b"name\nAda\n", "csv")]


@pytest.mark.asyncio
async def test_converter_lets_anydoc_reject_unknown_content_without_hint() -> None:
    engine = FakeAnyDocEngine(error=anydoc.UnsupportedError("unknown"))
    converter = AnyDocConverter(engine=engine)

    with pytest.raises(DocumentConversionError, match="unsupported") as caught:
        await converter.convert(b"unknown", filename="blob", media_type="application/octet-stream")

    assert caught.value.kind == "unsupported"
    assert engine.convert_calls == [(b"unknown", None)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (anydoc.UnsupportedError("unsupported"), "unsupported"),
        (anydoc.MalformedError("malformed"), "malformed"),
        (anydoc.EncryptedError("encrypted"), "encrypted"),
        (anydoc.ResourceLimitError("resource"), "resource_limit"),
        (anydoc.MissingPartError("missing"), "malformed"),
    ],
)
async def test_converter_normalizes_anydoc_failures(error: Exception, kind: str) -> None:
    converter = AnyDocConverter(engine=FakeAnyDocEngine(error=error))

    with pytest.raises(DocumentConversionError) as caught:
        await converter.convert(b"data", filename="file.bin", media_type="application/octet-stream")

    assert caught.value.kind == kind
    assert str(caught.value) == kind


@pytest.mark.asyncio
async def test_converter_rejects_empty_markdown() -> None:
    converter = AnyDocConverter(engine=FakeAnyDocEngine(markdown=" \n\t"))

    with pytest.raises(DocumentConversionError) as caught:
        await converter.convert(b"data", filename="empty.pdf", media_type="application/pdf")

    assert caught.value.kind == "empty"


@pytest.mark.asyncio
async def test_converter_truncates_markdown_and_sanitizes_filename() -> None:
    converter = AnyDocConverter(engine=FakeAnyDocEngine(markdown="abcdefghij"), max_markdown_chars=5)

    converted = await converter.convert(
        b"data",
        filename="  unsafe\nname\u202e.pdf\x00  ",
        media_type="application/pdf",
    )

    assert converted.markdown == f"abcde{DOCUMENT_TRUNCATION_MARKER}"
    assert converted.truncated is True
    assert converted.filename == "unsafe name .pdf"


@pytest.mark.asyncio
async def test_timeout_keeps_concurrency_slot_until_worker_finishes() -> None:
    release = threading.Event()
    first_started = threading.Event()
    call_count = 0
    call_lock = threading.Lock()

    class BlockingEngine(FakeAnyDocEngine):
        def to_markdown_bytes(self, data: bytes, format: str | None = None) -> str:
            nonlocal call_count
            with call_lock:
                call_count += 1
                current_call = call_count
            if current_call == 1:
                first_started.set()
                release.wait(timeout=2)
            return "done"

    converter = AnyDocConverter(engine=BlockingEngine(), timeout_seconds=0.01, max_concurrent=1)

    with pytest.raises(DocumentConversionError) as caught:
        await converter.convert(b"first", filename="first.pdf", media_type="application/pdf")
    assert caught.value.kind == "timeout"
    assert first_started.is_set()

    second = asyncio.create_task(converter.convert(b"second", filename="second.pdf", media_type="application/pdf"))
    await asyncio.sleep(0.02)
    assert not second.done()

    release.set()
    converted = await asyncio.wait_for(second, timeout=1)
    assert converted.markdown == "done"
    assert call_count == 2


@pytest.mark.asyncio
async def test_real_anydoc_csv_conversion() -> None:
    converter = AnyDocConverter()

    converted = await converter.convert(
        b"name,score\nAda,10\n",
        filename="scores.csv",
        media_type="text/csv",
    )

    assert converted.format == "csv"
    assert "| Ada | 10 |" in converted.markdown
