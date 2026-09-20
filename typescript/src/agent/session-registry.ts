import { rm } from "node:fs/promises";
import path from "node:path";

import type { AgentMessage } from "@earendil-works/pi-agent-core";
import type { ImageContent } from "@earendil-works/pi-ai";
import type { AgentSession } from "@earendil-works/pi-coding-agent";

import type { Logger } from "../logging.js";

export type SubmissionResult =
  | { kind: "completed"; text: string }
  | { kind: "steered"; text: string }
  | { kind: "followed_up"; text: string };

export type SubmissionIntent = "steer" | "followUp";

export interface SessionHandle {
  readonly isStreaming: boolean;
  readonly messages: AgentMessage[];
  prompt(text: string, options?: { images?: ImageContent[] }): Promise<void>;
  steer(text: string, images?: ImageContent[]): Promise<void>;
  followUp(text: string, images?: ImageContent[]): Promise<void>;
  clearQueue(): { steering: string[]; followUp: string[] };
  sendCustomMessage(
    message: { customType: string; content: string; display: boolean; details?: unknown },
    options?: { triggerTurn?: boolean; deliverAs?: "steer" | "followUp" | "nextTurn" },
  ): Promise<void>;
  abort(): Promise<void>;
  dispose(): void;
}

export type SessionCreator = (chatId: number) => Promise<SessionHandle>;

export class ChatSessionRegistry {
  readonly #sessions = new Map<number, SessionHandle>();
  readonly #creating = new Map<number, Promise<SessionHandle>>();

  constructor(
    private readonly createSession: SessionCreator,
    private readonly sessionRoot: string,
    private readonly logger: Logger,
  ) {}

  async submit(
    chatId: number,
    prompt: string,
    options: { images?: ImageContent[]; intent?: SubmissionIntent } = {},
  ): Promise<SubmissionResult> {
    const session = await this.#getOrCreate(chatId);
    const images = options.images ?? [];
    if (session.isStreaming) {
      if (options.intent === "followUp") {
        await session.followUp(prompt, images);
        return { kind: "followed_up", text: "已將新訊息排在目前任務完成後處理。" };
      }
      await session.steer(prompt, images);
      return { kind: "steered", text: "已將新訊息加入目前任務。" };
    }

    const previousMessageCount = session.messages.length;
    await session.prompt(prompt, images.length > 0 ? { images } : undefined);
    return {
      kind: "completed",
      text: lastAssistantText(session.messages.slice(previousMessageCount)) || "模型沒有回覆內容，請稍後再試。",
    };
  }

  async appendPassiveContext(chatId: number, text: string): Promise<void> {
    if (!text) return;
    const session = await this.#getOrCreate(chatId);
    await session.sendCustomMessage(
      { customType: "telegram-passive-context", content: text, display: false },
      { triggerTurn: false, deliverAs: "nextTurn" },
    );
  }

  async cancel(chatId: number): Promise<boolean> {
    const session = this.#sessions.get(chatId);
    if (!session?.isStreaming) return false;
    session.clearQueue();
    await session.abort();
    return true;
  }

  async reset(chatId: number): Promise<void> {
    const session = this.#sessions.get(chatId);
    if (session) {
      if (session.isStreaming) {
        session.clearQueue();
        await session.abort();
      }
      session.dispose();
      this.#sessions.delete(chatId);
    }
    this.#creating.delete(chatId);
    await rm(path.join(this.sessionRoot, String(chatId), "pi"), { force: true, recursive: true });
  }

  async dispose(): Promise<void> {
    await Promise.all(
      [...this.#sessions.values()].map(async (session) => {
        if (session.isStreaming) {
          session.clearQueue();
          await session.abort();
        }
        session.dispose();
      }),
    );
    this.#sessions.clear();
    this.#creating.clear();
  }

  async #getOrCreate(chatId: number): Promise<SessionHandle> {
    const existing = this.#sessions.get(chatId);
    if (existing) return existing;

    const inflight = this.#creating.get(chatId);
    if (inflight) return inflight;

    const creation = this.createSession(chatId);
    this.#creating.set(chatId, creation);
    try {
      const session = await creation;
      this.#sessions.set(chatId, session);
      this.logger.debug(`Created Pi AgentSession for chat_id=${chatId}`);
      return session;
    } finally {
      this.#creating.delete(chatId);
    }
  }
}

export function asSessionCreator(factory: { create(chatId: number): Promise<AgentSession> }): SessionCreator {
  return (chatId) => factory.create(chatId);
}

function lastAssistantText(messages: AgentMessage[]): string {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message?.role !== "assistant") continue;
    return message.content
      .filter((content) => content.type === "text")
      .map((content) => content.text)
      .join("\n")
      .trim();
  }
  return "";
}
