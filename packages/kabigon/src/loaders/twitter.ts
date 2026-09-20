import type { Page } from "playwright";

import { LoaderContentError, LoaderNotApplicableError } from "../core/errors.js";
import type { Loader } from "../core/loader.js";
import type { ResourceProvider } from "../core/resources.js";
import { parseTwitterTarget } from "../sources/applicability.js";
import {
  DEFAULT_BLOCKED_RESOURCE_TYPES,
  DEFAULT_BROWSER_USER_AGENT,
  fetchBrowserHtml,
  waitForSelectorIgnoringTimeout,
} from "./browser.js";
import { htmlToMarkdown } from "./utils.js";

function statusId(url: string): string | undefined {
  try {
    return /\/status\/([0-9]+)(?:\/|$)/u.exec(new URL(url, "https://x.com").pathname)?.[1];
  } catch {
    return undefined;
  }
}

export function replaceDomain(url: string, newDomain = "x.com"): string {
  const target = parseTwitterTarget(url);
  const parsed = new URL(target.normalizedUrl);
  parsed.hostname = newDomain;
  return parsed.toString();
}

export class TwitterLoader implements Loader {
  readonly timeoutMs: number;
  readonly waitForTweetTimeoutMs: number;
  readonly resources?: ResourceProvider;

  constructor(options: { timeoutMs?: number; waitForTweetTimeoutMs?: number; resources?: ResourceProvider } = {}) {
    this.timeoutMs = options.timeoutMs ?? 20_000;
    this.waitForTweetTimeoutMs = options.waitForTweetTimeoutMs ?? 15_000;
    this.resources = options.resources;
  }

  async load(url: string, signal?: AbortSignal): Promise<string> {
    const target = parseTwitterTarget(url);
    if (!target.statusId) {
      throw new LoaderNotApplicableError("TwitterLoader", url, "URL is not a Twitter/X status URL");
    }
    const selectors = [
      `article a[href$="/status/${target.statusId}"] time`,
      `article a[href$="/status/${target.statusId}/"] time`,
      `article a[href*="/status/${target.statusId}?"] time`,
      `article a[href*="/status/${target.statusId}#"] time`,
    ];
    const waitForTweet = (page: Page) =>
      waitForSelectorIgnoringTimeout(page, selectors.join(", "), {
        state: "visible",
        timeout: Math.min(this.timeoutMs || this.waitForTweetTimeoutMs, this.waitForTweetTimeoutMs),
      });
    const extractTweet = async (page: Page): Promise<string> => {
      for (const article of await page.locator("article").all()) {
        const permalink = article.locator('a[href*="/status/"]:has(time)').first();
        if ((await permalink.count()) === 0) continue;
        const href = await permalink.getAttribute("href");
        if (href && statusId(href) === target.statusId) {
          return article.evaluate((element) => element.outerHTML);
        }
      }
      throw new LoaderContentError(
        "TwitterLoader",
        target.normalizedUrl,
        `Could not find the requested tweet (${target.statusId})`,
      );
    };
    const browser = await this.resources?.browser();
    const content = await fetchBrowserHtml(target.normalizedUrl, {
      loaderName: "TwitterLoader",
      timeoutMs: this.timeoutMs,
      timeoutSuggestion: "Twitter/X pages can be slow. Try increasing the timeout or check if the page requires login.",
      waitUntil: "domcontentloaded",
      userAgent: DEFAULT_BROWSER_USER_AGENT,
      blockedResourceTypes: DEFAULT_BLOCKED_RESOURCE_TYPES,
      afterGoto: waitForTweet,
      extractContent: extractTweet,
      ...(browser ? { browser } : {}),
      signal,
    });
    return htmlToMarkdown(content);
  }
}
