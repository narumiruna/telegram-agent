# Telegram AnyDoc Input Plan

## Goal

Allow an authorized Telegram user to attach any document format supported by `firecrawl-anydoc`, convert the bounded download to Markdown, and provide that Markdown plus the user's caption/instruction to the agent as durable conversation context without sending the original binary to the model.

## Context

- `TelegramMessage` already models Telegram's `document` payload, but `TelegramBot.handle_update()` currently accepts only text, photos, and image documents; a non-image document with no caption is ignored.
- Images are downloaded through `TelegramGateway.get_file()` and `download_file()`, checked against `BOT_IMAGE_MAX_BYTES`, and passed as binary model input. Non-image documents need a separate conversion path so image-document behavior remains unchanged.
- `AgentRuntime` and `ChatAgent` already persist exact user model requests. Formatting converted Markdown into the user request lets later turns use the document without adding binary attachment semantics to the agent loop.
- PyPI `firecrawl-anydoc` 0.1.6 imports as `anydoc`, supports Python `>=3.10` through CPython ABI3 wheels, and converts `.doc`, `.docx`, `.docm`, `.ppt`, `.pps`, `.pot`, `.pptx`, `.pptm`, `.ppsx`, `.ppsm`, `.xls`, `.xlsx`, `.xlsm`, `.xlsb`, `.odt`, `.ods`, `.odp`, `.rtf`, `.epub`, `.csv`, and `.pdf`. Signature-less CSV input requires an extension/format hint.
- AnyDoc exposes `to_markdown_bytes`, `format_from_bytes`, `format_from_extension`, and typed conversion failures (`UnsupportedError`, `MalformedError`, `EncryptedError`, `ResourceLimitError`, and `MissingPartError`). Image-only PDFs are unsupported by the local package and require hosted OCR, which is outside this feature.

## Architecture

- Add `src/telegramagent/documents.py` as the document boundary. It will own a converted-document value object, the AnyDoc adapter, content/extension format selection, async thread offloading, conversion timeout/concurrency control, Markdown truncation, and package-error normalization. No temporary file path will be created from a user filename.
- Add a non-image `TelegramDocumentRef` parser in `src/telegramagent/telegram_messages.py`. Direct documents and documents in the replied message are eligible; image MIME types stay exclusively on the existing vision path. Unaddressed group documents remain metadata-only passive context and are not downloaded.
- `TelegramBot` validates authorization and group-response policy before delegating image/document work to `src/telegramagent/telegram_attachments.py`, which enforces size limits before and after `getFile`, performs bounded downloads, converts document bytes, and returns first-class attachment context. Management commands retain their current precedence, document turns bypass proactive URL routing, and the final agent prompt clearly labels document Markdown as untrusted reference material rather than instructions.
- `AgentRuntime`, `ChatAgent`, and the v2 session schema remain unchanged: they receive the final text prompt containing filename, format, truncation state, and Markdown. The non-runtime fallback history records the same formatted user context so follow-up behavior is consistent.
- Extend `TelegramGateway.download_file()` with an optional byte limit and implement streaming accumulation in `TelegramClient`; both document and image callers will use it so a missing or false Telegram size field cannot cause an unbounded in-memory download.
- Wire the converter and document capability in `src/telegramagent/cli.py`. Configuration ownership remains in `Settings`, `.env.example`, and README.

## Tech Stack

- Add the runtime dependency with `uv add firecrawl-anydoc`; import it as `anydoc`.
- Use `asyncio.to_thread()` for the synchronous native conversion and bound caller wait time with `asyncio.timeout()`; AnyDoc's built-in resource limits remain the hard parser guard.
- Use existing pytest async fakes and `httpx.MockTransport`/test streams; tests must not call Telegram or another network service.

## Non-Goals

- OCR for scanned/image-only PDFs or calling Firecrawl's hosted Parse API.
- Sending embedded document assets or images from `document.assets` to the vision model.
- Editing, exporting, or storing the uploaded binary outside the transient in-memory conversion flow.
- Supporting Telegram video, audio, voice, animation, sticker, or executable/archive attachments.
- Replacing AnyDoc's parser with per-format application libraries or maintaining a duplicate parser allow-list.

## Assumptions

