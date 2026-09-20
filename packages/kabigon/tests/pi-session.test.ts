import { describe, expect, it } from "vitest";

import type { ResourceProvider } from "../src/core/resources.js";
import { PiSessionLoader } from "../src/loaders/pi-session.js";

const sharedUrl = "https://pi.dev/session/#0230effc86f4a142c885cb59fe9725d5";
const rawUrl = "https://gist.githubusercontent.com/alice/id/raw/revision/session.html";

function sessionHtml(): string {
  const session = {
    header: { id: "session-123", timestamp: "2026-08-08T06:21:56.606Z", cwd: "/workspace/demo" },
    leafId: "tool-result",
    systemPrompt: "You are a coding assistant.",
    tools: [{ name: "read", description: "Read a file." }],
    entries: [
      { type: "model_change", id: "model", parentId: null, provider: "openai-codex", modelId: "gpt-test" },
      {
        type: "message",
        id: "user",
        parentId: "model",
        message: {
          role: "user",
          content: [
            { type: "text", text: "Create the loader." },
            { type: "image", mimeType: "image/png", data: "SECRET_IMAGE_DATA" },
          ],
        },
      },
      {
        type: "message",
        id: "assistant",
        parentId: "user",
        message: {
          role: "assistant",
          provider: "openai-codex",
          model: "gpt-test",
          content: [
            { type: "thinking", thinking: "Inspect the file first." },
            { type: "toolCall", name: "read", arguments: { path: "demo.ts" } },
          ],
        },
      },
      {
        type: "message",
        id: "tool-result",
        parentId: "assistant",
        message: { role: "toolResult", toolName: "read", content: [{ type: "text", text: "hello" }] },
      },
      {
        type: "message",
        id: "orphan",
        parentId: "user",
        message: { role: "assistant", content: [{ type: "text", text: "ORPHAN" }] },
      },
    ],
  };
  return `<script id="session-data">${Buffer.from(JSON.stringify(session)).toString("base64")}</script>`;
}

describe("Pi session loader", () => {
  it("fetches truncated Gists and renders only the selected ancestry", async () => {
    const requested: string[] = [];
    const resources = {
      fetch: async (input: string | URL) => {
        const url = String(input);
        requested.push(url);
        if (url.includes("api.github.com")) {
          return Response.json({ files: { "session.html": { truncated: true, raw_url: rawUrl, content: "bad" } } });
        }
        return new Response(sessionHtml());
      },
    } as unknown as ResourceProvider;
    const result = await new PiSessionLoader({ resources }).load(sharedUrl);
    expect(requested).toEqual(["https://api.github.com/gists/0230effc86f4a142c885cb59fe9725d5", rawUrl]);
    expect(result).toContain("# Pi Session session-123");
    expect(result).toContain("Create the loader.");
    expect(result).toContain("[Image: image/png]");
    expect(result).toContain("#### Thinking");
    expect(result).toContain('"path": "demo.ts"');
    expect(result).toContain("### Tool Result: read");
    expect(result).not.toContain("ORPHAN");
    expect(result).not.toContain("SECRET_IMAGE_DATA");
  });
});
