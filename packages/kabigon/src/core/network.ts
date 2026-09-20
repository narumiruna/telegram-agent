import type { LookupAddress } from "node:dns";
import { lookup } from "node:dns/promises";
import { isIP, type LookupFunction } from "node:net";

import ipaddr from "ipaddr.js";
import { Agent, fetch as undiciFetch, type RequestInit as UndiciRequestInit } from "undici";

const REDIRECT_STATUSES = new Set([301, 302, 303, 307, 308]);
const MAX_REDIRECTS = 5;
const SENSITIVE_HEADERS = ["authorization", "cookie", "proxy-authorization"] as const;

export type PublicUrlResolver = (hostname: string, options: { all: true; verbatim: true }) => Promise<LookupAddress[]>;
export type FetchImplementation = (input: string | URL, init?: RequestInit) => Promise<Response>;

const defaultResolver: PublicUrlResolver = (hostname, options) => lookup(hostname, options);
const resolverDispatchers = new WeakMap<PublicUrlResolver, Agent>();

interface PublicUrlOptions {
  resolve?: PublicUrlResolver;
  signal?: AbortSignal;
}

interface SafeFetchOptions extends PublicUrlOptions {
  fetchImplementation?: FetchImplementation;
  maxRedirects?: number;
}

export function isPublicIp(address: string): boolean {
  if (isIP(address) === 0) return false;
  return ipaddr.process(address).range() === "unicast";
}

function validateResolvedAddresses(addresses: readonly LookupAddress[]): void {
  if (addresses.length === 0 || addresses.some((entry) => !isPublicIp(entry.address))) {
    throw new TypeError("URL hostname resolved to a private or non-routable address");
  }
}

function createValidatedLookup(resolve: PublicUrlResolver): LookupFunction {
  return (hostname, options, callback) => {
    resolve(hostname, { all: true, verbatim: true }).then(
      (addresses) => {
        try {
          validateResolvedAddresses(addresses);
          const requestedFamily = options.family === 4 || options.family === "IPv4" ? 4 : 0;
          const family = options.family === 6 || options.family === "IPv6" ? 6 : requestedFamily;
          const compatible = family === 0 ? addresses : addresses.filter((entry) => entry.family === family);
          if (compatible.length === 0) throw new Error(`URL hostname has no IPv${family} address`);
          if (options.all) callback(null, [...compatible]);
          else {
            const selected = compatible[0];
            if (!selected) throw new Error("URL hostname did not resolve to an address");
            callback(null, selected.address, selected.family);
          }
        } catch (error) {
          callback(error instanceof Error ? error : new Error(String(error)), "", 0);
        }
      },
      (error: unknown) => callback(error instanceof Error ? error : new Error(String(error)), "", 0),
    );
  };
}

function dispatcherFor(resolve: PublicUrlResolver): Agent {
  const existing = resolverDispatchers.get(resolve);
  if (existing) return existing;
  const dispatcher = new Agent({ connect: { lookup: createValidatedLookup(resolve) } });
  resolverDispatchers.set(resolve, dispatcher);
  return dispatcher;
}

export async function assertPublicUrl(
  value: string | URL,
  options: PublicUrlOptions = {},
): Promise<{ url: URL; addresses: readonly LookupAddress[] }> {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new TypeError("URL is invalid");
  }
  if (!new Set(["http:", "https:"]).has(url.protocol)) {
    throw new TypeError(`URL scheme is not allowed: ${url.protocol.replace(/:$/u, "")}`);
  }
  if (url.username || url.password) throw new TypeError("URL credentials are not allowed");

  const hostname = url.hostname
    .toLowerCase()
    .replace(/^\[|\]$/gu, "")
    .replace(/\.$/u, "");
  if (!hostname || hostname === "localhost" || hostname.endsWith(".localhost") || hostname.endsWith(".local")) {
    throw new TypeError("Local URL targets are not allowed");
  }

  const family = isIP(hostname);
  if (family !== 0) {
    if (!isPublicIp(hostname)) throw new TypeError("Private or non-routable URL targets are not allowed");
    return { url, addresses: [{ address: hostname, family }] };
  }

  const addresses = await withAbortSignal(
    (options.resolve ?? defaultResolver)(hostname, { all: true, verbatim: true }),
    options.signal,
  );
  validateResolvedAddresses(addresses);
  return { url, addresses };
}