- Document input is enabled by default with `BOT_DOCUMENT_INPUT_ENABLED=true`.
- Defaults are `BOT_DOCUMENT_MAX_BYTES=20000000`, `BOT_DOCUMENT_MAX_MARKDOWN_CHARS=50000`, `BOT_DOCUMENT_CONVERSION_TIMEOUT_SECONDS=30`, and `BOT_DOCUMENT_MAX_CONCURRENT_CONVERSIONS=2`.
- A caption is the user's instruction. With no caption, use a Traditional Chinese default asking the agent to read, summarize, and answer likely questions about the document.
- Format selection prefers AnyDoc content detection and falls back to the sanitized filename extension; this preserves CSV support while not trusting Telegram MIME metadata as proof of content.
- Direct and replied documents may be included in one turn, but the aggregate Markdown sent to the model must remain within the configured character budget and report truncation explicitly.
- Converted Markdown is intentionally persisted in normal conversation history so immediate follow-ups can refer to the file; raw bytes and document body are not written to logs.

## Risks

- Documents can be decompression bombs or parser stress inputs. Telegram metadata checks alone are insufficient, so byte-bounded streaming, AnyDoc resource limits, conversion timeout, and bounded concurrency are all required.
- A timed-out `asyncio.to_thread()` call cannot forcibly terminate its native worker. Keep the concurrency semaphore held until the worker actually finishes, report the timeout to the user, and rely on AnyDoc's fixed resource limits rather than spawning unlimited abandoned conversions.
- Document text can contain prompt-injection instructions. The prompt wrapper must identify it as quoted, untrusted source material and keep the Telegram caption/user request authoritative.
- Large Markdown can consume model context and durable-session space. Apply one aggregate cap before agent submission, preserve a visible truncation marker, and let existing context compaction handle later turns.
- Legacy Office formats and platform wheels may behave differently in Docker. Dependency and representative conversion smoke checks must run under the repository's Python 3.14 environment.

## Rollback / Recovery

- Disabling `BOT_DOCUMENT_INPUT_ENABLED` must reject documents without downloading or converting them while preserving text and image behavior.
- The feature adds no database or session migration; rollback consists of removing the converter wiring and dependency. Existing sessions remain valid text transcripts, including already-converted Markdown turns.
- If a specific parser format fails, return an honest per-file error and keep the bot process healthy; do not fall back to unsafe shell converters or persist the binary for later processing.

## Plan

