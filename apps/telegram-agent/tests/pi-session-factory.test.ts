import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { describe, expect, it, vi } from "vitest";

import { createPiSessionFactory } from "../src/agent/pi-session-factory.js";
import { loadSettings } from "../src/config/settings.js";
import type { Logger } from "../src/logging.js";

const logger: Logger = {
  debug: vi.fn(),
  info: vi.fn(),
  warn: vi.fn(),
  error: vi.fn(),
};

describe("createPiSessionFactory", () => {
  it("creates an isolated persistent Pi AgentSession with only approved custom tools", async () => {
    const root = await mkdtemp(path.join(tmpdir(), "telegramagent-pi-"));
    const settings = loadSettings(
      {
        BOT_SESSION_LOG_DIR: ".sessions",
        BOT_SKILLS_DIR: ".agents/skills",
        OPENAI_API_KEY: "test-key",
        OPENAI_BASE_URL: "https://api.example.test/v1",
        OPENAI_MODEL: "test-model",
      },
      root,
    );
    const factory = await createPiSessionFactory(settings, logger);
    const session = await factory.create(123);

    try {
      expect(session.model).toMatchObject({ provider: "telegramagent-openai", id: "test-model" });
      expect(session.sessionFile).toContain(path.join(".sessions", "123", "pi"));
      expect(session.getActiveToolNames()).toEqual(["load_public_url"]);
      expect(session.systemPrompt).toContain("Telegram 機器人助理");
    } finally {
      session.dispose();
    }
  });
});
