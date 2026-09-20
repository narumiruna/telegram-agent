import { spawn } from "node:child_process";
import { mkdtemp, readFile, rm, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { LoaderContentError, LoaderTimeoutError, MissingDependencyError } from "../core/errors.js";
import type { Loader } from "../core/loader.js";
import { parseYouTubeVideoTarget, requireLoaderApplicability } from "../sources/applicability.js";

interface CommandResult {
  stdout: string;
  stderr: string;
}

type CommandRunner = (command: string, args: readonly string[], signal?: AbortSignal) => Promise<CommandResult>;

export const DEFAULT_YTDLP_DOWNLOAD_TIMEOUT_MS = 120_000;
export const DEFAULT_WHISPER_TIMEOUT_MS = 300_000;
export const DEFAULT_MAX_MEDIA_BYTES = 100 * 1024 * 1024;
export const DEFAULT_MAX_MEDIA_DURATION_SECONDS = 3_600;

export const runCommand: CommandRunner = (command, args, signal) =>
  new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(signal.reason);
      return;
    }
    const detached = process.platform !== "win32";
    const child = spawn(command, args, { detached, stdio: ["ignore", "pipe", "pipe"] });
    const stdout: Buffer[] = [];
    const stderr: Buffer[] = [];
    let killTimer: NodeJS.Timeout | undefined;
    const kill = (signalName: NodeJS.Signals) => {
      try {
        if (detached && child.pid) process.kill(-child.pid, signalName);
        else child.kill(signalName);
      } catch {
        // The process has already exited.
      }
    };
    const onAbort = () => {
      kill("SIGTERM");
      killTimer = setTimeout(() => kill("SIGKILL"), 1_000);
      killTimer.unref();
    };
    const finish = (operation: () => void) => {
      signal?.removeEventListener("abort", onAbort);
      if (killTimer) clearTimeout(killTimer);
      operation();
    };
    signal?.addEventListener("abort", onAbort, { once: true });
    if (signal?.aborted) onAbort();
    child.stdout.on("data", (chunk: Buffer) => stdout.push(chunk));
    child.stderr.on("data", (chunk: Buffer) => stderr.push(chunk));
    child.once("error", (error) => finish(() => reject(error)));
    child.once("close", (code) =>
      finish(() => {
        if (signal?.aborted) {
          reject(signal.reason);
          return;
        }
        const result = { stdout: Buffer.concat(stdout).toString(), stderr: Buffer.concat(stderr).toString() };
        if (code === 0) resolve(result);
        else reject(new Error(`${command} exited with code ${String(code)}: ${result.stderr.trim()}`));
      }),
    );
  });

export class YtdlpLoader implements Loader {
  constructor(
    private readonly options: {
      model?: string;
      ytdlpPath?: string;
      whisperPath?: string;
      downloadTimeoutMs?: number;
      transcriptionTimeoutMs?: number;
      maxMediaBytes?: number;
      maxDurationSeconds?: number;
      commandRunner?: CommandRunner;
    } = {},
  ) {}

  async load(url: string, signal?: AbortSignal): Promise<string> {
    const directory = await mkdtemp(join(tmpdir(), "kabigon-audio-"));
    const audioPath = join(directory, "audio.mp3");
    try {
      const ytdlp = this.options.ytdlpPath ?? process.env.YTDLP_PATH ?? "yt-dlp";
      const maxMediaBytes = this.options.maxMediaBytes ?? DEFAULT_MAX_MEDIA_BYTES;
      const maxDurationSeconds = this.options.maxDurationSeconds ?? DEFAULT_MAX_MEDIA_DURATION_SECONDS;
      const downloadArgs = [
        "--no-playlist",
        "--format",
        "bestaudio/best",
        "--extract-audio",
        "--audio-format",
        "mp3",
        "--audio-quality",
        "192K",
        "--max-filesize",
        String(maxMediaBytes),
        "--match-filter",
        `duration <= ${maxDurationSeconds}`,
        "--output",
        join(directory, "audio.%(ext)s"),
      ];
      if (process.env.FFMPEG_PATH) downloadArgs.push("--ffmpeg-location", process.env.FFMPEG_PATH);
      downloadArgs.push(url);
      const commandRunner = this.options.commandRunner ?? runCommand;
      const downloadTimeoutMs = this.options.downloadTimeoutMs ?? DEFAULT_YTDLP_DOWNLOAD_TIMEOUT_MS;
      const downloadTimeout = AbortSignal.timeout(downloadTimeoutMs);
      const downloadSignal = signal ? AbortSignal.any([signal, downloadTimeout]) : downloadTimeout;
      try {
        await commandRunner(ytdlp, downloadArgs, downloadSignal);
      } catch (error) {
        if (signal?.aborted) throw signal.reason;
        if (downloadTimeout.aborted) {
          throw new LoaderTimeoutError("YtdlpLoader", url, downloadTimeoutMs / 1_000, "yt-dlp timed out.");
        }
        if (error instanceof Error && "code" in error && error.code === "ENOENT") {
          throw new MissingDependencyError("ytdlp", ytdlp, "Install yt-dlp or set YTDLP_PATH.");
        }
        throw error;
      }
      if ((await stat(audioPath)).size > maxMediaBytes) {
        throw new LoaderContentError("YtdlpLoader", url, `Downloaded audio exceeds the ${maxMediaBytes} byte limit`);
      }

      const whisper = this.options.whisperPath ?? process.env.WHISPER_PATH ?? "whisper";
      const transcriptionTimeoutMs = this.options.transcriptionTimeoutMs ?? DEFAULT_WHISPER_TIMEOUT_MS;
      const transcriptionTimeout = AbortSignal.timeout(transcriptionTimeoutMs);
      const transcriptionSignal = signal ? AbortSignal.any([signal, transcriptionTimeout]) : transcriptionTimeout;
      try {
        await commandRunner(
          whisper,
          [audioPath, "--model", this.options.model ?? "tiny", "--output_dir", directory, "--output_format", "txt"],
          transcriptionSignal,
        );
      } catch (error) {
        if (signal?.aborted) throw signal.reason;
        if (transcriptionTimeout.aborted) {
          throw new LoaderTimeoutError(
            "YtdlpLoader",
            url,
            transcriptionTimeoutMs / 1_000,
            "Whisper transcription timed out.",
          );
        }
        if (error instanceof Error && "code" in error && error.code === "ENOENT") {
          throw new MissingDependencyError("ytdlp", whisper, "Install openai-whisper or set WHISPER_PATH.");
        }
        throw error;
      }
      return (await readFile(join(directory, "audio.txt"), "utf8")).trim();
    } catch (error) {
      if (
        error instanceof MissingDependencyError ||
        error instanceof LoaderContentError ||
        error instanceof LoaderTimeoutError
      )
        throw error;
      if (signal?.aborted) throw signal.reason;
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
