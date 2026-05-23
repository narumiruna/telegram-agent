## Goal

Add optional local container tools for the Telegram chat agent so the LLM can use `bash`, `edit`, `find`, `grep`, `ls`, `read`, and `write` only when the bot is running inside Docker. Success means the tools are unavailable for normal local `uv run telegramagent`, enabled by default in Docker Compose, registered as Pydantic AI tools when both gates pass, and covered by tests.

## Context

Current agent wiring lives in `src/telegramagent/llm.py`: `ChatAgent._create_agent()` passes a static `tools=[kabigon_load_url]` list into `PydanticAgent`. Runtime capability reporting is built in `src/telegramagent/cli.py` with `CapabilityRegistry`, then injected into instructions. Settings are centralized in `src/telegramagent/settings.py`, and Docker Compose already overrides runtime environment under `services.telegramagent.environment`.

The requested tools are powerful because `bash`, `write`, and `edit` can mutate the container filesystem. Docker Compose currently does not bind-mount the whole repository into `/app`; writes to image-layer code are container-local and non-persistent, while mounted paths such as `.agents`, `.events`, `.telegramagent`, `SOUL.md`, and `MEMORY.md` have their existing mount behavior.

## Architecture

Implement a new small module, likely `src/telegramagent/container_tools.py`, that owns:

- Docker/container runtime detection.
- Path sandboxing under a configured root.
- Tool construction for Pydantic AI, using `pydantic_ai.Tool(..., name="bash" | "read" | ...)` when needed to expose exact tool names.
- Shared output truncation and timeout behavior.

Use two gates before registering the tools:

1. `BOT_CONTAINER_TOOLS_ENABLED=true`.
2. Runtime is detected as Docker/container, e.g. `/.dockerenv` exists or another narrowly-scoped container marker is present.

When either gate fails, do not register the tools and report the runtime capability as unavailable with a clear reason.

## Non-Goals

- Do not build a general host automation agent.
- Do not expose tools outside Docker by default.
- Do not add a Markdown, shell, filesystem, or sandbox dependency.
- Do not mount the full host repository into the container as part of this change.
- Do not implement interactive shell sessions, REPLs, pagers, editors, or long-running terminal UI commands.

## Assumptions

- The intended initial scope is container filesystem access, not persistent host code editing.
- `bash` is acceptable as an explicitly enabled, container-only capability, with documentation that it can execute arbitrary commands inside the container.
- `find`, `grep`, `ls`, `read`, `write`, and `edit` can be implemented with Python stdlib for safer path handling; `bash` can use `asyncio.create_subprocess_exec` with `/bin/bash -lc`.

## Plan

