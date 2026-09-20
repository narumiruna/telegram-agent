import type { Loader } from "../core/loader.js";
import type { ResourceProvider } from "../core/resources.js";
import { parsePttTarget } from "../sources/applicability.js";
import { HttpLoader } from "./generic.js";

export class PttLoader implements Loader {
  private readonly http: HttpLoader;

  constructor(options: { resources?: ResourceProvider } = {}) {
    this.http = new HttpLoader({
      headers: {
        "Accept-Language": "zh-TW,zh;q=0.9,ja;q=0.8,en-US;q=0.7,en;q=0.6",
        "User-Agent":
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        Cookie: "over18=1",
      },
      resources: options.resources,
    });
  }

  async load(url: string, signal?: AbortSignal): Promise<string> {
    parsePttTarget(url);
    return this.http.load(url, signal);
  }
}
