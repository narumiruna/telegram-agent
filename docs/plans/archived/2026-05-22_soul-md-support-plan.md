## Goal

Add `SOUL.md` and `MEMORY.md` support so the Telegram bot can load a local soul file for identity/persona and a local memory file for durable, evolving context, then inject both into the Pydantic AI agent instructions in a safe, concise, reloadable way.

## Context

External SOUL.md references converge on a few design principles:

- A soul file defines who the agent is, not what task it is currently doing.
- Strong soul files are specific enough that readers can predict the agent's takes and replies.
- SOUL.md should stay lean; bloated always-on persona files slow the model down and dilute instruction-following.
- Operational procedures belong in Agent Skills, AGENTS.md, README, or task-specific files, not SOUL.md.

`MEMORY.md` complements SOUL.md:

- SOUL.md should be stable identity, worldview, voice, values, and hard boundaries.
- MEMORY.md should be evolving facts, preferences, recurring context, and lessons learned.
- MEMORY.md can change over time; SOUL.md should change rarely and intentionally.

## Architecture

- `SOUL.md` is a first-class bot identity/persona file.
- `MEMORY.md` is a first-class durable context file.
- Agent Skills remain task/workflow instructions.
- Recommended loading order in final Pydantic AI instructions:
  1. Core immutable system rules
  2. `SOUL.md` persona
  3. `MEMORY.md` durable context
  4. Agent Skills
  5. Runtime conversation prompt/history
- Telegram admin commands should allow inspecting metadata and reloading soul/memory without restarting the container.
- Docker users should be able to bind-mount `SOUL.md` and `MEMORY.md` into the running container and reload them at runtime.

## Non-Goals

- Do not build a full soul interview/builder workflow in this phase.
- Do not build autonomous memory writing by default; start with read/reload/show commands and explicit admin-controlled updates only if implemented.
- Do not fine-tune models or add RAG over large personal data folders.
- Do not execute scripts or tools declared inside SOUL.md or MEMORY.md.
- Do not turn SOUL.md or MEMORY.md into task procedures; keep task procedures in skills or other operational files.

## Plan

- [x] Add settings for `BOT_SOUL_PATH=SOUL.md`, `BOT_SOUL_REQUIRED=false`, `BOT_SOUL_MAX_CHARS=8000`, `BOT_MEMORY_PATH=MEMORY.md`, `BOT_MEMORY_REQUIRED=false`, and `BOT_MEMORY_MAX_CHARS=12000`; verify defaults and env parsing with settings tests.
- [x] Add `src/telegramagent/context_files.py` or separate `soul.py` / `memory.py` loaders with metadata for path, existence, truncation, and content; verify missing file, empty file, populated file, and over-limit file behavior with tests.
- [x] Inject loaded context into `ChatAgent` instructions in the order core rules → soul → memory → skills; verify order with a fake Pydantic agent factory.
- [x] Add `ChatAgent.reload_context(soul, memory)` or equivalent rebuild path; verify reloading updates Pydantic AI instructions without reconstructing the whole Telegram bot.
- [x] Add a small `SoulManagementTool` for `/soul show`, `/soul reload`, and `/soul path`; verify admin and non-admin behavior with Telegram command tests.
- [x] Add a small `MemoryManagementTool` for `/memory show`, `/memory reload`, `/memory path`, and optionally `/memory append <text>`; verify admin and non-admin behavior with Telegram command tests.
- [x] Wire both tools in `cli.py` using `BOT_SKILL_ADMINS` or a shared `BOT_CONTEXT_ADMINS` setting if added; verify bot construction tests or CLI-level smoke tests.
- [x] Add `SOUL.md.example` with a lean, production-safe persona template; verify it includes identity, worldview, voice, preferences, values, boundaries, and examples.
- [x] Add `MEMORY.md.example` with a durable context template; verify it includes stable user preferences, recurring facts, relationship context, and gotchas while warning against secrets.
- [x] Update `.env.example` with soul and memory settings; verify all new env vars are documented.
- [x] Update README in English with SOUL.md and MEMORY.md purposes, precedence, Docker bind-mount guidance, size guidance, and `/soul` / `/memory` command examples; verify README references both example files.
- [x] Update `docker-compose.yml` to make `SOUL.md` and `MEMORY.md` available in-container when present, or document mount lines users can uncomment; verify `docker compose config --quiet`.
- [x] Run quality gates: `uv run ruff format --check`, `uv run ruff check`, `uv run ty check`, and `uv run pytest -q`.

## Recommended SOUL.md Shape

Keep this file short. Target 150–400 words for normal bots; treat 800+ words as a warning sign unless there is a strong reason.

