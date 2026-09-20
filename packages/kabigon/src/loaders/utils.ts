import * as cheerio from "cheerio"
import TurndownService from "turndown"

export function normalizeWhitespace(text: string): string {
  return text
    .split(/\r?\n/u)
    .map((line) => line.trim())
    .filter(Boolean)
    .join("\n")
}

export function htmlToMarkdown(content: string): string {
  const turndown = new TurndownService({
    bulletListMarker: "-",
    codeBlockStyle: "fenced",
    headingStyle: "atx",
  })
  turndown.addRule("plain-links", {
    filter: "a",
    replacement: (innerContent) => innerContent,
  })
  turndown.addRule("drop-images", {
    filter: "img",
    replacement: () => "",
  })
  return normalizeWhitespace(turndown.turndown(content))
}

export function extractFirstTagSubtree(
  html: string,
  tags: readonly string[],
  ignoredSelectors = "script,style,noscript,svg,nav,header,footer",
): string {
  const $ = cheerio.load(html)
  for (const tag of tags) {
    const element = $(tag).first()
    if (element.length > 0) {
      element.find(ignoredSelectors).remove()
      return $.html(element)
    }
  }
  return html
}

function findArticleBody(value: unknown): string | undefined {
  if (Array.isArray(value)) {
    for (const item of value) {
      const found = findArticleBody(item)
      if (found) return found
    }
    return undefined
  }
  if (!value || typeof value !== "object") return undefined
  const record = value as Record<string, unknown>
  if (typeof record.articleBody === "string" && record.articleBody.trim()) return record.articleBody
  for (const item of Object.values(record)) {
    const found = findArticleBody(item)
    if (found) return found
  }
  return undefined
}

export function extractArticleBodyFromJsonLd(html: string): string | undefined {
  const $ = cheerio.load(html)
  for (const script of $('script[type="application/ld+json"]').toArray()) {
    try {
      const body = findArticleBody(JSON.parse($(script).text()))
      if (body) return normalizeWhitespace(body)
    } catch {
      // Ignore malformed metadata and continue to the next JSON-LD block.
    }
  }
  return undefined
}
