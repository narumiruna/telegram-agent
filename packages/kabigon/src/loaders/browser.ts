import type { Browser, Page, Response } from "playwright";

import { LoaderContentError, LoaderTimeoutError } from "../core/errors.js";
import type { RetrievedHtml } from "../core/retrieval.js";

export const DEFAULT_BROWSER_USER_AGENT =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36";
export const DEFAULT_BLOCKED_RESOURCE_TYPES = new Set(["font", "image", "media"]);

export type BrowserWaitUntil = "commit" | "domcontentloaded" | "load" | "networkidle";
export type BrowserPageHook = (page: Page) => Promise<void>;
export type BrowserContentExtractor = (page: Page) => Promise<string>;

interface BrowserFetchOptions {
  loaderName: string;
  timeoutMs?: number;
  timeoutSuggestion: string;
  waitUntil?: BrowserWaitUntil;
  userAgent?: string;
  headless?: boolean;
  blockedResourceTypes?: ReadonlySet<string>;
  afterGoto?: BrowserPageHook;
  extractContent?: BrowserContentExtractor;
  browser?: Browser;
  signal?: AbortSignal;
}

async function withBrowser(browser: Browser, url: string, options: BrowserFetchOptions): Promise<RetrievedHtml> {
  if (options.signal?.aborted) throw options.signal.reason;
  const context = await browser.newContext(options.userAgent ? { userAgent: options.userAgent } : {});
  try {
    const page = await context.newPage();
    if (options.blockedResourceTypes && options.blockedResourceTypes.size > 0) {
      await page.route("**/*", async (route) => {
        if (options.blockedResourceTypes?.has(route.request().resourceType())) await route.abort();
        else await route.continue();
      });
    }
    let response: Response | null;
    try {
      response = await page.goto(url, {
        ...(options.timeoutMs !== undefined ? { timeout: options.timeoutMs } : {}),
        ...(options.waitUntil ? { waitUntil: options.waitUntil } : {}),
      });
    } catch (error) {
      if (error instanceof Error && error.name === "TimeoutError") {
        throw new LoaderTimeoutError(
          options.loaderName,
          url,
          (options.timeoutMs ?? 30_000) / 1_000,
          options.timeoutSuggestion,
        );
      }
      throw error;
    }
    if (response && response.status() >= 400) {
      throw new LoaderContentError(options.loaderName, url, `HTTP request failed with status ${response.status()}`);
    }
    if (options.afterGoto) await options.afterGoto(page);
    const content = options.extractContent ? await options.extractContent(page) : await page.content();
    return { content, contentType: (await response?.headerValue("content-type")) ?? "text/html" };
  } finally {
    await context.close();
  }
}

export async function fetchBrowserHtmlResponse(url: string, options: BrowserFetchOptions): Promise<RetrievedHtml> {
  if (options.browser) return withBrowser(options.browser, url, options);
  const { chromium } = await import("playwright");
  const browser = await chromium.launch({ headless: options.headless ?? true });
  try {
    return await withBrowser(browser, url, options);
  } finally {
    await browser.close();
  }
}

export async function fetchBrowserHtml(url: string, options: BrowserFetchOptions): Promise<string> {
  return (await fetchBrowserHtmlResponse(url, options)).content;
}

export async function waitForSelectorIgnoringTimeout(
  page: Page,
  selector: string,
  options: { state?: "attached" | "detached" | "visible" | "hidden"; timeout?: number },
): Promise<void> {
  try {
    await page.waitForSelector(selector, options);
  } catch (error) {
    if (!(error instanceof Error && error.name === "TimeoutError")) throw error;
  }
}
