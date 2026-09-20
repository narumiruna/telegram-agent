import * as cheerio from "cheerio";

import { LoaderContentError, LoaderTimeoutError } from "../core/errors.js";
import type { Loader } from "../core/loader.js";
import { readResponseText } from "../core/network.js";
import type { ResourceProvider } from "../core/resources.js";
import { type PiSessionTarget, parsePiSessionTarget } from "../sources/applicability.js";

const GITHUB_GIST_API = "https://api.github.com/gists/{gistId}";
const GIST_RAW_HOST = "gist.githubusercontent.com";
export const MAX_PI_SESSION_BYTES = 10 * 1024 * 1024;
type JsonRecord = Record<string, unknown>;

function record(value: unknown): JsonRecord | undefined {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as JsonRecord) : undefined;
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function label(value: unknown, fallback: string): string {
  return text(value).trim().replace(/\n/gu, " ") || fallback;
}

function contentError(url: string, reason: string): LoaderContentError {
  return new LoaderContentError(
    "PiSessionLoader",
    url,
    reason,
    "Check that the shared session still exists and contains a valid Pi session export.",
  );
}

export function decodeSessionExport(html: string, sourceUrl: string): JsonRecord {
  const encoded = cheerio.load(html)("script#session-data").text().trim();
  if (!encoded) throw contentError(sourceUrl, 'Missing embedded <script id="session-data"> payload.');
  try {
    const value = JSON.parse(Buffer.from(encoded, "base64").toString("utf8")) as unknown;
    const session = record(value);
    if (!session) throw new Error("Embedded session data is not a JSON object.");
    return session;
  } catch (error) {
    if (error instanceof LoaderContentError) throw error;
    throw contentError(sourceUrl, `Invalid embedded session data: ${String(error)}`);
  }
}

function selectedEntries(session: JsonRecord, requestedLeafId: string | undefined, sourceUrl: string): JsonRecord[] {
  if (!Array.isArray(session.entries)) throw contentError(sourceUrl, "Embedded session data has no entries list.");
  const entries: JsonRecord[] = [];
  const byId = new Map<string, JsonRecord>();
  for (const raw of session.entries) {
    const entry = record(raw);
    const id = text(entry?.id);
    if (!entry || !id) throw contentError(sourceUrl, "Embedded session data contains an invalid entry.");
    if (byId.has(id))
      throw contentError(sourceUrl, `Embedded session data contains duplicate entry ID ${JSON.stringify(id)}.`);
    entries.push(entry);
    byId.set(id, entry);
  }
  if (entries.length === 0) return [];
  const leafId = requestedLeafId || text(session.leafId) || text(entries.at(-1)?.id);
  if (!byId.has(leafId)) throw contentError(sourceUrl, `Session leaf ${JSON.stringify(leafId)} was not found.`);

  const selected: JsonRecord[] = [];
  const visited = new Set<string>();
  let currentId: string | undefined = leafId;
  while (currentId) {
    if (visited.has(currentId)) throw contentError(sourceUrl, "Embedded session entry ancestry contains a cycle.");
    visited.add(currentId);
    const entry = byId.get(currentId);
    if (!entry) throw contentError(sourceUrl, `Session parent entry ${JSON.stringify(currentId)} was not found.`);
    selected.push(entry);
    if (entry.parentId === null || entry.parentId === undefined) currentId = undefined;
    else if (typeof entry.parentId === "string" && entry.parentId) currentId = entry.parentId;
    else throw contentError(sourceUrl, `Entry ${JSON.stringify(currentId)} has an invalid parent ID.`);
  }
  return selected.reverse();
}

