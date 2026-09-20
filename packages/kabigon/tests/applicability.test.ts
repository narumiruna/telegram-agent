import { describe, expect, it } from "vitest";

import { LoaderNotApplicableError } from "../src/core/errors.js";
import { planForUrl } from "../src/pipelines/catalog.js";
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
  isTwitterUrl,
  isYouTubeVideoUrl,
  parseGitHubRawContentTarget,
  parsePiSessionTarget,
  parseTwitterTarget,
  parseYouTubeVideoTarget,
  requireLoaderApplicability,
} from "../src/sources/applicability.js";

const supported: Array<[string, (url: string) => boolean]> = [
  ["https://www.ptt.cc/bbs/Gossiping/M.1746078381.A.FFC.html", isPttUrl],
  ["https://x.com/howie_serious/status/1917768568135115147", isTwitterUrl],
  ["https://truthsocial.com/@realDonaldTrump/posts/115830428767897167", isTruthSocialUrl],
  ["https://www.reddit.com/r/python/comments/abc/example/", isRedditUrl],
  ["https://www.youtube.com/watch?v=dQw4w9WgXcQ", isYouTubeVideoUrl],
  ["https://www.instagram.com/reel/CuA0XYZ1234/", isReelUrl],
  ["https://github.com/anthropics/claude-code/blob/main/README.md", isGitHubUrl],
  ["https://pi.dev/session/#0230effc86f4a142c885cb59fe9725d5", isPiSessionUrl],
  ["https://www.bbc.com/news/articles/c70k29914q4o", isBbcUrl],
  ["https://edition.cnn.com/2026/03/16/tech/example", isCnnUrl],
  ["https://news.ltn.com.tw/news/life/breakingnews/5432239", isLtnUrl],
  ["https://openai.com/pricing", isOpenAiWebUrl],
  ["/tmp/demo.pdf", isPdfTarget],
  ["C:\\docs\\demo.pdf", isPdfTarget],
  ["https://arxiv.org/pdf/2603.20617", isPdfTarget],
];

describe("source applicability", () => {
  it.each(supported)("accepts %s", (url, matcher) => expect(matcher(url)).toBe(true));

  it.each(supported.map(([, matcher]) => matcher))("rejects unknown targets", (matcher) => {
    expect(matcher("https://example.com/not-supported")).toBe(false);
  });

  it("parses source-specific targets", () => {
    expect(parseYouTubeVideoTarget("https://youtu.be/dQw4w9WgXcQ").videoId).toBe("dQw4w9WgXcQ");
    expect(parseGitHubRawContentTarget("https://github.com/a/b/blob/main/README.md").rawUrl).toBe(
      "https://raw.githubusercontent.com/a/b/main/README.md",
    );
    expect(parseTwitterTarget("https://fxtwitter.com/user/status/1")).toMatchObject({
      normalizedUrl: "https://x.com/user/status/1",
      statusId: "1",
    });
    expect(parsePiSessionTarget("https://pi.dev/session/#abc123/custom%20session.html&leafId=leaf-1")).toMatchObject({
      gistId: "abc123",
      fileName: "custom session.html",
      leafId: "leaf-1",
    });
  });

  it("rejects playlists and unsupported PDF schemes", () => {
    expect(isYouTubeVideoUrl("https://www.youtube.com/playlist?list=PL123")).toBe(false);
    expect(isPdfTarget("ftp://example.com/document.pdf")).toBe(false);
    expect(isPdfTarget("not-a-valid-url")).toBe(false);
  });

  it("normalizes parser failures to loader applicability errors", () => {
    expect(() =>
      requireLoaderApplicability("YouTubeLoader", "https://example.com/watch?v=dQw4w9WgXcQ", parseYouTubeVideoTarget),
    ).toThrow(LoaderNotApplicableError);
  });
});

describe("pipeline planning", () => {
  it("keeps strict YouTube plans source-specific", () => {
    expect(planForUrl("https://www.youtube.com/watch?v=dQw4w9WgXcQ")).toMatchObject({
      pipelineName: "youtube",
      contentType: "youtube_video",
      executionPlan: ["youtube", "youtube-ytdlp"],
      fallbackLoaders: [],
    });
  });

  it("uses the generic transport order for ordinary pages and source homepages", () => {
    const genericOrder = ["curl-cffi", "playwright-networkidle", "playwright-fast", "httpx"];
    expect(planForUrl("https://example.com").executionPlan).toEqual(genericOrder);
    expect(isLtnUrl("https://www.ltn.com.tw/")).toBe(false);
    expect(planForUrl("https://www.ltn.com.tw/").executionPlan).toEqual(genericOrder);
  });

  it("gives GitHub blob precedence over its PDF suffix", () => {
    expect(planForUrl("https://github.com/a/b/blob/main/file.pdf").pipelineName).toBe("github");
  });
});
