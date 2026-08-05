# Module Boundaries Refactor Plan

## Goal

Separate Telegram transport/rendering/message parsing, public URL extraction, and module-specific tests so each responsibility has a clear owner, existing imports and runtime behavior remain compatible, and no Python source or test module exceeds 1,000 lines.

## Architecture

- Keep `telegramagent.telegram` as the stable home of `TelegramBot` and as a compatibility export for `TelegramClient`, `TelegramFile`, and `TelegramUpdate`.
- Move Telegram Bot API transport into `telegram_client.py`, message/type parsing into `telegram_messages.py`, and safe HTML/chunk rendering into `telegram_rendering.py`.
- Keep action routing and proactive orchestration in `actions.py`; move SSRF-safe fetching and URL-context extraction into `url_context.py`, with compatibility exports from `actions.py` only where current public imports require them.
- Split tests by the production module they exercise; use small test-owned fakes rather than adding production seams.

## Risks

- Private monkeypatch paths in URL tests must move with ownership; missing one can accidentally hit the network.
- Telegram formatting and URL sanitization are security-sensitive and must remain byte-for-byte compatible.
- Existing session JSONL and public imports used by `cli.py` and tests must continue to load.

## Plan

- [x] Establish the current baseline with the full repository quality gate; 169 tests plus Ruff format/lint and ty passed.
- [x] Extract Telegram rendering, message parsing/types, and API transport into focused modules while preserving `telegramagent.telegram` compatibility exports; 62 focused tests passed and the largest Telegram source is 818 lines.
- [x] Extract public-network safety and URL-context ownership from `actions.py` into `url_context.py`, update callers and monkeypatch seams, and verify 27 URL/action tests with no real network access.
- [x] Split the mixed Telegram test module into module-owned test files and shared test-only support where justified; focused bot/group/client/LLM/Telegraph tests passed and the largest test file is 679 lines.
- [x] Audit the final dependency direction, imports, file sizes, and diff for superseded paths or duplicated policy; direct owners are imported, compatibility exports resolve, and all Python files are below 1,000 lines.

## Completion Checklist

- [x] `uv run ruff format --check` passes.
- [x] `uv run ruff check .` passes.
- [x] `uv run ty check .` passes.
- [x] `uv run pytest -q tests` passes with all 169 baseline tests still discovered.
- [x] All `src/telegramagent/*.py` and `tests/test_*.py` files are at most 1,000 lines.
- [x] Existing `telegramagent.telegram` imports used by the repository still resolve.