export async function safeFetch(
  input: string | URL,
  init: RequestInit = {},
  options: SafeFetchOptions = {},
): Promise<Response> {
  const maxRedirects = options.maxRedirects ?? MAX_REDIRECTS;
  const signal = options.signal ?? init.signal ?? undefined;
  let current = new URL(input);
  let method = (init.method ?? "GET").toUpperCase();
  let body = init.body;
  const headers = new Headers(init.headers);

  for (let redirects = 0; redirects <= maxRedirects; redirects += 1) {
    await assertPublicUrl(current, { resolve: options.resolve, signal });
    const requestInit = {
      ...init,
      method,
      body,
      headers,
      redirect: "manual" as const,
      signal,
    };
    let response: Response;
    if (options.fetchImplementation) response = await options.fetchImplementation(current, requestInit);
    else {
      try {
        response = (await undiciFetch(current, {
          ...requestInit,
          dispatcher: dispatcherFor(options.resolve ?? defaultResolver),
        } as unknown as UndiciRequestInit)) as unknown as Response;
      } catch (error) {
        if (
          error instanceof Error &&
          error.cause instanceof TypeError &&
          error.cause.message.includes("private or non-routable")
        ) {
          throw error.cause;
        }
        throw error;
      }
    }
    if (!REDIRECT_STATUSES.has(response.status)) return response;
    if (init.redirect === "manual") return response;

    await response.body?.cancel();
    if (init.redirect === "error") throw new Error("URL redirect was not allowed");
    if (redirects === maxRedirects) throw new Error("URL exceeded the redirect limit");
    const location = response.headers.get("location");
    if (!location) throw new Error("URL redirect did not include a Location header");
    const next = new URL(location, current);
    if (next.origin !== current.origin) {
      for (const name of SENSITIVE_HEADERS) headers.delete(name);
    }
    if (response.status === 303 || ((response.status === 301 || response.status === 302) && method === "POST")) {
      method = "GET";
      body = undefined;
      headers.delete("content-length");
      headers.delete("content-type");
    }
    current = next;
  }
  throw new Error("URL redirect handling ended unexpectedly");
}

export async function readResponseBytes(response: Response, maxBytes: number): Promise<Uint8Array> {
  const declaredLength = Number(response.headers.get("content-length"));
  if (Number.isFinite(declaredLength) && declaredLength > maxBytes) {
    await response.body?.cancel();
    throw new Error(`Response exceeds the ${maxBytes} byte limit`);
  }
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
      throw new Error(`Response exceeds the ${maxBytes} byte limit`);
    }
    chunks.push(value);
  }
  return Buffer.concat(chunks.map((chunk) => Buffer.from(chunk)));
}

export async function readResponseText(response: Response, maxBytes: number): Promise<string> {
  return new TextDecoder().decode(await readResponseBytes(response, maxBytes));
}

function withAbortSignal<T>(operation: Promise<T>, signal?: AbortSignal): Promise<T> {
  if (!signal) return operation;
  if (signal.aborted) return Promise.reject(signal.reason);
  return new Promise<T>((resolve, reject) => {
    const onAbort = () => reject(signal.reason);
    signal.addEventListener("abort", onAbort, { once: true });
    operation.then(
      (value) => {
        signal.removeEventListener("abort", onAbort);
        resolve(value);
      },
      (error: unknown) => {
        signal.removeEventListener("abort", onAbort);
        reject(error);
      },
    );
  });
}
