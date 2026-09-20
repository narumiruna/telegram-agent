import { describe, expect, it } from "vitest"

import {
  imageReferences,
  isBotAddressed,
  passiveGroupContext,
  promptWithReplyContext,
  stripBotMention,
} from "../src/telegram/messages.js"

describe("Telegram message normalization", () => {
  it("detects and strips case-insensitive mentions", () => {
    const message = { message_id: 1, text: "@FakeBot 請回答" }
    expect(isBotAddressed(message, 42, "fakebot")).toBe(true)
    expect(stripBotMention(message.text, "fakebot")).toBe("請回答")
  })

  it("records unaddressed user and sender-chat messages as passive context", () => {
    expect(
      passiveGroupContext({
        message_id: 1,
        text: "我想吃牛肉麵",
        from: { id: 7, username: "alice" },
      }),
    ).toBe("[群組旁聽訊息 from @alice] 我想吃牛肉麵")
    expect(
      passiveGroupContext({
        message_id: 2,
        text: "頻道公告",
        sender_chat: { id: -100, title: "公告頻道" },
      }),
    ).toBe("[群組旁聽訊息 from 公告頻道] 頻道公告")
  })

  it("includes replied human content in the prompt", () => {
    const prompt = promptWithReplyContext(
      {
        message_id: 11,
        text: "@fakebot 怎麼看？",
        reply_to_message: {
          message_id: 10,
          date: 1_700_000_000,
          from: { id: 7, username: "alice" },
          text: "原始內容",
        },
      },
      "怎麼看？",
    )

    expect(prompt).toContain("Sender: @alice")
    expect(prompt).toContain("Message ID: 10")
    expect(prompt).toContain("Content: 原始內容")
    expect(prompt).toContain("Current user message:\n怎麼看？")
  })

  it("selects the largest current and replied image", () => {
    const references = imageReferences({
      message_id: 2,
      photo: [
        { file_id: "small", width: 100, height: 100 },
        { file_id: "large", width: 800, height: 600, file_size: 123 },
      ],
      reply_to_message: {
        message_id: 1,
        document: { file_id: "document", file_name: "plot.webp", mime_type: "image/webp" },
      },
    })

    expect(references).toEqual([
      { fileId: "large", filename: "telegram-photo.jpg", mediaType: "image/jpeg", fileSize: 123 },
      { fileId: "document", filename: "replied-plot.webp", mediaType: "image/webp" },
    ])
  })
})
