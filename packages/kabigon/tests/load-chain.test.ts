import { afterEach, describe, expect, it } from "vitest";

import {
  LoaderContentError,
  LoaderError,
  LoaderNotApplicableError,
  LoaderTimeoutError,
  MissingRequirementError,
} from "../src/core/errors.js";
import { withDeadline } from "../src/core/execution.js";
import type { Loader } from "../src/core/loader.js";
import { AttemptStatus } from "../src/core/results.js";
import { explainLoadChain, LoadChain, LoadChainExplanation, resolveExplicitLoadChain } from "../src/load-chain.js";
import { ContentContract, ContentType } from "../src/pipelines/catalog.js";

const originalKey = process.env.FIRECRAWL_API_KEY;
afterEach(() => {
  if (originalKey === undefined) delete process.env.FIRECRAWL_API_KEY;
  else process.env.FIRECRAWL_API_KEY = originalKey;
});

class SuccessLoader implements Loader {
  async load(url: string): Promise<string> {
    return `loaded ${url}`;
  }
}

class EmptyLoader implements Loader {
  async load(): Promise<string> {
    return "";
  }
}

class NotApplicableLoader implements Loader {
  async load(url: string): Promise<string> {
    throw new LoaderNotApplicableError("NotApplicableLoader", url, "unsupported domain");
  }
}

class TimeoutLoader implements Loader {
  async load(url: string): Promise<string> {
    throw new LoaderTimeoutError("TimeoutLoader", url, 3);
  }
}

class ContentFailLoader implements Loader {
  async load(url: string): Promise<string> {
    throw new LoaderContentError("ContentFailLoader", url, "parse failed");
  }
}

const factories = {
  success: () => new SuccessLoader(),
  empty: () => new EmptyLoader(),
  "not-applicable": () => new NotApplicableLoader(),
  timeout: () => new TimeoutLoader(),
  "content-fail": () => new ContentFailLoader(),
};

describe("load chain", () => {
  it("explains plans without constructing loaders", () => {
    expect(explainLoadChain("https://www.youtube.com/watch?v=dQw4w9WgXcQ").toObject()).toMatchObject({
      pipeline: "youtube",
      content_type: "youtube_video",
      execution_plan: ["youtube", "youtube-ytdlp"],
      missing_requirements: [],
    });
  });

  it("runs custom loaders lazily and records empty attempts", async () => {
    const built: string[] = [];
    const chain = resolveExplicitLoadChain("https://example.com", ["empty", "success", "unused"], {
      getFactory: (name) => () => {
        built.push(name);
        if (name === "empty") return new EmptyLoader();
        if (name === "success") return new SuccessLoader();
        throw new Error("must not construct");
      },
    });
    const result = await chain.loadDetailed();
    expect(result.content).toBe("loaded https://example.com");
    expect(result.attempts.map((attempt) => attempt.status)).toEqual([AttemptStatus.Empty, AttemptStatus.Success]);
    expect(built).toEqual(["empty", "success"]);
  });

  it("reports typed failures in attempt order", async () => {
    const names = ["not-applicable", "timeout", "content-fail", "empty"];
    const chain = resolveExplicitLoadChain("https://example.com", names, {
      getFactory: (name) => factories[name as keyof typeof factories],
    });
    const error = await chain.load().catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(LoaderError);
    expect((error as LoaderError).details).toEqual([
      "not-applicable: Not applicable (unsupported domain)",
      "timeout: Timeout after 3s",
      "content-fail: Content extraction failed - parse failed",
      "empty: Empty result",
    ]);
  });

  it("skips unavailable alternatives but fails early when none are eligible", async () => {
    delete process.env.FIRECRAWL_API_KEY;
    const chain = resolveExplicitLoadChain("https://example.com", ["firecrawl", "success"], {
      getFactory: (name) => factories[name === "success" ? "success" : "empty"],
      getRequirements: (name) => (name === "firecrawl" ? ["FIRECRAWL_API_KEY"] : []),
    });
    const result = await chain.loadDetailed();
    expect(result.attempts.map((attempt) => attempt.status)).toEqual([AttemptStatus.Skipped, AttemptStatus.Success]);

    expect(() =>
      resolveExplicitLoadChain("https://example.com", ["firecrawl"], {
        getFactory: () => () => new SuccessLoader(),
        getRequirements: () => ["FIRECRAWL_API_KEY"],
      }),
    ).toThrow(MissingRequirementError);
  });

  it("stops after a shared deadline", async () => {
    class WaitingLoader implements Loader {
      async load(_url: string, signal?: AbortSignal): Promise<string> {
        await new Promise<void>((resolve, reject) => {
          signal?.addEventListener("abort", () => reject(signal.reason), { once: true });
          if (!signal) resolve();
        });
        return "late";
      }
    }
    const built: string[] = [];
    const chain = resolveExplicitLoadChain("https://example.com", ["waiting", "success"], {
      getFactory: (name) => () => {
        built.push(name);
        return name === "waiting" ? new WaitingLoader() : new SuccessLoader();
      },
    });
    await expect(withDeadline(performance.now() + 10, () => chain.load())).rejects.toBeInstanceOf(LoaderError);
    expect(built).toEqual(["waiting"]);
  });

  it("races caller cancellation without a shared deadline", async () => {
    class IgnoringLoader implements Loader {
      async load(): Promise<string> {
        return new Promise(() => undefined);
      }
    }
    const chain = resolveExplicitLoadChain("https://example.com", ["ignoring"], {
      getFactory: () => () => new IgnoringLoader(),
    });
    const controller = new AbortController();
    const reason = new Error("cancelled by caller");
    const loading = chain.load(controller.signal);
    controller.abort(reason);
    await expect(loading).rejects.toBe(reason);
  });

  it("rejects a generic result for a source-required contract", async () => {
    const explanation = new LoadChainExplanation(
      "https://x.com/user/status/1",
      "twitter",
      ContentType.SocialPost,
      ["generic"],
      [],
      ["generic"],
      [],
      [],
      ["generic"],
      [],
      ContentContract.SourceRequired,
    );
    const chain = new LoadChain({
      getFactory: () => () => new SuccessLoader(),
      explanation,
      getRequirements: () => [],
      getContentType: () => ContentType.GenericWeb,
    });
    await expect(chain.load()).rejects.toThrow("Rejected content type");
  });
});
