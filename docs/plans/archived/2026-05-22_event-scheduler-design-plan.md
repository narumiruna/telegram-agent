## Goal

Implement an immediate file-backed event feature for this Telegram bot, inspired by `third_party/pi-bot-core/src/events.ts`, without adding time-based scheduling. Success means external scripts can write bounded JSON event files that trigger synthetic Telegram chat messages, while invalid files, bursts, and management-command abuse are handled safely.

## Context

`third_party/pi-bot-core` has a platform-agnostic event watcher that tails an `events/*.json` directory and supports immediate, one-shot, and periodic events. For this project, the implemented scope is intentionally smaller: **no schedule, no timers, no cron**. The borrowed pattern is file-backed event ingestion: external scripts or tools write JSON into an inbox, and the bot turns each valid file into a synthetic chat message.

Implemented in this Python Telegram bot:

- `src/telegramagent/events.py` for event parsing, validation, polling, dispatch, processed/failed file handling, queue caps, and `/events` management.
- `TelegramBot.dispatch_synthetic_message(...)` in `src/telegramagent/telegram.py` for safe synthetic dispatch, including optional `edit-status` replies.
- Runtime wiring in `src/telegramagent/cli.py`.
- Settings in `src/telegramagent/settings.py`.
- Docs in `README.md` and `.env.example`.
- Docker Compose mount for `./.events:/app/.events`.

## Architecture

### Modules

- `src/telegramagent/events.py`
  - `ImmediateEvent`: strict Pydantic event model.
  - `EventWatcher`: polls `inbox/*.json`, validates, dispatches, archives processed files, quarantines failed files, and enforces per-chat queue caps.
  - `EventManagementTool`: admin-gated `/events list|show|cancel|reload` commands.
- `src/telegramagent/telegram.py`
  - `dispatch_synthetic_message(...)`: sends event-generated text through proactive/generic reply generation while blocking management commands.
  - `edit-status` mode: sends `處理中…`, then edits that bot-owned status message into the final result; falls back to a new message if editing is unavailable.
- `tests/test_events.py`
  - Covers schema validation, schedule-field rejection, dispatch/archive, invalid-file quarantine, queue caps, archive-disabled behavior, and management commands.

### Storage layout

```text
.events/
  inbox/
    <name>.json
  processed/
    <name>.json
  failed/
    <name>.<Reason>.json
```

### Event schema

```json
{
  "type": "immediate",
  "name": "summarize-video",
  "chat_id": 123456,
  "text": "請整理 https://youtu.be/iG-hzh9roNw",
  "created_by": "external-script",
  "created_at": "2026-05-22T12:00:00+08:00"
}
```

Required fields:

- `type`: must be `immediate`.
- `name`: must match `^[a-z0-9-]{1,40}$`.
- `chat_id`: Telegram chat id.
- `text`: non-empty synthetic user message text, bounded by `BOT_EVENTS_MAX_TEXT_CHARS`.

Optional fields:

- `reply_to_message_id`
- `reply_mode`: `send` by default, or `edit-status` to edit a bot-owned processing message into the result.
- `created_by`
- `created_at`
- `dedupe_key`

Schedule fields `at`, `schedule`, and `timezone` are rejected.

### Dispatch behavior

When an event file appears:

1. Validate JSON with Pydantic and explicit no-schedule checks.
2. Build synthetic text: `[EVENT:<name>] <text>`.
3. Call `TelegramBot.dispatch_synthetic_message(...)`.
4. Block management commands for event text.
5. Allow proactive URL actions and normal chat fallback.
6. Send the reply through `TelegramGateway.send_message(...)`, or use `edit-status` to send `處理中…` and then call `editMessageText` on that bot-owned message.
7. Move successful files to `processed/` when enabled, or delete them; move invalid/failed files to `failed/`.

### Settings

- `BOT_EVENTS_ENABLED=false`
- `BOT_EVENTS_DIR=.events`
- `BOT_EVENTS_SCAN_SECONDS=2`
- `BOT_EVENTS_MAX_QUEUED_PER_CHAT=5`
- `BOT_EVENTS_MAX_TEXT_CHARS=4000`
- `BOT_EVENTS_ARCHIVE_PROCESSED=true`

## Non-Goals

- [x] No one-shot reminders were added.
- [x] No periodic cron jobs were added.
- [x] No `schedule_event` tool was added.
- [x] No database-backed event system was added.
- [x] No unauthenticated remote webhook endpoint was added.
- [x] No cross-process distributed locking was added; one bot process owns the events directory.

## Plan

- [x] Confirm scope: immediate file-backed events only, with no one-shot, periodic, cron, or schedule tool; verified by user request “不做 schedule” and no schedule dependencies or cron code added.
- [x] Define Pydantic `ImmediateEvent` model in `src/telegramagent/events.py` with strict rejection of `at`, `schedule`, and `timezone`; verified by `tests/test_events.py::test_parse_event_rejects_schedule_fields` and `test_parse_event_validates_name_and_text_length`.
- [x] Define `EventWatcher` lifecycle with `run_forever()`, `stop()`, `scan_once()`, dispatch, move-to-processed, and move-to-failed behavior; verified by async temporary-directory tests in `tests/test_events.py`.
- [x] Add `TelegramBot.dispatch_synthetic_message(...)` that routes event text through proactive/generic reply generation while blocking management commands; verified by `test_synthetic_message_blocks_management_commands`, `test_synthetic_message_allows_proactive_and_generic_reply`, and `test_synthetic_message_edit_status_mode_edits_processing_message`.
- [x] Add per-chat queue cap for event dispatch bursts; verified by `test_event_watcher_defers_events_over_per_chat_queue_cap`.
- [x] Wire settings and CLI startup so the watcher starts only when `BOT_EVENTS_ENABLED=true`; verified by `tests/test_settings.py::test_event_settings_parse_env`, `uv run ty check`, and `uv run telegramagent --help`.
- [x] Add admin-gated `/events list/show/cancel/reload` commands; verified by `test_event_management_tool_is_admin_gated_and_can_list_show_cancel`.
- [x] Add Docker/README docs for mounting `.events/`, JSON schema examples, and safety limits; verified by `README.md`, `.env.example`, `docker-compose.yml`, and `docker compose config --quiet`.
- [x] Run quality gates after implementation; verified by `uv run ruff format`, `uv run ruff check --fix`, `uv run ty check`, `uv run pytest -q` (`65 passed`), `docker compose config --quiet`, and `uv run telegramagent --help`.

## Risks

- [x] File bursts can spam chats; mitigated with `BOT_EVENTS_MAX_QUEUED_PER_CHAT` and tested queue deferral.
- [x] File writes can be observed mid-write; mitigated with parse retries and invalid-file quarantine.
- [x] External producers may write malformed JSON; mitigated with strict validation and `failed/` moves.
- [x] Management command execution from stored event text is dangerous; mitigated by synthetic-message command blocking and tests.

## Completion Checklist

- [x] Event design is accepted by the user or revised from feedback; verified by user scope change “不做 schedule”.
- [x] Event schema covers immediate file-backed events and rejects schedule fields with objective validation rules; verified by `tests/test_events.py`.
- [x] Safety model covers queue limits, invalid files, management-command blocking, and processed/failed file handling; verified by `tests/test_events.py` and `tests/test_telegram_bot.py`.
- [x] Implementation is complete and verified by quality gates: `uv run ruff format`, `uv run ruff check --fix`, `uv run ty check`, `uv run pytest -q` (`65 passed`), `docker compose config --quiet`, and `uv run telegramagent --help`.
