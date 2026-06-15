## GOTCHA

- Symptom: LLM/agent behavior becomes fragile when code repairs model output or upstream warnings by matching text fragments. Cause: behavior policy is encoded in ad hoc string operations instead of instructions or structured fields. Fix: prefer structured tool outputs, explicit policy fields, tool descriptions, and response contracts; reserve string handling for input normalization and display formatting.
- Symptom: Link messages can reply with raw `[Errno 17] File exists: '.telegramagent'`. Cause: background task/session persistence failures can happen after a successful agent run and leak `str(exc)` to Telegram. Fix: keep replies independent from durable history writes and use generic user-facing task failure text while logging server-side details.

## TASTE

- Prefer instructions, tool descriptions, response contracts, and structured tool fields when shaping LLM/agent behavior; avoid parsing or repairing final answers with regex or string fragments.
- Prefer deterministic code for data integrity in LLM/agent features, including normalization, validation, filtering, pagination, and structured result shaping; use instructions for how the model should interpret and present those results.
