import type { Page } from "playwright";
import { describe, expect, it } from "vitest";

import { LoaderContentError, LoaderTimeoutError } from "../src/core/errors.js";
import type { ImpersSession, ResourceProvider } from "../src/core/resources.js";
import { FirecrawlLoader } from "../src/loaders/firecrawl.js";
import { CurlCffiLoader, fetchImpersHtml, fetchImpersResponse, HttpLoader } from "../src/loaders/generic.js";
import { GitHubLoader, MAX_GITHUB_BYTES, toRawGitHubUrl } from "../src/loaders/github.js";
import { MAX_PDF_BYTES, PdfLoader } from "../src/loaders/pdf.js";
import {
  convertToOldReddit,
  MAX_REDDIT_BYTES,
  RedditLoader,
  rssToMarkdown,
  toRedditJsonUrl,
  toRedditRssUrl,
} from "../src/loaders/reddit.js";
import { ReelLoader } from "../src/loaders/reel.js";
import { extractTruthSocialPost } from "../src/loaders/truthsocial.js";
import { renderFxTwitterPayload, TwitterLoader, toFxTwitterApiUrl } from "../src/loaders/twitter.js";

describe("source loaders", () => {
  it("normalizes GitHub blob URLs and preserves raw content", async () => {
    const resources = {
      fetch: async (input: string | URL) => {
        expect(String(input)).toBe("https://github.com/a/b/blob/main/demo.ts?raw=1");
        return new Response("export const value = 1;", { headers: { "content-type": "text/plain" } });
      },
    } as unknown as ResourceProvider;
    expect(toRawGitHubUrl("https://github.com/a/b/blob/main/demo.ts")).toBe(
      "https://github.com/a/b/blob/main/demo.ts?raw=1",
    );
    await expect(new GitHubLoader({ resources }).load("https://github.com/a/b/blob/main/demo.ts")).resolves.toBe(
      "export const value = 1;",
    );
  });

  it("times out stalled GitHub requests", async () => {
    const resources = {
      fetch: async (_input: string | URL, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => reject(init.signal?.reason), { once: true });
        }),
    } as unknown as ResourceProvider;

    await expect(
      new GitHubLoader({ resources, timeoutMs: 5 }).load("https://raw.githubusercontent.com/a/b/main/demo.ts"),
    ).rejects.toBeInstanceOf(LoaderTimeoutError);
  });

  it("bounds GitHub response bodies", async () => {
    const resources = {
      fetch: async () =>
        new Response(null, {
          headers: {
            "content-length": String(MAX_GITHUB_BYTES + 1),
            "content-type": "text/plain",
          },
        }),
    } as unknown as ResourceProvider;
    await expect(
      new GitHubLoader({ resources }).load("https://raw.githubusercontent.com/a/b/main/demo.ts"),
    ).rejects.toThrow(`${MAX_GITHUB_BYTES} byte limit`);
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
      proxy: expect.stringMatching(/^http:\/\/127\.0\.0\.1:[0-9]+$/u),
    });
  });

  it("rejects non-text responses before generic Markdown conversion", async () => {
    const session = {
      get: async () => ({
        status: 200,
        text: "binary data",
        headers: { get: (name: string) => (name === "content-type" ? "image/png" : null) },
        setContent: () => undefined,
        close: async () => undefined,
      }),
      close: async () => undefined,
    } as unknown as ImpersSession;
    const resources = {
      validateUrl: async (input: string | URL) => new URL(input),
      impersProxy: async () => "http://127.0.0.1:8080",
      impersSession: async () => session,
    } as unknown as ResourceProvider;

    await expect(new CurlCffiLoader({ resources }).load("https://example.com/image")).rejects.toThrow(
      "Expected textual content",
    );
  });

  it("strips sensitive impers headers on cross-origin redirects", async () => {
    const requestedHeaders: Record<string, string>[] = [];
    let request = 0;
    const session = {
      get: async (_url: string, options: Record<string, unknown>) => {
        requestedHeaders.push({ ...(options.headers as Record<string, string>) });
        request += 1;
        return {
          status: request === 1 ? 302 : 200,
          text: "done",
          headers: {
            get: (name: string) => (request === 1 && name === "location" ? "https://other.example/final" : null),
          },
          setContent: () => undefined,
          close: async () => undefined,
        };
      },
      close: async () => undefined,
    } as unknown as ImpersSession;
    const resources = {
      validateUrl: async (input: string | URL) => new URL(input),
      impersProxy: async () => "http://127.0.0.1:8080",
    } as unknown as ResourceProvider;

    await fetchImpersResponse("https://example.com/start", {
      session,
      resources,
      headers: {
        Authorization: "Bearer secret",
        Cookie: "session=secret",
        "Proxy-Authorization": "Basic secret",
        Accept: "text/html",
      },
    });

    expect(requestedHeaders[0]).toMatchObject({ Authorization: "Bearer secret", Cookie: "session=secret" });
    expect(requestedHeaders[1]).toEqual({ Accept: "text/html" });
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

  it("times out stalled remote PDF requests by default", async () => {
    const resources = {
      fetch: async (_input: string | URL, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => reject(init.signal?.reason), { once: true });
        }),
    } as unknown as ResourceProvider;
    await expect(
      new PdfLoader({ resources, timeoutMs: 5 }).load("https://example.com/stalled.pdf"),
    ).rejects.toBeInstanceOf(LoaderTimeoutError);
  });

  it("treats mixed-case HTTP schemes as remote PDF URLs", async () => {
    const requests: string[] = [];
    const resources = {
      fetch: async (input: string | URL) => {
        requests.push(String(input));
        return new Response("not a PDF", { headers: { "content-type": "text/plain" } });
      },
    } as unknown as ResourceProvider;

    await expect(new PdfLoader({ resources }).load("HTTPS://example.com/file.pdf")).rejects.toThrow("Not a PDF file");
    expect(requests).toEqual(["https://example.com/file.pdf"]);
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

  it("bounds Reddit RSS and JSON responses before parsing", async () => {
    const requests: string[] = [];
    let browserStarted = false;
    const resources = {
      fetch: async (input: string | URL) => {
        const url = String(input);
        requests.push(url);
        if (url.endsWith(".rss")) return new Response("not a feed");
        return new Response(null, { headers: { "content-length": String(MAX_REDDIT_BYTES + 1) } });
      },
      browser: async () => {
        browserStarted = true;
        throw new Error("browser fallback started");
      },
      runBrowser: async (operation: () => Promise<unknown>) => operation(),
    } as unknown as ResourceProvider;

    await expect(new RedditLoader({ resources }).load("https://reddit.com/comments/abc/post")).rejects.toThrow(
      "browser fallback started",
    );
    expect(requests).toEqual([
      "https://www.reddit.com/comments/abc/post/.rss",
      "https://www.reddit.com/comments/abc/post.json",
    ]);
    expect(browserStarted).toBe(true);
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

  it("times out stalled Firecrawl requests", async () => {
    const resources = {
      fetch: async (_input: string | URL, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => reject(init.signal?.reason), { once: true });
        }),
    } as unknown as ResourceProvider;
    await expect(
      new FirecrawlLoader({ apiKey: "test", resources, timeoutMs: 5 }).load("https://example.com"),
    ).rejects.toBeInstanceOf(LoaderTimeoutError);
  });

  it("abandons a stalled FxTwitter request and starts the browser fallback", async () => {
    let apiAborted = false;
    const resources = {
      fetch: async (_input: string | URL, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener(
            "abort",
            () => {
              apiAborted = true;
              reject(init.signal?.reason);
            },
            { once: true },
          );
        }),
      browser: async () => {
        throw new Error("browser fallback started");
      },
      runBrowser: async (operation: () => Promise<unknown>) => operation(),
    } as unknown as ResourceProvider;

    await expect(
      new TwitterLoader({ resources, timeoutMs: 5 }).load("https://x.com/example/status/123456789"),
    ).rejects.toThrow("browser fallback started");
    expect(apiAborted).toBe(true);
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
