import { FirecrawlApiKeyNotSetError, LoaderContentError } from "../core/errors.js";
import type { Loader } from "../core/loader.js";
import type { ResourceProvider } from "../core/resources.js";

export class FirecrawlLoader implements Loader {
  readonly apiKey: string;
  readonly timeoutMs?: number;
  readonly resources?: ResourceProvider;

  constructor(options: { apiKey?: string; timeoutMs?: number; resources?: ResourceProvider } = {}) {
    const apiKey = options.apiKey ?? process.env.FIRECRAWL_API_KEY;
    if (!apiKey) throw new FirecrawlApiKeyNotSetError();
    this.apiKey = apiKey;
    this.timeoutMs = options.timeoutMs;
    this.resources = options.resources;
  }

  async load(url: string, signal?: AbortSignal): Promise<string> {
    const timeoutSignal = this.timeoutMs ? AbortSignal.timeout(this.timeoutMs) : undefined;
    const activeSignal = signal && timeoutSignal ? AbortSignal.any([signal, timeoutSignal]) : (signal ?? timeoutSignal);
    const response = await (this.resources?.fetch("https://api.firecrawl.dev/v1/scrape", {
      method: "POST",
      headers: { Authorization: `Bearer ${this.apiKey}`, "Content-Type": "application/json" },
      body: JSON.stringify({ url, formats: ["markdown"], ...(this.timeoutMs ? { timeout: this.timeoutMs } : {}) }),
      signal: activeSignal,
    }) ??
      fetch("https://api.firecrawl.dev/v1/scrape", {
        method: "POST",
        headers: { Authorization: `Bearer ${this.apiKey}`, "Content-Type": "application/json" },
        body: JSON.stringify({ url, formats: ["markdown"], ...(this.timeoutMs ? { timeout: this.timeoutMs } : {}) }),
        signal: activeSignal,
      }));
    if (!response.ok)
      throw new LoaderContentError("FirecrawlLoader", url, `Firecrawl returned HTTP ${response.status}`);
    const payload = (await response.json()) as Record<string, unknown>;
    if (payload.success === false)
      throw new LoaderContentError("FirecrawlLoader", url, "Firecrawl scrape was unsuccessful");
    const data = payload.data && typeof payload.data === "object" ? (payload.data as Record<string, unknown>) : payload;
    if (typeof data.markdown !== "string") {
      throw new LoaderContentError("FirecrawlLoader", url, "Firecrawl scrape result did not include markdown");
    }
    return data.markdown;
  }
}
