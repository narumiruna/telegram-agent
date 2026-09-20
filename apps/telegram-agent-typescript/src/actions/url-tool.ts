import type { LookupAddress } from "node:dns"
import { lookup } from "node:dns/promises"
import { isIP, type LookupFunction } from "node:net"

import { Type } from "@earendil-works/pi-ai"
import { defineTool, type ToolDefinition } from "@earendil-works/pi-coding-agent"
import {
  isTwitterStatusUrl,
  isYouTubeVideoUrl,
  type LoadResult as KabigonLoadResult,
  loadUrlDetailed,
} from "@telegram-agent/kabigon"
import ipaddr from "ipaddr.js"
import { Agent, type Dispatcher, fetch as undiciFetch } from "undici"

import type { Settings } from "../config/settings.js"

const redirectStatuses = new Set([301, 302, 303, 307, 308])
const acceptedContentTypes = [
  "text/",
  "application/json",
  "application/xml",
  "application/xhtml+xml",
]
const blockerPhrases = [
  "javascript is not available",
  "javascript is disabled in this browser",
  "please wait for verification",
  "verify you are a human",
  "verify you're a human",
]

export function buildUrlTools(settings: Settings): ToolDefinition[] {
  if (!settings.botProactiveEnabled) return []
  return [
    defineTool({
      name: "load_public_url",
      label: "Load public URL",
      description:
        "Load readable text or Markdown from a public HTTP(S) URL. The bounded built-in loader is tried first, then kabigon handles source-specific or blocked content. Private, local, oversized, and unsafe redirect targets are rejected.",
      parameters: Type.Object({
        url: Type.String({ description: "The absolute public HTTP(S) URL to load" }),
      }),
      execute: async (_toolCallId, parameters, signal) => {
        const result = await loadPublicUrl(parameters.url, {
          allowedSchemes: settings.botProactiveAllowedSchemes,
          maxChars: settings.botProactiveMaxExtractedChars,
          timeoutMs: Math.round(settings.botProactiveUrlTimeoutSeconds * 1_000),
          kabigonTimeoutSeconds: settings.botKabigonTimeoutSeconds,
          signal,
        })
        return {
          content: [{ type: "text", text: JSON.stringify(result) }],
          details: result,
        }
      },
    }),
  ]
}

export interface LoadedUrl {
  url: string
  finalUrl: string
  source: "built-in" | "kabigon"
  contentType: string
  title?: string
  text: string
  truncated: boolean
  status?: number
  loaderId?: string
}

export interface FetchedUrl {
  url: string
  finalUrl: string
  status: number
  contentType: string
  title?: string
  text: string
  truncated: boolean
}

type UrlFetchImplementation = (
  input: string | URL,
  init?: Omit<RequestInit, "dispatcher"> & { dispatcher?: Dispatcher },
) => Promise<Response>

type PublicUrlResolver = typeof lookup

interface FetchPublicUrlOptions {
  allowedSchemes: ReadonlySet<string>
  maxChars: number
  timeoutMs: number
  signal?: AbortSignal
  fetchImplementation?: UrlFetchImplementation
  resolve?: PublicUrlResolver
}

type KabigonLoadImplementation = (
  url: string,
  options: { deadlineSeconds?: number; signal?: AbortSignal },
) => Promise<KabigonLoadResult>

interface LoadPublicUrlOptions extends FetchPublicUrlOptions {
  kabigonTimeoutSeconds: number
  kabigonLoadImplementation?: KabigonLoadImplementation
}

interface ResolvedPublicUrl {
  url: URL
  addresses: LookupAddress[]
}

export async function loadPublicUrl(
  urlValue: string,
  options: LoadPublicUrlOptions,
): Promise<LoadedUrl> {
  const validationTimeout = AbortSignal.timeout(options.timeoutMs)
  const validationSignal = options.signal
    ? AbortSignal.any([options.signal, validationTimeout])
    : validationTimeout
  await assertPublicUrl(
    urlValue,
    options.allowedSchemes,
    options.resolve ?? lookup,
    validationSignal,
  )

  let builtInError: unknown
  try {
    const result = await fetchPublicUrl(urlValue, options)
    if (!requiresKabigon(urlValue, result)) {
      return {
        url: result.url,
        finalUrl: result.finalUrl,
        source: "built-in",
        contentType: result.contentType,
        ...(result.title ? { title: result.title } : {}),
        text: result.text,
        truncated: result.truncated,
        status: result.status,
      }
    }
    builtInError = new Error("The built-in loader did not extract source-specific content")
  } catch (error) {
    builtInError = error
  }

  try {
    const load = options.kabigonLoadImplementation ?? loadUrlDetailed
    const result = await load(urlValue, {
      deadlineSeconds: options.kabigonTimeoutSeconds,
      ...(options.signal ? { signal: options.signal } : {}),
    })
    const content = result.content.trim()
    if (!content) throw new Error("kabigon returned no content")
    const truncated = content.length > options.maxChars
    return {
      url: urlValue,
      finalUrl: urlValue,
      source: "kabigon",
      contentType: result.contentType,
      text: truncated
        ? `${content.slice(0, options.maxChars)}\n\n[truncated by telegramagent: ${content.length} -> ${options.maxChars} chars]`
        : content,
      truncated,
      loaderId: result.loaderId,
    }
  } catch (kabigonError) {
    throw new AggregateError(
      [builtInError, kabigonError],
      "Built-in and kabigon URL loading both failed",
    )
  }
}

