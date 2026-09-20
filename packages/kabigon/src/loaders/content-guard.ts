import { LoaderContentError } from "../core/errors.js";

export const MIN_CONTENT_LENGTH = 1;
export const BLOCKED_MARKERS = [
  "just a moment...",
  "checking your browser",
  "attention required! | cloudflare",
  "ddos protection by cloudflare",
  "enable javascript and cookies to continue",
] as const;

export function ensureUsableContent(
  content: string,
  options: { loaderName: string; url: string; minLength?: number },
): void {
  const stripped = content.trim();
  const minLength = options.minLength ?? MIN_CONTENT_LENGTH;
  if (stripped.length < minLength) {
    throw new LoaderContentError(
      options.loaderName,
      options.url,
      `Extracted content too short (${stripped.length} chars < ${minLength})`,
      "Page may be JS-heavy or blocking requests; chain will try next loader.",
    );
  }
  const heading = (stripped.split(/\r?\n/u)[0] ?? "").replace(/^[#*_ \t]+|[#*_ \t]+$/gu, "").toLowerCase();
  if (BLOCKED_MARKERS.some((marker) => heading.startsWith(marker))) {
    throw new LoaderContentError(
      options.loaderName,
      options.url,
      `Detected block/challenge marker: ${JSON.stringify(heading)}`,
      "The site appears to be blocking automated requests.",
    );
  }
}
