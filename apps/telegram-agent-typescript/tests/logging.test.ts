import { describe, expect, it } from "vitest"

import { redactLogMessage } from "../src/logging.js"

describe("redactLogMessage", () => {
  it("redacts Telegram, Firecrawl, bearer, and named secrets", () => {
    const redacted = redactLogMessage(
      "POST https://api.telegram.org/bot123456:secret-token/getMe " +
        "https://mcp.firecrawl.dev/fc-secret/v2/mcp token=abc Authorization=xyz Bearer bearer-secret " +
        "{ apiKey: 'inspect-secret', cookie: \"session-secret\" }",
    )

    expect(redacted).toContain("/bot[redacted]/getMe")
    expect(redacted).toContain("https://mcp.firecrawl.dev/[redacted]/v2/mcp")
    expect(redacted).toContain("token=[redacted]")
    expect(redacted).toContain("Authorization=[redacted]")
    expect(redacted).toContain("Bearer [redacted]")
    expect(redacted).not.toContain("secret-token")
    expect(redacted).not.toContain("fc-secret")
    expect(redacted).not.toContain("bearer-secret")
    expect(redacted).not.toContain("inspect-secret")
    expect(redacted).not.toContain("session-secret")
    expect(redacted).toContain("apiKey: '[redacted]'")
    expect(redacted).toContain('cookie: "[redacted]"')
  })
})
