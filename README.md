# telegramagent 🤖

Telegram AI bot powered by the Telegram Bot API, Pydantic AI, and an OpenAI-compatible Chat Completions endpoint.

It can chat in private messages, behave politely in groups, read replied messages, enrich URLs with extracted content,
summarize links, understand Telegram images, generate images, publish long replies to Telegraph, and expose optional
runtime tools such as kabigon, Yahoo Finance MCP, and container-local file tools.

## ✨ Highlights

- **Telegram-native behavior**: private chat replies, group mention handling, reply-to-bot handling, and bot-loop guards.
- **Reply context**: when mentioned in a group reply, the bot includes the replied message sender, type, date, text/caption,
  and URL context in the LLM prompt.
- **URL enrichment**: HTTP(S), YouTube, X/Twitter, and general webpages are fetched or loaded through kabigon when possible.
- **Image input/output**: Telegram photos can be sent to a vision-capable model; `/image` can call an image-generation
  endpoint when enabled.
- **Long replies**: replies over Telegram's practical limit are published to Telegraph and replaced with a link.
- **Durable context**: `SOUL.md`, `MEMORY.md`, and per-chat session logs survive restarts.
- **Agent Skills**: local `.agents/skills/*/SKILL.md` files are loaded as model instructions.
- **Docker-ready**: Compose includes mounted runtime state, Playwright browser assets, and optional container tools.

## 🧱 Architecture

```text
Telegram updates
  -> telegramagent.telegram
  -> command / image / reply-context / proactive URL routing
  -> telegramagent.llm via Pydantic AI
  -> OpenAI-compatible API
  -> Telegram response
```

Important modules:

| Area | File |
| --- | --- |
| CLI and app wiring | `src/telegramagent/cli.py` |
| Telegram Bot API client and handlers | `src/telegramagent/telegram.py` |
| LLM agent wiring | `src/telegramagent/llm.py` |
| Proactive URL and YouTube handling | `src/telegramagent/actions.py` |
| Configuration | `src/telegramagent/settings.py` |
| Telegraph publishing | `src/telegramagent/telegraph_pages.py` |
| Tests | `tests/` |

## 🚀 Quick Start

Install dependencies:

```bash
uv sync
```

Create a local `.env`:

```bash
cp .env.example .env
```

Set at least:

```env
BOT_TOKEN=your Telegram Bot token
OPENAI_API_KEY=your API key
OPENAI_MODEL=gpt-5.4-mini
```

Run locally:

```bash
uv run telegramagent
```

Run with Docker Compose:

```bash
docker compose up -d --build
docker compose logs -f telegramagent
```

Stop Docker Compose:

```bash
docker compose down
```

## ⚙️ Configuration

All runtime settings are environment variables. Start from `.env.example`; the most common settings are below.

### Telegram

| Variable | Default | Purpose |
| --- | --- | --- |
| `BOT_TOKEN` | empty | Telegram Bot API token from BotFather. |
| `BOT_WHITELIST` | empty | Comma-separated chat IDs or user IDs allowed to use the bot. Empty allows everyone. |
| `BOT_MAX_CONSECUTIVE_REPLIES_TO_BOTS` | `1` | Safety limit for bot-to-bot reply chains. Use `0` to never reply to bots. |
| `BOT_GROUP_PASSIVE_CONTEXT_ENABLED` | `true` | Store unaddressed group messages as passive context without replying. |

### Model

| Variable | Default | Purpose |
| --- | --- | --- |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible API base URL. |
| `OPENAI_API_KEY` | empty | API key for the configured provider. |
| `OPENAI_MODEL` | `gpt-5.4-mini` | Chat model used for replies. |

### Context