- [x] Run `uv add firecrawl-anydoc` to add the package to `pyproject.toml` and `uv.lock`, then verify the installed Python 3.14 API and a minimal CSV conversion with `uv run python -c`; evidence: `firecrawl-anydoc==0.1.6` installed, `anydoc.to_markdown_bytes(..., "csv")` rendered a Markdown table under Python 3.14, and `uv lock --check` passed.
- [x] Add red-first adapter tests in `tests/test_documents.py` using test-owned bytes/fixtures for content-detected input, CSV extension fallback, empty/oversized Markdown truncation, timeout, concurrency, and normalized AnyDoc failures; evidence: `uv run pytest -q tests/test_documents.py` failed during collection because `telegramagent.documents` did not exist.
- [x] Implement `src/telegramagent/documents.py` with `ConvertedDocument`, a narrow converter protocol, the AnyDoc bytes adapter, content-first format detection, safe filename metadata, aggregate-ready truncation metadata, thread offloading, bounded wait/concurrency, and typed domain errors; evidence: all 12 focused document adapter tests pass, including a real AnyDoc CSV conversion.
- [x] Add red-first Telegram message tests in `tests/test_telegram_documents.py` and `tests/test_telegram_groups.py` for a direct document with a caption, a captionless document, a replied document, an unaddressed group document, and an image document; evidence: all five focused tests failed because `TelegramBot` had no document-converter boundary.
- [x] Extend `src/telegramagent/telegram_types.py` and `src/telegramagent/telegram_messages.py` with non-image document references and direct/reply extraction, then update `TelegramBot.handle_update()` and response/history helpers to accept converted documents, preserve command precedence, bypass proactive URL handling for attachment turns, wrap Markdown as untrusted reference material, and persist the same model-visible context; evidence: six direct/reply/captionless/image/unaddressed/unauthorized document routing tests pass, and the full 55-test Telegram bot/group suite remains green.
- [x] Add red-first bounded-download tests in `tests/test_telegram_client.py` and bot error-path tests in `tests/test_telegram_documents.py` for message metadata overflow, `getFile` overflow, streamed overflow with missing metadata, disabled input, Telegram download failure, unsupported/image-only PDF, encrypted/malformed/resource-limited conversion, timeout, and empty output; evidence: the focused run failed because the bounded-download exception/API did not exist; all bot expectations use deterministic fakes and safe Traditional Chinese messages.
- [x] Change `TelegramGateway.download_file()` and `TelegramClient.download_file()` to accept `max_bytes`, stream into a bounded buffer, and raise a dedicated size error; update image and document callers plus `FakeTelegram` so both attachment paths remain bounded; evidence: the final Telegram client/document/bot/group command passes all 77 tests, including streamed overflow without metadata and the unchanged image path.
- [x] Add the five `BOT_DOCUMENT_*` fields and validation to `src/telegramagent/settings.py`, construct/wire the AnyDoc converter and `document_input.anydoc` capability in `src/telegramagent/cli.py`, and add behavior-focused settings/CLI tests without asserting against real config files; evidence: 21 settings/CLI tests pass and `uv run ty check .` passes.
- [x] Update `.env.example`, README features/architecture/configuration, and the Telegram `/help` text with supported formats, limits, caption/default behavior, persistence, and the no-OCR/embedded-assets limitations; evidence: review confirms all five names/defaults match `Settings`, and 32 focused settings/CLI/help/document tests pass.
- [x] Add an integration test using the real AnyDoc adapter and a small test-owned supported document to prove Telegram bytes become Markdown in the agent request, filename/caption survive, proactive URL routing is skipped, the binary is absent, and a follow-up can use the persisted document turn; evidence: a real CSV attachment reaches the text-only agent as a Markdown table, persists for follow-up, and the final focused document/runtime/session command passes all 50 tests.
- [x] Run `uv run ruff format --check`, `uv run ruff check .`, `uv run ty check .`, and `uv run pytest -q tests`; then audit the diff for generated/vendored edits, secret or document-content logging, session-schema changes, unbounded reads, and image-input regressions. Evidence: Ruff format reports 55 files clean, Ruff lint and `ty` pass, all 246 tests pass, `uv lock --check`, Tombi, and `git diff --check` pass; a final fresh 1,330-node graph traces `handle_update` through the attachment boundary, every attachment caller supplies `max_bytes`, `session.py` is unchanged, no document body is logged, and no generated/vendored file is modified.

## Completion Checklist

- [x] `firecrawl-anydoc` is locked as a runtime dependency and imports successfully under the project's Python 3.14 environment. Evidence: version 0.1.6 is in `uv.lock`, the CSV smoke conversion renders Markdown, and `uv lock --check` passes.
- [x] Authorized users can send every AnyDoc-supported Telegram document type through one content-detected/extension-fallback path, including CSV, with or without a caption. Evidence: the adapter delegates every non-image document to AnyDoc without a duplicate allow-list; content detection, extension fallback, caption, captionless, direct, and replied cases pass.
- [x] The agent receives bounded Markdown, safe filename/format metadata, the authoritative user instruction, and an explicit truncation marker when needed; raw binary is never sent to the model or persisted. Evidence: adapter and Telegram integration tests assert sanitization, truncation, prompt metadata, text-only agent input, and empty image input.
- [x] Converted document context is available to immediate and later turns through the existing agent runtime/session behavior without a v2 schema migration. Evidence: the real CSV integration restores converted Markdown on follow-up and `src/telegramagent/session.py` has no diff.
- [x] Image documents still use vision input, document turns do not accidentally trigger proactive URL actions, and unaddressed or unauthorized group documents are not downloaded. Evidence: dedicated image-routing, proactive-bypass, unaddressed-group, and unauthorized tests pass.
- [x] Metadata, download, conversion, output, timeout, and concurrency limits are enforced with safe Traditional Chinese failures for unsupported, encrypted, malformed, resource-limited, empty, oversized, and transient Telegram cases. Evidence: adapter, bounded-stream, and Telegram failure-mapping tests cover every listed case.
- [x] README, `/help`, `.env.example`, capability reporting, and `Settings` describe the same supported formats, defaults, persistence, and OCR/embedded-asset limitations. Evidence: documentation/config review matches the five validated settings and capability tests.
- [x] Focused tests and all repository formatting, lint, type, and test gates pass with no generated/vendored files, secrets, document bodies in logs, or unintended session-schema changes. Evidence: 50 focused integration tests, 77 Telegram tests, 246 total tests, all quality gates, and the final diff audit pass.
