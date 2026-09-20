import { LoaderContentError } from "../core/errors.js";
import type { Loader } from "../core/loader.js";
import { parseYouTubeVideoTarget, requireLoaderApplicability } from "../sources/applicability.js";

export const DEFAULT_LANGUAGES = [
  "zh-TW",
  "zh-Hant",
  "zh",
  "zh-Hans",
  "ja",
  "ko",
  "en",
  "fr",
  "de",
  "es",
  "it",
  "pt",
  "pt-BR",
  "nl",
  "sv",
  "pl",
  "th",
  "vi",
  "id",
  "ms",
  "fil",
  "ru",
  "ar",
  "hi",
] as const;

export function parseVideoId(url: string): string {
  return parseYouTubeVideoTarget(url).videoId;
}

export class YouTubeLoader implements Loader {
  constructor(private readonly languages: readonly string[] = DEFAULT_LANGUAGES) {}

  async load(url: string, signal?: AbortSignal): Promise<string> {
    signal?.throwIfAborted();
    const videoId = requireLoaderApplicability("YouTubeLoader", url, parseYouTubeVideoTarget).videoId;
    const { fetchTranscript } = await import("youtube-transcript");
    const failures: unknown[] = [];
    for (const language of [...this.languages, undefined]) {
      signal?.throwIfAborted();
      try {
        const snippets = await fetchTranscript(videoId, language ? { lang: language } : undefined);
        signal?.throwIfAborted();
        const result = snippets
          .map((snippet) => snippet.text.trim())
          .filter(Boolean)
          .join("\n");
        if (result) return result;
      } catch (error) {
        signal?.throwIfAborted();
        failures.push(error);
      }
    }
    const last = failures.at(-1);
    throw new LoaderContentError(
      "YouTubeLoader",
      url,
      `Failed to fetch transcript: ${String(last ?? "Transcript is empty")}`,
      "The video may not have captions available, or captions may be disabled.",
    );
  }
}

export { YouTubeLoader as YoutubeLoader };
