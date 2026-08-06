# Pi-style Agent Runtime Plan

## Goal

Replace the request/response-only chat path with a Pydantic AI-backed, pi-style runtime that provides per-chat execution state, streaming lifecycle events, mid-run steering, cancellation, cross-chat concurrency, structured v2 sessions, automatic context compaction, bounded transient retries, and observable parallel tool execution.

## Context

Confirmed product decisions:

- Keep Pydantic AI as the model and tool-loop implementation.
- A new message received while the same chat is running is steering input and must be injected at the earliest Pydantic AI checkpoint.
- Telegram streaming edits one throttled status message until it becomes the final answer.
- `/cancel` cancels the active run for the current chat and clears queued steering input.
- Replace the existing session format without backward compatibility; operators must clear old sessions before deployment.
- Compact automatically near the configured context budget, preserving a compaction record.
- Retry only transient provider/MCP failures, at most three attempts with exponential backoff.
- Execute tool calls in parallel by default while honoring Pydantic AI tools marked sequential.

## Architecture

- Add `agent_runtime.py` as the owner of per-chat `AgentSession` state, active run control, steering, cancellation, retry policy, event fan-out, and compaction checkpoints. Telegram depends on this runtime instead of coordinating LLM state itself.
- Keep `llm.py` as the Pydantic AI adapter. It will expose a streamed run contract, translate Pydantic model/tool events into normalized runtime events, bind the active `AgentRun.enqueue()` steering seam, and return exact structured model messages plus artifacts.
- Replace `session.py` with a v2 append-only JSONL store for structured Pydantic messages and compaction records. It will provide exact model history to the runtime and a text projection for proactive URL behavior.
- Add a throttled Telegram progress renderer. The polling loop dispatches updates concurrently; the runtime serializes work per chat while allowing different chats to proceed independently.
- Use Pydantic AI's default parallel tool manager and per-tool `sequential` declaration. Runtime before/after hooks observe normalized tool lifecycle events without reimplementing tool execution.
- Automatic compaction estimates history tokens conservatively, asks a tool-free compactor for a summary, replaces old context with a summary request/response pair, and records the replacement. A failed compaction leaves the original history intact and reports an event.

## Non-Goals

- Crash-resuming an in-flight provider stream or tool side effect.
- Durable operation journals, conversation trees, branches, or multi-process writers.
- Reading or migrating the old `SessionLog` JSONL shape.
- Reimplementing Pydantic AI's provider protocol or tool executor.

## Risks

- Telegram edit rate limits: throttle progress edits and ignore recoverable edit failures without cancelling the run.
- Pydantic AI event/API drift: isolate framework-specific types in `llm.py` and test the runtime through protocols/fakes.
- Context estimation is approximate when providers cannot count ahead: expose the budget and character-to-token ratio as settings and retain actual request usage in session metadata.
- Cancelling a non-idempotent tool cannot undo effects already completed; cancellation stops future work and propagates to in-flight async operations where supported.

## Plan

- [x] Establish a green baseline and create `feat/pi-style-agent-runtime`; verified 170 tests passed and the active branch is `feat/pi-style-agent-runtime`.
- [ ] Replace `src/telegramagent/session.py` with the structured v2 message/compaction store and update focused session tests; verify with `uv run pytest -q tests/test_session.py`, then commit the isolated store change.
- [ ] Add normalized agent events and the Pydantic AI streamed backend adapter in `src/telegramagent/agent_runtime.py` and `src/telegramagent/llm.py`, including parallel tool lifecycle events and exact message capture; verify with focused runtime/LLM tests, then commit.
- [ ] Add per-chat sessions, earliest-checkpoint steering, real cancellation, transient retry/backoff, automatic summary compaction, and runtime hooks; verify with deterministic async tests for each behavior, then commit.
- [ ] Integrate the runtime into Telegram and CLI: concurrent update dispatch, throttled edit-in-place progress, `/cancel`, same-chat serialization, cross-chat parallelism, and structured history projection; verify with focused Telegram/CLI tests, then commit.
- [ ] Add runtime settings to `Settings`, `.env.example`, and README, including the breaking session-format deployment note; verify settings/docs-related tests and commit.
- [ ] Re-index changed source and audit the final dependency direction and diff; run `uv run ruff format --check`, `uv run ruff check .`, `uv run ty check .`, and `uv run pytest -q tests`.
- [ ] Push `feat/pi-style-agent-runtime`, create a pull request against `main`, and verify the remote branch and PR URL.

## Completion Checklist

- [ ] Same-chat messages steer an active run and different chats run concurrently.
- [ ] Telegram uses one throttled editable progress message and finishes it with the final response.
- [ ] `/cancel` stops the active run and discards queued steering messages.
- [ ] Structured v2 sessions restore exact model history and persist compaction records; old files are explicitly unsupported.
- [ ] Transient failures retry no more than three times with exponential backoff; permanent and cancellation failures do not retry.
- [ ] Model, tool, retry, compaction, and lifecycle events are observable without duplicating Pydantic AI's tool loop.
- [ ] All repository quality gates pass.
- [ ] Focused commits are pushed and a pull request is open.
