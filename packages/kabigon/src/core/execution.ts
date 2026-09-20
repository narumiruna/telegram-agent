import { AsyncLocalStorage } from "node:async_hooks"

import type { AttemptRecord } from "./results.js"

interface ExecutionContext {
  deadlineAt?: number
  attempts?: AttemptRecord[]
}

const execution = new AsyncLocalStorage<ExecutionContext>()

function mergedContext(patch: Partial<ExecutionContext>): ExecutionContext {
  return { ...execution.getStore(), ...patch }
}

export function withDeadline<T>(
  deadlineAt: number | undefined,
  operation: () => Promise<T>,
): Promise<T> {
  return execution.run(mergedContext({ deadlineAt }), operation)
}

export function withAttemptSink<T>(
  attempts: AttemptRecord[],
  operation: () => Promise<T>,
): Promise<T> {
  return execution.run(mergedContext({ attempts }), operation)
}

export function remainingMilliseconds(): number | undefined {
  const deadlineAt = execution.getStore()?.deadlineAt
  return deadlineAt === undefined ? undefined : Math.max(0, deadlineAt - performance.now())
}

export function recordAttempt(record: AttemptRecord): void {
  execution.getStore()?.attempts?.push(record)
}
