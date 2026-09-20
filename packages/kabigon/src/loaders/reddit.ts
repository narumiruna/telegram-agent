import { XMLParser } from "fast-xml-parser";

import { LoaderContentError, LoaderTimeoutError } from "../core/errors.js";
import { remainingMilliseconds } from "../core/execution.js";
import type { Loader } from "../core/loader.js";
import { readResponseText } from "../core/network.js";
import type { ResourceProvider } from "../core/resources.js";
import { parseRedditTarget } from "../sources/applicability.js";
import { DEFAULT_BROWSER_USER_AGENT, fetchBrowserHtml } from "./browser.js";
import { htmlToMarkdown } from "./utils.js";

const REDDIT_SHORT_HOSTS = new Set(["redd.it", "www.redd.it"]);
const USER_AGENT = DEFAULT_BROWSER_USER_AGENT;
export const MAX_REDDIT_BYTES = 10 * 1024 * 1024;

type JsonRecord = Record<string, unknown>;

function endpointPath(parsed: URL): string {
  const path = parsed.pathname.replace(/\/$/u, "");
  if (!REDDIT_SHORT_HOSTS.has(parsed.hostname.toLowerCase())) return path;
  const postId = parsed.pathname.split("/").filter(Boolean)[0];
  return postId ? `/comments/${postId}` : path;
}

function endpoint(url: string, hostname: string, suffix: string): string {
  const parsed = new URL(url);
  const path = endpointPath(parsed);
  parsed.hostname = hostname;
  parsed.pathname = path.endsWith(suffix) ? path : suffix === ".rss" ? `${path}/.rss` : `${path}${suffix}`;
  parsed.search = "";
  parsed.hash = "";
  return parsed.toString();
}

export function convertToOldReddit(url: string): string {
  const parsed = new URL(url);
  const path = endpointPath(parsed);
  parsed.hostname = "old.reddit.com";
  parsed.pathname = path;
  parsed.search = "";
  parsed.hash = "";
  return parsed.toString();
}

export const toRedditJsonUrl = (url: string): string => endpoint(url, "www.reddit.com", ".json");
export const toRedditRssUrl = (url: string): string => endpoint(url, "www.reddit.com", ".rss");

function asRecord(value: unknown): JsonRecord | undefined {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as JsonRecord) : undefined;
}

function listingChildren(value: unknown, apiUrl: string): JsonRecord[] {
  const children = asRecord(asRecord(value)?.data)?.children;
  if (!Array.isArray(children)) throw new LoaderContentError("RedditLoader", apiUrl, "Invalid listing payload.");
  return children.flatMap((child) => (asRecord(child) ? [child as JsonRecord] : []));
}

function postMarkdown(post: JsonRecord): string {
  const title = String(post.title ?? "").trim();
  const permalink = String(post.permalink ?? "").trim();
  const body = String(post.selftext ?? "").trim();
  const externalUrl = String(post.url ?? "").trim();
  const lines = [
    `# ${title || "[untitled]"}`,
    "",
    `- Author: u/${String(post.author ?? "unknown")}`,
    `- Subreddit: r/${String(post.subreddit ?? "unknown")}`,
  ];
  if (post.score !== undefined && post.score !== null) lines.push(`- Score: ${String(post.score)}`);
  if (permalink) lines.push(`- Permalink: https://www.reddit.com${permalink}`);
  if (body) lines.push("", body);
  else if (externalUrl) lines.push("", `External URL: ${externalUrl}`);
  return lines.join("\n");
}

function appendComments(lines: string[], children: JsonRecord[], depth = 0): void {
  const indent = "  ".repeat(depth);
  for (const child of children) {
    if (child.kind !== "t1") continue;
    const data = asRecord(child.data);
    if (!data) continue;
    const score = data.score !== undefined && data.score !== null ? ` (${String(data.score)})` : "";
    lines.push(`${indent}- u/${String(data.author ?? "unknown")}${score}:`);
    const body = String(data.body ?? "").trim();
    lines.push(...(body ? body.split(/\r?\n/u).map((line) => `${indent}  ${line}`) : [`${indent}  [no text]`]));
    const replies = asRecord(data.replies);
    const repliesData = asRecord(replies?.data);
    if (Array.isArray(repliesData?.children)) {
      appendComments(
        lines,
        repliesData.children.flatMap((item) => (asRecord(item) ? [item as JsonRecord] : [])),
        depth + 1,
      );
    }
  }
}

export function rssToMarkdown(xml: string, sourceUrl: string): string {
  let parsed: JsonRecord;
  try {
    parsed = new XMLParser({ ignoreAttributes: false, removeNSPrefix: true }).parse(xml) as JsonRecord;
  } catch (error) {
    throw new LoaderContentError("RedditLoader", sourceUrl, `Invalid RSS XML payload: ${String(error)}`);
  }
  const feed = asRecord(parsed.feed);
  if (!feed) throw new LoaderContentError("RedditLoader", sourceUrl, "Response is not an Atom feed.");
  const entriesValue = feed.entry;
  const entries = Array.isArray(entriesValue) ? entriesValue : entriesValue ? [entriesValue] : [];
  if (entries.length === 0) throw new LoaderContentError("RedditLoader", sourceUrl, "Atom feed has no entries.");
  const lines = [`# ${String(feed.title ?? "Reddit Post")}`, "", `- URL Source: ${sourceUrl}`, "", "## Entries", ""];
  for (const raw of entries) {
    const entry = asRecord(raw) ?? {};
    const author = asRecord(entry.author);
    const contentValue = asRecord(entry.content)?.["#text"] ?? entry.content;
    const content = typeof contentValue === "string" ? htmlToMarkdown(contentValue).trim() : "";
    lines.push(`### ${String(entry.title ?? "[untitled]")}`);
    if (author?.name) lines.push(`- Author: ${String(author.name)}`);
    if (entry.updated) lines.push(`- Updated: ${String(entry.updated)}`);
    lines.push("", content || "(No content)", "");
  }
  return lines.join("\n").trim();
}

