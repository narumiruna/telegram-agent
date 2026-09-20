import { mkdtemp } from "node:fs/promises"
import { tmpdir } from "node:os"
import path from "node:path"

import type { AgentMessage } from "@earendil-works/pi-agent-core"
import { describe, expect, it, vi } from "vitest"

import { ChatSessionRegistry, type SessionHandle } from "../src/agent/session-registry.js"
import type { Logger } from "../src/logging.js"

const logger: Logger = {
  debug: vi.fn(),
  info: vi.fn(),
  warn: vi.fn(),
  error: vi.fn(),
}

class FakeSession implements SessionHandle {
  isStreaming = false
  messages: AgentMessage[] = []
  readonly prompts: string[] = []
  readonly steering: string[] = []
  readonly followUps: string[] = []
  readonly contexts: string[] = []
  aborted = false
  disposed = false

  async prompt(text: string): Promise<void> {
    this.prompts.push(text)
    this.messages.push({ role: "user", content: text, timestamp: Date.now() })
    this.messages.push(assistant(`AI: ${text}`))
  }

  async steer(text: string): Promise<void> {
    this.steering.push(text)
  }

  async followUp(text: string): Promise<void> {
    this.followUps.push(text)
  }

  clearQueue() {
    const queued = { steering: [...this.steering], followUp: [...this.followUps] }
    this.steering.length = 0
    this.followUps.length = 0
    return queued
  }

  async sendCustomMessage(message: { content: string }): Promise<void> {
    this.contexts.push(message.content)
  }

  async abort(): Promise<void> {
    this.aborted = true
    this.isStreaming = false
  }

  dispose(): void {
    this.disposed = true
  }
}

describe("ChatSessionRegistry", () => {
  it("reuses one Pi session per chat and isolates different chats", async () => {
    const root = await mkdtemp(path.join(tmpdir(), "telegramagent-ts-"))
    const sessions = new Map<number, FakeSession>()
    const registry = new ChatSessionRegistry(
      async (chatId) => {
        const session = new FakeSession()
        sessions.set(chatId, session)
        return session
      },
      root,
      logger,
    )

    await expect(registry.submit(1, "one")).resolves.toEqual({ kind: "completed", text: "AI: one" })
    await expect(registry.submit(1, "two")).resolves.toEqual({ kind: "completed", text: "AI: two" })
    await expect(registry.submit(2, "other")).resolves.toEqual({
      kind: "completed",
      text: "AI: other",
    })

    expect(sessions.size).toBe(2)
    expect(sessions.get(1)?.prompts).toEqual(["one", "two"])
    expect(sessions.get(2)?.prompts).toEqual(["other"])
  })

  it("invalidates a session that finishes creating after reset", async () => {
    const root = await mkdtemp(path.join(tmpdir(), "telegramagent-ts-"))
    const staleSession = new FakeSession()
    const replacementSession = new FakeSession()
    let finishCreation: ((session: SessionHandle) => void) | undefined
    const pendingCreation = new Promise<SessionHandle>((resolve) => {
      finishCreation = resolve
    })
    const createSession = vi.fn(async () =>
      createSession.mock.calls.length === 1 ? pendingCreation : replacementSession,
    )
    const registry = new ChatSessionRegistry(createSession, root, logger)

    const staleSubmission = registry.submit(1, "stale")
    const concurrentStaleSubmission = registry.submit(1, "also stale")
    const staleOutcome = expect(staleSubmission).rejects.toThrow("invalidated by reset")
    const concurrentStaleOutcome =
      expect(concurrentStaleSubmission).rejects.toThrow("invalidated by reset")
    await vi.waitFor(() => expect(createSession).toHaveBeenCalledOnce())
    await registry.reset(1)
    const replacementSubmission = registry.submit(1, "fresh")
    await vi.waitFor(() => expect(createSession).toHaveBeenCalledTimes(2))
    finishCreation?.(staleSession)

    await Promise.all([staleOutcome, concurrentStaleOutcome])
    await expect(replacementSubmission).resolves.toEqual({ kind: "completed", text: "AI: fresh" })
    expect(staleSession.disposed).toBe(true)
    expect(staleSession.prompts).toEqual([])
    expect(replacementSession.prompts).toEqual(["fresh"])
  })

  it("rejects access to an existing session when reset wins the acceptance race", async () => {
    const root = await mkdtemp(path.join(tmpdir(), "telegramagent-ts-"))
    const session = new FakeSession()
    const registry = new ChatSessionRegistry(async () => session, root, logger)
    await registry.submit(1, "initial")

    const staleSubmission = registry.submit(1, "stale")
    const staleOutcome = expect(staleSubmission).rejects.toThrow("invalidated by reset")
    await registry.reset(1)

    await staleOutcome
    expect(session.prompts).toEqual(["initial"])
  })

  it("delegates steering, follow-up, passive context, cancellation, and reset to Pi sessions", async () => {
    const root = await mkdtemp(path.join(tmpdir(), "telegramagent-ts-"))
    const session = new FakeSession()
    const registry = new ChatSessionRegistry(async () => session, root, logger)

    await registry.appendPassiveContext(1, "旁聽內容")
    session.isStreaming = true
    await expect(registry.submit(1, "改做這個")).resolves.toMatchObject({ kind: "steered" })
    await expect(registry.submit(1, "完成後處理", { intent: "followUp" })).resolves.toMatchObject({
      kind: "followed_up",
    })
    await expect(registry.cancel(1)).resolves.toBe(true)
    await registry.reset(1)

    expect(session.contexts).toEqual(["旁聽內容"])
    expect(session.aborted).toBe(true)
    expect(session.disposed).toBe(true)
  })
})

function assistant(text: string): AgentMessage {
  return {
    role: "assistant",
    content: [{ type: "text", text }],
    api: "openai-completions",
    provider: "test",
    model: "test",
    usage: {
      input: 0,
      output: 0,
      cacheRead: 0,
      cacheWrite: 0,
      totalTokens: 0,
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
    },
    stopReason: "stop",
    timestamp: Date.now(),
  }
}
