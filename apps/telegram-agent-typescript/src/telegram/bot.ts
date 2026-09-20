import { type RunnerHandle, run } from "@grammyjs/runner";
import { Bot, type Context, GrammyError, HttpError } from "grammy";
import type { UserFromGetMe } from "grammy/types";

import type { ChatSessionRegistry } from "../agent/session-registry.js";
import type { Settings } from "../config/settings.js";
import type { Logger } from "../logging.js";
import { createMorselPublisher, type MorselPublisher } from "../morsel.js";
import { downloadTelegramImage, TelegramDownloadTooLargeError } from "./files.js";
import {
  defaultImagePrompt,
  imageReferences,
  isBotAddressed,
  messageText,
  passiveGroupContext,
  promptWithReplyContext,
  stripBotMention,
  type TelegramMessageLike,
} from "./messages.js";
import { sanitizeTelegramText, telegramHtmlChunks } from "./rendering.js";

export interface TelegramAgentBot {
  bot: Bot;
  start(): Promise<void>;
  stop(): Promise<void>;
}

interface TelegramBotDependencies {
  botInfo?: UserFromGetMe;
  imageFetchImplementation?: typeof fetch;
  morselPublisher?: Pick<MorselPublisher, "isConfigured" | "publish">;
}

const updateConcurrency = 16;

