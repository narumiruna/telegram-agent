import { LoaderContentError, LoaderTimeoutError } from "../core/errors.js";
import { remainingMilliseconds } from "../core/execution.js";
import type { Loader } from "../core/loader.js";
import { assertPublicUrl, readResponseText, safeFetch } from "../core/network.js";
import type { ImpersSession, ResourceProvider } from "../core/resources.js";
import type { RetrievedHtml } from "../core/retrieval.js";
import { DEFAULT_BROWSER_TIMEOUT_MS, fetchBrowserHtml } from "./browser.js";
import { ensureUsableContent } from "./content-guard.js";
import { htmlToMarkdown } from "./utils.js";

export const DEFAULT_HTTP_TIMEOUT_MS = 20_000;
export const DEFAULT_PLAYWRIGHT_TIMEOUT_MS = DEFAULT_BROWSER_TIMEOUT_MS;
export const MAX_HTML_BYTES = 10 * 1024 * 1024;
const SENSITIVE_HEADERS = new Set(["authorization", "cookie", "proxy-authorization"]);

export const DEFAULT_HTTP_HEADERS = {
  "User-Agent":
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
  Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
  "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
} as const;

async function checkedFetch(
  url: string,
  options: {
    headers?: HeadersInit;
    resources?: ResourceProvider;
    signal?: AbortSignal;
    loaderName: string;
    timeoutMs: number;
  },
): Promise<Response> {
  const timeoutSignal = AbortSignal.timeout(options.timeoutMs);
  const signal = options.signal ? AbortSignal.any([options.signal, timeoutSignal]) : timeoutSignal;
  let response: Response;
  try {
    response = await (options.resources?.fetch(url, {
      headers: options.headers,
      redirect: "follow",
      signal,
    }) ?? safeFetch(url, { headers: options.headers, redirect: "follow", signal }));
  } catch (error) {
    if (timeoutSignal.aborted) throw new LoaderTimeoutError(options.loaderName, url, options.timeoutMs / 1_000);
    throw new LoaderContentError(options.loaderName, url, `HTTP request failed: ${String(error)}`);
  }
  if (!response.ok) {
    throw new LoaderContentError(options.loaderName, url, `HTTP request failed with status ${response.status}`);
  }
  return response;
}

export async function fetchHttpHtml(
  url: string,
  options: {
    headers?: HeadersInit;
    resources?: ResourceProvider;
    signal?: AbortSignal;
    loaderName?: string;
    timeoutMs?: number;
    maxBytes?: number;
  } = {},
): Promise<RetrievedHtml> {
  const response = await checkedFetch(url, {
    ...options,
    loaderName: options.loaderName ?? "HttpLoader",
    timeoutMs: options.timeoutMs ?? DEFAULT_HTTP_TIMEOUT_MS,
  });
  return {
    content: await readResponseText(response, options.maxBytes ?? MAX_HTML_BYTES),
    contentType: response.headers.get("content-type") ?? "",
  };
}

interface GenericLoaderOptions {
  headers?: Record<string, string>;
  resources?: ResourceProvider;
  timeoutMs?: number;
}

export class HttpLoader implements Loader {
  readonly headers: Record<string, string>;
  readonly resources?: ResourceProvider;
  readonly timeoutMs: number;

  constructor(options: GenericLoaderOptions = {}) {
    this.headers = { ...DEFAULT_HTTP_HEADERS, ...options.headers };
    this.resources = options.resources;
    this.timeoutMs = options.timeoutMs ?? DEFAULT_HTTP_TIMEOUT_MS;
  }