| Variable | Default | Purpose |
| --- | --- | --- |
| `BOT_SOUL_PATH` | `SOUL.md` | Persona and voice instructions. |
| `BOT_MEMORY_PATH` | `MEMORY.md` | Durable user/project memory loaded into instructions. |
| `BOT_SESSION_LOG_DIR` | `.telegramagent/sessions` | Per-chat JSONL history used after restarts. |
| `BOT_SKILLS_DIR` | `.agents/skills` | Directory for Agent Skills. |
| `BOT_ENABLED_SKILLS` | empty | Comma-separated skill names. Empty loads every skill. |
| `BOT_SKILL_ADMINS` | empty | Users/chats allowed to manage skills/context files. Empty reuses `BOT_WHITELIST`. |

### URL Handling

| Variable | Default | Purpose |
| --- | --- | --- |
| `BOT_PROACTIVE_ENABLED` | `true` | Automatically handle safe URL actions and short follow-ups like `go`. |
| `BOT_PROACTIVE_URL_TIMEOUT_SECONDS` | `15` | Built-in URL fetch timeout. |
| `BOT_KABIGON_TIMEOUT_SECONDS` | `180` | kabigon fallback timeout. |
| `BOT_PROACTIVE_MAX_EXTRACTED_CHARS` | `12000` | Max content sent into URL-summary prompts. |
| `BOT_PROACTIVE_PENDING_TTL_SECONDS` | `900` | How long follow-up actions can reuse a pending URL. |
| `BOT_PROACTIVE_ALLOWED_SCHEMES` | `http,https` | URL schemes accepted by the proactive router. |

### Images

| Variable | Default | Purpose |
| --- | --- | --- |
| `BOT_IMAGE_INPUT_ENABLED` | `true` | Allow Telegram photos/image documents as model input. |
| `BOT_IMAGE_MAX_BYTES` | `8000000` | Reject larger image downloads. |
| `BOT_IMAGE_GENERATION_ENABLED` | `false` | Enable `/image <prompt>`. |
| `BOT_IMAGE_GENERATION_MODEL` | `gpt-image-1` | Image generation model. |
| `BOT_IMAGE_GENERATION_SIZE` | `1024x1024` | Image generation size. |

### Optional Integrations

| Variable | Default | Purpose |
| --- | --- | --- |
| `BOT_YFINANCE_MCP_ENABLED` | `true` | Register Yahoo Finance MCP tools through `yfmcp`. |
| `BOT_GURUME_MCP_ENABLED` | `false` | Register Gurume MCP tools through `gurume mcp` for Japanese restaurant search. |
| `BOT_EVENTS_ENABLED` | `false` | Enable file-backed immediate events. |
| `BOT_CONTAINER_TOOLS_ENABLED` | `true` in Compose | Register Docker-only local tools when running inside a container. |
| `LOGFIRE_ENABLED` | `true` | Configure Logfire when `LOGFIRE_TOKEN` is set. |
| `LOGFIRE_INCLUDE_CONTENT` | `false` | Include prompts/model content in traces only when explicitly enabled. |

## 💬 Telegram Behavior

### Private Chats

In private chats, the bot replies to normal text, commands, images, and supported proactive URL actions.

### Groups and Supergroups

To avoid interrupting group conversations, the bot replies only when:

1. The message mentions the bot, for example `@your_bot 你怎麼看？`
2. The message directly replies to a bot message

When `BOT_GROUP_PASSIVE_CONTEXT_ENABLED=true`, unaddressed group messages are stored as passive context without calling
the LLM. The next addressed message can then use recent group context.

### Reply Context 🧵

When a group message mentions the bot while replying to another message, the prompt includes:

- replied message sender
- message type (`text`, `photo`, `video`, `document`, `sticker`, `voice`, and so on)
- message date when available
- text or caption when available
- readable placeholder for non-text messages
- URLs found in the replied message and current message
- extracted URL context when URL enrichment succeeds

If the current message is only the bot mention, the bot is instructed to respond directly to the replied content instead
of asking what to do.

## 🔗 URL Handling

When proactive mode is enabled, supported links are handled directly instead of asking the user what to do.

| URL type | Behavior |
| --- | --- |
| YouTube | Fetch available transcripts/subtitles, then summarize. |
| X/Twitter status URLs | Try source-aware kabigon/browser extraction; detect and reject X browser-blocker pages. |
| General HTTP(S) pages | Use bounded built-in text/HTML fetch first, then kabigon fallback. |
| Follow-ups | `go`, `開始`, `繼續`, `抓抓看`, and similar triggers reuse the most recent pending URL. |

