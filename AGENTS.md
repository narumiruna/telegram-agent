# Repository Guidelines

## Repository structure

- Work from the repository root unless a project command explicitly requires another directory.
- Python bot code, tests, packaging, Compose, and detailed docs live in `apps/telegram-agent-python/`.
- TypeScript bot code and docs live in `apps/telegram-agent-typescript/`; shared TypeScript packages live in `packages/`.
- The root `package.json` and `package-lock.json` own npm workspaces; do not move them into an app.
- Shared runtime resources remain at the root: `SOUL.md`, `.agents/`, `.events/`, `.telegramagent/`, and `.env`.
- Treat `.venv/`, `node_modules/`, `dist/`, coverage files, caches, `.events/`, and `.telegramagent/` as generated state.

## Commands

- Install Node dependencies with `npm ci`; run workspace checks with `npm run format:check`, `npm run lint`, `npm run typecheck`, and `npm test`.
- Follow `apps/telegram-agent-python/AGENTS.md` for Python commands and conventions.
- Use `docker compose -f apps/telegram-agent-python/docker-compose.yml ...` or `docker compose -f apps/telegram-agent-typescript/docker-compose.yml ...`; there is no root Compose file.
- GitHub CI, container publishing, releases, dependency updates, and deployment target the TypeScript bot; validate the retained Python app locally.

## Security

- Never commit `.env`, bot tokens, API keys, cookies, private URLs, or sensitive personal data.
- Keep `MEMORY.md` maintainer-facing and `SOUL.md` runtime-facing; neither file may contain secrets.