  async load(url: string, signal?: AbortSignal): Promise<string> {
    const response = await fetchHttpHtml(url, {
      headers: this.headers,
      resources: this.resources,
      signal,
      loaderName: "HttpLoader",
      timeoutMs: this.timeoutMs,
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
  maxBytes?: number;
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

    let currentUrl = new URL(url);
    const requestHeaders = { ...options.headers };
    for (let redirects = 0; redirects <= 5; redirects += 1) {
      if (options.resources) await options.resources.validateUrl(currentUrl, options.signal);
      else await assertPublicUrl(currentUrl, { signal: options.signal });
      const maxBytes = options.maxBytes;
      const chunks: Buffer[] = [];
      let total = 0;
      let exceededLimit = false;
      const limitController = maxBytes === undefined ? undefined : new AbortController();
      const requestSignal =
        options.signal && limitController
          ? AbortSignal.any([options.signal, limitController.signal])
          : (options.signal ?? limitController?.signal);
      let response: Awaited<ReturnType<ImpersSession["get"]>>;
      try {
        response = await session.get(currentUrl.toString(), {
          impersonate: options.impersonate ?? "chrome",
          timeout,
          headers: requestHeaders,
          allowRedirects: false,
          signal: requestSignal,
          stream: maxBytes !== undefined,
          ...(maxBytes === undefined
            ? {}
            : {
                acceptEncoding: "identity",
                contentCallback: (chunk: Buffer) => {
                  total += chunk.byteLength;
                  if (total > maxBytes) {
                    exceededLimit = true;
                    limitController?.abort(new Error(`Response exceeds the ${maxBytes} byte limit`));
                    return;
                  }
                  chunks.push(Buffer.from(chunk));
                },
              }),
        });
      } catch (error) {
        if (exceededLimit) {
          throw new LoaderContentError(loaderName, url, `Response exceeds the ${maxBytes} byte limit`);
        }
        throw error;
      }
      if ([301, 302, 303, 307, 308].includes(response.status)) {
        const location = response.headers.get("location");
        await response.close();
        if (redirects === 5) throw new LoaderContentError(loaderName, url, "URL exceeded the redirect limit");
        if (!location) throw new LoaderContentError(loaderName, url, "URL redirect did not include a Location header");
        const nextUrl = new URL(location, currentUrl);
        if (nextUrl.origin !== currentUrl.origin) {
          for (const name of Object.keys(requestHeaders)) {
            if (SENSITIVE_HEADERS.has(name.toLowerCase())) delete requestHeaders[name];
          }
        }
        currentUrl = nextUrl;
        continue;
      }
      if (response.status >= 400) {
        await response.close();
        throw new LoaderContentError(loaderName, url, `HTTP request failed with status ${response.status}`);
      }
      if (maxBytes !== undefined) {
        const declaredLength = Number(response.headers.get("content-length"));
        if (Number.isFinite(declaredLength) && declaredLength > maxBytes) {
          await response.close();
          throw new LoaderContentError(loaderName, url, `Response exceeds the ${maxBytes} byte limit`);
        }
        response.setContent(Buffer.concat(chunks));
      }
      return response;
    }
    throw new LoaderContentError(loaderName, url, "URL redirect handling ended unexpectedly");
  } catch (error) {
    if (error instanceof LoaderContentError) throw error;
    throw new LoaderContentError(loaderName, url, `HTTP request failed: ${String(error)}`);
  } finally {
    await owned?.close();
  }
}

export async function fetchImpersHtml(url: string, options: ImpersFetchOptions = {}): Promise<RetrievedHtml> {
  const response = await fetchImpersResponse(url, { ...options, maxBytes: options.maxBytes ?? MAX_HTML_BYTES });
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
    const resources = this.options.resources;
    const browser = await resources?.browser();
    const content = await fetchBrowserHtml(url, {
      loaderName: "PlaywrightLoader",
      timeoutMs: this.options.timeoutMs ?? DEFAULT_PLAYWRIGHT_TIMEOUT_MS,
      timeoutSuggestion:
        "The page took too long to load. Try increasing the timeout or using a faster waitUntil option.",
      ...(this.options.waitUntil ? { waitUntil: this.options.waitUntil } : {}),
      headless: this.options.headless ?? true,
      ...(browser ? { browser } : {}),
      ...(resources
        ? {
            validateUrl: resources.validateUrl.bind(resources),
            fetchUrl: resources.fetch.bind(resources),
          }
        : {}),
      signal,
    });
    const result = htmlToMarkdown(content);
    ensureUsableContent(result, { loaderName: "PlaywrightLoader", url });
    return result;
  }
}
