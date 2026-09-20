import type { Loader } from "../core/loader.js";
import { InvalidUrlError } from "../core/errors.js";
import type { ResourceProvider } from "../core/resources.js";
import {
  RAW_GITHUB_HOST,
  parseGitHubRawContentTarget,
  parseGitHubTarget,
  requireLoaderApplicability,
} from "../sources/applicability.js";
import { extractFirstTagSubtree, htmlToMarkdown } from "./utils.js";

export function toRawGitHubUrl(url: string): string {
  return parseGitHubRawContentTarget(url).rawUrl ?? url;
}

export function extractMainHtml(html: string): string {
  return extractFirstTagSubtree(html, ["main", "article"]);
}

export class GitHubLoader implements Loader {
  constructor(private readonly options: { resources?: ResourceProvider } = {}) {}

  private async get(url: string, headers: HeadersInit, signal?: AbortSignal): Promise<Response> {
    const response = await (this.options.resources?.fetch(url, { headers, redirect: "follow", signal }) ??
      fetch(url, { headers, redirect: "follow", signal }));
    if (!response.ok) throw new Error(`GitHub returned HTTP ${response.status}`);
    return response;
  }

  async load(url: string, signal?: AbortSignal): Promise<string> {
    const target = requireLoaderApplicability("GitHubLoader", url, parseGitHubTarget);
    if (new URL(url).hostname === RAW_GITHUB_HOST || target.isRawContent) {
      const response = await this.get(
        target.rawUrl ?? toRawGitHubUrl(url),
        { Accept: "text/plain, text/markdown;q=0.9, */*;q=0.1" },
        signal,
      );
      const contentType = response.headers.get("content-type") ?? "";
      if (!["text", "json", "xml"].some((type) => contentType.includes(type))) {
        throw new InvalidUrlError(url, `GitHub text content-type (got ${JSON.stringify(contentType)})`);
      }
      return response.text();
    }

    const response = await this.get(
      url,
      { Accept: "text/html,application/xhtml+xml", "User-Agent": "kabigon-typescript" },
      signal,
    );
    const contentType = response.headers.get("content-type") ?? "";
    if (!contentType.includes("html")) {
      throw new InvalidUrlError(url, `GitHub HTML content-type (got ${JSON.stringify(contentType)})`);
    }
    return htmlToMarkdown(extractMainHtml(await response.text()));
  }
}
