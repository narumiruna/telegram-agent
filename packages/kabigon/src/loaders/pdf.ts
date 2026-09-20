import { readFile } from "node:fs/promises"

import { LoaderContentError, LoaderNotApplicableError, LoaderTimeoutError } from "../core/errors.js"
import type { Loader } from "../core/loader.js"
import { readResponseBytes, safeFetch } from "../core/network.js"
import type { ResourceProvider } from "../core/resources.js"
import { parsePdfTarget, requireLoaderApplicability } from "../sources/applicability.js"
import { fetchImpersResponse } from "./generic.js"

export const DEFAULT_PDF_TIMEOUT_MS = 20_000
export const MAX_PDF_BYTES = 25 * 1024 * 1024

const DEFAULT_HEADERS = {
  "Accept-Language": "zh-TW,zh;q=0.9,ja;q=0.8,en-US;q=0.7,en;q=0.6",
  "User-Agent":
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
}

export async function readPdfContent(data: Uint8Array): Promise<string> {
  const { PDFParse } = await import("pdf-parse")
  const parser = new PDFParse({ data })
  try {
    const result = await parser.getText()
    return result.text
      .split(/\r?\n/u)
      .map((line) => line.trim())
      .filter(Boolean)
      .join("\n")
  } finally {
    await parser.destroy()
  }
}

export class PdfLoader implements Loader {
  constructor(
    private readonly options: { resources?: ResourceProvider; timeoutMs?: number } = {},
  ) {}

  async load(target: string, signal?: AbortSignal): Promise<string> {
    const parsedTarget = requireLoaderApplicability("PdfLoader", target, parsePdfTarget)
    let remoteUrl: URL | undefined
    try {
      const parsed = new URL(parsedTarget)
      if (["http:", "https:"].includes(parsed.protocol)) remoteUrl = parsed
    } catch {
      // Valid non-URL PDF targets are local paths.
    }
    if (!remoteUrl) {
      try {
        return await readPdfContent(await readFile(parsedTarget))
      } catch (error) {
        throw new LoaderContentError(
          "PdfLoader",
          target,
          `Failed to read local PDF: ${String(error)}`,
          "Check that the file exists and is a valid PDF.",
        )
      }
    }

    const remoteTarget = remoteUrl.toString()
    const timeoutMs = this.options.timeoutMs ?? DEFAULT_PDF_TIMEOUT_MS
    const timeoutSignal = AbortSignal.timeout(timeoutMs)
    const requestSignal = signal ? AbortSignal.any([signal, timeoutSignal]) : timeoutSignal
    let data: Uint8Array
    let contentType: string
    try {
      const response = await (this.options.resources?.fetch(remoteTarget, {
        headers: DEFAULT_HEADERS,
        redirect: "follow",
        signal: requestSignal,
      }) ??
        safeFetch(remoteTarget, {
          headers: DEFAULT_HEADERS,
          redirect: "follow",
          signal: requestSignal,
        }))
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      contentType = response.headers.get("content-type") ?? ""
      data = await readResponseBytes(response, MAX_PDF_BYTES)
    } catch (httpError) {
      if (timeoutSignal.aborted)
        throw new LoaderTimeoutError("PdfLoader", target, timeoutMs / 1_000)
      try {
        const fallback = await fetchImpersResponse(remoteTarget, {
          headers: DEFAULT_HEADERS,
          resources: this.options.resources,
          signal: requestSignal,
          loaderName: "PdfLoader",
          maxBytes: MAX_PDF_BYTES,
        })
        contentType = fallback.headers.get("content-type") ?? ""
        data = fallback.content
      } catch (impersError) {
        if (timeoutSignal.aborted)
          throw new LoaderTimeoutError("PdfLoader", target, timeoutMs / 1_000)
        throw new LoaderContentError(
          "PdfLoader",
          target,
          `HTTP request failed: ${String(httpError)}; impers fallback failed: ${String(impersError)}`,
          "Check that the URL is accessible and valid.",
        )
      }
    }
    if (!contentType.toLowerCase().includes("application/pdf")) {
      throw new LoaderNotApplicableError(
        "PdfLoader",
        target,
        `Not a PDF file (content-type: ${contentType})`,
      )
    }
    try {
      return await readPdfContent(data)
    } catch (error) {
      throw new LoaderContentError(
        "PdfLoader",
        target,
        `Failed to parse PDF: ${String(error)}`,
        "The PDF may be corrupted or use unsupported features.",
      )
    }
  }
}

export { PdfLoader as PDFLoader }
