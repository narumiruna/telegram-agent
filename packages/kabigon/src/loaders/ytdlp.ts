import { spawn } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { LoaderContentError, MissingDependencyError } from "../core/errors.js";
import type { Loader } from "../core/loader.js";
import { parseYouTubeVideoTarget, requireLoaderApplicability } from "../sources/applicability.js";

interface CommandResult {
  stdout: string;
  stderr: string;
}

async function runCommand(command: string, args: readonly string[], signal?: AbortSignal): Promise<CommandResult> {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { signal, stdio: ["ignore", "pipe", "pipe"] });
    const stdout: Buffer[] = [];
    const stderr: Buffer[] = [];
    child.stdout.on("data", (chunk: Buffer) => stdout.push(chunk));
    child.stderr.on("data", (chunk: Buffer) => stderr.push(chunk));
    child.once("error", reject);
    child.once("close", (code) => {
      const result = { stdout: Buffer.concat(stdout).toString(), stderr: Buffer.concat(stderr).toString() };
      if (code === 0) resolve(result);
      else reject(new Error(`${command} exited with code ${String(code)}: ${result.stderr.trim()}`));
    });
  });
}

export class YtdlpLoader implements Loader {
  constructor(
    private readonly options: {
      model?: string;
      ytdlpPath?: string;
      whisperPath?: string;
    } = {},
  ) {}

  async load(url: string, signal?: AbortSignal): Promise<string> {
    const directory = await mkdtemp(join(tmpdir(), "kabigon-audio-"));
    const audioPath = join(directory, "audio.mp3");
    try {
      const ytdlp = this.options.ytdlpPath ?? process.env.YTDLP_PATH ?? "yt-dlp";
      const downloadArgs = [
        "--no-playlist",
        "--format",
        "bestaudio/best",
        "--extract-audio",
        "--audio-format",
        "mp3",
        "--audio-quality",
        "192K",
        "--output",
        join(directory, "audio.%(ext)s"),
      ];
      if (process.env.FFMPEG_PATH) downloadArgs.push("--ffmpeg-location", process.env.FFMPEG_PATH);
      downloadArgs.push(url);
      try {
        await runCommand(ytdlp, downloadArgs, signal);
      } catch (error) {
        if (error instanceof Error && "code" in error && error.code === "ENOENT") {
          throw new MissingDependencyError("ytdlp", ytdlp, "Install yt-dlp or set YTDLP_PATH.");
        }
        throw error;
      }

      const whisper = this.options.whisperPath ?? process.env.WHISPER_PATH ?? "whisper";
      try {
        await runCommand(
          whisper,
          [audioPath, "--model", this.options.model ?? "tiny", "--output_dir", directory, "--output_format", "txt"],
          signal,
        );
      } catch (error) {
        if (error instanceof Error && "code" in error && error.code === "ENOENT") {
          throw new MissingDependencyError("ytdlp", whisper, "Install openai-whisper or set WHISPER_PATH.");
        }
        throw error;
      }
      return (await readFile(join(directory, "audio.txt"), "utf8")).trim();
    } catch (error) {
      if (error instanceof MissingDependencyError) throw error;
      throw new LoaderContentError("YtdlpLoader", url, `Audio transcription failed: ${String(error)}`);
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  }
}

export class YouTubeYtdlpLoader implements Loader {
  constructor(private readonly loader: Loader = new YtdlpLoader()) {}

  async load(url: string, signal?: AbortSignal): Promise<string> {
    requireLoaderApplicability("YouTubeYtdlpLoader", url, parseYouTubeVideoTarget);
    return this.loader.load(url, signal);
  }
}

export { YouTubeYtdlpLoader as YoutubeYtdlpLoader };