Safety rules:

- Only public `http` and `https` URLs are accepted.
- Localhost, private networks, link-local addresses, and cloud metadata IPs are blocked.
- Built-in fetch has timeout and max-size limits.
- Large extracted content is truncated before it is sent to the model.
- Fetch failures are reported honestly; the bot should not pretend it read content it did not read.

### X/Twitter Notes

Telegram link previews do not expose the preview card title/body through Bot API message fields. For X/Twitter links,
the bot must fetch the target URL itself. Some X pages return a browser blocker page such as "JavaScript is not
available"; these are treated as extraction failures and trigger kabigon/browser fallback.

The Docker image installs Playwright Chromium and its Debian runtime dependencies so kabigon's browser-based loaders can
run inside the container.

## 🧠 Context Files

The bot can load two always-on context files before Agent Skills:

1. `SOUL.md`: identity, voice, values, and hard boundaries
2. `MEMORY.md`: durable user preferences, project context, gotchas, and open threads

Instruction order:

```text
core rules -> SOUL.md -> MEMORY.md -> Agent Skills -> conversation history -> user message
```

Start from templates:

```bash
cp SOUL.md.example SOUL.md
cp MEMORY.md.example MEMORY.md
```

Reload at runtime:

```text
/soul reload
/memory reload
```

Keep these files safe. Do not store API keys, bot tokens, cookies, private URLs, passwords, or sensitive personal data.

## 🧩 Agent Skills

Skills are loaded from:

```text
.agents/skills/<skill-name>/SKILL.md
```

Minimal skill:

```md
---
name: chat-style
description: Telegram reply style. Use when replying to Telegram messages.
---

# Chat Style

- Use Traditional Chinese.
- Keep replies short.
```

Load only selected skills:

```env
BOT_ENABLED_SKILLS=chat-style,other-skill
```

Install skills from Telegram:

```text
/skills add vercel-labs/agent-skills --skill commit
/skills list
```

Natural-language install requests are also supported:

```text
安裝 narumiruna/skills 的 skills 所有
```

This becomes a non-interactive `npx --yes skills@1.5.7 add ... --agent universal --yes --copy` command. Compose mounts
`./.agents:/app/.agents`, so installed skills persist across container rebuilds.

Skills are instructions only. They do not make scripts/tools executable unless runtime code also wires a capability,
Pydantic AI tool, or MCP toolset.

## 🛠 Commands

| Command | Description |
| --- | --- |
| `/start` | Show an introduction. |
| `/help` | Show help. |
| `/id` | Show current chat/user ID for allowlist setup. |
| `/reset` | Clear conversation memory for the current chat. |
| `/ask <question>` | Ask the AI assistant directly. |
| `/image <prompt>` | Generate an image when image output is enabled. |
| `/skills add <package>` | Install Agent Skills with `npx`. |
| `/skills list` | List installed Agent Skills. |
| `/soul show\|reload\|path` | Inspect or reload `SOUL.md`. |
| `/memory show\|reload\|path` | Inspect or reload `MEMORY.md`. |
| `/events list\|show <name>\|cancel <name>\|reload` | Manage file-backed events. |
| `/tasks list\|show <id>\|cancel <id>` | Inspect or cancel proactive runtime tasks. |

## 🖼 Image Input and Output

Image input:

- Users can send Telegram photos or image documents.
- Captions are preserved as the user prompt.
- The configured chat model/provider must support vision.
- Oversized images are rejected before model submission.

Image output:

- Disabled by default.
- Enable `BOT_IMAGE_GENERATION_ENABLED=true`.
- Use `/image <prompt>`.
- Requires an OpenAI-compatible `/images/generations` endpoint.

## 📣 Telegraph Long Replies

