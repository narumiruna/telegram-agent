import {
  BBC,
  CNN,
  CURL_CFFI,
  FIRECRAWL,
  GITHUB,
  HTTPX,
  LTN,
  PDF,
  PI_SESSION,
  PLAYWRIGHT_FAST,
  PLAYWRIGHT_NETWORKIDLE,
  PTT,
  REDDIT,
  REEL,
  TRUTHSOCIAL,
  TWITTER,
  YOUTUBE,
  YOUTUBE_YTDLP,
} from "../loader-registry.js"
import {
  isBbcUrl,
  isCnnUrl,
  isGitHubUrl,
  isLtnUrl,
  isOpenAiWebUrl,
  isPdfTarget,
  isPiSessionUrl,
  isPttUrl,
  isRedditUrl,
  isReelUrl,
  isTruthSocialUrl,
  isTwitterStatusUrl,
  isYouTubeVideoUrl,
} from "../sources/applicability.js"

export const ContentType = {
  YouTubeVideo: "youtube_video",
  SocialPost: "social_post",
  NewsArticle: "news_article",
  DocumentPdf: "document_pdf",
  AiSession: "ai_session",
  CodeContent: "code_content",
  GenericWeb: "generic_web",
} as const
export type ContentType = (typeof ContentType)[keyof typeof ContentType]

export const ContentContract = {
  SourceRequired: "source_required",
  GenericHtml: "generic_html",
} as const
export type ContentContract = (typeof ContentContract)[keyof typeof ContentContract]

export interface Pipeline {
  name: string
  contentType: ContentType
  targetedLoaders: readonly string[]
  contentContract?: ContentContract
  fallbackLoaders?: readonly string[]
}

export interface PipelinePlan {
  pipelineName?: string
  contentType: ContentType
  targetedLoaders: readonly string[]
  fallbackLoaders: readonly string[]
  executionPlan: readonly string[]
  contentContract: ContentContract
}

export const GENERIC_HTML_LOADERS = [
  CURL_CFFI,
  PLAYWRIGHT_NETWORKIDLE,
  PLAYWRIGHT_FAST,
  HTTPX,
] as const

type PipelineEntry = readonly [Pipeline, (url: string) => boolean]

const PIPELINE_ENTRIES: readonly PipelineEntry[] = [
  [{ name: PTT, contentType: ContentType.SocialPost, targetedLoaders: [PTT] }, isPttUrl],
  [
    { name: TWITTER, contentType: ContentType.SocialPost, targetedLoaders: [TWITTER] },
    isTwitterStatusUrl,
  ],
  [
    { name: TRUTHSOCIAL, contentType: ContentType.SocialPost, targetedLoaders: [TRUTHSOCIAL] },
    isTruthSocialUrl,
  ],
  [{ name: REDDIT, contentType: ContentType.SocialPost, targetedLoaders: [REDDIT] }, isRedditUrl],
  [
    {
      name: YOUTUBE,
      contentType: ContentType.YouTubeVideo,
      targetedLoaders: [YOUTUBE, YOUTUBE_YTDLP],
    },
    isYouTubeVideoUrl,
  ],
  [{ name: REEL, contentType: ContentType.SocialPost, targetedLoaders: [REEL] }, isReelUrl],
  [
    { name: PI_SESSION, contentType: ContentType.AiSession, targetedLoaders: [PI_SESSION] },
    isPiSessionUrl,
  ],
  [{ name: GITHUB, contentType: ContentType.CodeContent, targetedLoaders: [GITHUB] }, isGitHubUrl],
  [{ name: BBC, contentType: ContentType.NewsArticle, targetedLoaders: [BBC] }, isBbcUrl],
  [{ name: CNN, contentType: ContentType.NewsArticle, targetedLoaders: [CNN] }, isCnnUrl],
  [{ name: LTN, contentType: ContentType.NewsArticle, targetedLoaders: [LTN] }, isLtnUrl],
  [
    {
      name: "openai_web",
      contentType: ContentType.GenericWeb,
      targetedLoaders: [FIRECRAWL],
      contentContract: ContentContract.GenericHtml,
    },
    isOpenAiWebUrl,
  ],
  [{ name: PDF, contentType: ContentType.DocumentPdf, targetedLoaders: [PDF] }, isPdfTarget],
]

export function matchPipeline(url: string): Pipeline | undefined {
  return PIPELINE_ENTRIES.find(([, matches]) => matches(url))?.[0]
}

export function planForUrl(url: string): PipelinePlan {
  const pipeline = matchPipeline(url)
  if (!pipeline) {
    return {
      contentType: ContentType.GenericWeb,
      targetedLoaders: [],
      fallbackLoaders: GENERIC_HTML_LOADERS,
      executionPlan: GENERIC_HTML_LOADERS,
      contentContract: ContentContract.GenericHtml,
    }
  }
  const fallbackLoaders = pipeline.fallbackLoaders ?? []
  return {
    pipelineName: pipeline.name,
    contentType: pipeline.contentType,
    targetedLoaders: pipeline.targetedLoaders,
    fallbackLoaders,
    executionPlan: [...new Set([...pipeline.targetedLoaders, ...fallbackLoaders])],
    contentContract: pipeline.contentContract ?? ContentContract.SourceRequired,
  }
}

export function listPipelines(): readonly Pipeline[] {
  return PIPELINE_ENTRIES.map(([pipeline]) => pipeline)
}
