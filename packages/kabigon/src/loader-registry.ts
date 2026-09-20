import type { Loader, LoaderFactory } from "./core/loader.js"
import type { ResourceProvider } from "./core/resources.js"

export const PTT = "ptt"
export const TWITTER = "twitter"
export const TRUTHSOCIAL = "truthsocial"
export const REDDIT = "reddit"
export const YOUTUBE = "youtube"
export const REEL = "reel"
export const YOUTUBE_YTDLP = "youtube-ytdlp"
export const PDF = "pdf"
export const PI_SESSION = "pi-session"
export const GITHUB = "github"
export const BBC = "bbc"
export const CNN = "cnn"
export const LTN = "ltn"
export const PLAYWRIGHT_NETWORKIDLE = "playwright-networkidle"
export const PLAYWRIGHT_FAST = "playwright-fast"
export const PLAYWRIGHT = "playwright"
export const CURL_CFFI = "curl-cffi"
export const HTTPX = "httpx"
export const FIRECRAWL = "firecrawl"
export const YTDLP = "ytdlp"

export type ResourceKind = "request" | "browser" | "worker"

export interface LoaderDef {
  name: string
  description: string
  contentType: string
  requirements: readonly string[]
  cliVisible: boolean
  resourceKind: ResourceKind
}

function definition(
  name: string,
  description: string,
  contentType: string,
  options: Partial<Pick<LoaderDef, "requirements" | "cliVisible" | "resourceKind">> = {},
): LoaderDef {
  return {
    name,
    description,
    contentType,
    requirements: options.requirements ?? [],
    cliVisible: options.cliVisible ?? true,
    resourceKind: options.resourceKind ?? "request",
  }
}

export const LOADER_DEFS: readonly LoaderDef[] = [
  definition(PTT, "Taiwan PTT forum posts", "social_post"),
  definition(TWITTER, "Extracts Twitter/X post content", "social_post"),
  definition(TRUTHSOCIAL, "Extracts Truth Social posts", "social_post", {
    resourceKind: "browser",
  }),
  definition(REDDIT, "Extracts Reddit posts and comments", "social_post"),
  definition(YOUTUBE, "Extracts YouTube video transcripts", "youtube_video", {
    resourceKind: "worker",
  }),
  definition(REEL, "Instagram Reels audio transcription + metadata", "social_post", {
    resourceKind: "worker",
  }),
  definition(YOUTUBE_YTDLP, "YouTube audio transcription via yt-dlp + Whisper", "youtube_video", {
    resourceKind: "worker",
  }),
  definition(PDF, "Extracts text from PDF files", "document_pdf", { resourceKind: "worker" }),
  definition(PI_SESSION, "Extracts pi.dev shared session transcripts", "ai_session"),
  definition(GITHUB, "Fetches GitHub pages and file content", "code_content"),
  definition(BBC, "BBC article extraction with article-aware parsing", "news_article"),
  definition(CNN, "CNN article extraction with article-aware parsing", "news_article"),
  definition(LTN, "Liberty Times Net article extraction", "news_article"),
  definition(
    PLAYWRIGHT_NETWORKIDLE,
    "Browser-based scraping with networkidle wait",
    "generic_web",
    {
      cliVisible: false,
      resourceKind: "browser",
    },
  ),
  definition(PLAYWRIGHT_FAST, "Browser-based scraping with faster defaults", "generic_web", {
    cliVisible: false,
    resourceKind: "browser",
  }),
  definition(PLAYWRIGHT, "Browser-based scraping for any website", "generic_web", {
    resourceKind: "browser",
  }),
  definition(CURL_CFFI, "HTTP fetch with browser TLS fingerprint via impers", "generic_web"),
  definition(HTTPX, "Simple HTTP fetch + HTML to markdown", "generic_web"),
  definition(
    FIRECRAWL,
    "Firecrawl-based web extraction (requires FIRECRAWL_API_KEY)",
    "generic_web",
    {
      requirements: ["FIRECRAWL_API_KEY"],
      resourceKind: "worker",
    },
  ),
  definition(YTDLP, "Audio transcription via yt-dlp + Whisper", "generic_web", {
    resourceKind: "worker",
  }),
]

