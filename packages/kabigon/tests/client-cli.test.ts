import { describe, expect, it, vi } from "vitest";
import { main, parseArgs } from "../src/cli.js";
import { KabigonClient } from "../src/client.js";

describe("KabigonClient", () => {
  it("validates options and requires explicit lifecycle start", async () => {
    expect(() => new KabigonClient({ deadlineSeconds: 0 })).toThrow("positive");
    expect(() => new KabigonClient({ workerLimit: 0 })).toThrow("limits");
    const client = new KabigonClient();
    await expect(client.loadUrl("https://example.com")).rejects.toThrow("start");
  });

  it("rejects invalid and private targets before planning", async () => {
    const fetchImplementation = vi.fn();
    const client = new KabigonClient({ fetchImplementation }).start();
    await expect(client.loadUrl("not-a-valid-url")).rejects.toThrow("HTTP(S)");
    await expect(client.loadUrl("http://127.0.0.1/private")).rejects.toThrow("Private");
    expect(fetchImplementation).not.toHaveBeenCalled();
    await client.close();
  });

  it("rejects targets whose hostname resolves to a private address", async () => {
    const client = new KabigonClient({
      resolve: async () => [{ address: "10.0.0.1", family: 4 }],
    }).start();
    await expect(client.loadUrl("https://public.example/private")).rejects.toThrow("private");
    await client.close();
  });
});

describe("CLI", () => {
  it("parses explicit loader lists", () => {
    expect(parseArgs(["--loader", "httpx,curl-cffi", "https://example.com"])).toEqual({
      list: false,
      loaderNames: ["httpx", "curl-cffi"],
      url: "https://example.com",
    });
  });

  it("lists only public loaders", async () => {
    const log = vi.spyOn(console, "log").mockImplementation(() => undefined);
    await main(["--list"]);
    const output = log.mock.calls.flat().join("\n");
    log.mockRestore();
    expect(output).toContain("pi-session -");
    expect(output).toContain("curl-cffi -");
    expect(output).not.toContain("playwright-networkidle -");
  });
});
