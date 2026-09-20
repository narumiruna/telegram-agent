import { describe, expect, it, vi } from "vitest";

import { MorselPublishError, MorselPublisher } from "../src/morsel.js";

const capability = "a".repeat(43);

describe("MorselPublisher", () => {
  it("publishes bounded metadata and validates the returned capability URL", async () => {
    const fetchImplementation = vi.fn<typeof fetch>(async (_input, init) => {
      const payload = JSON.parse(String(init?.body)) as Record<string, unknown>;
      expect(payload.content).toBe("# 標題\n\n內容");
      expect(payload.telegram_instant_view).toBe(true);
      expect(payload).not.toHaveProperty("expires_in");
      return Response.json({ id: "share", share_url: `https://morsel.example/s/${capability}` }, { status: 201 });
    });
    const publisher = new MorselPublisher("https://morsel.example/", "secret", {
      timeoutMs: 1_000,
      expiresInSeconds: 60,
      telegramInstantView: true,
      telegramInstantViewRhash: "preview-hash",
      fetchImplementation,
    });

    await expect(publisher.publish("# 標題\n\n內容")).resolves.toBe(
      `https://morsel.example/s/${capability}?tg_rhash=preview-hash`,
    );
  });

  it("appends the Instant View hash inside fragment-form share routes", async () => {
    const publisher = new MorselPublisher("https://morsel.example/", "secret", {
      timeoutMs: 1_000,
      expiresInSeconds: 60,
      telegramInstantView: true,
      telegramInstantViewRhash: "preview-hash",
      fetchImplementation: async () =>
        Response.json({ id: "share", share_url: `https://morsel.example/#/s/${capability}` }, { status: 201 }),
    });

    await expect(publisher.publish("content")).resolves.toBe(
      `https://morsel.example/#/s/${capability}?tg_rhash=preview-hash`,
    );
  });

  it.each([
    `https://evil.example/s/${capability}`,
    `https://morsel.example/\ts/${capability}`,
    `https://morsel.example/s/${capability}\n`,
  ])("rejects cross-origin, malformed, and control-containing share URLs", async (shareUrl) => {
    const publisher = new MorselPublisher("https://morsel.example/", "secret", {
      timeoutMs: 1_000,
      expiresInSeconds: 60,
      telegramInstantView: false,
      fetchImplementation: async () => Response.json({ id: "share", share_url: shareUrl }, { status: 201 }),
    });

    await expect(publisher.publish("content")).rejects.toBeInstanceOf(MorselPublishError);
  });

  it("fails honestly when no API key is configured", async () => {
    const publisher = new MorselPublisher("https://morsel.example/", undefined, {
      timeoutMs: 1_000,
      expiresInSeconds: 60,
      telegramInstantView: false,
    });

    expect(publisher.isConfigured).toBe(false);
    await expect(publisher.publish("content")).rejects.toThrow("MORSEL_API_KEY");
  });
});
