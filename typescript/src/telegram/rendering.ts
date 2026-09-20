const fencedCodePattern = /```(?:[^\n`]*\n)?([\s\S]*?)```/g;
const inlineCodePattern = /`([^`\n]+)`/g;
const headingPattern = /^(#{1,6})[ \t]+(.+)$/;
const boldPattern = /\*\*(.+?)\*\*/gs;
const plainUrlPattern = /https?:\/\/[^\s<>()]+/gi;

export function sanitizeTelegramText(text: string): string {
  return Array.from(text.replace(/\r\n?/g, "\n"))
    .filter((character) => {
      const codePoint = character.codePointAt(0) ?? 0;
      return (
        character === "\n" || character === "\t" || (codePoint >= 0x20 && !(codePoint >= 0xd800 && codePoint <= 0xdfff))
      );
    })
    .join("");
}

export function telegramHtmlChunks(text: string, limit = 4_096): string[] {
  if (!Number.isInteger(limit) || limit < 1) throw new RangeError("limit must be a positive integer");
  const sanitized = sanitizeTelegramText(text);
  return chunkByCodePoints(sanitized || " ", limit).map(formatTelegramHtml);
}

export function trimUrl(url: string): string {
  return url.trim().replace(/[.,，。!！?)）\]} >]+$/u, "");
}

function formatTelegramHtml(text: string): string {
  const output: string[] = [];
  let cursor = 0;
  for (const match of text.matchAll(fencedCodePattern)) {
    const index = match.index;
    output.push(formatInline(text.slice(cursor, index)));
    output.push(`<pre>${escapeHtml(match[1] ?? "")}</pre>`);
    cursor = index + match[0].length;
  }
  output.push(formatInline(text.slice(cursor)));
  return output.join("");
}

function formatInline(text: string): string {
  return text
    .split(/(?<=\n)/)
    .map((line) => {
      const hasNewline = line.endsWith("\n");
      const content = hasNewline ? line.slice(0, -1) : line;
      const heading = headingPattern.exec(content);
      const formatted = heading
        ? `<b>${formatInlineMarkdown(heading[2] ?? "", false)}</b>`
        : formatInlineMarkdown(content, true);
      return formatted + (hasNewline ? "\n" : "");
    })
    .join("");
}

function formatInlineMarkdown(text: string, convertBold: boolean): string {
  const output: string[] = [];
  let cursor = 0;
  for (const match of text.matchAll(inlineCodePattern)) {
    const index = match.index;
    output.push(formatMarkdownText(text.slice(cursor, index), convertBold));
    output.push(`<code>${escapeHtml(match[1] ?? "")}</code>`);
    cursor = index + match[0].length;
  }
  output.push(formatMarkdownText(text.slice(cursor), convertBold));
  return output.join("");
}

function formatMarkdownText(text: string, convertBold: boolean): string {
  const escaped = escapeWithLinks(text);
  return convertBold ? escaped.replace(boldPattern, "<b>$1</b>") : escaped.replace(boldPattern, "$1");
}

function escapeWithLinks(text: string): string {
  const output: string[] = [];
  let cursor = 0;
  for (const match of text.matchAll(plainUrlPattern)) {
    const index = match.index;
    const rawUrl = match[0];
    const url = trimUrl(rawUrl);
    output.push(escapeHtml(text.slice(cursor, index)));
    output.push(`<a href="${escapeAttribute(encodeURI(url))}">${escapeHtml(url)}</a>`);
    output.push(escapeHtml(rawUrl.slice(url.length)));
    cursor = index + rawUrl.length;
  }
  output.push(escapeHtml(text.slice(cursor)));
  return output.join("");
}

function chunkByCodePoints(text: string, limit: number): string[] {
  const characters = Array.from(text);
  const chunks: string[] = [];
  for (let index = 0; index < characters.length; index += limit) {
    chunks.push(characters.slice(index, index + limit).join(""));
  }
  return chunks;
}

function escapeHtml(text: string): string {
  return text.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
}

function escapeAttribute(text: string): string {
  return escapeHtml(text).replaceAll('"', "&quot;");
}
