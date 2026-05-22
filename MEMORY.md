# MEMORY.md

## User Preferences
- Prefers Taiwan Traditional Chinese in conversation.
- Likes concise, practical answers with clear file paths when working on code.
- Values implementation and verification over plans-only responses.

## Bot Context
- This Telegram bot runs with Docker Compose and reads `.env`.
- `SOUL.md` defines the bot persona and voice.
- `MEMORY.md` stores durable context that is safe to load into instructions.
- Agent Skills are installed under `.agents/skills` for this bot runtime.

## Operating Notes
- Do not store API keys, Telegram tokens, passwords, cookies, private URLs, or secrets here.
- Keep memory factual, compact, and easy to audit.
- Use `/memory reload` after editing this file while the bot is running.
- Use `/soul reload` after editing `SOUL.md` while the bot is running.

## Open Threads
- Consider adding admin-reviewed memory append/edit commands later.
