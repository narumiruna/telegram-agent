import type { Page } from "playwright";
import { describe, expect, it } from "vitest";

import { LoaderContentError, LoaderTimeoutError } from "../src/core/errors.js";
import type { ImpersSession, ResourceProvider } from "../src/core/resources.js";
import { FirecrawlLoader } from "../src/loaders/firecrawl.js";
import { fetchImpersHtml, fetchImpersResponse, HttpLoader } from "../src/loaders/generic.js";
import { GitHubLoader, toRawGitHubUrl } from "../src/loaders/github.js";
import { MAX_PDF_BYTES, PdfLoader } from "../src/loaders/pdf.js";
import { convertToOldReddit, rssToMarkdown, toRedditJsonUrl, toRedditRssUrl } from "../src/loaders/reddit.js";
import { ReelLoader } from "../src/loaders/reel.js";
import { extractTruthSocialPost } from "../src/loaders/truthsocial.js";
import { renderFxTwitterPayload, TwitterLoader, toFxTwitterApiUrl } from "../src/loaders/twitter.js";

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
    let content: Buffer<ArrayBufferLike> = Buffer.from("<main>fingerprinted</main>");
    const session = {
      get: async (_url: string, options: Record<string, unknown>) => {
        requestOptions = options;
        if (typeof options.contentCallback === "function") {
          (options.contentCallback as (chunk: Buffer) => void)(content);
        }
        return {
          status: 200,
          get text() {
            return content.toString();
          },
          headers: { get: (name: string) => (name === "content-type" ? "text/html" : null) },
          iterContent: async function* () {
            yield content;
          },
          setContent: (value: Buffer) => {
            content = value;
          },
          close: async () => undefined,
        };
      },
      close: async () => undefined,
    } as unknown as ImpersSession;
    await expect(fetchImpersHtml("https://example.com", { session })).resolves.toEqual({
      content: "<main>fingerprinted</main>",
      contentType: "text/html",
    });
    expect(requestOptions).toMatchObject({
      impersonate: "chrome",
      allowRedirects: false,
      stream: true,
      acceptEncoding: "identity",
    });
  });

  it("aborts an impers response while it exceeds the byte limit", async () => {
    const session = {
      get: async (_url: string, options: Record<string, unknown>) => {
        const callback = options.contentCallback as (chunk: Buffer) => void;
        callback(Buffer.from("12"));
        callback(Buffer.from("34"));
        const signal = options.signal as AbortSignal;
        throw signal.reason;
      },
      close: async () => undefined,
    } as unknown as ImpersSession;
    await expect(fetchImpersResponse("https://example.com/file.pdf", { session, maxBytes: 3 })).rejects.toThrow(
      "3 byte limit",
    );
  });

  it("times out stalled HTTP requests by default", async () => {
    const resources = {
      fetch: async (_input: string | URL, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => reject(init.signal?.reason), { once: true });
        }),
    } as unknown as ResourceProvider;
    await expect(new HttpLoader({ resources, timeoutMs: 5 }).load("https://example.com")).rejects.toBeInstanceOf(
      LoaderTimeoutError,
    );
  });

  it("rejects remote PDFs that declare an oversized response", async () => {
    const resources = {
      fetch: async () =>
        new Response(null, {
          headers: {
            "content-length": String(MAX_PDF_BYTES + 1),
            "content-type": "application/pdf",
          },
        }),
      impersSession: async () => {
        throw new Error("fallback unavailable");
      },
    } as unknown as ResourceProvider;
    await expect(new PdfLoader({ resources }).load("https://example.com/oversized.pdf")).rejects.toThrow(
      `Response exceeds the ${MAX_PDF_BYTES} byte limit`,
    );
  });

  it("rejects blocker pages and empty Reddit feeds", () => {
    expect(() => rssToMarkdown("<html><body>verify</body></html>", "https://www.reddit.com/post/.rss")).toThrow(
      "not an Atom feed",
    );
    expect(() => rssToMarkdown("<feed><title>Empty</title></feed>", "https://www.reddit.com/post/.rss")).toThrow(
      "no entries",
    );
  });

  it("rejects missing or unlocated Truth Social posts", async () => {
    const missingPage = {
      locator: () => ({ count: async () => 1 }),
    } as unknown as Page;
    await expect(
      extractTruthSocialPost(missingPage, "https://truthsocial.com/@user/posts/123", "123", 100),
    ).rejects.toBeInstanceOf(LoaderContentError);

    const timeout = new Error("selector timed out");
    timeout.name = "TimeoutError";
    const blockedPage = {
      locator: () => ({ count: async () => 0 }),
      waitForSelector: async () => {
        throw timeout;
      },
    } as unknown as Page;
    await expect(
      extractTruthSocialPost(blockedPage, "https://truthsocial.com/@user/posts/123", "123", 100),
    ).rejects.toThrow("Could not find the requested post");
  });

  it("loads and verifies Twitter status content through FxTwitter", async () => {
    const statusId = "123456789";
    const payload = {
      code: 200,
      tweet: {
        id: statusId,
        url: `https://x.com/example/status/${statusId}`,
        text: "Tweet body",
        created_at: "Thu May 01 02:30:31 +0000 2025",
        likes: 3,
        author: { name: "Example", screen_name: "example" },
        article: {
          title: "Article title",
          content: { blocks: [{ type: "blockquote", text: "Article body" }] },
        },
        media: { all: [{ type: "photo", url: "https://pbs.twimg.com/example.jpg" }] },
      },
    };
    const resources = {
      fetch: async (input: string | URL) => {
        expect(String(input)).toBe(toFxTwitterApiUrl(statusId));
        return Response.json(payload);
      },
    } as unknown as ResourceProvider;

    const result = await new TwitterLoader({ resources }).load(`https://x.com/example/status/${statusId}`);
    expect(result).toContain("# Example (@example)");
    expect(result).toContain("Tweet body");
    expect(result).toContain("## Article title");
    expect(result).toContain("> Article body");
    expect(result).toContain("[photo 1](https://pbs.twimg.com/example.jpg)");
    expect(renderFxTwitterPayload(payload, statusId, "https://x.com/example/status/123456789")).toBe(result);
  });

  it("rejects an FxTwitter response for a different status", () => {
    expect(() =>
      renderFxTwitterPayload({ tweet: { id: "other" } }, "requested", "https://x.com/example/status/requested"),
    ).toThrow("requested tweet");
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