export async function fetchPublicUrl(
  urlValue: string,
  options: FetchPublicUrlOptions,
): Promise<FetchedUrl> {
  const fetchImplementation =
    options.fetchImplementation ?? (undiciFetch as unknown as UrlFetchImplementation)
  const timeoutSignal = AbortSignal.timeout(options.timeoutMs)
  const signal = options.signal ? AbortSignal.any([options.signal, timeoutSignal]) : timeoutSignal
  let currentValue = urlValue

  for (let redirects = 0; redirects <= 5; redirects += 1) {
    const current = await resolvePublicUrl(
      currentValue,
      options.allowedSchemes,
      options.resolve ?? lookup,
      signal,
    )
    const dispatcher = new Agent({ connect: { lookup: createPinnedLookup(current.addresses) } })
    try {
      const response = await fetchImplementation(current.url, {
        dispatcher,
        headers: { "user-agent": "telegramagent/0.0 (+public URL text loader)" },
        redirect: "manual",
        signal,
      })
      if (redirectStatuses.has(response.status)) {
        await response.body?.cancel()
        if (redirects === 5) throw new Error("URL exceeded the redirect limit")
        const location = response.headers.get("location")
        if (!location) throw new Error("URL redirect did not include a Location header")
        currentValue = new URL(location, current.url).toString()
        continue
      }
      if (!response.ok) {
        await response.body?.cancel()
        throw new Error(`URL returned HTTP ${response.status}`)
      }

      const contentType =
        (response.headers.get("content-type") ?? "").split(";", 1)[0]?.trim().toLowerCase() ?? ""
      if (!acceptedContentTypes.some((accepted) => contentType.startsWith(accepted))) {
        await response.body?.cancel()
        throw new Error(`URL returned unsupported content type: ${contentType || "unknown"}`)
      }
      const bytes = await readBoundedBody(response, Math.max(options.maxChars * 4, 64_000))
      const decoded = new TextDecoder("utf-8", { fatal: false }).decode(bytes)
      const title = contentType.includes("html") ? htmlTitle(decoded) : undefined
      const extracted = contentType.includes("html") ? htmlToText(decoded) : decoded.trim()
      const truncated = extracted.length > options.maxChars
      return {
        url: urlValue,
        finalUrl: current.url.toString(),
        status: response.status,
        contentType,
        ...(title ? { title } : {}),
        text: truncated ? `${extracted.slice(0, options.maxChars).trimEnd()}…` : extracted,
        truncated,
      }
    } finally {
      await dispatcher.close()
    }
  }
  throw new Error("URL loader ended unexpectedly")
}

function requiresKabigon(urlValue: string, result: FetchedUrl): boolean {
  if (!result.title && !result.text.trim()) return true
  if (
    isYouTubeVideoUrl(urlValue) ||
    isYouTubeVideoUrl(result.finalUrl) ||
    isTwitterStatusUrl(urlValue) ||
    isTwitterStatusUrl(result.finalUrl)
  ) {
    return true
  }
  const content = `${result.title ?? ""} ${result.text}`.toLowerCase()
  return blockerPhrases.some((phrase) => content.includes(phrase))
}

export async function assertPublicUrl(
  urlValue: string,
  allowedSchemes: ReadonlySet<string> = new Set(["http", "https"]),
  resolve: PublicUrlResolver = lookup,
  signal?: AbortSignal,
): Promise<URL> {
  return (await resolvePublicUrl(urlValue, allowedSchemes, resolve, signal)).url
}

