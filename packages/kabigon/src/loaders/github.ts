import { InvalidUrlError, LoaderTimeoutError } from "../core/errors.js"
import type { Loader } from "../core/loader.js"
import { readResponseText } from "../core/network.js"
import type { ResourceProvider } from "../core/resources.js"
import {
  parseGitHubRawContentTarget,
  parseGitHubTarget,
  RAW_GITHUB_HOST,
  requireLoaderApplicability,
} from "../sources/applicability.js"
import { extractFirstTagSubtree, htmlToMarkdown } from "./utils.js"

export const DEFAULT_GITHUB_TIMEOUT_MS = 20_000
export const MAX_GITHUB_BYTES = 10 * 1024 * 1024

export function toRawGitHubUrl(url: string): string {
  return parseGitHubRawContentTarget(url).rawUrl ?? url
}

export function extractMainHtml(html: string): string {
  return extractFirstTagSubtree(html, ["main", "article"])
}

export class GitHubLoader implements Loader {
  constructor(
    private readonly options: { resources?: ResourceProvider; timeoutMs?: number } = {},
  ) {}

  private async get(url: string, headers: HeadersInit, signal?: AbortSignal): Promise<Response> {
    const response = await (this.options.resources?.fetch(url, {
      headers,
      redirect: "follow",
      signal,
    }) ?? fetch(url, { headers, redirect: "follow", signal }))
    if (!response.ok) throw new Error(`GitHub returned HTTP ${response.status}`)
    return response
  }

  async load(url: string, signal?: AbortSignal): Promise<string> {
    const timeoutMs = this.options.timeoutMs ?? DEFAULT_GITHUB_TIMEOUT_MS
    const timeoutSignal = AbortSignal.timeout(timeoutMs)
    const activeSignal = signal ? AbortSignal.any([signal, timeoutSignal]) : timeoutSignal
    try {
      return await this.loadWithSignal(url, activeSignal)
    } catch (error) {
      if (timeoutSignal.aborted)
        throw new LoaderTimeoutError("GitHubLoader", url, timeoutMs / 1_000)
      throw error
    }
  }

  private async loadWithSignal(url: string, signal: AbortSignal): Promise<string> {
    const target = requireLoaderApplicability("GitHubLoader", url, parseGitHubTarget)
    if (new URL(url).hostname === RAW_GITHUB_HOST || target.isRawContent) {
      const response = await this.get(
        target.rawUrl ?? toRawGitHubUrl(url),
        { Accept: "text/plain, text/markdown;q=0.9, */*;q=0.1" },
        signal,
      )
      const contentType = response.headers.get("content-type") ?? ""
      if (!["text", "json", "xml"].some((type) => contentType.includes(type))) {
        throw new InvalidUrlError(
          url,
          `GitHub text content-type (got ${JSON.stringify(contentType)})`,
        )
      }
      return readResponseText(response, MAX_GITHUB_BYTES)
    }

    const response = await this.get(
      url,
      { Accept: "text/html,application/xhtml+xml", "User-Agent": "kabigon-typescript" },
      signal,
    )
    const contentType = response.headers.get("content-type") ?? ""
    if (!contentType.includes("html")) {
      throw new InvalidUrlError(
        url,
        `GitHub HTML content-type (got ${JSON.stringify(contentType)})`,
      )
    }
    return htmlToMarkdown(extractMainHtml(await readResponseText(response, MAX_GITHUB_BYTES)))
  }
}
