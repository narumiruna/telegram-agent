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
# Optional: chat_id or user_id values allowed to manage skills and context files from Telegram.
# Empty means BOT_WHITELIST is reused.
BOT_SKILL_ADMINS=

# SOUL.md is the bot identity/persona file.
BOT_SOUL_PATH=SOUL.md
BOT_SOUL_REQUIRED=false
BOT_SOUL_MAX_CHARS=8000

# MEMORY.md is durable context loaded into the bot instructions.
BOT_MEMORY_PATH=MEMORY.md
BOT_MEMORY_REQUIRED=false
BOT_MEMORY_MAX_CHARS=12000

# Proactive mode executes safe default actions for URLs and short follow-ups like "go".
BOT_PROACTIVE_ENABLED=true
BOT_PROACTIVE_URL_TIMEOUT_SECONDS=15
BOT_PROACTIVE_MAX_EXTRACTED_CHARS=12000
BOT_PROACTIVE_PENDING_TTL_SECONDS=900
BOT_PROACTIVE_ALLOWED_SCHEMES=http,https

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
- `/soul show|reload|path`: inspect or reload `SOUL.md`
- `/memory show|reload|path`: inspect or reload `MEMORY.md`

You can also send plain text directly to the bot.

## Proactive URL Handling

When proactive mode is enabled, the bot does not only suggest work for supported links. It executes a safe default action:

- YouTube links: fetch available subtitles/transcripts and summarize them.
- HTTP(S) text or HTML links: fetch bounded page text and summarize it.
- Short follow-ups such as `go`, `開始`, `繼續`, or `你就自動做事`: reuse the most recent pending URL/action in that chat for `BOT_PROACTIVE_PENDING_TTL_SECONDS` seconds.

Safety limits:

- Only `http` and `https` URLs are supported.
- Localhost, private networks, link-local addresses, and cloud metadata IPs are blocked.
- Redirects are not followed automatically.
- Large or non-text responses are rejected.
- If YouTube subtitles are disabled, unavailable, or blocked by YouTube, the bot says so instead of pretending it watched the video.

Disable this behavior with `BOT_PROACTIVE_ENABLED=false`.

## Group Reply Rules

In private chats, the bot replies to normal text messages. In groups and supergroups, to avoid interrupting the conversation, it only replies in either of these cases:

1. The message mentions the bot account, for example `@your_bot hello`
2. The message directly replies to a bot message

## SOUL.md and MEMORY.md

The bot can load two always-on context files before Agent Skills:

1. `SOUL.md`: who the bot is — identity, worldview, voice, values, and hard boundaries
2. `MEMORY.md`: what the bot should remember — durable user preferences, relationship context, facts, gotchas, and open threads

The instruction order is:

```text
core rules -> SOUL.md -> MEMORY.md -> Agent Skills -> conversation history -> user message
```

Start from the templates:

```bash
cp SOUL.md.example SOUL.md
cp MEMORY.md.example MEMORY.md
```

Keep `SOUL.md` short. A good soul file is usually 150–400 words; 800+ words should be treated as a warning sign. Put task procedures in Agent Skills, not in SOUL.md.

`MEMORY.md` should be factual, compact, and safe to load every turn. Do not store API keys, tokens, passwords, cookies, private URLs, or sensitive personal data in it.

Reload context files at runtime:

```text
/soul reload
/memory reload
```

Docker users can edit host files and mount them into the container. If you want live host edits without rebuilding the image, uncomment or add these Compose volumes:

```yaml
- ./SOUL.md:/app/SOUL.md:ro
- ./MEMORY.md:/app/MEMORY.md:ro
```

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
