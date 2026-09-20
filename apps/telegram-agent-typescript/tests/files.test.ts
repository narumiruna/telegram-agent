import type { Api } from "grammy"
import { describe, expect, it, vi } from "vitest"

import { downloadTelegramImage } from "../src/telegram/files.js"

describe("downloadTelegramImage", () => {
  it("rejects redirects instead of following Telegram file downloads to an untrusted host", async () => {
    const api = {
      getFile: vi.fn(async () => ({
        file_id: "image",
        file_unique_id: "image-unique-id",
        file_size: 5,
        file_path: "photos/image.jpg",
      })),
    } as unknown as Api
    const fetchImplementation = vi.fn<typeof fetch>(async (_input, init) => {
      expect(init?.redirect).toBe("manual")
      return new Response(null, {
        status: 302,
        headers: { location: "http://127.0.0.1/private" },
      })
    })

    await expect(
      downloadTelegramImage(
        api,
        "secret-token",
        { fileId: "image", filename: "image.jpg", mediaType: "image/jpeg" },
        100,
        fetchImplementation,
      ),
    ).rejects.toThrow("Telegram file download failed with HTTP 302")
    expect(fetchImplementation).toHaveBeenCalledOnce()
  })
})
