import type { Loader } from "../core/loader.js";
import { remainingMilliseconds } from "../core/execution.js";
import { LoaderContentError } from "../core/errors.js";
import type { ImpersSession, ResourceProvider } from "../core/resources.js";
import type { RetrievedHtml } from "../core/retrieval.js";
import { fetchBrowserHtml } from "./browser.js";
import { ensureUsableContent } from "./content-guard.js";
import { htmlToMarkdown } from "./utils.js";

export const DEFAULT_HTTP_HEADERS = {
  "User-Agent":
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
  Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
  "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
} as const;

async function checkedFetch(
  url: string,
  options: { headers?: HeadersInit; resources?: ResourceProvider; signal?: AbortSignal; loaderName: string },
): Promise<Response> {
  let response: Response;
  try {
    response = await (options.resources?.fetch(url, {
      headers: options.headers,
      redirect: "follow",
      signal: options.signal,
    }) ?? fetch(url, { headers: options.headers, redirect: "follow", signal: options.signal }));
  } catch (error) {
    throw new LoaderContentError(options.loaderName, url, `HTTP request failed: ${String(error)}`);
  }
  if (!response.ok) {
    throw new LoaderContentError(options.loaderName, url, `HTTP request failed with status ${response.status}`);
  }
  return response;
}

export async function fetchHttpHtml(
  url: string,
  options: { headers?: HeadersInit; resources?: ResourceProvider; signal?: AbortSignal; loaderName?: string } = {},
): Promise<RetrievedHtml> {
  const response = await checkedFetch(url, {
    ...options,
    loaderName: options.loaderName ?? "HttpLoader",
  });
  return { content: await response.text(), contentType: response.headers.get("content-type") ?? "" };
}

interface GenericLoaderOptions {
  headers?: Record<string, string>;
  resources?: ResourceProvider;
}

export class HttpLoader implements Loader {
  readonly headers: Record<string, string>;
  readonly resources?: ResourceProvider;

  constructor(options: GenericLoaderOptions = {}) {
    this.headers = { ...DEFAULT_HTTP_HEADERS, ...options.headers };
    this.resources = options.resources;
  }

  async load(url: string, signal?: AbortSignal): Promise<string> {
    const response = await fetchHttpHtml(url, {
      headers: this.headers,
      resources: this.resources,
      signal,
      loaderName: "HttpLoader",
    });
    const result = htmlToMarkdown(response.content);
    ensureUsableContent(result, { loaderName: "HttpLoader", url });
    return result;
  }
}

interface CurlCffiLoaderOptions extends GenericLoaderOptions {
  impersonate?: string;
  timeoutSeconds?: number;
}

export { HttpLoader as HttpxLoader };

export class CurlCffiLoader implements Loader {
  readonly impersonate: string;
  readonly timeoutSeconds: number;
  readonly headers?: Record<string, string>;
  readonly resources?: ResourceProvider;

  constructor(options: CurlCffiLoaderOptions = {}) {
    this.impersonate = options.impersonate ?? "chrome";
    this.timeoutSeconds = options.timeoutSeconds ?? 20;
    this.headers = options.headers;
    this.resources = options.resources;
  }

  async load(url: string, signal?: AbortSignal): Promise<string> {
    const response = await fetchImpersHtml(url, {
      impersonate: this.impersonate,
      timeoutSeconds: this.timeoutSeconds,
      headers: this.headers,
      resources: this.resources,
      signal,
      loaderName: "CurlCffiLoader",
    });
    const result = htmlToMarkdown(response.content);
    ensureUsableContent(result, { loaderName: "CurlCffiLoader", url });
    return result;
  }
}

interface ImpersFetchOptions {
  impersonate?: string;
  timeoutSeconds?: number;
  headers?: Record<string, string>;
  resources?: ResourceProvider;
  session?: ImpersSession;
  signal?: AbortSignal;
  loaderName?: string;
}

export async function fetchImpersResponse(url: string, options: ImpersFetchOptions = {}) {
  if (options.signal?.aborted) throw options.signal.reason;
  const loaderName = options.loaderName ?? "CurlCffiLoader";
  const remaining = remainingMilliseconds();
  const timeout = Math.min(
    options.timeoutSeconds ?? 20,
    remaining === undefined ? Number.POSITIVE_INFINITY : remaining / 1_000,
  );
  let owned: ImpersSession | undefined;
  try {
    let session = options.session;
    if (!session) {
      if (options.resources) session = await options.resources.impersSession();
      else {
        const { Session } = await import("impers");
        owned = new Session();
        session = owned;
      }
    }
    const response = await session.get(url, {
      impersonate: options.impersonate ?? "chrome",
      timeout,
      headers: options.headers,
      allowRedirects: true,
    });
    if (response.status >= 400) {
      throw new LoaderContentError(loaderName, url, `HTTP request failed with status ${response.status}`);
    }
    return response;
  } catch (error) {
    if (error instanceof LoaderContentError) throw error;
    throw new LoaderContentError(loaderName, url, `HTTP request failed: ${String(error)}`);
  } finally {
    await owned?.close();
  }
}

export async function fetchImpersHtml(url: string, options: ImpersFetchOptions = {}): Promise<RetrievedHtml> {
  const response = await fetchImpersResponse(url, options);
  return { content: response.text, contentType: response.headers.get("content-type") ?? "" };
}

interface PlaywrightLoaderOptions {
  timeoutMs?: number;
  waitUntil?: "commit" | "domcontentloaded" | "load" | "networkidle";
  headless?: boolean;
  resources?: ResourceProvider;
}

export class PlaywrightLoader implements Loader {
  constructor(private readonly options: PlaywrightLoaderOptions = {}) {}

  async load(url: string, signal?: AbortSignal): Promise<string> {
    const browser = await this.options.resources?.browser();
    const content = await fetchBrowserHtml(url, {
      loaderName: "PlaywrightLoader",
      timeoutMs: this.options.timeoutMs ?? 0,
      timeoutSuggestion:
        "The page took too long to load. Try increasing the timeout or using a faster waitUntil option.",
      ...(this.options.waitUntil ? { waitUntil: this.options.waitUntil } : {}),
      headless: this.options.headless ?? true,
      ...(browser ? { browser } : {}),
      signal,
    });
    const result = htmlToMarkdown(content);
    ensureUsableContent(result, { loaderName: "PlaywrightLoader", url });
    return result;
  }
}
