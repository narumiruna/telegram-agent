import type { Page } from "playwright";

import { LoaderContentError, LoaderNotApplicableError } from "../core/errors.js";
import type { Loader } from "../core/loader.js";
import type { ResourceProvider } from "../core/resources.js";
import { parseTruthSocialTarget } from "../sources/applicability.js";
import { DEFAULT_BLOCKED_RESOURCE_TYPES, DEFAULT_BROWSER_USER_AGENT, fetchBrowserHtml } from "./browser.js";
import { htmlToMarkdown } from "./utils.js";

function truthStatusId(url: string): string | undefined {
  try {
    const finalPart = new URL(url).pathname.split("/").filter(Boolean).at(-1);
    return finalPart && /^\d+$/u.test(finalPart) ? finalPart : undefined;
  } catch {
    return undefined;
  }
}

export async function extractTruthSocialPost(
  page: Page,
  url: string,
  statusId: string,
  timeoutMs: number,
): Promise<string> {
  if ((await page.locator('[data-testid="missing-indicator"]').count()) > 0) {
    throw new LoaderContentError("TruthSocialLoader", url, `The requested post (${statusId}) was not found`);
  }
  const postSelector = [
    `article:has(a[href*="/${statusId}"])`,
    `[data-testid="status"]:has(a[href*="/${statusId}"])`,
    `[data-testid="post"]:has(a[href*="/${statusId}"])`,
  ].join(", ");
  try {
    await page.waitForSelector(postSelector, { state: "attached", timeout: Math.min(timeoutMs, 10_000) });
  } catch (error) {
    if (error instanceof Error && error.name === "TimeoutError") {
      throw new LoaderContentError("TruthSocialLoader", url, `Could not find the requested post (${statusId})`);
    }
    throw error;
  }
  return page
    .locator(postSelector)
    .first()
    .evaluate((element) => element.outerHTML);
}

export class TruthSocialLoader implements Loader {
  readonly timeoutMs: number;
  readonly resources?: ResourceProvider;

  constructor(options: { timeoutMs?: number; resources?: ResourceProvider } = {}) {
    this.timeoutMs = options.timeoutMs ?? 60_000;
    this.resources = options.resources;
  }

  async load(url: string, signal?: AbortSignal): Promise<string> {
    parseTruthSocialTarget(url);
    const statusId = truthStatusId(url);
    if (!statusId) {
      throw new LoaderNotApplicableError("TruthSocialLoader", url, "URL is not a Truth Social status URL");
    }
    const resources = this.resources;
    const browser = await resources?.browser();
    const content = await fetchBrowserHtml(url, {
      loaderName: "TruthSocialLoader",
      timeoutMs: this.timeoutMs,
      timeoutSuggestion: "Truth Social pages require JavaScript and can be slow. Try increasing the timeout.",
      waitUntil: "domcontentloaded",
      userAgent: DEFAULT_BROWSER_USER_AGENT,
      blockedResourceTypes: DEFAULT_BLOCKED_RESOURCE_TYPES,
      extractContent: (page) => extractTruthSocialPost(page, url, statusId, this.timeoutMs),
      ...(browser ? { browser } : {}),
      ...(resources
        ? {
            validateUrl: resources.validateUrl.bind(resources),
            fetchUrl: resources.fetch.bind(resources),
          }
        : {}),
      signal,
    });
    return htmlToMarkdown(content);
  }
}
