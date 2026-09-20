import * as cheerio from "cheerio"

import { LoaderContentError } from "../core/errors.js"
import { recordAttempt, remainingMilliseconds } from "../core/execution.js"
import type { Loader } from "../core/loader.js"
import type { ResourceProvider } from "../core/resources.js"
import { AttemptStatus } from "../core/results.js"
import { parseBbcTarget, parseCnnTarget, parseLtnTarget } from "../sources/applicability.js"
import { fetchBrowserHtmlResponse } from "./browser.js"
import { fetchHttpHtml, fetchImpersHtml } from "./generic.js"
import { extractArticleBodyFromJsonLd, htmlToMarkdown } from "./utils.js"

export const DEFAULT_NEWS_ARTICLE_HEADERS = {
  Accept: "text/html,application/xhtml+xml",
  "User-Agent":
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
}

type NewsSource = "bbc" | "cnn" | "ltn"

export function extractNewsArticleText(html: string, url: string, loaderName: string): string {
  const jsonLdBody = extractArticleBodyFromJsonLd(html)
  if (jsonLdBody) return jsonLdBody
  const $ = cheerio.load(html)
  const article = $("article").first()
  if (article.length === 0)
    throw new LoaderContentError(loaderName, url, "Could not find article body")
  article.find("script,style,noscript,svg").remove()
  const result = htmlToMarkdown($.html(article)).trim()
  if (!result) throw new LoaderContentError(loaderName, url, "Could not find article body")
  return result
}

export function extractLtnArticleText(html: string, url: string, loaderName: string): string {
  const $ = cheerio.load(html)
  const article = $("div.text.boxTitle.boxText").first()
  if (article.length > 0) {
    article
      .find(
        "script,style,noscript,svg,[id^='ad-'],.adHeight250,.adHeight280,.after_ir,.appE1121,.before_ir,.suggest_m,.suggest_pc",
      )
      .remove()
    return htmlToMarkdown($.html(article))
  }
  const body = extractArticleBodyFromJsonLd(html)
  if (body) return body
  throw new LoaderContentError(loaderName, url, "Could not find LTN article body")
}

export class NewsArticleLoader implements Loader {
  readonly loaderName: string
  readonly validate: (url: string) => string
  readonly resources?: ResourceProvider
  readonly headers: Record<string, string>

  constructor(
    public readonly source: NewsSource,
    options: { resources?: ResourceProvider; headers?: Record<string, string> } = {},
  ) {
    this.loaderName = source === "bbc" ? "BBCLoader" : source === "cnn" ? "CNNLoader" : "LTNLoader"
    this.validate =
      source === "bbc" ? parseBbcTarget : source === "cnn" ? parseCnnTarget : parseLtnTarget
    this.resources = options.resources
    this.headers = options.headers ?? DEFAULT_NEWS_ARTICLE_HEADERS
  }

  async load(url: string, signal?: AbortSignal): Promise<string> {
    this.validate(url)
    const resources = this.resources
    const transports: readonly [string, () => Promise<{ content: string; contentType: string }>][] =
      [
        [
          "httpx",
          () =>
            fetchHttpHtml(url, {
              headers: this.headers,
              resources: this.resources,
              signal,
              loaderName: this.loaderName,
            }),
        ],
        [
          "curl-cffi",
          () =>
            fetchImpersHtml(url, {
              headers: this.headers,
              resources: this.resources,
              signal,
              loaderName: this.loaderName,
            }),
        ],
        [
          "browser",
          async () => {
            const operation = async () => {
              const browser = await resources?.browser()
              return fetchBrowserHtmlResponse(url, {
                loaderName: this.loaderName,
                timeoutMs: Math.min(30_000, remainingMilliseconds() ?? 30_000),
                timeoutSuggestion: "Article page timed out while using the browser transport.",
                waitUntil: "domcontentloaded",
                ...(browser ? { browser } : {}),
                ...(resources
                  ? {
                      validateUrl: resources.validateUrl.bind(resources),
                      fetchUrl: resources.fetch.bind(resources),
                    }
                  : {}),
                signal,
              })
            }
            return resources ? resources.runBrowser(operation) : operation()
          },
        ],
      ]

    const errors: Error[] = []
    for (const [transportId, transport] of transports) {
      const started = performance.now()
      try {
        const response = await transport()
        if (!response.contentType.toLowerCase().includes("html")) {
          throw new LoaderContentError(
            this.loaderName,
            url,
            `Expected HTML content, got: ${JSON.stringify(response.contentType)}`,
          )
        }
        const result =
          this.source === "ltn"
            ? extractLtnArticleText(response.content, url, this.loaderName)
            : extractNewsArticleText(response.content, url, this.loaderName)
        recordTransport(this.source, transportId, AttemptStatus.Success, started)
        return result
      } catch (error) {
        const typed = error instanceof Error ? error : new Error(String(error))
        errors.push(typed)
        recordTransport(this.source, transportId, AttemptStatus.Failed, started, typed)
      }
    }
    throw new LoaderContentError(
      this.loaderName,
      url,
      `All article transports failed: ${errors.map((error) => `${error.name}: ${error.message}`).join("; ")}`,
    )
  }
}

export class BbcLoader extends NewsArticleLoader {
  constructor(options: { resources?: ResourceProvider; headers?: Record<string, string> } = {}) {
    super("bbc", options)
  }
}

export class CnnLoader extends NewsArticleLoader {
  constructor(options: { resources?: ResourceProvider; headers?: Record<string, string> } = {}) {
    super("cnn", options)
  }
}

export class LtnLoader extends NewsArticleLoader {
  constructor(options: { resources?: ResourceProvider; headers?: Record<string, string> } = {}) {
    super("ltn", options)
  }
}

export { BbcLoader as BBCLoader, CnnLoader as CNNLoader, LtnLoader as LTNLoader }

function recordTransport(
  source: string,
  transport: string,
  status: (typeof AttemptStatus)[keyof typeof AttemptStatus],
  started: number,
  error?: Error,
): void {
  recordAttempt({
    loaderId: `${source}:${transport}`,
    status,
    elapsedSeconds: Math.round((performance.now() - started) * 1_000) / 1_000_000,
    ...(error ? { errorType: error.name, message: "Article transport failed" } : {}),
  })
}