Telegram messages over the long-message threshold are published to Telegraph, and the Telegram reply becomes the
Telegraph URL. The default threshold is **1000 characters**, so messages with 1001+ characters are published to
Telegraph. This keeps long model replies readable while avoiding Telegram chunk spam.

The publisher sanitizes content to Telegraph's supported HTML subset before creating the page.

## 📁 File-Backed Immediate Events

When `BOT_EVENTS_ENABLED=true`, the bot scans:

```text
BOT_EVENTS_DIR/inbox/*.json
```

Example `.events/inbox/summarize-video.json`:

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

`reply_mode` values:

- `send`: send the result as a new message.
- `edit-status`: send `處理中…`, then edit that bot-owned status message into the final result.

Event safety:

- Event names must match `^[a-z0-9-]{1,40}$`.
- Event text is length-limited.
- Event text cannot execute management commands.
- Successful events can be archived to `processed/`; invalid or failed events go to `failed/`.

Compose mounts `./.events:/app/.events`, so host scripts can write event files without rebuilding the image.

## 🧰 Docker-Only Container Tools

Docker Compose enables optional local tools for the chat model:

```text
bash, edit, find, grep, ls, read, write
```

These tools are registered only when the runtime detects it is inside a container and
`BOT_CONTAINER_TOOLS_ENABLED=true`.

Important limits:

- Filesystem tools are scoped to `BOT_CONTAINER_TOOLS_ROOT`.
- `bash` runs in that root but can mutate container files or mounted state.
- Tool output is bounded by timeout/read/result limits.
- Tool results may be sent to the model provider.
- Writes to image-layer `/app` are not durable across rebuilds; mounted volumes persist.

Disable them with:

```env
BOT_CONTAINER_TOOLS_ENABLED=false
```

## 📊 Yahoo Finance MCP

When `BOT_YFINANCE_MCP_ENABLED=true`, the bot registers the `yfmcp` MCP toolset for stock, ETF, options, financial
statement, holder, sector, news, and price-chart lookups.

Financial responses are informational only and are not investment advice.

## 🍽️ Gurume MCP

Set `BOT_GURUME_MCP_ENABLED=true` to register Gurume's MCP toolset for Japanese restaurant search through Tabelog.
By default, the bot starts it with `gurume mcp`; override `BOT_GURUME_MCP_COMMAND` and `BOT_GURUME_MCP_ARGS` when using
`uvx` or another launcher.

## 🔭 Observability

Set `LOGFIRE_TOKEN` to enable Logfire at startup. The bot forwards Loguru logs and instruments HTTPX, Pydantic AI, and
MCP calls.

Prompt/model-response content is not sent by default. Enable content capture only when you intentionally want it:

```env
LOGFIRE_INCLUDE_CONTENT=true
```

Stdlib logging is routed through Loguru. Noisy HTTP/OpenAI debug loggers are suppressed by default, and common token-like
values are redacted before forwarding.

## 🧪 Development

Common commands:

```bash
uv sync
uv run telegramagent
docker compose up -d --build
docker compose logs -f telegramagent
```

Quality gate:

```bash
uv run ruff format --check
uv run ruff check .
uv run ty check .
uv run pytest -q tests
```

The `justfile` also provides aggregate recipes:

```bash
just all
just test
just lint
just type
```

Note: `just lint` applies Ruff fixes.

## 🔒 Security Notes

- Never commit `.env`, bot tokens, API keys, cookies, or private URLs.
- Keep `SOUL.md` and `MEMORY.md` free of secrets.
- URL fetching blocks private/local network targets.
- Risky side-effect actions should require explicit confirmation.
- Container tools can expose file contents to the model provider; keep secrets outside mounted tool roots.

## 📦 Docker Volumes

Compose mounts these paths by default:

```yaml
- ./.agents:/app/.agents
- ./.events:/app/.events
- ./.telegramagent:/app/.telegramagent
- ./SOUL.md:/app/SOUL.md:ro
- ./MEMORY.md:/app/MEMORY.md:ro
```

This keeps skills, events, and session logs durable while allowing `SOUL.md` and `MEMORY.md` edits without rebuilding the
image.