export class RedditLoader implements Loader {
  readonly timeoutMs: number;
  readonly resources?: ResourceProvider;

  constructor(options: { timeoutMs?: number; resources?: ResourceProvider } = {}) {
    this.timeoutMs = options.timeoutMs ?? 30_000;
    this.resources = options.resources;
  }

  private signal(signal?: AbortSignal): AbortSignal {
    const remaining = remainingMilliseconds();
    const timeout = AbortSignal.timeout(Math.max(1, Math.ceil(Math.min(this.timeoutMs, remaining ?? this.timeoutMs))));
    return signal ? AbortSignal.any([signal, timeout]) : timeout;
  }

  private async request(url: string, headers: HeadersInit | undefined, signal?: AbortSignal): Promise<Response> {
    try {
      const response = await (this.resources?.fetch(url, {
        headers,
        redirect: "follow",
        signal: this.signal(signal),
      }) ?? fetch(url, { headers, redirect: "follow", signal: this.signal(signal) }));
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response;
    } catch (error) {
      if (error instanceof DOMException && error.name === "TimeoutError") {
        throw new LoaderTimeoutError("RedditLoader", url, this.timeoutMs / 1_000);
      }
      throw new LoaderContentError("RedditLoader", url, `Reddit request failed: ${String(error)}`);
    }
  }

  private async boundedText(response: Response, url: string): Promise<string> {
    try {
      return await readResponseText(response, MAX_REDDIT_BYTES);
    } catch (error) {
      throw new LoaderContentError("RedditLoader", url, `Reddit response could not be read: ${String(error)}`);
    }
  }

  private async loadViaRss(url: string, signal?: AbortSignal): Promise<string> {
    const rssUrl = toRedditRssUrl(url);
    return rssToMarkdown(await this.boundedText(await this.request(rssUrl, undefined, signal), rssUrl), rssUrl);
  }

  private async loadViaJson(url: string, signal?: AbortSignal): Promise<string> {
    const apiUrl = toRedditJsonUrl(url);
    let payload: unknown;
    try {
      payload = JSON.parse(
        await this.boundedText(
          await this.request(apiUrl, { "User-Agent": USER_AGENT, Accept: "application/json" }, signal),
          apiUrl,
        ),
      ) as unknown;
    } catch (error) {
      if (error instanceof LoaderContentError) throw error;
      throw new LoaderContentError("RedditLoader", apiUrl, `Invalid Reddit JSON payload: ${String(error)}`);
    }
    if (!Array.isArray(payload) || payload.length < 1) {
      throw new LoaderContentError("RedditLoader", apiUrl, "Unexpected Reddit JSON payload shape.");
    }
    const posts = listingChildren(payload[0], apiUrl);
    const post = asRecord(posts[0]?.data);
    if (!post) throw new LoaderContentError("RedditLoader", apiUrl, "Post not found in Reddit JSON payload.");
    const comments = payload.length >= 2 ? listingChildren(payload[1], apiUrl) : [];
    const lines = [postMarkdown(post), "", "## Comments", ""];
    if (comments.length > 0) appendComments(lines, comments);
    else lines.push("(No comments)");
    return lines.join("\n").trim();
  }

  private async loadViaBrowser(url: string, signal?: AbortSignal): Promise<string> {
    const resources = this.resources;
    const operation = async () => {
      const browser = await resources?.browser();
      return fetchBrowserHtml(convertToOldReddit(url), {
        loaderName: "RedditLoader",
        timeoutMs: Math.min(this.timeoutMs, remainingMilliseconds() ?? this.timeoutMs),
        timeoutSuggestion: "Reddit pages can be slow to load. Try increasing the timeout.",
        waitUntil: "networkidle",
        userAgent: DEFAULT_BROWSER_USER_AGENT,
        ...(browser ? { browser } : {}),
        ...(resources
          ? {
              validateUrl: resources.validateUrl.bind(resources),
              fetchUrl: resources.fetch.bind(resources),
            }
          : {}),
        signal,
      });
    };
    return htmlToMarkdown(resources ? await resources.runBrowser(operation) : await operation());
  }

  async load(url: string, signal?: AbortSignal): Promise<string> {
    parseRedditTarget(url);
    try {
      return await this.loadViaRss(url, signal);
    } catch (rssError) {
      if (!(rssError instanceof LoaderContentError || rssError instanceof LoaderTimeoutError)) throw rssError;
      try {
        return await this.loadViaJson(url, signal);
      } catch (jsonError) {
        if (!(jsonError instanceof LoaderContentError || jsonError instanceof LoaderTimeoutError)) throw jsonError;
        return this.loadViaBrowser(url, signal);
      }
    }
  }
}