- [x] Add settings in `src/telegramagent/settings.py` for `BOT_CONTAINER_TOOLS_ENABLED=false`, `BOT_CONTAINER_TOOLS_ROOT=.` or `/app`, `BOT_CONTAINER_TOOLS_TIMEOUT_SECONDS`, and output/read-size limits; verified with `uv run pytest -q tests`.
- [x] Add Docker Compose defaults in `docker-compose.yml`, preferably `BOT_CONTAINER_TOOLS_ENABLED: ${BOT_CONTAINER_TOOLS_ENABLED:-true}` and `BOT_CONTAINER_TOOLS_ROOT: ${BOT_CONTAINER_TOOLS_ROOT:-/app}`, so Compose enables the tools by default while local `uv run` does not; verified with `docker compose config` showing `BOT_CONTAINER_TOOLS_ENABLED: "true"` and `BOT_CONTAINER_TOOLS_ROOT: /app`.
- [x] Implement `src/telegramagent/container_tools.py` with container detection, sandboxed path resolution, output truncation, and tool functions named/exposed as `bash`, `read`, `write`, `edit`, `ls`, `find`, and `grep`; verified with `tests/test_container_tools.py`.
- [x] Implement `bash` with a timeout, captured stdout/stderr, non-interactive command guidance, output truncation, and root working directory; verified with `tests/test_container_tools.py`. Avoid blocking APIs and never allocate a TTY.
- [x] Implement `read`, `write`, and `edit` with UTF-8 text semantics, exact replacement for `edit`, parent-directory creation for `write`, and size limits; verified with `tests/test_container_tools.py`.
- [x] Implement `ls`, `find`, and `grep` as Python stdlib read-only tools under the sandbox root, not as shell passthroughs; verified with `tests/test_container_tools.py`.
- [x] Update `ChatAgent` construction in `src/telegramagent/llm.py` to accept an additional `tools` sequence or `container_tools` sequence, append it to `[kabigon_load_url]`, and keep existing MCP toolset behavior; verified by extending the existing PydanticAgent capture test in `tests/test_telegram_bot.py`.
- [x] Update `src/telegramagent/cli.py` to construct container tools only when enabled and detected in-container, register a `container_tools` capability with available/unavailable reason, and pass tools into `ChatAgent`; verified with `tests/test_container_tool_wiring.py`.
- [x] Update `README.md` and `.env.example` to document the Docker-only tools, their risk model, environment variables, Compose default, and the fact that Agent Skills do not grant these tools unless runtime capability is enabled; verified with `rg "BOT_CONTAINER_TOOLS" README.md .env.example docker-compose.yml`.
- [x] Run full validation: `uv run ruff format --check`, `uv run ruff check .`, `uv run ty check .`, `uv run pytest -q tests`, and Docker smoke checks for Compose config plus in-container tool names.

## Risks

- `bash` is intentionally powerful; a compromised prompt could mutate container state or mounted state directories. The Docker-only gate and sandbox root reduce host exposure but do not make arbitrary command execution safe.
- Running Compose as root currently avoids bind-mount permission issues; if `bash` is enabled, commands also run as root in the container. A later hardening pass could run the container as a non-root user or add a separate writable workspace mount.
- If the tool root is `/app`, writes to application code inside the image layer are not persistent across rebuilds/restarts. This should be documented to avoid false expectations.
- Tool result content may be sent to the LLM provider. Documentation should warn users not to let the bot read secrets unless they accept that exposure.

## Rollback / Recovery

- Disable tools immediately by setting `BOT_CONTAINER_TOOLS_ENABLED=false` in Docker Compose or `.env`, then restart the container.
- If container files are mutated unexpectedly, recreate the container from the image and restore mounted state directories from backups if needed.
- If the feature causes tool-call loops or latency, reduce timeout/size limits or remove the tool list passed to `ChatAgent` while keeping settings in place.

## Completion Checklist

- [x] Local runs cannot register container tools by default, verified by `tests/test_container_tool_wiring.py` with disabled settings and no container marker.
- [x] Docker Compose enables container tools by default, verified by `docker compose config | rg -n "BOT_CONTAINER_TOOLS_(ENABLED|ROOT)|HOME"` showing `BOT_CONTAINER_TOOLS_ENABLED: "true"` and `BOT_CONTAINER_TOOLS_ROOT: /app`; Docker image smoke also showed `is_running_in_container() == True` and tool names `['bash', 'edit', 'find', 'grep', 'ls', 'read', 'write']`.
- [x] The agent registers exactly `bash`, `edit`, `find`, `grep`, `ls`, `read`, and `write` only when both gates pass, verified by `tests/test_container_tool_wiring.py` and the PydanticAgent capture test in `tests/test_telegram_bot.py`.
- [x] All filesystem tools reject paths outside the configured root, verified by `tests/test_container_tools.py` using `..`, absolute outside paths, and an escaping symlink.
- [x] `bash` is bounded by timeout and output limits, verified by `tests/test_container_tools.py`.
- [x] Documentation explains the Docker-only scope, security risks, env vars, and persistence caveats, verified by README and `.env.example` updates plus `rg "BOT_CONTAINER_TOOLS" README.md .env.example docker-compose.yml`.
- [x] Full quality gate passes with `uv run ruff format --check`, `uv run ruff check .`, `uv run ty check .`, and `uv run pytest -q tests` (`120 passed`).
