import type { Transformer } from "grammy";
import type { Update, UserFromGetMe } from "grammy/types";
import { describe, expect, it, vi } from "vitest";
import type { ChatSessionRegistry } from "../src/agent/session-registry.js";
import { loadSettings } from "../src/config/settings.js";
import type { Logger } from "../src/logging.js";
import { createTelegramAgentBot } from "../src/telegram/bot.js";

const botInfo: UserFromGetMe = {
  id: 999,
  is_bot: true,
  first_name: "Test Bot",
  username: "test_bot",
  can_join_groups: true,
  can_read_all_group_messages: false,
  supports_inline_queries: false,
  can_connect_to_business: false,
  has_main_web_app: false,
  has_topics_enabled: false,
  allows_users_to_create_topics: false,
  can_manage_bots: false,
  supports_join_request_queries: false,
};

const logger: Logger = {
  debug: vi.fn(),
  info: vi.fn(),
  warn: vi.fn(),
  error: vi.fn(),
};

function createSessions(overrides: Partial<ChatSessionRegistry> = {}): ChatSessionRegistry {
  return {
    submit: vi.fn(async (_chatId: number, _prompt: string, options: { onAccepted?: () => void } = {}) => {
      options.onAccepted?.();
      return { kind: "completed" as const, text: "AI 回覆" };
    }),
    appendPassiveContext: vi.fn(async () => undefined),
    cancel: vi.fn(async () => false),
    reset: vi.fn(async () => undefined),
    ...overrides,
  } as unknown as ChatSessionRegistry;
}

function privateMessage(updateId: number, text: string, userId = 7): Update {
  return {
    update_id: updateId,
    message: {
      message_id: updateId,
      date: 1_700_000_000,
      chat: { id: userId, type: "private", first_name: "Alice" },
      from: { id: userId, is_bot: false, first_name: "Alice" },
      text,
    },
  };
}

function installApiMock(
  bot: ReturnType<typeof createTelegramAgentBot>["bot"],
  beforeResponse: (method: string, payload: Record<string, unknown>) => Promise<void> | void = () => {},
) {
  const calls: Array<{ method: string; payload: Record<string, unknown> }> = [];
  let nextMessageId = 100;
  const transformer: Transformer = async (_previous, method, payload) => {
    const recordedPayload = payload as Record<string, unknown>;
    calls.push({ method, payload: recordedPayload });
    await beforeResponse(method, recordedPayload);
    if (method === "sendMessage") {
      return {
        ok: true,
        result: {
          message_id: nextMessageId++,
          date: 1_700_000_001,
          chat: { id: Number(recordedPayload.chat_id), type: "private", first_name: "Alice" },
          text: String(recordedPayload.text),
        },
      } as never;
    }
    if (method === "getFile") {
      return {
        ok: true,
        result: {
          file_id: String(recordedPayload.file_id),
          file_unique_id: "image-unique-id",
          file_size: 5,
          file_path: "photos/image.jpg",
        },
      } as never;
    }
    return { ok: true, result: true } as never;
  };
  bot.api.config.use(transformer);
  return calls;
}

