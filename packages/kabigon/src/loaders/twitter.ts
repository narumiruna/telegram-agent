import type { Page } from "playwright"

import { LoaderContentError, LoaderNotApplicableError } from "../core/errors.js"
import type { Loader } from "../core/loader.js"
import type { ResourceProvider } from "../core/resources.js"
import { parseTwitterTarget, type TwitterTarget } from "../sources/applicability.js"
import {
  DEFAULT_BLOCKED_RESOURCE_TYPES,
  DEFAULT_BROWSER_USER_AGENT,
  fetchBrowserHtml,
  waitForSelectorIgnoringTimeout,
} from "./browser.js"
import { htmlToMarkdown } from "./utils.js"

const FXTWITTER_API = "https://api.fxtwitter.com/status/{statusId}"
type JsonRecord = Record<string, unknown>

function record(value: unknown): JsonRecord | undefined {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonRecord)
    : undefined
}

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : ""
}

function statusId(url: string): string | undefined {
  try {
    return /\/status\/([0-9]+)(?:\/|$)/u.exec(new URL(url, "https://x.com").pathname)?.[1]
  } catch {
    return undefined
  }
}

export function replaceDomain(url: string, newDomain = "x.com"): string {
  const target = parseTwitterTarget(url)
  const parsed = new URL(target.normalizedUrl)
  parsed.hostname = newDomain
  return parsed.toString()
}

export function toFxTwitterApiUrl(statusIdValue: string): string {
  return FXTWITTER_API.replace("{statusId}", statusIdValue)
}

function renderArticleBlock(value: unknown): string | undefined {
  const block = record(value)
  const body = text(block?.text)
  if (!block || !body || block.type === "atomic") return undefined
  switch (block.type) {
    case "header-one":
      return `## ${body}`
    case "header-two":
      return `### ${body}`
    case "header-three":
      return `#### ${body}`
    case "blockquote":
      return body
        .split(/\r?\n/u)
        .map((line) => `> ${line}`)
        .join("\n")
    case "unordered-list-item":
      return `- ${body}`
    case "ordered-list-item":
      return `1. ${body}`
    default:
      return body
  }
}

function renderMedia(tweet: JsonRecord): string[] {
  const all = record(tweet.media)?.all
  if (!Array.isArray(all)) return []
  return all.flatMap((value, index) => {
    const media = record(value)
    const mediaUrl = text(media?.url) || text(media?.thumbnail_url)
    if (!media || !mediaUrl) return []
    const kind = text(media.type) || "media"
    return [`- [${kind} ${index + 1}](${mediaUrl})`]
  })
}

export function renderFxTwitterPayload(
  payload: unknown,
  expectedStatusId: string,
  sourceUrl: string,
): string {
  const envelope = record(payload)
  const tweet = record(envelope?.tweet)
  if (!tweet || text(tweet.id) !== expectedStatusId) {
    throw new LoaderContentError(
      "TwitterLoader",
      sourceUrl,
      "FxTwitter did not return the requested tweet",
    )
  }

  const author = record(tweet.author)
  const authorName = text(author?.name) || "Unknown author"
  const screenName = text(author?.screen_name)
  const lines = [`# ${authorName}${screenName ? ` (@${screenName})` : ""}`, ""]
  if (text(tweet.created_at)) lines.push(`- Published: ${text(tweet.created_at)}`)
  lines.push(`- URL: ${text(tweet.url) || sourceUrl}`)
  for (const [label, field] of [
    ["Replies", "replies"],
    ["Reposts", "retweets"],
    ["Likes", "likes"],
    ["Views", "views"],
  ] as const) {
    if (typeof tweet[field] === "number") lines.push(`- ${label}: ${tweet[field]}`)
  }

  const article = record(tweet.article)
  const tweetText = text(tweet.text)
  if (tweetText) lines.push("", tweetText)
  if (article) {
    const title = text(article.title)
    if (title) lines.push("", `## ${title}`)
    const content = record(article.content)
    const blocks = Array.isArray(content?.blocks)
      ? content.blocks.flatMap((block) => renderArticleBlock(block) ?? [])
      : []
    if (blocks.length > 0) lines.push("", ...blocks)
    else if (text(article.preview_text)) lines.push("", text(article.preview_text))
  }

  const media = renderMedia(tweet)
  if (media.length > 0) lines.push("", "## Media", ...media)
  return lines.join("\n").trim()
}

export class TwitterLoader implements Loader {
  readonly timeoutMs: number
  readonly waitForTweetTimeoutMs: number
  readonly resources?: ResourceProvider

