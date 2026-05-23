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
# In groups, keep unaddressed messages as passive context without replying or calling the LLM.
BOT_GROUP_PASSIVE_CONTEXT_ENABLED=true
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
BOT_KABIGON_TIMEOUT_SECONDS=180
BOT_PROACTIVE_MAX_EXTRACTED_CHARS=12000
BOT_PROACTIVE_PENDING_TTL_SECONDS=900
BOT_PROACTIVE_ALLOWED_SCHEMES=http,https

# File-backed immediate events. External scripts can write JSON files to BOT_EVENTS_DIR/inbox.
BOT_EVENTS_ENABLED=false
BOT_EVENTS_DIR=.events
BOT_EVENTS_SCAN_SECONDS=2
BOT_EVENTS_MAX_QUEUED_PER_CHAT=5
BOT_EVENTS_MAX_TEXT_CHARS=4000
BOT_EVENTS_ARCHIVE_PROCESSED=true

# Durable proactive runtime state and task queue limits.
BOT_SESSION_LOG_DIR=.telegramagent/sessions
BOT_TASKS_MAX_CONCURRENT_PER_CHAT=1

# Image input/output.
# Image input sends Telegram photos/image documents to the chat model; use a vision-capable model/provider.
BOT_IMAGE_INPUT_ENABLED=true
BOT_IMAGE_MAX_BYTES=8000000
# Image output uses the OpenAI-compatible /images/generations endpoint and is off by default.
BOT_IMAGE_GENERATION_ENABLED=false
BOT_IMAGE_GENERATION_MODEL=gpt-image-1
BOT_IMAGE_GENERATION_SIZE=1024x1024
BOT_IMAGE_GENERATION_TIMEOUT_SECONDS=120

# Yahoo Finance MCP tools. Enabled by default through the installed yfmcp command.
BOT_YFINANCE_MCP_ENABLED=true
BOT_YFINANCE_MCP_COMMAND=yfmcp
BOT_YFINANCE_MCP_ARGS=
BOT_YFINANCE_MCP_INIT_TIMEOUT_SECONDS=10
BOT_YFINANCE_MCP_READ_TIMEOUT_SECONDS=120

OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=your API key
OPENAI_MODEL=gpt-5.4-mini

# Optional Logfire observability. Set LOGFIRE_TOKEN to send traces and logs to Logfire.
LOGFIRE_ENABLED=true
LOGFIRE_TOKEN=
LOGFIRE_ENVIRONMENT=dev
LOGFIRE_SERVICE_NAME=telegramagent
# Keep false unless you intentionally want prompts and model responses in Logfire.
LOGFIRE_INCLUDE_CONTENT=false
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

## Observability

Logfire support is opt-in by token: set `LOGFIRE_TOKEN` to enable Logfire configuration at startup. The bot forwards Loguru logs and instruments HTTPX, Pydantic AI, and MCP calls. Prompt and model-response content is not sent by default; set `LOGFIRE_INCLUDE_CONTENT=true` only if you intentionally want that content in traces.

## Commands

- `/start`: show an introduction
- `/help`: show help
- `/id`: show the current chat/user ID, useful for allowlist configuration
- `/reset`: clear conversation memory for the current chat
- `/ask <question>`: ask the AI assistant
- `/image <prompt>`: generate an image through the configured OpenAI-compatible `/images/generations` endpoint
- `/skills add <package>`: install Agent Skills in the local project through `npx`
- `/skills list`: list installed Agent Skills
- `/soul show|reload|path`: inspect or reload `SOUL.md`
- `/memory show|reload|path`: inspect or reload `MEMORY.md`
- `/events list|show <name>|cancel <name>|reload`: manage pending file-backed events
- `/tasks list|show <id>|cancel <id>`: inspect or cancel proactive runtime tasks

You can also send plain text directly to the bot.

## Proactive URL Handling

When proactive mode is enabled, the bot does not only suggest work for supported links. It executes a safe default action:

- YouTube links: fetch available subtitles/transcripts and summarize them.
- HTTP(S) links: try the bounded built-in text/HTML fetcher first, then fall back to `kabigon.api.load_url` for supported public URLs when built-in extraction fails.
- Short follow-ups such as `go`, `開始`, `繼續`, or `你就自動做事`: reuse the most recent pending URL/action in that chat for `BOT_PROACTIVE_PENDING_TTL_SECONDS` seconds.

Safety limits:

- Only `http` and `https` URLs are supported.
- Localhost, private networks, link-local addresses, and cloud metadata IPs are blocked.
- Redirects are not followed automatically.
- Large built-in responses are rejected before fallback; kabigon results are truncated to `BOT_PROACTIVE_MAX_EXTRACTED_CHARS`.
- If built-in fetch and kabigon both fail, the bot reports that honestly instead of pretending it read the page.
- If YouTube subtitles are disabled, unavailable, or blocked by YouTube, the bot tries kabigon fallback and says so if both paths fail.

Disable this behavior with `BOT_PROACTIVE_ENABLED=false`.

## File-Backed Immediate Events

When `BOT_EVENTS_ENABLED=true`, the bot scans `BOT_EVENTS_DIR/inbox/*.json` for immediate events. External scripts can create one JSON file to make the bot send a synthetic message into a chat. This is not a scheduler: `at`, `schedule`, and `timezone` fields are rejected.

Example event file at `.events/inbox/summarize-video.json`:

```json
{
  "type": "immediate",
  "name": "summarize-video",
  "chat_id": 123456,
  "text": "請整理 https://youtu.be/iG-hzh9roNw",
  "reply_mode": "edit-status",
  "created_by": "external-script"
}
```

The bot dispatches it as:

```text
[EVENT:summarize-video] 請整理 https://youtu.be/iG-hzh9roNw
```

`reply_mode` is optional:

- `send` (default): send the result as a new message.
- `edit-status`: first send `處理中…`, then edit that bot-owned status message into the final result. If editing fails, the bot falls back to sending a new message. Telegram only allows editing messages sent by the bot itself.

Safety limits:

- Event names must match `^[a-z0-9-]{1,40}$`.
- Event text is limited by `BOT_EVENTS_MAX_TEXT_CHARS`.
- Event text cannot execute management commands such as `/skills`, `/memory`, `/soul`, or `/events`.
- A scan processes at most `BOT_EVENTS_MAX_QUEUED_PER_CHAT` events per chat; excess files stay in `inbox/` for a later scan.
- Successful events are moved to `processed/` when `BOT_EVENTS_ARCHIVE_PROCESSED=true`; invalid or failed events are moved to `failed/`.

Docker Compose mounts `./.events:/app/.events` by default, so host scripts can write to `.events/inbox/`.

## Proactive Runtime State

The bot stores durable per-chat JSONL session logs under `BOT_SESSION_LOG_DIR` (default `.telegramagent/sessions`). These logs let it reconstruct recent context after a restart, including previously shared URLs, synthetic event messages, and assistant replies. Runtime state directories are git-ignored.

The proactive runtime has a small task queue:

- `BOT_TASKS_MAX_CONCURRENT_PER_CHAT` limits concurrent proactive work per chat.
- Long-running proactive work can send `處理中…` and then edit that bot-owned status message into the final result.
- `/tasks list`, `/tasks show <id>`, and `/tasks cancel <id>` expose task state.

Runtime capabilities are explicit. Built-in web fetch, YouTube transcript extraction, Telegram image input, optional image generation, file-backed events, `kabigon.api.load_url` URL extraction, and Yahoo Finance MCP tools are available by default. The chat agent registers a Pydantic AI tool named `kabigon_load_url`, so the model can call kabigon directly for supported public HTTP(S) URLs. It also registers the `yfmcp` MCP toolset when `BOT_YFINANCE_MCP_ENABLED=true`, allowing stock, ETF, options, financial statement, holder, sector, market-news, and price-chart lookups through Yahoo Finance data. When yfmcp returns chart images, the bot sends those image artifacts to Telegram after the text reply. Financial responses are informational only and should not be treated as investment advice. Some kabigon loaders may need extra runtime assets such as Playwright browsers, depending on the URL type. Agent Skills alone still do not make a tool executable; a capability, Pydantic AI tool, or MCP toolset must be wired in runtime code.

Docker Compose mounts `./.telegramagent:/app/.telegramagent` by default so session logs survive container restarts.

## Image Input and Output

When `BOT_IMAGE_INPUT_ENABLED=true`, users can send Telegram photos or image documents with an optional caption. The bot downloads the image through Telegram `getFile` and sends it to the chat model as multimodal input. The configured `OPENAI_MODEL` and provider must support vision; otherwise the bot will honestly report the provider/model limitation. Images larger than `BOT_IMAGE_MAX_BYTES` are rejected before model submission.

Image output is explicit and off by default. Set `BOT_IMAGE_GENERATION_ENABLED=true` and use `/image <prompt>` to call the configured OpenAI-compatible `/images/generations` endpoint with `BOT_IMAGE_GENERATION_MODEL` and `BOT_IMAGE_GENERATION_SIZE`. Providers that do not implement that endpoint will return an error instead of pretending an image was generated.

## Group Reply Rules

In private chats, the bot replies to normal text messages. In groups and supergroups, to avoid interrupting the conversation, it only replies in either of these cases:

1. The message mentions the bot account, for example `@your_bot hello`
2. The message directly replies to a bot message

When `BOT_GROUP_PASSIVE_CONTEXT_ENABLED=true`, unaddressed group messages are still recorded as passive user-message history for that chat. This does not call the LLM and does not send a reply by itself; it only lets the bot see recent group context the next time it is addressed. Disable it if the group should not be persisted in `.telegramagent/sessions`.

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
npx --yes skills@1.5.7 add narumiruna/skills --skill '*' --agent universal --yes --copy
```

`/skills add` runs `npx --yes skills@1.5.7 add ... --yes --copy` in the project directory where the bot is running, then reloads skills after installation. By default it adds `--agent universal`, so it writes only to `.agents/skills`, which is the directory the bot reads, instead of installing into every agent directory. Before reinstalling, it detects already installed skills; use `--force` to force reinstall.

The Docker image includes `git` / `nodejs` / `npm` / `npx`. Compose mounts local `./.agents` into the container so installed skills persist, and runs as root inside the container to avoid bind-mount permission failures during skill installation.

## Bot-to-Bot Topic Ending

When the incoming message is from another Telegram bot, the program first asks a topic-ending judge agent whether it should silently stop the conversation:

- If the conversation is only a closing acknowledgement or repeated agreement such as `好` / `好的` / `了解`, the bot stops replying to avoid infinite bot-to-bot loops.
- If the other bot asks a clear new question or provides new information, the judge can allow the bot to continue replying.
- `BOT_MAX_CONSECUTIVE_REPLIES_TO_BOTS` is a safety limit. Even if the judge does not stop the topic, replies stop after this limit is exceeded.
