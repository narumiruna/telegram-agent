import { describe, expect, it, vi } from "vitest";

import { YouTubeLoader } from "../src/loaders/youtube.js";

const mocks = vi.hoisted(() => ({ fetchTranscript: vi.fn() }));

vi.mock("youtube-transcript", () => ({ fetchTranscript: mocks.fetchTranscript }));

describe("YouTube loader cancellation", () => {
  it("stops language attempts after cancellation", async () => {
    let finishAttempt: ((value: Array<{ text: string }>) => void) | undefined;
    mocks.fetchTranscript.mockImplementationOnce(
      () =>
        new Promise<Array<{ text: string }>>((resolve) => {
          finishAttempt = resolve;
        }),
    );
    const controller = new AbortController();
    const loading = new YouTubeLoader(["en", "fr"]).load(
      "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
      controller.signal,
    );
    await vi.waitFor(() => expect(mocks.fetchTranscript).toHaveBeenCalledOnce());

    controller.abort(new Error("cancelled"));
    finishAttempt?.([{ text: "stale transcript" }]);

    await expect(loading).rejects.toThrow("cancelled");
    expect(mocks.fetchTranscript).toHaveBeenCalledOnce();
  });
});
