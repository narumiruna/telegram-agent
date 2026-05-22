# telegramagent

A simple Telegram AI bot that uses Telegram Bot API long polling and replies through an OpenAI-compatible Chat Completions API.

## Configuration

Create a `.env` file:

```env
BOT_TOKEN=your Telegram Bot token
# Optional: comma-separated chat_id or user_id values. Leave empty to allow everyone.
BOT_WHITELIST=
# Maximum consecutive replies to other bots in bot-to-bot reply chains. Use 0 to never reply to other bots.
BOT_MAX_CONSECUTIVE_REPLIES_TO_BOTS=1
# Agent Skills directory. Leave BOT_ENABLED_SKILLS empty to load every skill under the directory.
BOT_SKILLS_DIR=.agents/skills
BOT_ENABLED_SKILLS=
# Optional: chat_id or user_id values allowed to manage skills from Telegram. Empty means BOT_WHITELIST is reused.
BOT_SKILL_ADMINS=

OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=your API key
OPENAI_MODEL=gpt-5.4-mini
```

## Run

```bash
uv run telegramagent
```

Or run the bot with Docker Compose, loading environment variables from `.env`:

```bash
docker compose up -d --build
```

Follow logs:

```bash
docker compose logs -f telegramagent
```

Stop:

```bash
docker compose down
```

## Commands

- `/start`: show an introduction
- `/help`: show help
- `/id`: show the current chat/user ID, useful for allowlist configuration
- `/reset`: clear conversation memory for the current chat
- `/ask <question>`: ask the AI assistant
- `/skills add <package>`: install Agent Skills in the local project with `npx skills add <package> --yes --copy`
- `/skills list`: list installed Agent Skills

You can also send plain text directly to the bot.

## Group Reply Rules

In private chats, the bot replies to normal text messages. In groups and supergroups, to avoid interrupting the conversation, it only replies in either of these cases:

1. The message mentions the bot account, for example `@your_bot hello`
2. The message directly replies to a bot message

## Agent Skills

The bot uses a Pydantic AI Agent to answer messages and loads Agent Skills as instructions at startup.

Default skills directory:

```text
.agents/skills/<skill-name>/SKILL.md
```

Example:

```md
---
name: chat-style
description: Telegram reply style. Use when replying to Telegram messages.
---

# Chat Style

- Use Traditional Chinese.
- Keep replies short.
```

Set `BOT_ENABLED_SKILLS=chat-style,other-skill` to load only selected skills. Leave it empty to load all skills under `BOT_SKILLS_DIR`.

Skills are currently injected as Pydantic AI instructions. The bot does not execute scripts bundled inside skills.

You can also install skills from Telegram:

```text
/skills add vercel-labs/agent-skills --skill commit
/skills list
```

Natural-language install requests are also supported, for example:

```text
安裝 narumiruna/skills 的 skills 所有
```

This is converted to:

```bash
npx skills add narumiruna/skills --skill '*' --agent universal --yes --copy
```

`/skills add` runs `npx skills add ... --yes --copy` in the project directory where the bot is running, then reloads skills after installation. By default it adds `--agent universal`, so it writes only to `.agents/skills`, which is the directory the bot reads, instead of installing into every agent directory. Before reinstalling, it detects already installed skills; use `--force` to force reinstall.

The Docker image includes `git` / `nodejs` / `npm` / `npx`. Compose mounts local `./.agents` into the container so installed skills persist, and runs as root inside the container to avoid bind-mount permission failures during skill installation.

## Bot-to-Bot Topic Ending

When the incoming message is from another Telegram bot, the program first asks a topic-ending judge agent whether it should silently stop the conversation:

- If the conversation is only a closing acknowledgement or repeated agreement such as `好` / `好的` / `了解`, the bot stops replying to avoid infinite bot-to-bot loops.
- If the other bot asks a clear new question or provides new information, the judge can allow the bot to continue replying.
- `BOT_MAX_CONSECUTIVE_REPLIES_TO_BOTS` is a safety limit. Even if the judge does not stop the topic, replies stop after this limit is exceeded.
