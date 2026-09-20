import type { Browser, Page, Response } from "playwright"

import { LoaderContentError, LoaderTimeoutError } from "../core/errors.js"
import {
  assertPublicUrl,
  type FetchImplementation,
  readResponseBytes,
  safeFetch,
} from "../core/network.js"
import type { RetrievedHtml } from "../core/retrieval.js"

export const DEFAULT_BROWSER_USER_AGENT =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
export const DEFAULT_BLOCKED_RESOURCE_TYPES = new Set(["font", "image", "media"])
export const DEFAULT_BROWSER_TIMEOUT_MS = 30_000
export const MAX_BROWSER_BYTES = 10 * 1024 * 1024

export type BrowserWaitUntil = "commit" | "domcontentloaded" | "load" | "networkidle"
export type BrowserPageHook = (page: Page) => Promise<void>
export type BrowserContentExtractor = (page: Page) => Promise<string>

function raceWithSignal<T>(operation: Promise<T>, signal: AbortSignal): Promise<T> {
  if (signal.aborted) return Promise.reject(signal.reason)
  return new Promise<T>((resolve, reject) => {
    const onAbort = () => reject(signal.reason)
    signal.addEventListener("abort", onAbort, { once: true })
    operation.then(
      (value) => {
        signal.removeEventListener("abort", onAbort)
        resolve(value)
      },
      (error: unknown) => {
        signal.removeEventListener("abort", onAbort)
        reject(error)
      },
    )
  })
}

interface BrowserFetchOptions {
  loaderName: string
  timeoutMs?: number
  timeoutSuggestion: string
  waitUntil?: BrowserWaitUntil
  userAgent?: string
  headless?: boolean
  blockedResourceTypes?: ReadonlySet<string>
  afterGoto?: BrowserPageHook
  extractContent?: BrowserContentExtractor
  browser?: Browser
  signal?: AbortSignal
  validateUrl?: (url: string | URL, signal?: AbortSignal) => Promise<URL>
  fetchUrl?: FetchImplementation
  maxBytes?: number
}

async function withBrowser(
  browser: Browser,
  url: string,
  options: BrowserFetchOptions,
): Promise<RetrievedHtml> {
  if (options.signal?.aborted) throw options.signal.reason
  const timeoutMs = options.timeoutMs ?? DEFAULT_BROWSER_TIMEOUT_MS
  const timeoutSignal = AbortSignal.timeout(timeoutMs)
  const activeSignal = options.signal
    ? AbortSignal.any([options.signal, timeoutSignal])
    : timeoutSignal
  const validateUrl =
    options.validateUrl ??
    (async (target: string | URL, signal?: AbortSignal) =>
      (await assertPublicUrl(target, { signal })).url)
  await validateUrl(url, activeSignal)
  const fetchUrl = options.fetchUrl ?? safeFetch
  const maxBytes = options.maxBytes ?? MAX_BROWSER_BYTES
  const context = await browser.newContext({
    ...(options.userAgent ? { userAgent: options.userAgent } : {}),
    serviceWorkers: "block",
  })
  try {
    let routeError: unknown
    await context.routeWebSocket("**/*", async (route) =>
      route.close({ code: 1008, reason: "Blocked by kabigon" }),
    )
    await context.route("**/*", async (route) => {
      const request = route.request()
      if (options.blockedResourceTypes?.has(request.resourceType())) {
        await route.abort()
        return
      }
      const requestUrl = request.url()
      if (!requestUrl.startsWith("http://") && !requestUrl.startsWith("https://")) {
        if (request.isNavigationRequest()) await route.abort("blockedbyclient")
        else await route.continue()
        return
      }
      try {
        const postData = request.postDataBuffer()
        const response = await fetchUrl(requestUrl, {
          method: request.method(),
          headers: await request.allHeaders(),
          body: postData ? Uint8Array.from(postData) : undefined,
          redirect: "manual",
          signal: activeSignal,
        })
        const headers = Object.fromEntries(
          [...response.headers].filter(
            ([name]) =>
              !["content-encoding", "content-length", "transfer-encoding"].includes(
                name.toLowerCase(),
              ),
          ),
        )
        await route.fulfill({
          status: response.status,
          headers,
          body: Buffer.from(await readResponseBytes(response, maxBytes)),
        })
      } catch (error) {
        routeError ??= error
        await route.abort("blockedbyclient")
      }
    })
    const page = await context.newPage()
    let response: Response | null
    try {
      response = await page.goto(url, {
        timeout: timeoutMs,
        ...(options.waitUntil ? { waitUntil: options.waitUntil } : {}),
      })
    } catch (error) {
      if (options.signal?.aborted) throw options.signal.reason
      if (timeoutSignal.aborted || (error instanceof Error && error.name === "TimeoutError")) {
        throw new LoaderTimeoutError(
          options.loaderName,
          url,
          timeoutMs / 1_000,
          options.timeoutSuggestion,
        )
      }
      if (routeError) throw routeError
      throw error
    }
    if (routeError) throw routeError
    if (response && response.status() >= 400) {
      throw new LoaderContentError(
        options.loaderName,
        url,
        `HTTP request failed with status ${response.status()}`,
      )
    }
    try {
      return await raceWithSignal(
        (async () => {
          if (options.afterGoto) await options.afterGoto(page)
          const domBytes = await page.evaluate(
            () => new Blob([document.documentElement.outerHTML]).size,
          )
          if (domBytes > maxBytes) {
            throw new LoaderContentError(
              options.loaderName,
              url,
              `Browser DOM exceeds the ${maxBytes} byte limit`,
            )
          }
          const content = options.extractContent
            ? await options.extractContent(page)
            : await page.content()
          if (Buffer.byteLength(content) > maxBytes) {
            throw new LoaderContentError(
              options.loaderName,
              url,
              `Browser content exceeds the ${maxBytes} byte limit`,
            )
          }
          return {
            content,
            contentType: (await response?.headerValue("content-type")) ?? "text/html",
          }
        })(),
        activeSignal,
      )
    } catch (error) {
      if (options.signal?.aborted) throw options.signal.reason
      if (timeoutSignal.aborted) {
        throw new LoaderTimeoutError(
          options.loaderName,
          url,
          timeoutMs / 1_000,
          options.timeoutSuggestion,
        )
      }
      throw error
    }
  } finally {
    await context.close()
  }
}

export async function fetchBrowserHtmlResponse(
  url: string,
  options: BrowserFetchOptions,
): Promise<RetrievedHtml> {
  if (options.browser) return withBrowser(options.browser, url, options)
  const { chromium } = await import("playwright")
  const browser = await chromium.launch({ headless: options.headless ?? true })
  try {
    return await withBrowser(browser, url, options)
  } finally {
    await browser.close()
  }
}

export async function fetchBrowserHtml(url: string, options: BrowserFetchOptions): Promise<string> {
  return (await fetchBrowserHtmlResponse(url, options)).content
}

export async function waitForSelectorIgnoringTimeout(
  page: Page,
  selector: string,
  options: { state?: "attached" | "detached" | "visible" | "hidden"; timeout?: number },
): Promise<void> {
  try {
    await page.waitForSelector(selector, options)
  } catch (error) {
    if (!(error instanceof Error && error.name === "TimeoutError")) throw error
  }
}
