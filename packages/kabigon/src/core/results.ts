export const AttemptStatus = {
  Success: "success",
  Failed: "failed",
  Skipped: "skipped",
  NotApplicable: "not_applicable",
  Timeout: "timeout",
  Empty: "empty",
  Rejected: "rejected",
} as const

export type AttemptStatus = (typeof AttemptStatus)[keyof typeof AttemptStatus]

export interface AttemptRecord {
  loaderId: string
  status: AttemptStatus
  elapsedSeconds: number
  errorType?: string
  message?: string
}

export interface LoadResult {
  content: string
  loaderId: string
  contentType: string
  downgraded: boolean
  attempts: readonly AttemptRecord[]
}

export function attemptRecordToObject(record: AttemptRecord): Record<string, unknown> {
  return {
    loader_id: record.loaderId,
    status: record.status,
    elapsed_seconds: record.elapsedSeconds,
    error_type: record.errorType ?? null,
    message: record.message ?? null,
  }
}

export function loadResultToObject(result: LoadResult): Record<string, unknown> {
  return {
    content: result.content,
    loader_id: result.loaderId,
    content_type: result.contentType,
    downgraded: result.downgraded,
    attempts: result.attempts.map(attemptRecordToObject),
  }
}
