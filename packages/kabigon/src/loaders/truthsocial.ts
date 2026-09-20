import type { Loader } from "../core/loader.js";
import type { ResourceProvider } from "../core/resources.js";
import { parseTruthSocialTarget } from "../sources/applicability.js";
import {
  DEFAULT_BLOCKED_RESOURCE_TYPES,
  DEFAULT_BROWSER_USER_AGENT,
  fetchBrowserHtml,
  waitForSelectorIgnoringTimeout,
} from "./browser.js";
import { htmlToMarkdown } from "./utils.js";

export class TruthSocialLoader implements Loader {
  readonly timeoutMs: number;
  readonly resources?: ResourceProvider;

  constructor(options: { timeoutMs?: number; resources?: ResourceProvider } = {}) {
    this.timeoutMs = options.timeoutMs ?? 60_000;
    this.resources = options.resources;
  }

  async load(url: string, signal?: AbortSignal): Promise<string> {
    parseTruthSocialTarget(url);
    const browser = await this.resources?.browser();
    const content = await fetchBrowserHtml(url, {
      loaderName: "TruthSocialLoader",
      timeoutMs: this.timeoutMs,
      timeoutSuggestion: "Truth Social pages require JavaScript and can be slow. Try increasing the timeout.",
      waitUntil: "domcontentloaded",
      userAgent: DEFAULT_BROWSER_USER_AGENT,
      blockedResourceTypes: DEFAULT_BLOCKED_RESOURCE_TYPES,
      afterGoto: (page) =>
        waitForSelectorIgnoringTimeout(page, "article, .status, [data-testid='status'], [data-testid='post-content']", {
          state: "attached",
          timeout: Math.min(this.timeoutMs, 5_000),
        }),
      ...(browser ? { browser } : {}),
      signal,
    });
    return htmlToMarkdown(content);
  }
}