describe("Telegram bot update routing", () => {
  it("routes private messages through the Pi session and edits the status reply", async () => {
    const sessions = createSessions();
    const telegram = createTelegramAgentBot(loadSettings({ BOT_TOKEN: "test-token" }), sessions, logger, { botInfo });
    const calls = installApiMock(telegram.bot);

    await telegram.bot.handleUpdate(privateMessage(1, "你好"));

    expect(sessions.submit).toHaveBeenCalledWith(7, "你好", {
      images: [],
      onAccepted: expect.any(Function),
    });
    expect(calls.map((call) => call.method)).toEqual(["sendMessage", "editMessageText"]);
    expect(calls[1]?.payload.text).toBe("AI 回覆");
  });

  it("enforces the allowlist before invoking session or Telegram APIs", async () => {
    const sessions = createSessions();
    const settings = loadSettings({ BOT_TOKEN: "test-token", BOT_WHITELIST: "7" });
    const telegram = createTelegramAgentBot(settings, sessions, logger, { botInfo });
    const calls = installApiMock(telegram.bot);

    await telegram.bot.handleUpdate(privateMessage(2, "不應處理", 8));

    expect(sessions.submit).not.toHaveBeenCalled();
    expect(calls).toEqual([]);
  });

  it("records unaddressed group updates as passive context", async () => {
    const sessions = createSessions();
    const telegram = createTelegramAgentBot(loadSettings({ BOT_TOKEN: "test-token" }), sessions, logger, { botInfo });
    installApiMock(telegram.bot);
    const update: Update = {
      update_id: 3,
      message: {
        message_id: 3,
        date: 1_700_000_000,
        chat: { id: -100, type: "supergroup", title: "測試群組" },
        from: { id: 136_817_688, is_bot: true, first_name: "Channel", username: "Channel_Bot" },
        sender_chat: { id: -200, type: "channel", title: "公告頻道" },
        text: "頻道公告",
      },
    };

    await telegram.bot.handleUpdate(update);

    expect(sessions.appendPassiveContext).toHaveBeenCalledWith(-100, "[群組旁聽訊息 from 公告頻道] 頻道公告");
    expect(sessions.submit).not.toHaveBeenCalled();
  });

  it("can process /cancel while an earlier agent update is still running", async () => {
    let finishSubmit: ((value: { kind: "completed"; text: string }) => void) | undefined;
    const pendingSubmit = new Promise<{ kind: "completed"; text: string }>((resolve) => {
      finishSubmit = resolve;
    });
    const sessions = createSessions({
      submit: vi.fn(async () => pendingSubmit),
      cancel: vi.fn(async () => true),
    });
    const telegram = createTelegramAgentBot(loadSettings({ BOT_TOKEN: "test-token" }), sessions, logger, { botInfo });
    installApiMock(telegram.bot);

    const runningUpdate = telegram.bot.handleUpdate(privateMessage(4, "長任務"));
    await vi.waitFor(() => expect(sessions.submit).toHaveBeenCalledOnce());
    const cancelUpdate = privateMessage(5, "/cancel");
    if (cancelUpdate.message) {
      cancelUpdate.message.entities = [{ offset: 0, length: 7, type: "bot_command" }];
    }
    await telegram.bot.handleUpdate(cancelUpdate);

    expect(sessions.cancel).toHaveBeenCalledWith(7);
    finishSubmit?.({ kind: "completed", text: "已完成" });
    await runningUpdate;
  });

  it("preserves per-chat submission order while an earlier image downloads", async () => {
    let finishImageDownload: ((response: Response) => void) | undefined;
    const pendingImageDownload = new Promise<Response>((resolve) => {
      finishImageDownload = resolve;
    });
    let finishFirstSubmission: ((value: { kind: "completed"; text: string }) => void) | undefined;
    const pendingFirstSubmission = new Promise<{ kind: "completed"; text: string }>((resolve) => {
      finishFirstSubmission = resolve;
    });
    const submissionOrder: string[] = [];
    const sessions = createSessions({
      submit: vi.fn(async (_chatId, prompt, options) => {
        submissionOrder.push(prompt);
        options.onAccepted?.();
        return prompt === "第一張" ? pendingFirstSubmission : { kind: "completed" as const, text: "AI 回覆" };
      }),
    });
    const imageFetchImplementation = vi.fn<typeof fetch>(async () => pendingImageDownload);
    const telegram = createTelegramAgentBot(loadSettings({ BOT_TOKEN: "test-token" }), sessions, logger, {
      botInfo,
      imageFetchImplementation,
    });
    installApiMock(telegram.bot);
    const photoUpdate = privateMessage(6, "");
    if (photoUpdate.message) {
      photoUpdate.message.photo = [
        { file_id: "image", file_unique_id: "image-unique-id", width: 100, height: 100, file_size: 5 },
      ];
      photoUpdate.message.caption = "第一張";
      delete photoUpdate.message.text;
    }

    const firstHandling = telegram.bot.handleUpdate(photoUpdate);
    await vi.waitFor(() => expect(imageFetchImplementation).toHaveBeenCalledOnce());
    const secondHandling = telegram.bot.handleUpdate(privateMessage(7, "第二則"));
    await Promise.resolve();
    expect(sessions.submit).not.toHaveBeenCalled();

    finishImageDownload?.(new Response("image"));
    await vi.waitFor(() => expect(sessions.submit).toHaveBeenCalledTimes(2));
    await secondHandling;
    expect(submissionOrder).toEqual(["第一張", "第二則"]);

    finishFirstSubmission?.({ kind: "completed", text: "第一則完成" });
    await firstHandling;
  });

  it("invalidates queued pre-reset work without blocking post-reset submissions", async () => {
    let finishImageDownload: ((response: Response) => void) | undefined;
    const pendingImageDownload = new Promise<Response>((resolve) => {
      finishImageDownload = resolve;
    });
    const submittedPrompts: string[] = [];
    const sessions = createSessions({
      submit: vi.fn(async (_chatId, prompt, options) => {
        submittedPrompts.push(prompt);
        options.onAccepted?.();
        return { kind: "completed" as const, text: "AI 回覆" };
      }),
    });
    const imageFetchImplementation = vi.fn<typeof fetch>(async () => pendingImageDownload);
    const telegram = createTelegramAgentBot(loadSettings({ BOT_TOKEN: "test-token" }), sessions, logger, {
      botInfo,
      imageFetchImplementation,
    });
    installApiMock(telegram.bot);
    const photoUpdate = privateMessage(8, "");
    if (photoUpdate.message) {
      photoUpdate.message.photo = [
        { file_id: "image", file_unique_id: "image-unique-id", width: 100, height: 100, file_size: 5 },
      ];
      photoUpdate.message.caption = "重設前圖片";
      delete photoUpdate.message.text;
    }

    const imageHandling = telegram.bot.handleUpdate(photoUpdate);
    await vi.waitFor(() => expect(imageFetchImplementation).toHaveBeenCalledOnce());
    const queuedHandling = telegram.bot.handleUpdate(privateMessage(9, "重設前排隊訊息"));
    await new Promise<void>((resolve) => setImmediate(resolve));
    const resetUpdate = privateMessage(10, "/reset");
    if (resetUpdate.message) {
      resetUpdate.message.entities = [{ offset: 0, length: 6, type: "bot_command" }];
    }
    await telegram.bot.handleUpdate(resetUpdate);
    await telegram.bot.handleUpdate(privateMessage(11, "重設後"));

    expect(sessions.reset).toHaveBeenCalledWith(7);
    expect(submittedPrompts).toEqual(["重設後"]);

    finishImageDownload?.(new Response("image"));
    await Promise.all([imageHandling, queuedHandling]);
    expect(submittedPrompts).toEqual(["重設後"]);
  });

  it("replaces the pending status when reset invalidates a completed submission", async () => {
    let finishSubmission: ((value: { kind: "completed"; text: string }) => void) | undefined;
    const pendingSubmission = new Promise<{ kind: "completed"; text: string }>((resolve) => {
      finishSubmission = resolve;
    });
    const sessions = createSessions({
      submit: vi.fn(async (_chatId, _prompt, options) => {
        options.onAccepted?.();
        return pendingSubmission;
      }),
    });
    const telegram = createTelegramAgentBot(loadSettings({ BOT_TOKEN: "test-token" }), sessions, logger, { botInfo });
    const calls = installApiMock(telegram.bot);

    const runningUpdate = telegram.bot.handleUpdate(privateMessage(12, "長任務"));
    await vi.waitFor(() => expect(sessions.submit).toHaveBeenCalledOnce());
    const resetUpdate = privateMessage(13, "/reset");
    if (resetUpdate.message) {
      resetUpdate.message.entities = [{ offset: 0, length: 6, type: "bot_command" }];
    }
    await telegram.bot.handleUpdate(resetUpdate);

    finishSubmission?.({ kind: "completed", text: "過期回覆" });
    await runningUpdate;
    expect(calls.map((call) => call.method)).toEqual(["sendMessage", "sendMessage", "editMessageText"]);
    expect(calls[2]?.payload.text).toBe("此請求已因重設對話而取消。");
  });

  it("does not publish a stale Morsel reply after reset", async () => {
    let finishPublication: ((value: string) => void) | undefined;
    const pendingPublication = new Promise<string>((resolve) => {
      finishPublication = resolve;
    });
    const sessions = createSessions({
      submit: vi.fn(async (_chatId, _prompt, options) => {
        options.onAccepted?.();
        return { kind: "completed" as const, text: "這是一段很長的回覆內容" };
      }),
    });
    const publish = vi.fn(async () => pendingPublication);
    const settings = loadSettings({
      BOT_TOKEN: "test-token",
      MORSEL_API_KEY: "secret",
      MORSEL_LONG_REPLY_THRESHOLD: "5",
    });
    const telegram = createTelegramAgentBot(settings, sessions, logger, {
      botInfo,
      morselPublisher: { isConfigured: true, publish },
    });
    const calls = installApiMock(telegram.bot);

    const runningUpdate = telegram.bot.handleUpdate(privateMessage(12, "請回答"));
    await vi.waitFor(() => expect(publish).toHaveBeenCalledOnce());
    const resetUpdate = privateMessage(13, "/reset");
    if (resetUpdate.message) {
      resetUpdate.message.entities = [{ offset: 0, length: 6, type: "bot_command" }];
    }
    await telegram.bot.handleUpdate(resetUpdate);

    finishPublication?.("https://morsel.example/s/share");
    await runningUpdate;
    expect(calls.map((call) => call.method)).toEqual(["sendMessage", "sendMessage", "editMessageText"]);
    expect(calls[2]?.payload.text).toBe("此請求已因重設對話而取消。");
  });

  it("replaces the pending status when an invalidated submission fails", async () => {
    let failSubmission: ((reason: Error) => void) | undefined;
    const pendingSubmission = new Promise<never>((_resolve, reject) => {
      failSubmission = reject;
    });
    const sessions = createSessions({
      submit: vi.fn(async () => pendingSubmission),
    });
    const telegram = createTelegramAgentBot(loadSettings({ BOT_TOKEN: "test-token" }), sessions, logger, { botInfo });
    const calls = installApiMock(telegram.bot);

    const runningUpdate = telegram.bot.handleUpdate(privateMessage(14, "長任務"));
    await vi.waitFor(() => expect(sessions.submit).toHaveBeenCalledOnce());
    const resetUpdate = privateMessage(15, "/reset");
    if (resetUpdate.message) {
      resetUpdate.message.entities = [{ offset: 0, length: 6, type: "bot_command" }];
    }
    await telegram.bot.handleUpdate(resetUpdate);

    failSubmission?.(new Error("Pi session access was invalidated by reset"));
    await runningUpdate;
    expect(calls.map((call) => call.method)).toEqual(["sendMessage", "sendMessage", "editMessageText"]);
    expect(calls[2]?.payload.text).toBe("此請求已因重設對話而取消。");
  });

  it("cleans up reply continuations when reset interrupts chunk delivery", async () => {
    let finishChunkDelivery: (() => void) | undefined;
    const pendingChunkDelivery = new Promise<void>((resolve) => {
      finishChunkDelivery = resolve;
    });
    const completedChunk = "b".repeat(4_096);
    const inFlightChunk = "c".repeat(4_096);
    const sessions = createSessions({
      submit: vi.fn(async (_chatId, _prompt, options) => {
        options.onAccepted?.();
        return { kind: "completed" as const, text: `${"a".repeat(4_096)}${completedChunk}${inFlightChunk}d` };
      }),
    });
    const telegram = createTelegramAgentBot(loadSettings({ BOT_TOKEN: "test-token" }), sessions, logger, { botInfo });
    const calls = installApiMock(telegram.bot, async (method, payload) => {
      if (method === "sendMessage" && payload.text === inFlightChunk) await pendingChunkDelivery;
    });

    const runningUpdate = telegram.bot.handleUpdate(privateMessage(16, "長回覆"));
    await vi.waitFor(() => expect(calls.some((call) => call.payload.text === inFlightChunk)).toBe(true));
    const resetUpdate = privateMessage(17, "/reset");
    if (resetUpdate.message) {
      resetUpdate.message.entities = [{ offset: 0, length: 6, type: "bot_command" }];
    }
    await telegram.bot.handleUpdate(resetUpdate);

    finishChunkDelivery?.();
    await runningUpdate;
    expect(calls.map((call) => call.method)).toEqual([
      "sendMessage",
      "editMessageText",
      "sendMessage",
      "sendMessage",
      "sendMessage",
      "deleteMessage",
      "deleteMessage",
      "editMessageText",
    ]);
    expect(calls.some((call) => call.payload.text === "d")).toBe(false);
    expect(calls.filter((call) => call.method === "deleteMessage").map((call) => call.payload.message_id)).toEqual([
      101, 103,
    ]);
    expect(calls.at(-1)?.payload.text).toBe("此請求已因重設對話而取消。");
  });

  it("orders passive group context after an earlier addressed image submission", async () => {
    let finishImageDownload: ((response: Response) => void) | undefined;
    const pendingImageDownload = new Promise<Response>((resolve) => {
      finishImageDownload = resolve;
    });
    const events: string[] = [];
    const sessions = createSessions({
      submit: vi.fn(async (_chatId, _prompt, options) => {
        events.push("submit");
        options.onAccepted?.();
        return { kind: "completed" as const, text: "AI 回覆" };
      }),
      appendPassiveContext: vi.fn(async () => {
        events.push("passive");
      }),
    });
    const imageFetchImplementation = vi.fn<typeof fetch>(async () => pendingImageDownload);
    const telegram = createTelegramAgentBot(loadSettings({ BOT_TOKEN: "test-token" }), sessions, logger, {
      botInfo,
      imageFetchImplementation,
    });
    installApiMock(telegram.bot);
    const addressedUpdate: Update = {
      update_id: 12,
      message: {
        message_id: 12,
        date: 1_700_000_000,
        chat: { id: -100, type: "supergroup", title: "測試群組" },
        from: { id: 7, is_bot: false, first_name: "Alice" },
        photo: [{ file_id: "image", file_unique_id: "image-unique-id", width: 100, height: 100, file_size: 5 }],
        caption: "@test_bot 請看圖",
      },
    };
    const passiveUpdate: Update = {
      update_id: 13,
      message: {
        message_id: 13,
        date: 1_700_000_001,
        chat: { id: -100, type: "supergroup", title: "測試群組" },
        from: { id: 8, is_bot: false, first_name: "Bob" },
        text: "後續群組訊息",
      },
    };

    const addressedHandling = telegram.bot.handleUpdate(addressedUpdate);
    await vi.waitFor(() => expect(imageFetchImplementation).toHaveBeenCalledOnce());
    const passiveHandling = telegram.bot.handleUpdate(passiveUpdate);
    await Promise.resolve();
    expect(events).toEqual([]);

    finishImageDownload?.(new Response("image"));
    await Promise.all([addressedHandling, passiveHandling]);
    expect(events).toEqual(["submit", "passive"]);
  });

  it("reports oversized image errors without invoking the agent", async () => {
    const sessions = createSessions();
    const settings = loadSettings({ BOT_TOKEN: "test-token", BOT_IMAGE_MAX_BYTES: "10" });
    const telegram = createTelegramAgentBot(settings, sessions, logger, { botInfo });
    const calls = installApiMock(telegram.bot);
    const update = privateMessage(6, "看圖");
    if (update.message) {
      update.message.photo = [{ file_id: "large", file_unique_id: "large", width: 100, height: 100, file_size: 11 }];
    }

    await telegram.bot.handleUpdate(update);

    expect(sessions.submit).not.toHaveBeenCalled();
    expect(calls).toHaveLength(1);
    expect(calls[0]?.payload.text).toBe("圖片超過允許的大小，無法處理。");
  });

  it("publishes long replies through the configured Morsel path", async () => {
    const sessions = createSessions({
      submit: vi.fn(async () => ({ kind: "completed" as const, text: "這是一段很長的回覆內容" })),
    });
    const publish = vi.fn(async () => "https://morsel.example/s/share");
    const settings = loadSettings({
      BOT_TOKEN: "test-token",
      MORSEL_API_KEY: "secret",
      MORSEL_LONG_REPLY_THRESHOLD: "5",
    });
    const telegram = createTelegramAgentBot(settings, sessions, logger, {
      botInfo,
      morselPublisher: { isConfigured: true, publish },
    });
    const calls = installApiMock(telegram.bot);

    await telegram.bot.handleUpdate(privateMessage(7, "請回答"));

    expect(publish).toHaveBeenCalledWith("這是一段很長的回覆內容");
    expect(calls[1]?.payload.text).toContain("https://morsel.example/s/share");
  });
});
