# telegram-agent

Monorepo for Telegram AI bot implementations and shared URL-loading packages.

## Projects

| Path | Purpose |
| --- | --- |
| [`apps/telegram-agent-python`](apps/telegram-agent-python/README.md) | Retained Python 3.14 implementation built with Pydantic AI. |
| [`apps/telegram-agent-typescript`](apps/telegram-agent-typescript/README.md) | Primary TypeScript bot built on Pi and used by CI/CD. |
| [`packages/kabigon`](packages/kabigon/README.md) | Shared TypeScript URL-content extraction package. |

Shared runtime resources stay at the repository root:

- `SOUL.md`: bot persona and runtime context.
- `.agents/skills`: Agent Skills loaded by the bot.
- `.events` and `.telegramagent`: ignored runtime state.
- `.env`: ignored deployment and local configuration.

## Python bot

Run the Python bot from the repository root so it can load the shared `.env`, `SOUL.md`, skills, and runtime state:

```bash
uv sync --project apps/telegram-agent-python
cp apps/telegram-agent-python/.env.example .env
uv run --project apps/telegram-agent-python telegramagent
```

Docker Compose:

```bash
docker compose -f apps/telegram-agent-python/docker-compose.yml up -d --build
docker compose -f apps/telegram-agent-python/docker-compose.yml logs -f telegramagent
docker compose -f apps/telegram-agent-python/docker-compose.yml down
```

See the [Python app README](apps/telegram-agent-python/README.md) for configuration and behavior.

## Node workspaces

The root `package.json` manages `apps/*` and `packages/*` npm workspaces. GitHub CI, container publishing, and deployment target `apps/telegram-agent-typescript`; Python-only changes do not trigger CI or deployment, and the retained Python app is checked locally with its own toolchain.

The root `biome.json` defines formatting and lint rules for all TypeScript workspaces. `npm ci` installs the root tools and configures Husky. The pre-commit hook runs the repository-local Biome on staged files, applies safe fixes, and updates those staged files.

```bash
npm ci
npm run build
npm run format
npm run format:check
npm run lint
npm run typecheck
npm test
```

Run `npm run precommit` to check staged files manually.

Run one workspace with `--workspace`, for example:

```bash
npm test --workspace @telegram-agent/kabigon
```

## Security

Never commit `.env`, bot tokens, API keys, cookies, private URLs, or sensitive personal data. Keep `SOUL.md` and `MEMORY.md` free of secrets.
