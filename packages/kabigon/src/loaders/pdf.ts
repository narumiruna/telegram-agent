import { readFile } from "node:fs/promises";

import { LoaderContentError, LoaderNotApplicableError } from "../core/errors.js";
import type { Loader } from "../core/loader.js";
import { readResponseBytes, safeFetch } from "../core/network.js";
import type { ResourceProvider } from "../core/resources.js";
import { parsePdfTarget, requireLoaderApplicability } from "../sources/applicability.js";
import { fetchImpersResponse } from "./generic.js";

export const MAX_PDF_BYTES = 25 * 1024 * 1024;

const DEFAULT_HEADERS = {
  "Accept-Language": "zh-TW,zh;q=0.9,ja;q=0.8,en-US;q=0.7,en;q=0.6",
  "User-Agent":
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
};

export async function readPdfContent(data: Uint8Array): Promise<string> {
  const { PDFParse } = await import("pdf-parse");
  const parser = new PDFParse({ data });
  try {
    const result = await parser.getText();
    return result.text
      .split(/\r?\n/u)
      .map((line) => line.trim())
      .filter(Boolean)
      .join("\n");
  } finally {
    await parser.destroy();
  }
}

export class PdfLoader implements Loader {
  constructor(private readonly options: { resources?: ResourceProvider } = {}) {}

  async load(target: string, signal?: AbortSignal): Promise<string> {
    requireLoaderApplicability("PdfLoader", target, parsePdfTarget);
    if (!target.startsWith("http://") && !target.startsWith("https://")) {
      try {
        return await readPdfContent(await readFile(target));
      } catch (error) {
        throw new LoaderContentError(
          "PdfLoader",
          target,
          `Failed to read local PDF: ${String(error)}`,
          "Check that the file exists and is a valid PDF.",
        );
      }
    }

    let data: Uint8Array;
    let contentType: string;
    try {
      const response = await (this.options.resources?.fetch(target, {
        headers: DEFAULT_HEADERS,
        redirect: "follow",
        signal,
      }) ?? safeFetch(target, { headers: DEFAULT_HEADERS, redirect: "follow", signal }));
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      contentType = response.headers.get("content-type") ?? "";
      data = await readResponseBytes(response, MAX_PDF_BYTES);
    } catch (httpError) {
      try {
        const fallback = await fetchImpersResponse(target, {
          headers: DEFAULT_HEADERS,
          resources: this.options.resources,
          signal,
          loaderName: "PdfLoader",
          maxBytes: MAX_PDF_BYTES,
        });
        contentType = fallback.headers.get("content-type") ?? "";
        data = fallback.content;
      } catch (impersError) {
        throw new LoaderContentError(
          "PdfLoader",
          target,
          `HTTP request failed: ${String(httpError)}; impers fallback failed: ${String(impersError)}`,
          "Check that the URL is accessible and valid.",
        );
      }
    }
    if (!contentType.toLowerCase().includes("application/pdf")) {
      throw new LoaderNotApplicableError("PdfLoader", target, `Not a PDF file (content-type: ${contentType})`);
    }
    try {
      return await readPdfContent(data);
    } catch (error) {
      throw new LoaderContentError(
        "PdfLoader",
        target,
        `Failed to parse PDF: ${String(error)}`,
        "The PDF may be corrupted or use unsupported features.",
      );
    }
  }
}

export { PdfLoader as PDFLoader };
