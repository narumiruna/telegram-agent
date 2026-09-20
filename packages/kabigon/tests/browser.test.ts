import type { Browser, Page, Route, WebSocketRoute } from "playwright";
import { describe, expect, it, vi } from "vitest";

import { LoaderContentError } from "../src/core/errors.js";
import type { ResourceProvider } from "../src/core/resources.js";
import { fetchBrowserHtmlResponse } from "../src/loaders/browser.js";
import { DEFAULT_PLAYWRIGHT_TIMEOUT_MS, PlaywrightLoader } from "../src/loaders/generic.js";

function browserHarness(content: string) {
  let routeHandler: ((route: Route) => Promise<void>) | undefined;
  let webSocketHandler: ((route: WebSocketRoute) => Promise<void>) | undefined;
  const continueRequest = vi.fn(async () => undefined);
  const abortRequest = vi.fn(async () => undefined);
  const fulfillRequest = vi.fn(async () => undefined);
  const closeWebSocket = vi.fn(async () => undefined);
  const request = {
    resourceType: () => "document",
    url: () => "https://example.com/page",
    isNavigationRequest: () => true,
    method: () => "GET",
    allHeaders: async () => ({ accept: "text/html" }),
    postDataBuffer: () => null,
  };
  const route = {
    request: () => request,
    continue: continueRequest,
    abort: abortRequest,
    fulfill: fulfillRequest,
  } as unknown as Route;
  const gotoPage = vi.fn(async () => {
    await routeHandler?.(route);
    await webSocketHandler?.({ close: closeWebSocket } as unknown as WebSocketRoute);
    return {
      status: () => 200,
      headerValue: async (name: string) => (name === "content-type" ? "text/html" : null),
    };
  });
  const page = {
    goto: gotoPage,
    evaluate: async () => Buffer.byteLength(content),
    content: async () => content,
  } as unknown as Page;
  const closeContext = vi.fn(async () => undefined);
  const newContext = vi.fn(async () => ({
    route: async (_pattern: string, handler: (route: Route) => Promise<void>) => {
      routeHandler = handler;
    },
    routeWebSocket: async (_pattern: string, handler: (route: WebSocketRoute) => Promise<void>) => {
      webSocketHandler = handler;
    },
    newPage: async () => page,
    close: closeContext,
  }));
  const browser = { newContext } as unknown as Browser;
  return {
    abortRequest,
    browser,
    closeContext,
    closeWebSocket,
    continueRequest,
    fulfillRequest,
    gotoPage,
    newContext,
  };
}

describe("browser transport safety", () => {
  it("proxies HTTP requests through the validated fetch transport", async () => {
    const harness = browserHarness("<main>rendered</main>");
    const fetchUrl = vi.fn(async () => new Response("<main>network</main>", { status: 200 }));
    const validateUrl = vi.fn(async (value: string | URL) => new URL(value));

    await expect(
      fetchBrowserHtmlResponse("https://example.com/page", {
        loaderName: "TestLoader",
        timeoutSuggestion: "timed out",
        browser: harness.browser,
        fetchUrl,
        validateUrl,
      }),
    ).resolves.toEqual({ content: "<main>rendered</main>", contentType: "text/html" });

    expect(validateUrl).toHaveBeenCalledWith("https://example.com/page", expect.any(AbortSignal));
    expect(fetchUrl).toHaveBeenCalledWith(
      "https://example.com/page",
      expect.objectContaining({ method: "GET", redirect: "manual" }),
    );
    expect(harness.newContext).toHaveBeenCalledWith(expect.objectContaining({ serviceWorkers: "block" }));
    expect(harness.fulfillRequest).toHaveBeenCalledOnce();
    expect(harness.continueRequest).not.toHaveBeenCalled();
    expect(harness.abortRequest).not.toHaveBeenCalled();
    expect(harness.closeWebSocket).toHaveBeenCalledOnce();
    expect(harness.closeContext).toHaveBeenCalledOnce();
  });

  it("uses a bounded default navigation timeout", async () => {
    const harness = browserHarness("<main>loaded</main>");
    const resources = {
      browser: async () => harness.browser,
      fetch: async () => new Response("<main>network</main>"),
      validateUrl: async (value: string | URL) => new URL(value),
    } as unknown as ResourceProvider;

    await expect(new PlaywrightLoader({ resources }).load("https://example.com/page")).resolves.toBe("loaded");
    expect(harness.gotoPage).toHaveBeenCalledWith(
      "https://example.com/page",
      expect.objectContaining({ timeout: DEFAULT_PLAYWRIGHT_TIMEOUT_MS }),
    );
  });

  it("rejects oversized browser response bodies", async () => {
    const harness = browserHarness("ok");

    await expect(
      fetchBrowserHtmlResponse("https://example.com/page", {
        loaderName: "TestLoader",
        timeoutSuggestion: "timed out",
        browser: harness.browser,
        fetchUrl: async () => new Response("four"),
        validateUrl: async (value) => new URL(value),
        maxBytes: 3,
      }),
    ).rejects.toThrow("3 byte limit");
    expect(harness.abortRequest).toHaveBeenCalledOnce();
  });

  it("rejects oversized rendered DOM content", async () => {
    const harness = browserHarness("four");

    await expect(
      fetchBrowserHtmlResponse("https://example.com/page", {
        loaderName: "TestLoader",
        timeoutSuggestion: "timed out",
        browser: harness.browser,
        fetchUrl: async () => new Response("ok"),
        validateUrl: async (value) => new URL(value),
        maxBytes: 3,
      }),
    ).rejects.toBeInstanceOf(LoaderContentError);
    expect(harness.closeContext).toHaveBeenCalledOnce();
  });
});
