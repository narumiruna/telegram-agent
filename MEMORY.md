## GOTCHA

- Symptom: LLM/agent behavior becomes fragile when code repairs model output or upstream warnings by matching text fragments. Cause: behavior policy is encoded in ad hoc string operations instead of instructions or structured fields. Fix: prefer structured tool outputs, explicit policy fields, tool descriptions, and response contracts; reserve string handling for input normalization and display formatting.

## TASTE

- Prefer instructions, tool descriptions, response contracts, and structured tool fields when shaping LLM/agent behavior; avoid parsing or repairing final answers with regex or string fragments.
- Prefer deterministic code for data integrity in LLM/agent features, including normalization, validation, filtering, pagination, and structured result shaping; use instructions for how the model should interpret and present those results.
