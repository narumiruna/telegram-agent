## GOTCHA

- Symptom: LLM/agent behavior becomes fragile when code repairs model output or upstream warnings by matching text fragments. Cause: behavior policy is encoded in ad hoc string operations instead of instructions or structured fields. Fix: prefer structured tool outputs, explicit policy fields, tool descriptions, and response contracts; reserve string handling for input normalization and display formatting.
- Symptom: Link messages can reply with raw `[Errno 17] File exists: '.telegramagent'`. Cause: background task/session persistence failures can happen after a successful agent run and leak `str(exc)` to Telegram. Fix: keep replies independent from durable history writes and use generic user-facing task failure text while logging server-side details.
- Symptom: One malformed MCP tool call can repeatedly restart the bot on the same Telegram update. Cause: MCP JSON-RPC invalid-parameter errors can escape Pydantic AI as `McpError` before Telegram acknowledges the update. Fix: convert code `-32602` to `ModelRetry` at the MCP tool boundary and catch terminal MCP failures in user-response paths.
- Symptom: A mutating tool can run twice after a transient model-stream failure. Cause: retrying the complete backend run replays work before the failed model request. Fix: retry from the last complete `ModelRequest` checkpoint and never replay the whole tool run.

## TASTE

- Prefer instructions, tool descriptions, response contracts, and structured tool fields when shaping LLM/agent behavior; avoid parsing or repairing final answers with regex or string fragments.
- Prefer deterministic code for data integrity in LLM/agent features, including normalization, validation, filtering, pagination, and structured result shaping; use instructions for how the model should interpret and present those results.

## CONVENTIONS
