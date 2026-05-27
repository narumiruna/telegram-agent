# Repository Guidelines

## Project structure and scope

- Work from the repository root. This is a Python 3.14 Telegram bot packaged as `telegramagent` with source in `src/telegramagent/` and tests in `tests/`.
- Runtime entrypoint is `telegramagent.cli:app`; Telegram API plumbing lives in `src/telegramagent/telegram.py`, LLM wiring in `src/telegramagent/llm.py`, proactive URL handling in `src/telegramagent/actions.py`, and configuration in `src/telegramagent/settings.py`.
- Treat `third_party/`, `.venv/`, `.events/`, `.telegramagent/`, coverage files, caches, and build artifacts as generated or vendored; do not hand-edit them unless the task explicitly targets vendored code.
- `MEMORY.md` is maintainer-facing repo memory for future coding agents. `SOUL.md` is the runtime persona/context file. Do not add secrets or sensitive personal data to either file.

## Build, test, and development commands

- Install or refresh dependencies with `uv sync`.
- Run the bot locally with `uv run telegramagent`.
- Docker workflow: `docker compose up -d --build`, `docker compose logs -f telegramagent`, and `docker compose down`.
- Format code with `uv run ruff format`; check formatting with `uv run ruff format --check`.
- Lint with `uv run ruff check .`; type-check with `uv run ty check .`.
- Run tests with `uv run pytest -q tests` for a quick pass, or `uv run pytest -v -s --cov=src tests` to match CI coverage behavior.
- The `justfile` aggregates common tasks: `just all` runs format, lint, type, and test recipes, but note that `just lint` applies Ruff fixes.

## Code style and conventions

- Follow `pyproject.toml`: Ruff line length is 120, imports are single-line, and `third_party/` is excluded from Ruff.
- Keep async Telegram, HTTPX, Pydantic AI, and MCP code non-blocking; use bounded timeouts and explicit error messages for external services.
- Add new environment settings in `Settings`, `.env.example`, and README configuration docs together.
- Keep user-facing bot messages in Traditional Chinese unless the surrounding feature intentionally uses another language.
- For LLM/agent features, prefer instructions and structured tool outputs over ad hoc string manipulation; reserve string handling for input normalization and final display formatting.

## Testing and verification

- Place tests in `tests/test_*.py` and prefer focused async tests with `pytest.mark.asyncio` for async behavior.
- Update or add tests for behavior changes, especially Telegram command routing, settings parsing, URL/image handling, and error fallbacks.
- Before finishing code changes, run: `uv run ruff format --check`, `uv run ruff check .`, `uv run ty check .`, and `uv run pytest -q tests`.

## Security and configuration

- Never commit `.env`, bot tokens, API keys, cookies, private URLs, or personal secrets. Use `.env.example` for safe placeholders.
- Preserve SSRF and safety limits in proactive URL and Telegram file handling: public HTTP(S) only, size limits, no unsafe redirects, and honest failures when providers block access.
- Image input requires a vision-capable `OPENAI_MODEL`; image output requires `BOT_IMAGE_GENERATION_ENABLED=true` and a provider that supports `/images/generations`.
