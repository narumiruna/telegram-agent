import type { LookupAddress } from "node:dns";
import { lookup } from "node:dns/promises";
import { isIP, type LookupFunction } from "node:net";

import { Type } from "@earendil-works/pi-ai";
import { defineTool, type ToolDefinition } from "@earendil-works/pi-coding-agent";
import { Agent, type Dispatcher, fetch as undiciFetch } from "undici";

import type { Settings } from "../config/settings.js";

const redirectStatuses = new Set([301, 302, 303, 307, 308]);
const acceptedContentTypes = ["text/", "application/json", "application/xml", "application/xhtml+xml"];

export function buildUrlTools(settings: Settings): ToolDefinition[] {
  if (!settings.botProactiveEnabled) return [];
  return [
    defineTool({
      name: "load_public_url",
      label: "Load public URL",
      description:
        "Load readable text from a public HTTP(S) URL. Use this when the user asks about a URL. Private, local, oversized, non-text, and unsafe redirect targets are rejected.",
      parameters: Type.Object({
        url: Type.String({ description: "The absolute public HTTP(S) URL to load" }),
      }),
      execute: async (_toolCallId, parameters, signal) => {
        const result = await fetchPublicUrl(parameters.url, {
          allowedSchemes: settings.botProactiveAllowedSchemes,
          maxChars: settings.botProactiveMaxExtractedChars,
          timeoutMs: Math.round(settings.botProactiveUrlTimeoutSeconds * 1_000),
          signal,
        });
        return {
          content: [{ type: "text", text: JSON.stringify(result) }],
          details: result,
        };
      },
    }),
  ];
}

export interface FetchedUrl {
  url: string;
  finalUrl: string;
  status: number;
  contentType: string;
  title?: string;
  text: string;
  truncated: boolean;
}

type UrlFetchImplementation = (
  input: string | URL,
  init?: Omit<RequestInit, "dispatcher"> & { dispatcher?: Dispatcher },
) => Promise<Response>;

type PublicUrlResolver = typeof lookup;

interface FetchPublicUrlOptions {
  allowedSchemes: ReadonlySet<string>;
  maxChars: number;
  timeoutMs: number;
  signal?: AbortSignal;
  fetchImplementation?: UrlFetchImplementation;
  resolve?: PublicUrlResolver;
}

interface ResolvedPublicUrl {
  url: URL;
  addresses: LookupAddress[];
}

export async function fetchPublicUrl(urlValue: string, options: FetchPublicUrlOptions): Promise<FetchedUrl> {
  const fetchImplementation = options.fetchImplementation ?? (undiciFetch as unknown as UrlFetchImplementation);
  const timeoutSignal = AbortSignal.timeout(options.timeoutMs);
  const signal = options.signal ? AbortSignal.any([options.signal, timeoutSignal]) : timeoutSignal;
  let currentValue = urlValue;

  for (let redirects = 0; redirects <= 5; redirects += 1) {
    const current = await resolvePublicUrl(currentValue, options.allowedSchemes, options.resolve ?? lookup);
    const dispatcher = new Agent({ connect: { lookup: createPinnedLookup(current.addresses) } });
    try {
      const response = await fetchImplementation(current.url, {
        dispatcher,
        headers: { "user-agent": "telegramagent/0.0 (+public URL text loader)" },
        redirect: "manual",
        signal,
      });
      if (redirectStatuses.has(response.status)) {
        await response.body?.cancel();
        if (redirects === 5) throw new Error("URL exceeded the redirect limit");
        const location = response.headers.get("location");
        if (!location) throw new Error("URL redirect did not include a Location header");
        currentValue = new URL(location, current.url).toString();
        continue;
      }
      if (!response.ok) {
        await response.body?.cancel();
        throw new Error(`URL returned HTTP ${response.status}`);
      }

      const contentType = (response.headers.get("content-type") ?? "").split(";", 1)[0]?.trim().toLowerCase() ?? "";
      if (!acceptedContentTypes.some((accepted) => contentType.startsWith(accepted))) {
        await response.body?.cancel();
        throw new Error(`URL returned unsupported content type: ${contentType || "unknown"}`);
      }
      const bytes = await readBoundedBody(response, Math.max(options.maxChars * 4, 64_000));
      const decoded = new TextDecoder("utf-8", { fatal: false }).decode(bytes);
      const title = contentType.includes("html") ? htmlTitle(decoded) : undefined;
      const extracted = contentType.includes("html") ? htmlToText(decoded) : decoded.trim();
      const truncated = extracted.length > options.maxChars;
      return {
        url: urlValue,
        finalUrl: current.url.toString(),
        status: response.status,
        contentType,
        ...(title ? { title } : {}),
        text: truncated ? `${extracted.slice(0, options.maxChars).trimEnd()}…` : extracted,
        truncated,
      };
    } finally {
      await dispatcher.close();
    }
  }
  throw new Error("URL loader ended unexpectedly");
}