function fencedBlock(value: string, language = ""): string {
  const longest = Math.max(0, ...Array.from(value.matchAll(/`+/gu), (match) => match[0].length));
  const fence = "`".repeat(Math.max(3, longest + 1));
  return `${fence}${language}\n${value.trimEnd()}\n${fence}`;
}

function metadata(entry: JsonRecord, ...extra: string[]): string[] {
  const lines = extra.filter(Boolean);
  if (text(entry.timestamp)) lines.push(`- Timestamp: ${text(entry.timestamp)}`);
  return lines;
}

function renderContentBlock(block: JsonRecord): string[] {
  switch (text(block.type)) {
    case "text":
      return text(block.text).trim() ? [text(block.text).trim()] : [];
    case "thinking":
      return text(block.thinking).trim() ? ["#### Thinking", text(block.thinking).trim()] : [];
    case "toolCall":
      return [
        `#### Tool Call: ${label(block.name, "unknown")}`,
        fencedBlock(JSON.stringify(block.arguments ?? {}, undefined, 2), "json"),
      ];
    case "image":
      return [`[Image: ${label(block.mimeType, "unknown type")}]`];
    default:
      return [];
  }
}

function renderContent(content: unknown): string[] {
  if (typeof content === "string") return content.trim() ? [content.trim()] : [];
  if (!Array.isArray(content)) return [];
  return content.flatMap((item) => {
    const block = record(item);
    return block ? renderContentBlock(block) : [];
  });
}

function renderMessage(entry: JsonRecord, message: JsonRecord): string | undefined {
  const role = text(message.role);
  let heading: string;
  let meta: string[];
  let body: string[];
  if (role === "user") {
    heading = "### User";
    meta = metadata(entry);
    body = renderContent(message.content);
  } else if (role === "assistant") {
    heading = "### Assistant";
    const model = [text(message.provider), text(message.model)].filter(Boolean).join("/");
    meta = metadata(entry, model ? `- Model: ${model}` : "");
    body = renderContent(message.content);
    const stopReason = text(message.stopReason);
    if (stopReason === "aborted" || stopReason === "error") {
      body.push(
        `**${stopReason[0]?.toUpperCase()}${stopReason.slice(1)}:** ${text(message.errorMessage) || "No details provided."}`,
      );
    }
  } else if (role === "toolResult") {
    heading = `### Tool Result: ${label(message.toolName, "unknown")}`;
    meta = metadata(entry, `- Status: ${message.isError === true ? "error" : "success"}`);
    body = renderContent(message.content);
  } else if (role === "bashExecution") {
    heading = "### Bash Execution";
    meta = metadata(entry);
    body = [];
    if (text(message.command)) body.push(fencedBlock(text(message.command), "bash"));
    if (text(message.output)) body.push(fencedBlock(text(message.output), "text"));
  } else {
    heading = `### ${label(role, "Message")}`;
    meta = metadata(entry);
    body = renderContent(message.content);
  }
  if (meta.length === 0 && body.length === 0) return undefined;
  return [heading, ...meta, ...body].join("\n\n");
}

function renderNonMessage(entry: JsonRecord): string | undefined {
  const entryType = text(entry.type);
  if (entryType === "model_change") {
    const model = [text(entry.provider), text(entry.modelId)].filter(Boolean).join("/") || "unknown";
    return ["### Model Change", ...metadata(entry), model].join("\n\n");
  }
  if (entryType === "thinking_level_change") {
    return ["### Thinking Level Change", ...metadata(entry), label(entry.thinkingLevel, "unknown")].join("\n\n");
  }
  if (entryType === "compaction") {
    const tokens = Number.isInteger(entry.tokensBefore) ? `- Tokens before: ${String(entry.tokensBefore)}` : "";
    return ["### Compaction", ...metadata(entry, tokens), text(entry.summary).trim()].filter(Boolean).join("\n\n");
  }
  if (entryType === "branch_summary") {
    return ["### Branch Summary", ...metadata(entry), text(entry.summary).trim()].filter(Boolean).join("\n\n");
  }
  if (entryType === "custom_message" && entry.display === true) {
    const body = typeof entry.content === "string" ? entry.content : JSON.stringify(entry.content, undefined, 2);
    return [`### ${label(entry.customType, "Custom Message")}`, ...metadata(entry), body].filter(Boolean).join("\n\n");
  }
  return undefined;
}

export function renderPiSessionMarkdown(session: JsonRecord, options: { sourceUrl: string; leafId?: string }): string {
  const header = record(session.header);
  if (!header) throw contentError(options.sourceUrl, "Embedded session data has no header object.");
  const details: string[] = [];
  if (text(header.timestamp)) details.push(`- Date: ${text(header.timestamp)}`);
  if (text(header.cwd)) details.push(`- Working directory: \`${text(header.cwd)}\``);
  details.push(`- Source: ${options.sourceUrl}`);
  const sections = [`# Pi Session ${label(header.id, "unknown")}`, details.join("\n")];
  if (text(session.systemPrompt).trim()) sections.push("## System Prompt", text(session.systemPrompt).trim());

  const tools = Array.isArray(session.tools)
    ? session.tools.flatMap((item) => {
        const tool = record(item);
        if (!tool) return [];
        const description = text(tool.description).trim();
        return [`- **${label(tool.name, "unknown")}**${description ? ` — ${description}` : ""}`];
      })
    : [];
  if (tools.length > 0) sections.push("## Available Tools", tools.join("\n"));

  const rendered = selectedEntries(session, options.leafId, options.sourceUrl).flatMap((entry) => {
    const value =
      text(entry.type) === "message" ? renderMessage(entry, record(entry.message) ?? {}) : renderNonMessage(entry);
    return value ? [value] : [];
  });
  if (rendered.length > 0) sections.push("## Conversation", ...rendered);
  return sections.filter(Boolean).join("\n\n").trim();
}

function gistFile(gist: unknown, target: PiSessionTarget, sourceUrl: string): JsonRecord {
  const file = record(record(record(gist)?.files)?.[target.fileName]);
  if (!file) throw contentError(sourceUrl, `Gist does not contain ${JSON.stringify(target.fileName)}.`);
  return file;
}

function validatedRawUrl(value: unknown, sourceUrl: string): string {
  const rawUrl = text(value);
  try {
    const parsed = new URL(rawUrl);
    if (parsed.protocol !== "https:" || parsed.hostname !== GIST_RAW_HOST) throw new Error();
  } catch {
    throw contentError(sourceUrl, "Gist returned an invalid raw content URL.");
  }
  return rawUrl;
}

export class PiSessionLoader implements Loader {
  readonly timeoutMs: number;
  readonly resources?: ResourceProvider;

  constructor(options: { timeoutSeconds?: number; resources?: ResourceProvider } = {}) {
    this.timeoutMs = (options.timeoutSeconds ?? 30) * 1_000;
    this.resources = options.resources;
  }

  private async get(url: string, init: RequestInit): Promise<Response> {
    const response = await (this.resources?.fetch(url, init) ?? fetch(url, init));
    if (!response.ok) throw new Error(`GitHub returned HTTP ${response.status}`);
    return response;
  }

  async load(url: string, signal?: AbortSignal): Promise<string> {
    const target = parsePiSessionTarget(url);
    const timeout = AbortSignal.timeout(this.timeoutMs);
    const activeSignal = signal ? AbortSignal.any([signal, timeout]) : timeout;
    try {
      const apiUrl = GITHUB_GIST_API.replace("{gistId}", target.gistId);
      const response = await this.get(apiUrl, {
        headers: { Accept: "application/vnd.github+json", "User-Agent": "kabigon-typescript" },
        redirect: "follow",
        signal: activeSignal,
      });
      const file = gistFile((await response.json()) as unknown, target, url);
      let content = text(file.content);
      if (file.truncated === true || !content) {
        content = await readResponseText(
          await this.get(validatedRawUrl(file.raw_url, url), { redirect: "follow", signal: activeSignal }),
          MAX_PI_SESSION_BYTES,
        );
      }
      return renderPiSessionMarkdown(decodeSessionExport(content, url), { sourceUrl: url, leafId: target.leafId });
    } catch (error) {
      if (error instanceof LoaderContentError) throw error;
      if (timeout.aborted) {
        throw new LoaderTimeoutError(
          "PiSessionLoader",
          url,
          this.timeoutMs / 1_000,
          "GitHub timed out while retrieving the shared Pi session.",
        );
      }
      throw contentError(url, `GitHub request failed: ${String(error)}`);
    }
  }
}