export function createTelegramAgentBot(
  settings: Settings,
  sessions: ChatSessionRegistry,
  logger: Logger,
  dependencies: TelegramBotDependencies = {},
): TelegramAgentBot {
  const bot = new Bot(settings.botToken, dependencies.botInfo ? { botInfo: dependencies.botInfo } : {});
  const botReplyStreaks = new Map<number, number>();
  const submissionTails = new Map<number, Promise<void>>();
  let runner: RunnerHandle | undefined;
  const morselPublisher = dependencies.morselPublisher ?? createMorselPublisher(settings);

  bot.use(async (context, next) => {
    if (!isAllowed(context, settings.botWhitelist)) return;
    await next();
  });

  bot.command("start", async (context) => {
    await context.reply("你好！我是由 Pi agent 驅動的 Telegram AI 助理。使用 /help 查看可用指令。");
  });
  bot.command("help", async (context) => {
    await context.reply(
      [
        "/ask <問題> — 詢問 AI 助理",
        "/reset — 清除目前 chat 的 Pi session",
        "/cancel — 取消目前執行並清除 steering/follow-up queue",
        "/id — 顯示 chat ID 與 user ID",
      ].join("\n"),
    );
  });
  bot.command("id", async (context) => {
    await context.reply(`chat_id=${context.chat.id}\nuser_id=${context.from?.id ?? "unknown"}`);
  });
  bot.command("reset", async (context) => {
    await sessions.reset(context.chat.id);
    await context.reply("已清除這個對話的 Pi session。", replyOptions(context));
  });
  bot.command("cancel", async (context) => {
    const cancelled = await sessions.cancel(context.chat.id);
    await context.reply(cancelled ? "已取消目前任務。" : "目前沒有執行中的任務。", replyOptions(context));
  });
  bot.command("ask", async (context) => {
    const prompt = context.match.trim();
    if (!prompt) {
      await context.reply("請使用 /ask <問題>。", replyOptions(context));
      return;
    }
    await inSubmissionOrder(context.chat.id, (release) => answer(context, prompt, [], release));
  });

  bot.on("message", async (context) => {
    const message = context.message as unknown as TelegramMessageLike;
    const chatType = context.chat.type;
    const privateChat = chatType === "private";
    const fromBot = context.from?.is_bot === true;

    if (fromBot) {
      const streak = botReplyStreaks.get(context.chat.id) ?? 0;
      if (settings.botMaxConsecutiveRepliesToBots === 0 || streak >= settings.botMaxConsecutiveRepliesToBots) return;
    } else {
      botReplyStreaks.delete(context.chat.id);
    }

    const addressed = privateChat || isBotAddressed(message, context.me.id, context.me.username);
    if (!addressed) {
      if (settings.botGroupPassiveContextEnabled) {
        await sessions.appendPassiveContext(context.chat.id, passiveGroupContext(message));
      }
      return;
    }

    if (fromBot) botReplyStreaks.set(context.chat.id, (botReplyStreaks.get(context.chat.id) ?? 0) + 1);
    await inSubmissionOrder(context.chat.id, async (release) => {
      const strippedText = privateChat
        ? messageText(message).trim()
        : stripBotMention(messageText(message), context.me.username);
      const references = imageReferences(message);
      if (references.length > 0 && !settings.botImageInputEnabled) {
        await context.reply("目前未啟用圖片輸入。", replyOptions(context));
        return;
      }

      let images: Array<{ type: "image"; data: string; mimeType: string }>;
      try {
        images = await Promise.all(
          references.map((reference) =>
            downloadTelegramImage(
              context.api,
              settings.botToken,
              reference,
              settings.botImageMaxBytes,
              dependencies.imageFetchImplementation,
            ),
          ),
        );
      } catch (error) {
        const message =
          error instanceof TelegramDownloadTooLargeError
            ? "圖片超過允許的大小，無法處理。"
            : "無法下載 Telegram 圖片，請稍後再試。";
        logger.warn(`Telegram image input failed for chat_id=${context.chat.id}`, error);
        await context.reply(message, replyOptions(context));
        return;
      }

      const basePrompt = strippedText || (images.length > 0 ? defaultImagePrompt : "請回應這則訊息。");
      await answer(context, promptWithReplyContext(message, basePrompt), images, release);
    });
  });

  bot.catch((error) => {
    const context = error.ctx;
    const cause = error.error;
    if (cause instanceof GrammyError) {
      logger.error(`Telegram API error while handling update_id=${context.update.update_id}: ${cause.description}`);
    } else if (cause instanceof HttpError) {
      logger.error(`Telegram transport error while handling update_id=${context.update.update_id}`, cause);
    } else {
      logger.error(`Unhandled bot error while handling update_id=${context.update.update_id}`, cause);
    }
  });

  async function answer(
    context: Context,
    prompt: string,
    images: Array<{ type: "image"; data: string; mimeType: string }>,
    releaseSubmissionTurn: () => void,
  ): Promise<void> {
    const sourceMessageId = context.message?.message_id;
    const status = await context.reply(
      "處理中…",
      sourceMessageId ? { reply_parameters: { message_id: sourceMessageId } } : {},
    );
    try {
      const result = await sessions.submit(context.chat?.id ?? status.chat.id, prompt, {
        images,
        onAccepted: releaseSubmissionTurn,
      });
      let outboundText = result.text;
      const sanitized = sanitizeTelegramText(outboundText);
      if (
        settings.morselMode === "smart" &&
        morselPublisher.isConfigured &&
        sanitized.length > settings.morselLongReplyThreshold
      ) {
        try {
          const shareUrl = await morselPublisher.publish(sanitized);
          outboundText = `完整回覆已發布至 Morsel（${sanitized.length.toLocaleString("zh-TW")} 字）：\n${shareUrl}`;
        } catch (error) {
          logger.warn(`Morsel long-reply publication failed for chat_id=${status.chat.id}; falling back`, error);
        }
      }
      await editStatusWithChunks(context, status.chat.id, status.message_id, outboundText);
    } catch (error) {
      logger.error(`Pi agent request failed for chat_id=${status.chat.id}`, error);
      await editStatusWithChunks(context, status.chat.id, status.message_id, "AI 服務暫時無法使用，請稍後再試。");
    }
  }

  async function inSubmissionOrder(chatId: number, task: (release: () => void) => Promise<void>): Promise<void> {
    const previous = submissionTails.get(chatId) ?? Promise.resolve();
    let openGate = () => {};
    const gate = new Promise<void>((resolve) => {
      openGate = resolve;
    });
    const tail = previous.then(() => gate);
    submissionTails.set(chatId, tail);
    await previous;

    let released = false;
    const release = () => {
      if (released) return;
      released = true;
      openGate();
      if (submissionTails.get(chatId) === tail) submissionTails.delete(chatId);
    };
    try {
      await task(release);
    } finally {
      release();
    }
  }

  return {
    bot,
    async start() {
      await bot.init();
      logger.info(`Telegram bot started as @${bot.botInfo.username}`);
      runner = run(bot, {
        runner: { fetch: { allowed_updates: ["message"] } },
        sink: { concurrency: updateConcurrency },
      });
      await runner.task();
    },
    async stop() {
      if (!runner) return;
      await runner.stop();
      runner = undefined;
    },
  };
}

async function editStatusWithChunks(context: Context, chatId: number, messageId: number, text: string): Promise<void> {
  const [first = " ", ...rest] = telegramHtmlChunks(text);
  await context.api.editMessageText(chatId, messageId, first, { parse_mode: "HTML" });
  let replyTo = messageId;
  for (const chunk of rest) {
    const sent = await context.api.sendMessage(chatId, chunk, {
      parse_mode: "HTML",
      reply_parameters: { message_id: replyTo },
    });
    replyTo = sent.message_id;
  }
}

function isAllowed(context: Context, whitelist: ReadonlySet<number>): boolean {
  return (
    whitelist.size === 0 ||
    whitelist.has(context.chat?.id ?? Number.NaN) ||
    whitelist.has(context.from?.id ?? Number.NaN)
  );
}

function replyOptions(context: Context): { reply_parameters: { message_id: number } } | Record<string, never> {
  return context.message ? { reply_parameters: { message_id: context.message.message_id } } : {};
}
