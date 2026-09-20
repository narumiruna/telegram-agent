import path from "node:path";

import { describe, expect, it } from "vitest";
import { ZodError } from "zod";

import { loadSettings } from "../src/config/settings.js";

describe("loadSettings", () => {
  it("loads safe defaults and resolves repository paths", () => {
    const settings = loadSettings({}, "/workspace/project");

    expect(settings.botGroupPassiveContextEnabled).toBe(true);
    expect(settings.botSessionLogDir).toBe(path.resolve("/workspace/project/.telegramagent/sessions"));
    expect(settings.botSkillsDir).toBe(path.resolve("/workspace/project/.agents/skills"));
    expect(settings.openaiBaseUrl).toBe("https://api.openai.com/v1");
    expect(settings.morselLongReplyThreshold).toBe(2_000);
  });

  it("parses booleans and comma-separated sets", () => {
    const settings = loadSettings({
      BOT_GROUP_PASSIVE_CONTEXT_ENABLED: "false",
      BOT_WHITELIST: "123, -456,123",
      BOT_ENABLED_SKILLS: "kabigon, writing, kabigon",
      OPENAI_BASE_URL: "https://example.test/v1/",
    });

    expect(settings.botGroupPassiveContextEnabled).toBe(false);
    expect(settings.botWhitelist).toEqual(new Set([123, -456]));
    expect(settings.botEnabledSkills).toEqual(new Set(["kabigon", "writing"]));
    expect(settings.openaiBaseUrl).toBe("https://example.test/v1");
  });

  it("rejects invalid ranges and integer lists", () => {
    expect(() => loadSettings({ MORSEL_LONG_REPLY_THRESHOLD: "4097" })).toThrow(ZodError);
    expect(() => loadSettings({ BOT_WHITELIST: "123,nope" })).toThrow(ZodError);
    expect(() => loadSettings({ BOT_GROUP_PASSIVE_CONTEXT_ENABLED: "sometimes" })).toThrow(ZodError);
  });
});
