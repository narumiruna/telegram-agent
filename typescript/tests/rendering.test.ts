import { describe, expect, it } from "vitest";

import { sanitizeTelegramText, telegramHtmlChunks, trimUrl } from "../src/telegram/rendering.js";

describe("Telegram rendering", () => {
  it("renders basic Markdown as Telegram HTML and escapes unsafe markup", () => {
    expect(telegramHtmlChunks("# Title\n**bold** `<tag>`\nhttps://example.test/a?x=1&y=2")).toEqual([
      '<b>Title</b>\n<b>bold</b> <code>&lt;tag&gt;</code>\n<a href="https://example.test/a?x=1&amp;y=2">https://example.test/a?x=1&amp;y=2</a>',
    ]);
  });

  it("chunks by Unicode code point and strips disallowed controls", () => {
    expect(telegramHtmlChunks("😀😀😀", 2)).toEqual(["😀😀", "😀"]);
    expect(sanitizeTelegramText("a\u0000b\r\nc")).toBe("ab\nc");
  });

  it("trims punctuation from URLs", () => {
    expect(trimUrl("https://example.test/path。")).toBe("https://example.test/path");
  });
});
