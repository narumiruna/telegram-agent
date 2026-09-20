import type { LookupAddress } from "node:dns"

import { describe, expect, it, vi } from "vitest"

import {
  assertPublicUrl,
  type PublicUrlResolver,
  readResponseBytes,
  safeFetch,
} from "../src/core/network.js"

function resolverFor(addresses: readonly LookupAddress[]): PublicUrlResolver {
  return async () => [...addresses]
}

describe("network safety", () => {
  it.each([
    "http://127.0.0.1/private",
    "http://10.0.0.1/private",
    "http://169.254.169.254/latest/meta-data",
    "http://[::1]/private",
    "http://localhost/private",
  ])("rejects non-public target %s", async (url) => {
    await expect(assertPublicUrl(url)).rejects.toThrow(/Local|Private|non-routable/u)
  })

  it("rejects hostnames that resolve to a private address", async () => {
    const resolve = resolverFor([{ address: "192.168.1.10", family: 4 }])
    await expect(assertPublicUrl("https://public.example/path", { resolve })).rejects.toThrow(
      "private",
    )
  })

  it("rejects DNS rebinding at the connection lookup", async () => {
    let calls = 0
    const resolve: PublicUrlResolver = async () => {
      calls += 1
      return [{ address: calls === 1 ? "93.184.216.34" : "127.0.0.1", family: 4 }]
    }

    await expect(safeFetch("https://rebind.example/path", {}, { resolve })).rejects.toThrow(
      "private",
    )
    expect(calls).toBe(2)
  })

  it("returns redirects without following them in manual mode", async () => {
    const fetchImplementation = vi.fn(
      async () =>
        new Response(null, { status: 302, headers: { location: "https://other.example/final" } }),
    )
    const resolve = resolverFor([{ address: "93.184.216.34", family: 4 }])

    const response = await safeFetch(
      "https://public.example/start",
      { redirect: "manual" },
      { fetchImplementation, resolve },
    )
    expect(response.status).toBe(302)
    expect(fetchImplementation).toHaveBeenCalledTimes(1)
  })

  it("validates every redirect before issuing the next request", async () => {
    const fetchImplementation = vi.fn(
      async () =>
        new Response(null, { status: 302, headers: { location: "http://127.0.0.1/admin" } }),
    )
    const resolve = resolverFor([{ address: "93.184.216.34", family: 4 }])

    await expect(
      safeFetch("https://public.example/start", {}, { fetchImplementation, resolve }),
    ).rejects.toThrow("Private")
    expect(fetchImplementation).toHaveBeenCalledTimes(1)
  })

  it("stops reading a streamed response at the byte limit", async () => {
    const response = new Response(
      new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(new Uint8Array([1, 2]))
          controller.enqueue(new Uint8Array([3, 4]))
          controller.close()
        },
      }),
    )
    await expect(readResponseBytes(response, 3)).rejects.toThrow("3 byte limit")
  })
})
