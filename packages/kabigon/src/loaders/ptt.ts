import * as cheerio from "cheerio";

import { LoaderContentError } from "../core/errors.js";
import type { Loader } from "../core/loader.js";
import type { ResourceProvider } from "../core/resources.js";
import { parsePttTarget } from "../sources/applicability.js";
import { fetchHttpHtml } from "./generic.js";
import { htmlToMarkdown } from "./utils.js";

const PTT_HEADERS = {
  "Accept-Language": "zh-TW,zh;q=0.9,ja;q=0.8,en-US;q=0.7,en;q=0.6",
  "User-Agent":
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
  Cookie: "over18=1",
};

export function extractPttPost(html: string, url: string): string {
  const $ = cheerio.load(html);
  const post = $("#main-content").first();
  if (post.length === 0) throw new LoaderContentError("PttLoader", url, "Could not find PTT post body");
  post.find("script,style,noscript,svg").remove();
  const content = htmlToMarkdown($.html(post));
  if (!content) throw new LoaderContentError("PttLoader", url, "PTT post body is empty");
  return content;
}

export class PttLoader implements Loader {
  constructor(private readonly options: { resources?: ResourceProvider } = {}) {}

  async load(url: string, signal?: AbortSignal): Promise<string> {
    parsePttTarget(url);
    const response = await fetchHttpHtml(url, {
      headers: PTT_HEADERS,
      resources: this.options.resources,
      signal,
      loaderName: "PttLoader",
    });
    if (!response.contentType.toLowerCase().includes("html")) {
      throw new LoaderContentError("PttLoader", url, `Expected HTML content, got: ${response.contentType}`);
    }
    return extractPttPost(response.content, url);
  }
}
