# Python App Guidelines

## Project structure

- This directory is a Python 3.14 package named `telegramagent`; source is in `src/telegramagent/` and tests are in `tests/`.
- The runtime entrypoint is `telegramagent.cli:app`; Telegram API plumbing is in `src/telegramagent/telegram.py`, LLM wiring in `src/telegramagent/llm.py`, proactive URL handling in `src/telegramagent/actions.py`, and configuration in `src/telegramagent/settings.py`.
- Shared `.env`, `SOUL.md`, `.agents/`, `.events/`, and `.telegramagent/` resources remain at the repository root.

## Commands

- From this directory, install dependencies with `uv sync` and run checks with `uv run ruff format --check`, `uv run ruff check .`, `uv run ty check .`, and `uv run pytest -q tests`.
- From the repository root, run the bot with `uv run --project apps/telegram-agent-python telegramagent` so shared runtime resources resolve correctly.
- Run Compose from the repository root with `docker compose -f apps/telegram-agent-python/docker-compose.yml up -d --build`, and use the same `-f` path for `logs` or `down`.
- Run pre-commit from the repository root with `pre-commit run --config apps/telegram-agent-python/.pre-commit-config.yaml --all-files`.
- The local `justfile` aggregates Python tasks; `just lint` applies Ruff fixes.

## Code style

- Follow `pyproject.toml`: Ruff line length is 120 and imports are single-line.
- Keep async Telegram, HTTPX, Pydantic AI, and MCP code non-blocking; use bounded timeouts and explicit external-service failures.
- Add environment settings to `Settings`, `.env.example`, and `README.md` together.
- Keep user-facing bot messages in Traditional Chinese unless the feature intentionally uses another language.
- Prefer instructions and structured tool outputs over ad hoc string manipulation for LLM features.

## Testing

- Put tests in `tests/test_*.py` and use `pytest.mark.asyncio` for focused async behavior.
- Update tests for Telegram routing, settings, URL/image handling, and error fallbacks when those behaviors change.
- Before finishing Python changes, run formatting, lint, type checking, and the complete test suite.

## Security

- Preserve public-HTTP(S)-only SSRF controls, size limits, safe redirect handling, and honest provider-blocking failures.
- Image input requires a vision-capable `OPENAI_MODEL`; image output requires `BOT_IMAGE_GENERATION_ENABLED=true` and a compatible provider.
