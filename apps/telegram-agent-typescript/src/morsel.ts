import { Type } from "@earendil-works/pi-ai"
import { defineTool, type ToolDefinition } from "@earendil-works/pi-coding-agent"

import type { Settings } from "./config/settings.js"
import type { Logger } from "./logging.js"

const shareCapabilityPattern = /^[A-Za-z0-9_-]{43}$/u
const telegramRhashPattern = /^[A-Za-z0-9_-]{1,128}$/u

export class MorselPublishError extends Error {}

export class MorselPublisher {
  readonly #baseUrl: URL

  constructor(
    baseUrl: string,
    private readonly apiKey: string | undefined,
    private readonly options: {
      timeoutMs: number
      expiresInSeconds: number
      telegramInstantView: boolean
      telegramInstantViewRhash?: string
      fetchImplementation?: typeof fetch
    },
  ) {
    this.#baseUrl = validateMorselOrigin(baseUrl)
    if (
      options.telegramInstantViewRhash &&
      !telegramRhashPattern.test(options.telegramInstantViewRhash)
    ) {
      throw new Error("TELEGRAM_INSTANT_VIEW_RHASH must contain 1-128 URL-safe characters")
    }
  }

  get isConfigured(): boolean {
    return Boolean(this.apiKey)
  }

  async publish(content: string): Promise<string> {
    if (!this.apiKey) throw new MorselPublishError("MORSEL_API_KEY is not configured")
    const payload: Record<string, unknown> = {
      content,
      preview: previewMetadata(content),
      ...(this.options.telegramInstantView
        ? { telegram_instant_view: true }
        : { expires_in: this.options.expiresInSeconds }),
    }
    const fetchImplementation = this.options.fetchImplementation ?? fetch
    let response: Response
    try {
      response = await fetchImplementation(new URL("/v1/shares", this.#baseUrl), {
        body: JSON.stringify(payload),
        headers: {
          authorization: `Bearer ${this.apiKey}`,
          "content-type": "application/json",
        },
        method: "POST",
        redirect: "manual",
        signal: AbortSignal.timeout(this.options.timeoutMs),
      })
    } catch (error) {
      throw new MorselPublishError("Failed to create Morsel share", { cause: error })
    }
    if (response.status !== 201)
      throw new MorselPublishError(`Morsel share creation failed with HTTP ${response.status}`)
    const bytes = await readBoundedResponse(response, 65_536)
    let metadata: unknown
    try {
      metadata = JSON.parse(new TextDecoder().decode(bytes))
    } catch (error) {
      throw new MorselPublishError("Morsel returned invalid share metadata", { cause: error })
    }
    if (!isShareMetadata(metadata))
      throw new MorselPublishError("Morsel returned invalid share metadata")
    const shareUrl = validateShareUrl(metadata.share_url, this.#baseUrl)
    if (this.options.telegramInstantView && this.options.telegramInstantViewRhash) {
      return `${shareUrl}?tg_rhash=${this.options.telegramInstantViewRhash}`
    }
    return shareUrl
  }
}

export function createMorselPublisher(settings: Settings): MorselPublisher {
  return new MorselPublisher(settings.morselUrl, settings.morselApiKey, {
    timeoutMs: Math.round(settings.morselTimeoutSeconds * 1_000),
    expiresInSeconds: settings.morselShareExpiresInSeconds,
    telegramInstantView: settings.morselTelegramInstantView,
    ...(settings.morselTelegramInstantViewRhash
      ? { telegramInstantViewRhash: settings.morselTelegramInstantViewRhash }
      : {}),
  })
}

export function buildMorselTools(
  publisher: MorselPublisher,
  mode: Settings["morselMode"],
  logger: Logger,
): ToolDefinition[] {
  if (mode === "disabled" || !publisher.isConfigured) return []
  return [
    defineTool({
      name: "publish_markdown_to_morsel",
      label: "Publish Markdown to Morsel",
      description:
        "Publish a complete Markdown answer for rich Mermaid, Vega-Lite, or LaTeX rendering. Pass the complete answer exactly once, then return only the share URL when successful.",
      parameters: Type.Object({ content: Type.String({ minLength: 1 }) }),
      executionMode: "sequential",
      execute: async (_toolCallId, parameters) => {
        try {
          const shareUrl = await publisher.publish(parameters.content)
          return {
            content: [
              {
                type: "text",
                text: JSON.stringify({
                  status: "published",
                  share_url: shareUrl,
                  response_contract:
                    "Return the share_url with at most a brief introduction. Do not repeat the Markdown.",
                }),
              },
            ],
            details: morselToolDetails("published", shareUrl),
          }
        } catch (error) {
          logger.warn("Morsel rich publication failed", error)
          return {
            content: [
              {
                type: "text",
                text: JSON.stringify({
                  status: "error",
                  error: "Morsel publishing is unavailable.",
                  response_contract:
                    "Do not claim publication succeeded. Return Telegram-readable plain text without raw rich markup.",
                }),
              },
            ],
            details: morselToolDetails("error", ""),
          }
        }
      },
    }),
  ]
}

function morselToolDetails(
  status: "published" | "error",
  shareUrl: string,
): { status: string; shareUrl: string } {
  return { status, shareUrl }
}

function validateMorselOrigin(value: string): URL {
  const url = new URL(value)
  const loopback = ["localhost", "127.0.0.1", "[::1]", "::1"].includes(url.hostname)
  if (
    !["http:", "https:"].includes(url.protocol) ||
    (url.protocol !== "https:" && !loopback) ||
    url.username ||
    url.password ||
    (url.pathname !== "/" && url.pathname !== "") ||
    url.search ||
    url.hash
  ) {
    throw new Error(
      "MORSEL_URL must be a secure HTTP(S) origin without credentials, path, query, or fragment",
    )
  }
  return url
}

function validateShareUrl(value: string, base: URL): string {
  if (hasAsciiControl(value)) throw new MorselPublishError("Morsel returned an invalid share URL")
  let share: URL
  try {
    share = new URL(value)
  } catch {
    throw new MorselPublishError("Morsel returned an invalid share URL")
  }
  if (share.origin !== base.origin || share.username || share.password) {
    throw new MorselPublishError("Morsel returned an invalid share URL")
  }
  const pathCapability = share.pathname.startsWith("/s/") ? share.pathname.slice(3) : ""
  const fragmentCapability = share.hash.startsWith("#/s/") ? share.hash.slice(4) : ""
  const validPath = shareCapabilityPattern.test(pathCapability) && !share.search && !share.hash
  const validFragment =
    ["", "/"].includes(share.pathname) &&
    !share.search &&
    shareCapabilityPattern.test(fragmentCapability)
  if (!validPath && !validFragment)
    throw new MorselPublishError("Morsel returned an invalid share URL")
  return share.toString()
}

function hasAsciiControl(value: string): boolean {
  return Array.from(value).some((character) => {
    const codePoint = character.codePointAt(0) ?? 0
    return codePoint < 0x20 || codePoint === 0x7f
  })
}

function previewMetadata(text: string): { title: string; description: string } {
  const source = Buffer.from(text).subarray(0, 4_096).toString("utf8")
  const description = plainSingleLine(source).slice(0, 200) || "Shared with Morsel."
  const title =
    source
      .split("\n")
      .map((line) => plainSingleLine(line.replace(/^[#>*+_`~\s-]+/u, "")))
      .find(Boolean)
      ?.slice(0, 80) ?? "Morsel"
  return { title, description }
}

function plainSingleLine(value: string): string {
  return Array.from(value)
    .map((character) => {
      const codePoint = character.codePointAt(0) ?? 0
      return codePoint < 0x20 || codePoint === 0x2028 || codePoint === 0x2029 ? " " : character
    })
    .join("")
    .replace(/\s+/g, " ")
    .trim()
}

async function readBoundedResponse(response: Response, maxBytes: number): Promise<Uint8Array> {
  const declared = Number(response.headers.get("content-length"))
  if (Number.isFinite(declared) && declared > maxBytes)
    throw new MorselPublishError("Morsel response exceeded size limit")
  if (!response.body) return new Uint8Array()
  const reader = response.body.getReader()
  const chunks: Uint8Array[] = []
  let total = 0
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    total += value.byteLength
    if (total > maxBytes) {
      await reader.cancel()
      throw new MorselPublishError("Morsel response exceeded size limit")
    }
    chunks.push(value)
  }
  return Buffer.concat(chunks.map((chunk) => Buffer.from(chunk)))
}

function isShareMetadata(value: unknown): value is { id: string; share_url: string } {
  return (
    typeof value === "object" &&
    value !== null &&
    "id" in value &&
    typeof value.id === "string" &&
    "share_url" in value &&
    typeof value.share_url === "string"
  )
}