  constructor(
    options: {
      timeoutMs?: number
      waitForTweetTimeoutMs?: number
      resources?: ResourceProvider
    } = {},
  ) {
    this.timeoutMs = options.timeoutMs ?? 20_000
    this.waitForTweetTimeoutMs = options.waitForTweetTimeoutMs ?? 15_000
    this.resources = options.resources
  }

  private async loadViaFxTwitter(target: TwitterTarget, signal?: AbortSignal): Promise<string> {
    if (!target.statusId)
      throw new LoaderNotApplicableError("TwitterLoader", target.url, "Missing status ID")
    const apiUrl = toFxTwitterApiUrl(target.statusId)
    const timeoutSignal = AbortSignal.timeout(this.timeoutMs)
    const activeSignal = signal ? AbortSignal.any([signal, timeoutSignal]) : timeoutSignal
    const response = await (this.resources?.fetch(apiUrl, {
      headers: { Accept: "application/json", "User-Agent": DEFAULT_BROWSER_USER_AGENT },
      redirect: "follow",
      signal: activeSignal,
    }) ??
      fetch(apiUrl, {
        headers: { Accept: "application/json", "User-Agent": DEFAULT_BROWSER_USER_AGENT },
        redirect: "follow",
        signal: activeSignal,
      }))
    if (!response.ok) {
      throw new LoaderContentError(
        "TwitterLoader",
        target.url,
        `FxTwitter returned HTTP ${response.status}`,
      )
    }
    try {
      return renderFxTwitterPayload((await response.json()) as unknown, target.statusId, target.url)
    } catch (error) {
      if (error instanceof LoaderContentError) throw error
      throw new LoaderContentError(
        "TwitterLoader",
        target.url,
        `FxTwitter returned invalid JSON: ${String(error)}`,
      )
    }
  }

  private async loadViaBrowser(target: TwitterTarget, signal?: AbortSignal): Promise<string> {
    if (!target.statusId)
      throw new LoaderNotApplicableError("TwitterLoader", target.url, "Missing status ID")
    const selectors = [
      `article a[href$="/status/${target.statusId}"] time`,
      `article a[href$="/status/${target.statusId}/"] time`,
      `article a[href*="/status/${target.statusId}?"] time`,
      `article a[href*="/status/${target.statusId}#"] time`,
    ]
    const waitForTweet = (page: Page) =>
      waitForSelectorIgnoringTimeout(page, selectors.join(", "), {
        state: "visible",
        timeout: Math.min(this.timeoutMs || this.waitForTweetTimeoutMs, this.waitForTweetTimeoutMs),
      })
    const extractTweet = async (page: Page): Promise<string> => {
      for (const article of await page.locator("article").all()) {
        const permalink = article.locator('a[href*="/status/"]:has(time)').first()
        if ((await permalink.count()) === 0) continue
        const href = await permalink.getAttribute("href")
        if (href && statusId(href) === target.statusId)
          return article.evaluate((element) => element.outerHTML)
      }
      throw new LoaderContentError(
        "TwitterLoader",
        target.normalizedUrl,
        `Could not find the requested tweet (${target.statusId})`,
      )
    }
    const resources = this.resources
    const operation = async () => {
      const browser = await resources?.browser()
      return fetchBrowserHtml(target.normalizedUrl, {
        loaderName: "TwitterLoader",
        timeoutMs: this.timeoutMs,
        timeoutSuggestion:
          "Twitter/X pages can be slow. Try increasing the timeout or check if the page requires login.",
        waitUntil: "domcontentloaded",
        userAgent: DEFAULT_BROWSER_USER_AGENT,
        blockedResourceTypes: DEFAULT_BLOCKED_RESOURCE_TYPES,
        afterGoto: waitForTweet,
        extractContent: extractTweet,
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
    const content = resources ? await resources.runBrowser(operation) : await operation()
    return htmlToMarkdown(content)
  }

  async load(url: string, signal?: AbortSignal): Promise<string> {
    const target = parseTwitterTarget(url)
    if (!target.statusId) {
      throw new LoaderNotApplicableError("TwitterLoader", url, "URL is not a Twitter/X status URL")
    }
    try {
      return await this.loadViaFxTwitter(target, signal)
    } catch (apiError) {
      if (signal?.aborted) throw signal.reason
      try {
        return await this.loadViaBrowser(target, signal)
      } catch (browserError) {
        throw new LoaderContentError(
          "TwitterLoader",
          url,
          `FxTwitter failed: ${String(apiError)}; browser fallback failed: ${String(browserError)}`,
        )
      }
    }
  }
}