const byName = new Map(LOADER_DEFS.map((item) => [item.name, item]))

export function getLoaderDef(name: string): LoaderDef {
  const found = byName.get(name)
  if (!found) throw new Error(`Unknown loader: ${name}`)
  return found
}

export function getLoaderDescription(name: string): string {
  return getLoaderDef(name).description
}

export function getLoaderRequirements(name: string): readonly string[] {
  return getLoaderDef(name).requirements
}

export function getLoaderContentType(name: string): string {
  return getLoaderDef(name).contentType
}

export function listLoaderDefs(options: { cliVisible?: boolean } = {}): readonly LoaderDef[] {
  return options.cliVisible === undefined
    ? LOADER_DEFS
    : LOADER_DEFS.filter((item) => item.cliVisible === options.cliVisible)
}

export function listLoaderNames(options: { cliVisible?: boolean } = {}): string[] {
  return listLoaderDefs(options).map((item) => item.name)
}

export async function createLoader(name: string, resources?: ResourceProvider): Promise<Loader> {
  switch (name) {
    case HTTPX: {
      const { HttpLoader } = await import("./loaders/generic.js")
      return new HttpLoader({ resources })
    }
    case CURL_CFFI: {
      const { CurlCffiLoader } = await import("./loaders/generic.js")
      return new CurlCffiLoader({ resources })
    }
    case PLAYWRIGHT:
    case PLAYWRIGHT_FAST:
    case PLAYWRIGHT_NETWORKIDLE: {
      const { PlaywrightLoader } = await import("./loaders/generic.js")
      if (name === PLAYWRIGHT_NETWORKIDLE) {
        return new PlaywrightLoader({ resources, timeoutMs: 50_000, waitUntil: "networkidle" })
      }
      if (name === PLAYWRIGHT_FAST) {
        return new PlaywrightLoader({ resources, timeoutMs: 15_000, waitUntil: "domcontentloaded" })
      }
      return new PlaywrightLoader({ resources })
    }
    case PTT: {
      const { PttLoader } = await import("./loaders/ptt.js")
      return new PttLoader({ resources })
    }
    case TWITTER: {
      const { TwitterLoader } = await import("./loaders/twitter.js")
      return new TwitterLoader({ resources })
    }
    case TRUTHSOCIAL: {
      const { TruthSocialLoader } = await import("./loaders/truthsocial.js")
      return new TruthSocialLoader({ resources })
    }
    case REDDIT: {
      const { RedditLoader } = await import("./loaders/reddit.js")
      return new RedditLoader({ resources })
    }
    case YOUTUBE: {
      const { YouTubeLoader } = await import("./loaders/youtube.js")
      return new YouTubeLoader()
    }
    case YOUTUBE_YTDLP: {
      const { YouTubeYtdlpLoader } = await import("./loaders/ytdlp.js")
      return new YouTubeYtdlpLoader()
    }
    case YTDLP: {
      const { YtdlpLoader } = await import("./loaders/ytdlp.js")
      return new YtdlpLoader()
    }
    case REEL: {
      const { ReelLoader } = await import("./loaders/reel.js")
      return new ReelLoader({ resources })
    }
    case PDF: {
      const { PdfLoader } = await import("./loaders/pdf.js")
      return new PdfLoader({ resources })
    }
    case PI_SESSION: {
      const { PiSessionLoader } = await import("./loaders/pi-session.js")
      return new PiSessionLoader({ resources })
    }
    case GITHUB: {
      const { GitHubLoader } = await import("./loaders/github.js")
      return new GitHubLoader({ resources })
    }
    case BBC:
    case CNN:
    case LTN: {
      const { NewsArticleLoader } = await import("./loaders/news.js")
      return new NewsArticleLoader(name as "bbc" | "cnn" | "ltn", { resources })
    }
    case FIRECRAWL: {
      const { FirecrawlLoader } = await import("./loaders/firecrawl.js")
      return new FirecrawlLoader({ resources })
    }
    default:
      throw new Error(`Unknown loader: ${name}`)
  }
}

export function getLoaderFactory(name: string, resources?: ResourceProvider): LoaderFactory {
  getLoaderDef(name)
  return () => createLoader(name, resources)
}
