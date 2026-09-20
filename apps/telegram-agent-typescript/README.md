# telegramagent TypeScript

TypeScript migration of `telegramagent`, isolated under `./apps/telegram-agent-typescript` while the Python service remains available.

## Runtime stack

- `@earendil-works/pi-coding-agent`: complete per-chat `AgentSession` lifecycle, persistence, retry, compaction, steering, follow-up, tool loop, and Agent Skills.
- `@earendil-works/pi-agent-core`: official agent message and event contracts.
- `@earendil-works/pi-ai`: provider/model and media primitives.
- grammY: Telegram Bot API.
- Biome: formatting and linting.
- Vitest: tests.

There is no custom agent loop and no Vercel AI SDK. Telegram code owns only update routing and the mapping from Telegram chat IDs to Pi sessions.

## Current implementation

Available now:

- private chat and group mention/reply routing
- allowlist and bot-loop limits
- `/start`, `/help`, `/id`, `/ask`, `/cancel`, and `/reset`
- isolated durable Pi JSONL session per Telegram chat
- Pi-managed retry, compaction, steering, follow-up, abort, tool loop, and persistence
- `SOUL.md` and filtered Agent Skills
- bounded Telegram image input
- public HTTP(S)-only URL loading as a Pi tool, including redirect, timeout, type, and size checks
- Morsel rich-rendering tool and smart long-reply routing
- Telegram HTML rendering and 4096-character chunking
- secret-redacted logs

Not yet at Python parity:

- AnyDoc document conversion
- image generation command
- file-backed events and task management commands
- Telegram reply-tree to Pi session-tree mapping
- kabigon, Yahoo Finance MCP, Firecrawl MCP, Gurume, and bounded container tools
- Logfire integration

Track these items in [`docs/plans/2026-04-12_typescript-migration-plan.md`](docs/plans/2026-04-12_typescript-migration-plan.md).

## Requirements

- Node.js 22.19 or newer
- Telegram bot token
- OpenAI-compatible Chat Completions endpoint and API key

## Install and run

From the repository root:

```bash
cd apps/telegram-agent-typescript
npm install
npm run build
npm start
```

The scripts load `../../.env` first and then `./.env` as an optional override. Paths such as `SOUL.md`, `.agents`, and `.telegramagent` resolve against the repository root by default. Override that location with `TELEGRAMAGENT_PROJECT_ROOT`.

For development:

```bash
cd apps/telegram-agent-typescript
npm run dev -- --verbose
```

Do not run the Python and TypeScript bots with the same `BOT_TOKEN` simultaneously. Both would consume the same long-polling update stream.

## Quality gates

```bash
cd apps/telegram-agent-typescript
npm run format:check
npm run lint
npm run typecheck
npm test
npm run build
```

Apply Biome formatting and safe fixes with:

```bash
npm run check:write
```

## Session storage

Each chat uses a Pi-native session directory:

```text
BOT_SESSION_LOG_DIR/<chat-id>/pi/*.jsonl
```

Pi owns the agent session lifecycle and transcript format. The TypeScript service does not read or rewrite Python `session-v2.jsonl` files. `/reset` removes only that chat's TypeScript Pi directory.

## Model configuration

The existing variables remain the primary configuration:

```env
BOT_TOKEN=...
OPENAI_API_KEY=...
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-5.6-luna
```

The runtime registers these as an explicit Pi OpenAI-compatible provider. Coding tools are disabled. Only approved custom tools are exposed; the public URL loader is enabled by `BOT_PROACTIVE_ENABLED`, and Morsel is enabled by its existing mode and API-key settings.

## Docker

Build from the repository root so the Dockerfile can copy `apps/telegram-agent-typescript/` and `SOUL.md`:

```bash
docker build -f apps/telegram-agent-typescript/Dockerfile -t telegramagent-typescript:local .
docker compose -f apps/telegram-agent-typescript/docker-compose.yml up -d --build
docker compose -f apps/telegram-agent-typescript/docker-compose.yml logs -f telegramagent-typescript
docker compose -f apps/telegram-agent-typescript/docker-compose.yml down
```

The Compose file intentionally uses a different service and image name from the Python deployment. Stop the Python service before starting this one with the same bot token.
