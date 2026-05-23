## Goal

Make the Telegram agent more proactive in the coding-agent sense: it should infer safe next actions from conversation context, execute bounded work when it has enough information, show progress, and avoid asking the user to repeat already-provided context. Success means the bot behaves more like a task runner with explicit safe autonomy, not just a one-shot chat responder.

## Context

Relevant patterns from `third_party` were applied without vendoring or depending on those sources:

- OpenCode session messages inspired durable per-chat JSONL records.
- Claude Code command/task queues inspired task status, priority, cancellation, and progress-message editing.
- pi-bot-core event and message-log patterns inspired queue caps and append-only replay.

Implemented in this project:

- `src/telegramagent/session.py`: durable per-chat `log.jsonl` records and history reconstruction.
- `src/telegramagent/capabilities.py`: explicit runtime capability registry.
- `src/telegramagent/actions.py`: structured `ActionRouter` decisions and YouTube fallback policy.
- `src/telegramagent/tasks.py`: proactive task queue and `/tasks` management tool.
- `src/telegramagent/telegram.py`: session-backed history, background proactive task dispatch, edit-status progress, and management-command blocking.
- `src/telegramagent/cli.py`: runtime wiring for session logs, capabilities, and task queue.

## Architecture

1. **Session Store**
   - Durable per-chat JSONL records for user, assistant, synthetic, action-start, action-result, action-error, edit, and delete records.
   - `TelegramBot` uses `SessionLog` as source of truth when configured, so restart can recover recent context.

2. **Intent / Action Router v2**
   - `ActionRouter` returns `ActionDecision(kind=...)` for `answer`, `execute`, `ask`, `confirm`, `queue`, and `fallback_failed`.
   - Deterministic rules recover the last URL from durable history for follow-ups such as `有字幕`, `抓字幕`, `抓抓看`, and `kabigon` mentions.

3. **Task Queue**
   - `TaskQueue` stores per-chat task records with `pending/running/completed/failed/cancelled`.
   - Priority ordering is `now` > `next` > `later`.
   - Long-running Telegram updates are routed through background task dispatch with `處理中…` edited into the final result.

4. **Tool Capability Registry**
   - `CapabilityRegistry` separates executable runtime capabilities from Agent Skills.
   - Kabigon/external loaders are unavailable unless explicitly wired.
   - LLM instructions receive runtime capability state and are told not to claim unavailable tools.

5. **Progress + Notification Flow**
   - URL-like Telegram messages use background dispatch when `TaskQueue` is enabled.
   - `reply_mode=edit-status` sends a bot-owned status message and edits it to the final result.
   - If editing fails, Telegram fallback sends a new message.

## Non-Goals

- [x] No unbounded autonomous browsing was added.
- [x] No arbitrary shell execution from Telegram was added.
- [x] No heavy third-party tool such as kabigon was installed or made a required dependency.
- [x] Agent Skills still do not imply runtime tool availability.
- [x] No schedule/cron behavior was added.

## Plan

- [x] Add a durable `SessionLog` module to persist per-chat JSONL records for user, assistant, synthetic, action-start, action-result, action-error, and edit events; verified by `tests/test_session.py` for append, replay, edit tombstones, delete tombstones, and last-N context reconstruction.
- [x] Replace `TelegramBot.histories` as the source of truth with a session-history adapter backed by `SessionLog`; verified by `test_session_log_restores_history_after_restart` and `test_session_log_restores_url_for_kabigon_followup_after_restart`.
- [x] Add a `CapabilityRegistry` that lists executable runtime capabilities and their availability/status, separate from Agent Skills; verified by `tests/test_capabilities.py` and `test_chat_agent_injects_runtime_capabilities_into_pydantic_instructions`.
- [x] Refactor `ProactiveActionTool` into an `ActionRouter` that returns structured decisions (`execute`, `ask`, `confirm`, `queue`, `fallback_failed`) instead of plain strings; verified by `test_action_router_returns_structured_decisions_from_history_url` and transcript-style action tests in `tests/test_actions.py`.
- [x] Add explicit fallback policy for YouTube content: try current transcript fetcher, then optional configured external loader capability if available, then ask for pasted subtitles with a clear reason; verified by `test_youtube_fallback_uses_enabled_external_loader` and `test_youtube_fallback_reports_unavailable_external_loader_without_claiming_it_ran`.
- [x] Add `TaskQueue` for long-running proactive actions with per-chat concurrency limit, cancellation, status message id, output/error, and retry metadata; verified by `tests/test_tasks.py` for success, failure, cancellation, queue ordering, and management commands.
- [x] Extend `TelegramBot.dispatch_synthetic_message` / normal message handling to route long-running actions through `TaskQueue` and use `reply_mode=edit-status` for status updates; verified by `test_handle_update_routes_long_action_through_background_task_queue` and existing edit-status Telegram tests.
- [x] Add `/tasks list|show|cancel <id>` management commands, admin-gated where appropriate; verified by `test_task_management_tool_is_admin_gated`.
- [x] Update core instructions in `llm.py` to include runtime capability state and explicit rules: use prior messages, do not ask for repeated URL, do not claim unavailable tools, and report fallback attempts honestly; verified by instruction snapshot tests in `tests/test_telegram_bot.py`.
- [x] Add README and `.env.example` docs for proactive runtime settings, task limits, session log path, optional capability plugins, and Telegram UX; verified by file review and quality gates.
- [x] Run quality gates; verified by `uv run ruff format`, `uv run ruff check --fix`, `uv run ty check`, `uv run pytest -q` (`83 passed`), `docker compose config --quiet`, and `uv run telegramagent --help`.

## Risks

- [x] More autonomy can become noisy in groups; mitigated by preserving group mention/reply gates and adding task concurrency limits.
- [x] Durable logs can store sensitive user text; mitigated by local-only `.telegramagent/` state and git/docker ignores.
- [x] Optional external loaders can be heavy or unavailable in Docker; mitigated by capability registry defaulting kabigon/external loaders to unavailable.
- [x] Background tasks can outlive context; mitigated by task records with status/output/error and `/tasks` visibility.

## Completion Checklist

- [x] The bot can answer follow-ups after restart using durable prior context, verified by `test_session_log_restores_url_for_kabigon_followup_after_restart` with URL then `你用 kabigon 抓抓看阿`.
- [x] The bot has a runtime capability registry and never claims unavailable tools, verified by `tests/test_capabilities.py` and instruction snapshot tests.
- [x] Proactive actions use structured decisions and fallback attempts, verified by `tests/test_actions.py`.
- [x] Long-running proactive work sends or edits progress/status messages, verified by `test_handle_update_routes_long_action_through_background_task_queue` and edit-status Telegram tests.
- [x] Task queue limits, cancellation, and failure states are tested by `tests/test_tasks.py`.
- [x] Documentation describes proactive runtime behavior, limits, and optional capabilities, verified by `README.md`, `.env.example`, `.gitignore`, `.dockerignore`, and `docker-compose.yml`.
