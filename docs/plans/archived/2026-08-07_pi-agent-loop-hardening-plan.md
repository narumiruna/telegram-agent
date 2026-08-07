# Pi Agent Loop Hardening Plan

## Goal

Adopt the highest-leverage agent-loop patterns from `~/workspace/pi` without replacing Pydantic AI: retry failed model requests or assistant turns without replaying completed tool side effects, enforce context policy at every model-request boundary, and add explicit steering versus follow-up submission semantics while preserving per-chat isolation and exact structured history.

## Context

- `AgentRuntime._run_with_retry()` currently retries the complete `AgentBackend.run_streamed()` call from its original history and prompt. A transient failure after a successful tool may therefore replay that tool.
- Context compaction currently runs once before a Telegram submission. One Pydantic AI run may perform several model/tool turns after that checkpoint.
- A same-chat submission currently always becomes Pydantic AI `priority="asap"` steering. Pydantic AI 1.107.0 also supports `priority="when_idle"`, which can represent pi-style follow-up work without owning a second tool loop.
- Pydantic AI 1.107.0 provides per-run capabilities, `ProcessHistory` before every model request, and model-request error hooks. Implementation must verify these public contracts with fakes rather than depend on private graph internals.
- The completed plan at `docs/plans/archived/2026-08-07_pi-style-agent-runtime-plan.md` established Pydantic AI as the model/tool-loop owner. This plan hardens that architecture rather than reopening the decision.

## Architecture

- `src/telegramagent/agent_runtime.py` remains the owner of per-chat execution state, durable-history coordination, submission intent, cancellation, and one normalized run lifecycle. It must not retry an entire backend run after tools may have produced effects.
- `src/telegramagent/llm.py` remains the Pydantic AI adapter. It will install public per-run capabilities for model-request retry and history transformation, translate Pydantic events, and map steering/follow-up intent to `asap`/`when_idle` priorities.
- `src/telegramagent/session.py` remains the append-only source of exact durable messages and compaction records. Request-only transforms must not silently replace or lose the durable transcript.
- `src/telegramagent/telegram.py` selects submission intent but does not coordinate queues or Pydantic nodes. The existing behavior remains steering unless the queue UX decision in the plan explicitly changes it.
- Runtime events have one owner: `AgentRuntime` emits one `agent_start` and one terminal event per submission; backend retries and model/tool activity use subordinate events so retry attempts cannot create duplicate run lifecycles.

## Non-Goals

- Reimplementing Pydantic AI model, tool validation, parallel execution, MCP, or provider protocols.
- Exactly-once guarantees for external tools that fail after committing a side effect but before returning a result.
- Durable recovery of an in-flight process after a crash.
- Conversation branches, session trees, or migration away from the v2 JSONL format.
- Adding a new Telegram command before the steering/follow-up UX is explicitly selected.

## Assumptions

- Decision: ordinary same-chat messages remain `steer`; synthetic/scheduled messages use `follow_up`; direct runtime callers may select either intent. Follow-up acknowledgement is `已將新訊息排在目前任務完成後處理。`, and `/cancel` clears both kinds by cancelling the owning run.
- Existing `BOT_AGENT_MAX_ATTEMPTS` and `BOT_AGENT_RETRY_BASE_DELAY_SECONDS` settings can retain their names while their documented scope narrows from whole-run attempts to model-request/failed-turn attempts.
- Compaction keeps the current active turn and complete tool-call/result groups verbatim; only a completed historical prefix may be summarized.

## Risks

- A provider may fail after partial stream output; the retry adapter must prove whether Pydantic AI treats this as a model-request error and must not concatenate abandoned partial output with the retried response.
- An async history processor that calls the compactor can recursively invoke model infrastructure. The compactor must remain a separate tool-free call and the processor must guard against re-entry.
- Persisting a compaction checkpoint during an active run can diverge from the in-memory Pydantic transcript. A per-run transform state must consistently replace the same historical prefix on subsequent model requests.
- Changing event ownership may affect Telegram progress rendering and tests that currently expect `ChatAgent` to emit top-level lifecycle events.
- Steering versus follow-up is partly a product decision; queue implementation must not silently reinterpret ordinary user messages.

## Rollback / Recovery

- Keep the v2 session record schema unchanged so rollback does not require session migration.
- Land retry, context transformation, and queue semantics as separate focused changes. Each can be reverted without reverting the others.
- If public Pydantic capability behavior cannot satisfy the retry contract, retain only a conservative runtime guard that refuses whole-run retries after the first tool starts; do not restore unconditional whole-run replay.
- If per-request compaction cannot preserve transcript consistency, keep pre-run compaction and the new tests open rather than shipping request-only transformation with lossy persistence.