```markdown
# SOUL.md

## Who You Are
You are <name>, a Telegram-native AI companion.
Your job is <one-sentence mission>.

## Worldview
- <specific belief or recurring stance>
- <specific belief or recurring stance>

## Core Personality
- Direct but warm.
- Curious, playful, and emotionally aware.
- Prefer concise replies unless the user asks for detail.

## Voice
- Default language: Traditional Chinese.
- Write like a close, knowledgeable friend.
- Avoid corporate tone.
- Use humor lightly; do not force it.

## Preferences
- Likes: <topics, styles, aesthetics>
- Dislikes: <behaviors, tones, topics to avoid>
- Favorite topics: <topics>

## Values
- Be honest over pleasing.
- Admit uncertainty.
- Protect privacy.
- Help the user think clearly.

## Boundaries
- Never reveal secrets, tokens, or private config.
- Never claim you performed actions you did not perform.
- Do not continue bot-to-bot loops when the topic is only closing acknowledgements.
- Ask before irreversible or external-impact actions.

## Interaction Patterns
- If the user is casual, reply casually.
- If the user is debugging, be precise and action-oriented.
- If the user seems emotional, respond with empathy before advice.

## Example Exchanges
User: 今天好累
Bot: 辛苦了。要不要先把今天最煩的一件事丟給我，我幫你拆小一點？

User: 安裝 narumiruna/skills 的 skills 所有
Bot: 我會先檢查已安裝 skills，沒有的話再幫你安裝。
```

## Recommended MEMORY.md Shape

MEMORY.md should be factual, compact, and safe to load every turn. It should capture durable context that changes over time, not the bot's core identity.

```markdown
# MEMORY.md

## User Preferences
- Prefers Traditional Chinese in casual conversation.
- Likes concise answers first, details only when needed.

## Relationship Context
- The user is building and operating this Telegram bot.
- The user often runs the bot in Docker Compose.

## Durable Facts
- Skills are installed under `.agents/skills` for this bot.
- The bot uses Pydantic AI and OpenAI-compatible models.

## Gotchas
- Docker skill installation needs `git`, `nodejs`, `npm`, and writable `.agents`.
- Do not store API keys, tokens, passwords, cookies, or private URLs here.

## Open Threads
- Consider adding automatic but admin-reviewed memory updates later.
```

## Placement Rules

Put these in `SOUL.md`:

- identity, mission, worldview, values
- durable voice and style
- hard boundaries that should apply every turn
- a few high-signal example exchanges

Put these in `MEMORY.md`:

- durable user preferences
- recurring project facts
- relationship context between user and bot
- gotchas learned from operation
- explicit open threads that are safe to remember

Do not put these in either file:

- secrets, tokens, cookies, or credentials
- long CLI usage docs
- full repo architecture
- installation logs
- detailed tool workflows
- sensitive personal data

Use these files instead:

- `SOUL.md`: who the bot is and how it behaves
- `MEMORY.md`: durable context the bot should remember across sessions
- `.agents/skills/`: task-specific capabilities and workflows
- `README.md`: user/deployment documentation
- `AGENTS.md`: development and operational rules for coding agents

## Risks

- SOUL.md or MEMORY.md could contain prompt-injection-like instructions. Mitigation: wrap them as persona/context and keep immutable core rules before them.
- Large context files can bloat prompts and degrade behavior. Mitigation: enforce max character limits, log truncation, and document size guidance.
- MEMORY.md may accidentally accumulate sensitive data. Mitigation: document a no-secrets rule and avoid autonomous memory writes in the first implementation.
- Docker users may edit host files but forget to mount them. Mitigation: document and/or include Compose bind mounts for `./SOUL.md:/app/SOUL.md:ro` and `./MEMORY.md:/app/MEMORY.md:ro`.
- Persona/memory instructions may conflict with Agent Skills. Mitigation: document precedence and test instruction ordering.

## Completion Checklist

- [x] Bot persona from `SOUL.md` is present in Pydantic AI instructions, verified by unit tests using a fake agent factory.
- [x] Durable context from `MEMORY.md` is present in Pydantic AI instructions, verified by unit tests using a fake agent factory.
- [x] Instruction order is core rules → soul → memory → skills, verified by tests.
- [x] Admins can reload soul and memory at runtime with `/soul reload` and `/memory reload`, verified by Telegram command tests.
- [x] Missing and oversized SOUL/MEMORY behavior is predictable, verified by unit tests and documented in README.
- [x] `SOUL.md.example` exists and documents identity, worldview, voice, preferences, values, boundaries, interaction patterns, and examples.
- [x] `MEMORY.md.example` exists and documents user preferences, relationship context, durable facts, gotchas, open threads, and no-secrets guidance.
- [x] `.env.example` and README document all soul/memory settings and Docker usage.
- [x] All quality gates pass with `ruff`, `ty`, and `pytest`.


## Completion Evidence

- Implemented context loading in `src/telegramagent/context_files.py`.
- Wired SOUL/MEMORY settings in `src/telegramagent/settings.py` and CLI reload tools in `src/telegramagent/cli.py`.
- Injected context into Pydantic AI instructions in `src/telegramagent/llm.py`.
- Added runtime `/soul` and `/memory` management through `ContextManagementTool`.
- Added `SOUL.md.example`, `MEMORY.md.example`, `.env.example`, README, and Docker Compose documentation.
- Verified with `uv run ruff format && uv run ruff check --fix && uv run ty check && uv run pytest -q && docker compose config --quiet && uv run telegramagent --help`.
