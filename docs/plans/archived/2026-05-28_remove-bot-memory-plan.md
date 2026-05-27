## Goal

Remove `BOT_MEMORY.md` as a Telegram runtime context feature so the bot no longer loads, documents, mounts, or exposes `/memory` management for that file. Keep `MEMORY.md` as maintainer-facing repo memory for future coding agents.

## Context

`BOT_MEMORY.md` previously appeared in settings, CLI context loading, Docker Compose mounts, README setup docs, `.env.example`, and tests. `MEMORY.md` is a separate maintainer-facing file and is not loaded into Telegram runtime instructions.

## Non-Goals

- Do not remove `SOUL.md` support.
- Do not remove per-chat conversation/session memory.
- Do not make `MEMORY.md` runtime-visible to the Telegram bot.

## Plan

- [x] Remove runtime memory configuration from `src/telegramagent/settings.py` and `.env.example`; verified with `rg -n "BOT_MEMORY|bot_memory" src/telegramagent/settings.py .env.example`.
- [x] Remove `BOT_MEMORY.md` loading, reload closure, and `ContextManagementTool(command_name="memory")` wiring from `src/telegramagent/cli.py`; verified with `rg -n "BOT_MEMORY|bot_memory|command_name=\"memory\"" src/telegramagent/cli.py`.
- [x] Update `src/telegramagent/llm.py` so `ChatAgent` no longer stores or injects a separate memory context section; verified by focused context tests and instruction-order assertions.
- [x] Update Telegram command routing/help to remove `/memory`; verified with `rg -n '"/memory"|BOT_MEMORY' src/telegramagent/telegram.py`.
- [x] Delete `BOT_MEMORY.md` and `BOT_MEMORY.md.example`; verified with `test ! -e BOT_MEMORY.md && test ! -e BOT_MEMORY.md.example`.
- [x] Remove the read-only `BOT_MEMORY.md` bind mount from `docker-compose.yml`; verified with `rg -n "BOT_MEMORY" docker-compose.yml`.
- [x] Rewrite README context-file documentation so only `SOUL.md` is runtime context and `MEMORY.md` is maintainer-facing repo memory; verified with `rg -n "BOT_MEMORY|/memory" README.md AGENTS.md`.
- [x] Update tests in `tests/test_context_files.py` and `tests/test_telegram_bot.py` to remove memory-context expectations while keeping `SOUL.md` context management covered; verified with `uv run pytest -q tests/test_context_files.py tests/test_telegram_bot.py`.
- [x] Run full repository verification: `uv run ruff format --check`, `uv run ruff check .`, `uv run ty check .`, and `uv run pytest -q tests`.

## Risks

- Removing `/memory` changes the bot command surface; README and help text were updated together to avoid dangling commands.
- `ChatAgent` constructor changes can break tests or call sites if not updated consistently; focused and full tests passed.
- `MEMORY.md` and `BOT_MEMORY.md` names are easy to confuse; `MEMORY.md` remains documented as maintainer-facing only.

## Rollback / Recovery

- Revert the removal commit to restore `BOT_MEMORY.md` settings, docs, mount, and `/memory` command.
- If only Docker deployment breaks, restore the deleted bind mount or remove the missing mount line depending on the intended runtime file set.

## Completion Checklist

- [x] No runtime code references `BOT_MEMORY` or `bot_memory`, verified by `rg -n "BOT_MEMORY|bot_memory" src tests`.
- [x] No docs/config reference `/memory` or `BOT_MEMORY.md`, verified by `rg -n "BOT_MEMORY|/memory" README.md AGENTS.md .env.example docker-compose.yml`.
- [x] `BOT_MEMORY.md` and `BOT_MEMORY.md.example` are deleted, verified by `test ! -e BOT_MEMORY.md && test ! -e BOT_MEMORY.md.example`.
- [x] `SOUL.md` context loading and `/soul` management still work, verified by `uv run pytest -q tests/test_context_files.py tests/test_telegram_bot.py`.
- [x] Full quality gate passes with format, lint, type-check, and test commands.
