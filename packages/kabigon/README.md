# @telegram-agent/kabigon

A TypeScript and Node.js port of [kabigon](https://github.com/narumiruna/kabigon). It extracts text or Markdown from URLs and chooses a source-safe loader automatically.

## Features

- Source-aware plans for YouTube, Twitter/X, Truth Social, Reddit, Instagram Reels, PTT, GitHub, pi.dev sessions, BBC, CNN, LTN, PDFs, OpenAI pages, and generic web pages
- Ordered fallback attempts with structured status, timing, and error details
- Browser TLS/HTTP fingerprinting through [`impers`](https://github.com/lexiforest/impers), the TypeScript counterpart of `curl_cffi`
- Reusable fetch, `impers`, and Playwright resources with concurrency limits and total deadlines
- ESM library API, TypeScript declarations, and a `kabigon` CLI

## Workspace usage

From this repository root:

```bash
npm install
npm run build --workspace @telegram-agent/kabigon
npm test --workspace @telegram-agent/kabigon
npm exec --workspace @telegram-agent/kabigon -- kabigon --list
```

## Library API

```ts
import { explainPlan, loadUrl, loadUrlDetailed } from "@telegram-agent/kabigon";

const plan = explainPlan("https://www.youtube.com/watch?v=dQw4w9WgXcQ");
console.log(plan.execution_plan);

const text = await loadUrl("https://example.com", { deadlineSeconds: 30 });
console.log(text);

const result = await loadUrlDetailed("https://github.com/user/repo/blob/main/README.md");
console.log(result.loaderId, result.contentType, result.attempts);
```

JavaScript has no safe synchronous equivalent to Python's `load_url_sync`; the TypeScript API is async-only.

## Reusable client

A client must be started before use and closed when finished. It lazily owns one `impers` session and one Playwright browser.

```ts
import { KabigonClient } from "@telegram-agent/kabigon";

await using client = new KabigonClient({
  deadlineSeconds: 30,
  requestLimit: 8,
  browserLimit: 2,
  workerLimit: 2,
}).start();

const results = await Promise.all([
  client.loadUrl("https://example.com/one"),
  client.loadUrl("https://example.com/two"),
]);
```

A deadline includes time spent waiting for a concurrency slot. Cancellation is propagated through `AbortSignal` where the underlying library supports it.

## CLI

```bash
kabigon https://example.com
kabigon --loader curl-cffi,playwright,httpx https://example.com
kabigon --list
```

Automatic planning is preferred. Explicit loaders are intended for debugging.

## Runtime requirements

### `impers`

The `curl-cffi` loader uses `impers` with `impersonate: "chrome"`. On first use, `impers` may download its pinned `libcurl-impersonate` build. Set `LIBCURL_PATH` to use an existing library. Standard libcurl works without browser fingerprint impersonation.

### Playwright

Install Chromium for the browser and social loaders:

```bash
npx playwright install chromium
```

### Twitter/X

The Twitter loader first requests the exact status from `api.fxtwitter.com` and verifies the returned status ID. It falls back to Playwright when that API is unavailable. This avoids returning X login or error pages when X blocks browser automation.

### Firecrawl

Set `FIRECRAWL_API_KEY` for OpenAI web pages or explicit `firecrawl` loading:

```bash
export FIRECRAWL_API_KEY=...
```

The loader calls Firecrawl's v1 scrape endpoint and requests Markdown.

### PDF

PDF parsing uses `pdf-parse`. Remote targets must return `application/pdf`; local `.pdf` paths are also supported.

### yt-dlp and Whisper

YouTube captions use `youtube-transcript`. The `youtube-ytdlp`, `ytdlp`, and `reel` loaders need external commands:

- `yt-dlp`, or `YTDLP_PATH`
- OpenAI Whisper's `whisper` CLI, or `WHISPER_PATH`
- FFmpeg, optionally located through `FFMPEG_PATH`

The transcription loader writes only to an isolated temporary directory and removes it after each attempt.

## Extraction policy

Strict source plans do not accept unrelated generic HTML:

- YouTube video URLs require transcript output.
- Twitter status, Reddit, Truth Social, PTT, Reel, PDF, pi.dev session, and GitHub plans require their matching source loader.
- BBC, CNN, and LTN use the same article extractor after HTTP, `impers`, or browser retrieval.
- Generic pages try `curl-cffi` (`impers`), Playwright network-idle, faster Playwright, then standard fetch.
- Empty output and recognized challenge headings are rejected so the chain can continue.

## Compatibility notes

The public behavior follows Python kabigon 0.19.6, but implementation details differ:

- Names use TypeScript camelCase (`loadUrl`, `explainPlan`, `loaderId`). `toObject()` helpers expose the Python-style snake_case diagnostic shape.
- HTML-to-Markdown conversion uses Turndown, so whitespace and Markdown punctuation may differ while preserving extracted content.
- `impers` replaces `curl_cffi`.
- Audio transcription invokes the Python-installed Whisper CLI instead of embedding a Whisper runtime in Node.js.
- YouTube language preference is attempted in order through `youtube-transcript`.

## Development

```bash
npm run format:check --workspace @telegram-agent/kabigon
npm run lint --workspace @telegram-agent/kabigon
npm run typecheck --workspace @telegram-agent/kabigon
npm test --workspace @telegram-agent/kabigon
npm run build --workspace @telegram-agent/kabigon
```
