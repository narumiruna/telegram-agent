import type { lookup } from "node:dns/promises";

import { describe, expect, it, vi } from "vitest";

import { assertPublicUrl, createPinnedLookup, fetchPublicUrl, isPublicIp } from "../src/actions/url-tool.js";

const options = {
  allowedSchemes: new Set(["http", "https"]),
  maxChars: 100,
  timeoutMs: 1_000,
};

describe("public URL loading", () => {
  it("classifies private, local, and public IP addresses", () => {
    expect(isPublicIp("127.0.0.1")).toBe(false);
    expect(isPublicIp("10.0.0.1")).toBe(false);
    expect(isPublicIp("169.254.169.254")).toBe(false);
    expect(isPublicIp("::1")).toBe(false);
    expect(isPublicIp("fd00::1")).toBe(false);
    expect(isPublicIp("fec0::1")).toBe(false);
    expect(isPublicIp("feff::1")).toBe(false);
    expect(isPublicIp("2001:db8::1")).toBe(false);
    expect(isPublicIp("::ffff:127.0.0.1")).toBe(false);
    expect(isPublicIp("8.8.8.8")).toBe(true);
    expect(isPublicIp("2606:4700:4700::1111")).toBe(true);
  });

  it("rejects unsafe schemes, credentials, and local targets while accepting public IPv6 literals", async () => {
    await expect(assertPublicUrl("file:///etc/passwd")).rejects.toThrow("scheme");
    await expect(assertPublicUrl("https://user:pass@8.8.8.8/")).rejects.toThrow("credentials");
    await expect(assertPublicUrl("http://127.0.0.1/")).rejects.toThrow("Private");
    await expect(assertPublicUrl("http://[::1]/")).rejects.toThrow("Private");
    await expect(assertPublicUrl("http://localhost/")).rejects.toThrow("Local");
    await expect(assertPublicUrl("https://[2606:4700:4700::1111]/")).resolves.toMatchObject({
      hostname: "[2606:4700:4700::1111]",
    });
  });

  it("applies the operation timeout while DNS resolution is pending", async () => {
    const resolve = vi.fn(() => new Promise<never>(() => {})) as unknown as typeof lookup;
    const fetchImplementation = vi.fn(async () => new Response());

    await expect(
      fetchPublicUrl("https://pending.example/", {
        ...options,
        timeoutMs: 10,
        fetchImplementation,
        resolve,
      }),
    ).rejects.toMatchObject({ name: "TimeoutError" });
    expect(fetchImplementation).not.toHaveBeenCalled();
  });

  it("pins requests to the addresses that passed public-IP validation", async () => {
    const pinnedLookup = createPinnedLookup([{ address: "8.8.8.8", family: 4 }]);
    const result = await new Promise<{ address: string; family: number }>((resolve, reject) => {
      pinnedLookup("rebinding.example", { all: false }, (error, address, family) => {
        if (error) {
          reject(error);
          return;
        }
        if (typeof address !== "string") {
          reject(new Error("Expected one pinned address"));
          return;
        }
        resolve({ address, family: family ?? 0 });
      });
    });

    expect(result).toEqual({ address: "8.8.8.8", family: 4 });
  });

  it("extracts bounded text and checks every redirect target", async () => {
    const htmlFetch = vi.fn(async () => {
      return new Response(
        "<html><title>Example &amp; Test</title><body><h1>Hello</h1><script>bad()</script>world</body></html>",
        {
          headers: { "content-type": "text/html; charset=utf-8" },
          status: 200,
        },
      );
    });
    await expect(
      fetchPublicUrl("https://8.8.8.8/page", { ...options, fetchImplementation: htmlFetch }),
    ).resolves.toMatchObject({
      finalUrl: "https://8.8.8.8/page",
      title: "Example & Test",
      text: "Example & Test Hello\n world",
      truncated: false,
    });

    const redirectFetch = vi.fn(async () => {
      return new Response(null, { headers: { location: "http://127.0.0.1/private" }, status: 302 });
    });
    await expect(
      fetchPublicUrl("https://8.8.8.8/redirect", { ...options, fetchImplementation: redirectFetch }),
    ).rejects.toThrow("Private");
  });
});
