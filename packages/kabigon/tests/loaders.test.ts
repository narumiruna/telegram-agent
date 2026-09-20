import { describe, expect, it } from "vitest";

import type { ImpersSession, ResourceProvider } from "../src/core/resources.js";
import { FirecrawlLoader } from "../src/loaders/firecrawl.js";
import { fetchImpersHtml } from "../src/loaders/generic.js";
import { GitHubLoader, toRawGitHubUrl } from "../src/loaders/github.js";
import { convertToOldReddit, toRedditJsonUrl, toRedditRssUrl } from "../src/loaders/reddit.js";
import { ReelLoader } from "../src/loaders/reel.js";

describe("source loaders", () => {
  it("normalizes GitHub blob URLs and preserves raw content", async () => {
    const resources = {
      fetch: async (input: string | URL) => {
        expect(String(input)).toBe("https://raw.githubusercontent.com/a/b/main/demo.ts");
        return new Response("export const value = 1;", { headers: { "content-type": "text/plain" } });
      },
    } as unknown as ResourceProvider;
    expect(toRawGitHubUrl("https://github.com/a/b/blob/main/demo.ts")).toBe(
      "https://raw.githubusercontent.com/a/b/main/demo.ts",
    );
    await expect(new GitHubLoader({ resources }).load("https://github.com/a/b/blob/main/demo.ts")).resolves.toBe(
      "export const value = 1;",
    );
  });

  it("normalizes Reddit endpoint variants", () => {
    const short = "https://redd.it/abc";
    expect(convertToOldReddit(short)).toBe("https://old.reddit.com/comments/abc");
    expect(toRedditJsonUrl(short)).toBe("https://www.reddit.com/comments/abc.json");
    expect(toRedditRssUrl(short)).toBe("https://www.reddit.com/comments/abc/.rss");
  });

  it("uses impers as the curl-cffi transport", async () => {
    let requestOptions: Record<string, unknown> | undefined;
    const session = {
      get: async (_url: string, options: Record<string, unknown>) => {
        requestOptions = options;
        return {
          status: 200,
          text: "<main>fingerprinted</main>",
          headers: { get: (name: string) => (name === "content-type" ? "text/html" : null) },
        };
      },
      close: async () => undefined,
    } as unknown as ImpersSession;
    await expect(fetchImpersHtml("https://example.com", { session })).resolves.toEqual({
      content: "<main>fingerprinted</main>",
      contentType: "text/html",
    });
    expect(requestOptions).toMatchObject({ impersonate: "chrome", allowRedirects: true });
  });

  it("extracts markdown from Firecrawl's response envelope", async () => {
    const resources = {
      fetch: async () => Response.json({ success: true, data: { markdown: "# Loaded" } }),
    } as unknown as ResourceProvider;
    await expect(new FirecrawlLoader({ apiKey: "test", resources }).load("https://example.com")).resolves.toBe(
      "# Loaded",
    );
  });

  it("combines Reel audio transcription and HTML metadata", async () => {
    const audioLoader = { load: async () => "audio" };
    const htmlLoader = { load: async () => "metadata" };
    await expect(
      new ReelLoader({ audioLoader, htmlLoader }).load("https://www.instagram.com/reel/CuA0XYZ1234/"),
    ).resolves.toBe("audio\n\nmetadata");
  });
});