export async function assertPublicUrl(
  urlValue: string,
  allowedSchemes: ReadonlySet<string> = new Set(["http", "https"]),
  resolve: PublicUrlResolver = lookup,
): Promise<URL> {
  return (await resolvePublicUrl(urlValue, allowedSchemes, resolve)).url;
}

async function resolvePublicUrl(
  urlValue: string,
  allowedSchemes: ReadonlySet<string>,
  resolve: PublicUrlResolver,
): Promise<ResolvedPublicUrl> {
  let url: URL;
  try {
    url = new URL(urlValue);
  } catch {
    throw new Error("URL is invalid");
  }
  const scheme = url.protocol.replace(/:$/, "").toLowerCase();
  if (!allowedSchemes.has(scheme)) throw new Error(`URL scheme is not allowed: ${scheme}`);
  if (url.username || url.password) throw new Error("URL credentials are not allowed");
  const hostname = normalizeHostname(url.hostname);
  if (!hostname || hostname === "localhost" || hostname.endsWith(".localhost") || hostname.endsWith(".local")) {
    throw new Error("Local URL targets are not allowed");
  }

  const family = isIP(hostname);
  if (family) {
    if (!isPublicIp(hostname)) throw new Error("Private or non-routable URL targets are not allowed");
    return { url, addresses: [{ address: hostname, family }] };
  }
  const addresses = await resolve(hostname, { all: true, verbatim: true });
  if (addresses.length === 0 || addresses.some((entry) => !isPublicIp(entry.address))) {
    throw new Error("URL hostname resolved to a private or non-routable address");
  }
  return { url, addresses };
}

export function createPinnedLookup(addresses: readonly LookupAddress[]): LookupFunction {
  return (_hostname, options, callback) => {
    const requestedFamily = typeof options.family === "string" ? Number(options.family.slice(-1)) : options.family;
    const candidates = requestedFamily ? addresses.filter((entry) => entry.family === requestedFamily) : [...addresses];
    const selected = candidates[0];
    if (!selected) {
      const error = Object.assign(new Error("No validated address matches the requested family"), {
        code: "ENOTFOUND",
      });
      callback(error, options.all ? [] : "", requestedFamily || 0);
      return;
    }
    if (options.all) {
      callback(null, candidates);
      return;
    }
    callback(null, selected.address, selected.family);
  };
}

function normalizeHostname(hostname: string): string {
  return hostname
    .toLowerCase()
    .replace(/^\[|\]$/g, "")
    .replace(/\.$/, "");
}

export function isPublicIp(address: string): boolean {
  if (address.toLowerCase().startsWith("::ffff:")) return isPublicIp(address.slice(7));
  if (isIP(address) === 4) {
    const octets = address.split(".").map(Number);
    const [first = 0, second = 0] = octets;
    return !(
      first === 0 ||
      first === 10 ||
      first === 127 ||
      (first === 100 && second >= 64 && second <= 127) ||
      (first === 169 && second === 254) ||
      (first === 172 && second >= 16 && second <= 31) ||
      (first === 192 && second === 0) ||
      (first === 192 && second === 168) ||
      (first === 198 && (second === 18 || second === 19)) ||
      first >= 224
    );
  }
  if (isIP(address) === 6) {
    const normalized = address.toLowerCase();
    return !(
      normalized === "::" ||
      normalized === "::1" ||
      normalized.startsWith("fc") ||
      normalized.startsWith("fd") ||
      /^fe[89ab]/u.test(normalized) ||
      normalized.startsWith("ff")
    );
  }
  return false;
}

async function readBoundedBody(response: Response, maxBytes: number): Promise<Uint8Array> {
  const declaredLength = Number(response.headers.get("content-length"));
  if (Number.isFinite(declaredLength) && declaredLength > maxBytes) throw new Error("URL response is too large");
  if (!response.body) return new Uint8Array();

  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > maxBytes) {
      await reader.cancel();
      throw new Error("URL response is too large");
    }
    chunks.push(value);
  }
  return Buffer.concat(chunks.map((chunk) => Buffer.from(chunk)));
}

function htmlTitle(html: string): string | undefined {
  const match = /<title(?:\s[^>]*)?>([\s\S]*?)<\/title>/iu.exec(html);
  return match
    ? decodeHtmlEntities(stripTags(match[1] ?? ""))
        .replace(/\s+/g, " ")
        .trim() || undefined
    : undefined;
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
    .trim();
}

function stripTags(value: string): string {
  return value.replace(/<[^>]+>/g, " ");
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
    .replace(/&#x([0-9a-f]+);/gi, (_match, code: string) => String.fromCodePoint(Number.parseInt(code, 16)));
}