async function resolvePublicUrl(
  urlValue: string,
  allowedSchemes: ReadonlySet<string>,
  resolve: PublicUrlResolver,
  signal?: AbortSignal,
): Promise<ResolvedPublicUrl> {
  let url: URL
  try {
    url = new URL(urlValue)
  } catch {
    throw new Error("URL is invalid")
  }
  const scheme = url.protocol.replace(/:$/, "").toLowerCase()
  if (!allowedSchemes.has(scheme)) throw new Error(`URL scheme is not allowed: ${scheme}`)
  if (url.username || url.password) throw new Error("URL credentials are not allowed")
  const hostname = normalizeHostname(url.hostname)
  if (
    !hostname ||
    hostname === "localhost" ||
    hostname.endsWith(".localhost") ||
    hostname.endsWith(".local")
  ) {
    throw new Error("Local URL targets are not allowed")
  }

  const family = isIP(hostname)
  if (family) {
    if (!isPublicIp(hostname))
      throw new Error("Private or non-routable URL targets are not allowed")
    return { url, addresses: [{ address: hostname, family }] }
  }
  const addresses = await withAbortSignal(resolve(hostname, { all: true, verbatim: true }), signal)
  if (addresses.length === 0 || addresses.some((entry) => !isPublicIp(entry.address))) {
    throw new Error("URL hostname resolved to a private or non-routable address")
  }
  return { url, addresses }
}

export function createPinnedLookup(addresses: readonly LookupAddress[]): LookupFunction {
  return (_hostname, options, callback) => {
    const requestedFamily =
      typeof options.family === "string" ? Number(options.family.slice(-1)) : options.family
    const candidates = requestedFamily
      ? addresses.filter((entry) => entry.family === requestedFamily)
      : [...addresses]
    const selected = candidates[0]
    if (!selected) {
      const error = Object.assign(new Error("No validated address matches the requested family"), {
        code: "ENOTFOUND",
      })
      callback(error, options.all ? [] : "", requestedFamily || 0)
      return
    }
    if (options.all) {
      callback(null, candidates)
      return
    }
    callback(null, selected.address, selected.family)
  }
}

function normalizeHostname(hostname: string): string {
  return hostname
    .toLowerCase()
    .replace(/^\[|\]$/g, "")
    .replace(/\.$/, "")
}

export function isPublicIp(address: string): boolean {
  if (isIP(address) === 0) return false
  return ipaddr.process(address).range() === "unicast"
}

function withAbortSignal<T>(operation: Promise<T>, signal?: AbortSignal): Promise<T> {
  if (!signal) return operation
  if (signal.aborted)
    return Promise.reject(
      signal.reason ?? new DOMException("The operation was aborted", "AbortError"),
    )

  return new Promise((resolve, reject) => {
    const onAbort = () =>
      reject(signal.reason ?? new DOMException("The operation was aborted", "AbortError"))
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

async function readBoundedBody(response: Response, maxBytes: number): Promise<Uint8Array> {
  const declaredLength = Number(response.headers.get("content-length"))
  if (Number.isFinite(declaredLength) && declaredLength > maxBytes)
    throw new Error("URL response is too large")
  if (!response.body) return new Uint8Array()

  const reader = response.body.getReader()
  const chunks: Uint8Array[] = []
  let total = 0
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    total += value.byteLength
    if (total > maxBytes) {
      await reader.cancel()
      throw new Error("URL response is too large")
    }
    chunks.push(value)
  }
  return Buffer.concat(chunks.map((chunk) => Buffer.from(chunk)))
}

function htmlTitle(html: string): string | undefined {
  const match = /<title(?:\s[^>]*)?>([\s\S]*?)<\/title>/iu.exec(html)
  return match
    ? decodeHtmlEntities(stripTags(match[1] ?? ""))
        .replace(/\s+/g, " ")
        .trim() || undefined
    : undefined
}

function htmlToText(html: string): string {
  return decodeHtmlEntities(
    stripTags(
      html
        .replace(/<(script|style|noscript|svg)(?:\s[^>]*)?>[\s\S]*?<\/\1>/giu, " ")
        .replace(/<(br|\/p|\/div|\/li|\/h[1-6])\s*\/?>/giu, "\n"),
    ),
  )
    .replace(/[ \t]+/g, " ")
    .replace(/\n\s*\n\s*\n+/g, "\n\n")
    .trim()
}

function stripTags(value: string): string {
  return value.replace(/<[^>]+>/g, " ")
}

function decodeHtmlEntities(value: string): string {
  return value
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&quot;/gi, '"')
    .replace(/&#39;|&apos;/gi, "'")
    .replace(/&#(\d+);/g, (_match, code: string) => String.fromCodePoint(Number(code)))
    .replace(/&#x([0-9a-f]+);/gi, (_match, code: string) =>
      String.fromCodePoint(Number.parseInt(code, 16)),
    )
}
