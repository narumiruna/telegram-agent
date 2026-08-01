# Firecrawl MCP Support Plan

## Goal

Register Firecrawl's hosted Streamable HTTP MCP server when deployment provides `FIRECRAWL_API_KEY`, without exposing that key in runtime capability text or HTTP trace URL attributes.

## Context

- The hosted endpoint documented by `firecrawl/firecrawl-mcp-server` is `https://mcp.firecrawl.dev/{FIRECRAWL_API_KEY}/v2/mcp`.
- The deploy workflow already writes `FIRECRAWL_API_KEY` into `.env` from the GitHub secret.
- Existing MCP assembly in `src/telegramagent/mcp.py` creates Pydantic AI `MCPToolset` instances and passes them into `ChatAgent`.

## Plan

- [x] Confirm the hosted endpoint, Streamable HTTP transport, current deployment secret wiring, and installed FastMCP/Pydantic AI APIs; evidence: upstream README, `.github/workflows/deploy.yml`, and local API introspection.
- [x] Add focused failing tests for Firecrawl MCP enablement, missing-key behavior, URL construction, environment settings, and URL-secret redaction; initial evidence: `tests/test_mcp.py` failed collection because `FirecrawlMcpConfig` did not exist.
- [x] Implement Firecrawl MCP configuration/building in `src/telegramagent/mcp.py`, settings in `src/telegramagent/settings.py`, startup capability/wiring in `src/telegramagent/cli.py`, and Firecrawl path-key redaction in observability/log handling; evidence: 29 focused tests pass.
- [x] Document safe Firecrawl MCP settings in `.env.example` and `README.md`; evidence: configured local Firecrawl key is absent from the tracked diff.
- [x] Run formatting, linting, type checking, the complete test suite, and a credential-safe live MCP tool-list smoke test against the configured hosted endpoint; evidence: all repository gates pass and the live server listed 27 tools.

## Risks

- The API key is embedded in the endpoint path by Firecrawl's hosted contract. HTTP trace and log URL fields must redact that path segment.
- Firecrawl is remote; startup registration should require configuration but should not make a network request until Pydantic AI enters the MCP toolset lifecycle.

## Completion Checklist

- [x] `FIRECRAWL_API_KEY` enables a Firecrawl MCP toolset with ID `firecrawl`; disabled or absent-key configurations register no toolset.
- [x] Startup passes both Yahoo Finance and Firecrawl toolsets to `ChatAgent` and reports `mcp.firecrawl` accurately.
- [x] `.env.example` and `README.md` describe all new settings without secrets.
- [x] Firecrawl endpoint API keys are redacted from application logs, exception output, Logfire scrubbing, and HTTP trace URL attributes.
- [x] `uv run ruff format --check`, `uv run ruff check .`, `uv run ty check .`, and `uv run pytest -q tests` all pass.
