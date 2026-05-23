## Goal

Increase the Telegram bot's proactivity so that when a user gives an actionable input such as a URL and says "自動做事", the bot can execute a safe default action instead of only offering options or falsely promising to work. Success means the bot can detect supported tasks, run bounded tools, return results in the same conversation, and remain safe in groups and bot-to-bot conversations.

## Context

The current bot sends every non-management message to `ChatAgent.reply(...)`. The LLM has instructions and history, but it did not have runtime tools for fetching URLs, extracting YouTube/transcript content, or resolving follow-up messages such as `go`. This caused replies such as "我先幫你整理" without an actual action being executed.

## Research Findings

- Pydantic AI function tools are the right fit when the model needs to take actions or retrieve extra information instead of only answering from prompt context. Tools can be registered on `Agent`, validated by Pydantic, and observed in runs. Source: <https://pydantic.dev/docs/ai/tools-toolsets/tools/>
- Pydantic AI supports run/tool limits such as request limits and tool call limits, plus tool timeouts. These should be used to prevent runaway tool loops and long-running work. Source: <https://pydantic.dev/docs/ai/core-concepts/agent/>
- Pydantic AI has human-in-the-loop tool approval (`requires_approval`) and deferred tools. This is relevant for future actions with external side effects, but the first phase avoids side-effectful tools. Source: <https://pydantic.dev/docs/ai/api/pydantic-ai/agent/>
- URL-fetching tools create SSRF risk. OWASP recommends allowlists when possible, scheme validation, avoiding raw responses, disabling redirects where appropriate, and blocking access to localhost, private networks, and cloud metadata services. Source: <https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html>
- Additional SSRF guidance emphasizes least privilege, network segmentation, sanitized/validated input, parsing/sanitizing remote responses, and disabling redirects to avoid allowlist bypasses. Source: <https://www.ionix.io/guides/owasp-top-10/server-side-request-forgery-ssrf/>
- `youtube-transcript-api` can retrieve manual or auto-generated transcripts, list available languages, translate transcripts, and format text output. It does not require a headless browser, but it uses undocumented YouTube behavior and can fail due to IP blocking or unavailable subtitles. Source: <https://pypi.org/project/youtube-transcript-api/>
- YouTube auto-generated transcripts often lack punctuation; summarization should let the LLM repair punctuation and structure before producing the final summary. Source: <https://stackoverflow.com/questions/76856230/how-to-extract-youtube-video-transcripts-using-youtube-api-on-python>

## Architecture

Implemented a small proactive action layer before generic chat:

- `TelegramBot.build_reply(...)` keeps management commands first.
- `ProactiveActionTool` detects supported actionable inputs, including YouTube URLs, generic URLs, and follow-up triggers such as `go`, `開始`, `繼續`, and `你就自動做事`.
- `PendingActionStore` stores per-chat pending action context, such as the last URL and task, outside the LLM prompt so follow-up messages resolve deterministically.
- Action tools execute bounded work, return structured results, and pass the result to `ChatAgent` for final user-facing formatting.
- Unsupported, risky, or unavailable actions return a concise explanation instead of pretending to execute.

## Non-Goals

- Do not add unbounded autonomous browsing or background agents in this phase.
- Do not bypass existing group mention/reply rules, whitelist checks, or bot-to-bot loop prevention.
- Do not store secrets or private user data in `MEMORY.md` automatically.

## Plan

- [x] Define proactive behavior policy in core chat instructions so the bot must execute supported safe defaults and must not say "我先去做" unless code actually runs; verified by `src/telegramagent/llm.py` instruction text and `uv run ruff check --fix`.
- [x] Add settings for proactive execution, including `BOT_PROACTIVE_ENABLED`, URL timeout, max extracted characters, pending TTL, and allowed URL schemes; verified by `tests/test_settings.py` and `uv run telegramagent --help`.
- [x] Create an `ActionRouter`-equivalent module (`src/telegramagent/actions.py`) that detects actionable text for YouTube URLs, generic HTTP(S) URLs, and direct follow-up requests; verified by `tests/test_actions.py` including non-actionable normal chat.
- [x] Add a per-chat pending context store for recent URLs and proposed actions, with bounded TTL/count, so follow-up messages like `go` or `你就自動做事` reuse the prior URL without asking again; verified by `test_followup_go_uses_pending_youtube_url_without_asking_again` and `test_proactive_tool_runs_before_generic_chat_and_updates_history`.
- [x] Implement a bounded URL fetch/extraction tool using `httpx` for normal web pages, with size limits, timeout, content-type checks, redirect refusal, SSRF host checks, and readable error messages; verified by mocked `httpx` tests for success, timeout, non-HTML, oversized responses, redirect, and localhost blocking.
- [x] Implement a YouTube handling path that extracts transcript content when available and returns an honest fallback when transcript/content is unavailable; verified by mocked transcript tests, invalid YouTube URL test, and real local transcript extraction for `iG-hzh9roNw` returning 2294 characters.
- [x] Wire proactive actions into `TelegramBot.build_reply(...)` after management/builtin commands and before generic chat, so supported actions execute and feed results to the LLM for concise formatting; verified by `tests/test_telegram_bot.py` assertions that the proactive path runs before generic chat.
- [x] Add anti-overreach behavior for actions requiring credentials, payment, external side effects, or unsupported browsing depth; verified by `test_risky_action_requires_confirmation`.
- [x] Update README and `.env.example` with proactive mode behavior, limits, and examples; verified by file review and `uv run ruff check --fix`.
- [x] Run quality gates: `uv run ruff format`, `uv run ruff check --fix`, `uv run ty check`, `uv run pytest -q`, `docker compose config --quiet`, and `uv run telegramagent --help`; all passed.
- [x] Not applicable: live Telegram manual transcript was not available in this environment. Equivalent verification was completed with TelegramBot unit tests plus a real local YouTube transcript fetch for the sample video `iG-hzh9roNw`, proving the runtime action path can get actual content instead of asking for the URL again.

## Risks

- [x] YouTube transcript availability varies by video, language, region, and library behavior; mitigated by honest fallback messages and real sample fetch verification.
- [x] Web fetching can introduce SSRF or private-network access risk; mitigated by http/https-only schemes, public-IP DNS validation, redirect refusal, content-type checks, and size limits.
- [x] More proactive behavior may be noisy in groups; mitigated by keeping existing group mention/reply gates unchanged and adding `BOT_PROACTIVE_ENABLED=false` rollback.

## Rollback / Recovery

- Disable proactive behavior with `BOT_PROACTIVE_ENABLED=false` if actions become noisy or unreliable.
- Revert the proactive action wiring commit to return to pure chat replies while keeping management commands intact.

## Completion Checklist

- [x] Bot no longer falsely promises unsupported work, verified by `src/telegramagent/actions.py`, `src/telegramagent/llm.py`, and tests showing it either executes supported actions or returns unavailable/unsafe explanations.
- [x] Follow-up memory for pending actions is implemented, verified by `test_followup_go_uses_pending_youtube_url_without_asking_again` where the bot receives a YouTube URL, then `go`, and does not ask for the URL again.
- [x] YouTube URL proactive flow is implemented, verified by mocked unit tests and real local transcript extraction for `https://youtu.be/iG-hzh9roNw`.
- [x] Generic URL summary flow is implemented with bounded fetching, verified by mocked network tests in `tests/test_actions.py`.
- [x] Safety limits for timeouts, size, schemes, redirects, public hosts, and risky actions are verified by tests and documented in README.
- [x] Existing bot behavior remains intact, verified by `48 passed`, `uv run ty check`, and `docker compose config --quiet`.
