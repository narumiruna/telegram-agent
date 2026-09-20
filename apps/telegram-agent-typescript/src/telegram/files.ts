import type { Api } from "grammy";

import type { ImageReference } from "./messages.js";

export class TelegramDownloadTooLargeError extends Error {}

export async function downloadTelegramImage(
  api: Api,
  token: string,
  reference: ImageReference,
  maxBytes: number,
  fetchImplementation: typeof fetch = fetch,
): Promise<{ type: "image"; data: string; mimeType: string }> {
  if (reference.fileSize !== undefined && reference.fileSize > maxBytes) {
    throw new TelegramDownloadTooLargeError("Telegram image exceeds the configured byte limit");
  }
  const file = await api.getFile(reference.fileId);
  if (!file.file_path) throw new Error("Telegram did not return a file path");
  if (file.file_size !== undefined && file.file_size > maxBytes) {
    throw new TelegramDownloadTooLargeError("Telegram image exceeds the configured byte limit");
  }

  const response = await fetchImplementation(`https://api.telegram.org/file/bot${token}/${file.file_path}`, {
    signal: AbortSignal.timeout(60_000),
  });
  if (!response.ok) throw new Error(`Telegram file download failed with HTTP ${response.status}`);
  const declaredLength = Number(response.headers.get("content-length"));
  if (Number.isFinite(declaredLength) && declaredLength > maxBytes) {
    throw new TelegramDownloadTooLargeError("Telegram image exceeds the configured byte limit");
  }
  if (!response.body) throw new Error("Telegram file download returned an empty body");

  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let received = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    received += value.byteLength;
    if (received > maxBytes) {
      await reader.cancel();
      throw new TelegramDownloadTooLargeError("Telegram image exceeds the configured byte limit");
    }
    chunks.push(value);
  }
  const content = Buffer.concat(chunks.map((chunk) => Buffer.from(chunk)));
  return { type: "image", data: content.toString("base64"), mimeType: reference.mediaType };
}
