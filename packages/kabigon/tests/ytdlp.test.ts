import { writeFile } from "node:fs/promises"

import { describe, expect, it, vi } from "vitest"

import { runCommand, YtdlpLoader } from "../src/loaders/ytdlp.js"

describe("yt-dlp loader limits", () => {
  it("terminates subprocesses when their signal expires", async () => {
    const timeout = AbortSignal.timeout(10)
    await expect(
      runCommand(process.execPath, ["-e", "setInterval(() => undefined, 1000)"], timeout),
    ).rejects.toMatchObject({ name: "TimeoutError" })
  })

  it("rejects oversized audio before starting Whisper", async () => {
    const commands: Array<{ command: string; args: readonly string[] }> = []
    const whisper = vi.fn(async () => ({ stdout: "", stderr: "" }))
    const loader = new YtdlpLoader({
      ytdlpPath: "fake-ytdlp",
      whisperPath: "fake-whisper",
      maxMediaBytes: 3,
      maxDurationSeconds: 60,
      commandRunner: async (command, args) => {
        commands.push({ command, args })
        if (command === "fake-whisper") return whisper()
        const outputIndex = args.indexOf("--output")
        const output = args[outputIndex + 1]?.replace("%(ext)s", "mp3")
        if (!output) throw new Error("missing output path")
        await writeFile(output, "four")
        return { stdout: "", stderr: "" }
      },
    })

    await expect(loader.load("https://example.com/video")).rejects.toThrow("3 byte limit")
    expect(commands[0]?.args).toEqual(
      expect.arrayContaining(["--max-filesize", "3", "--match-filter", "duration <= 60"]),
    )
    expect(whisper).not.toHaveBeenCalled()
  })
})