## Plan

- [x] Add focused characterization tests in `tests/test_agent_runtime.py` and `tests/test_llm.py` for transient failures before and after a mutating tool, mid-stream failure, and lifecycle cardinality. Evidence: the retry test initially failed because `ChatAgent` lacked request-level attempts, and the runtime tests initially exposed whole-backend replay and missing lifecycle ownership; all focused tests now pass.
- [x] Prototype the Pydantic AI 1.107 retry seam with `FunctionModel`. Decision: retry a failed assistant turn from public `AgentRun.all_messages()` trimmed to the last complete `ModelRequest`, then call `iter(None, message_history=checkpoint)`; model-request hooks cannot catch failures after streamed deltas begin. Tests prove completed tool results survive, abandoned partial text is not emitted, and backoff is cancellable.
- [x] Implement checkpointed assistant-turn retry in `src/telegramagent/llm.py` with transient classification, bounded exponential backoff, cancellation, buffered failed-stream events, and `retry_scheduled` events. Evidence: fault injection proves a completed mutating tool executes once, and an escaped mutating-tool failure is never classified as a retryable model failure.
- [x] Remove whole-backend replay and make `AgentRuntime` the sole owner of top-level lifecycle events. Evidence: success, permanent failure, retry exhaustion, cancellation, and successful retry tests each assert one `agent_start` and one appropriate terminal event.
- [x] Rewire existing retry settings through `src/telegramagent/cli.py` to `ChatAgent` and document request/turn-level semantics in `src/telegramagent/settings.py`, `.env.example`, and `README.md`. Evidence: settings/CLI-focused tests pass within the 97-test affected suite and `ty` verifies constructor wiring.
- [x] Add multi-turn context tests that cross the threshold inside one run and assert a summary plus an intact tool-call/result tail. TDD exception: the public `ProcessHistory` integration required an exploratory adapter before a stable per-request test boundary existed; behavior is now covered through both a real Pydantic multi-request test and deterministic runtime fault tests.
- [x] Implement per-run `ProcessHistory` and `_RunHistoryCompactor` state that transforms every model request, incrementally summarizes stable prefixes, prevents re-entry, emits events, and persists compaction records. Evidence: one- and two-compaction variants restore summary plus exact paired tool messages without duplication.
- [x] Add failure-path tests for empty summaries, compactor errors, and compaction-record write failures. Evidence: each path retains the prior usable context, emits an error compaction event, and creates no replacement record.
- [x] Resolve and record the mapping: ordinary messages steer, synthetic/scheduled messages follow up when idle, direct runtime callers choose explicitly, and cancellation clears the owning run and queues.
- [x] Add queue behavior tests for mapping, `when_idle`, FIFO ordering, images, cancellation cleanup, and cross-chat isolation. Evidence: the first follow-up test failed on the missing `intent` API before production changes; all queue tests now pass.
- [x] Implement explicit `steer`/`follow_up` intent through runtime, Pydantic `asap`/`when_idle`, gateway types, and synthetic Telegram call sites, with distinct acknowledgements. Evidence: runtime, real Pydantic, and Telegram mapping tests pass.
- [x] Run affected integration coverage. Evidence: 98 focused tests passed.
- [x] Run repository gates. Evidence: Ruff format 51 files clean, Ruff lint passed, `ty` passed, and 210 tests passed.
- [x] Not applicable for fresh graph evidence: `index_repository` crashed in its isolated worker on every retry. Direct source/diff audit confirms `AgentRuntime.submit` calls the backend once, retry resumes in `ChatAgent` from a message checkpoint, `ProcessHistory` owns request transforms, Telegram only selects intent, `SessionLog` v2 is unchanged, and no generated files were edited. The pre-change graph index matched repository HEAD but does not cover this working-tree diff.

## Completion Checklist

- [x] A transient failure cannot replay a completed mutating tool; deterministic fault injection proves the tool executes once.
- [x] Retry is bounded with exponential backoff, backoff is cancellable, permanent failures fail fast, and one submission emits one balanced top-level lifecycle.
- [x] Every model request applies the configured `ProcessHistory` policy while active tool-call/result groups remain intact.
- [x] Successful compaction is durable and restart-safe; failed compaction preserves the previous usable history.
- [x] Steering and follow-up have explicit Telegram semantics and map to Pydantic AI `asap` and `when_idle` without cross-chat leakage.
- [x] Existing v2 session files remain readable and no session migration is required; `session.py` schema code is unchanged.
- [x] Focused tests and all repository quality gates pass: 98 focused and 210 total tests.
- [x] Final dependency and diff audit confirms Pydantic AI still owns the model/tool loop and no unresolved high-risk unknown remains.
