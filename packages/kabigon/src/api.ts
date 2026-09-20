import { KabigonClient } from "./client.js"
import type { LoadResult } from "./core/results.js"
import { explainLoadChain } from "./load-chain.js"
import { listLoaderNames } from "./loader-registry.js"

export async function loadUrlDetailed(
  url: string,
  options: { deadlineSeconds?: number; signal?: AbortSignal } = {},
): Promise<LoadResult> {
  const client = new KabigonClient({ deadlineSeconds: options.deadlineSeconds }).start()
  try {
    return await client.loadUrlDetailed(url, options.signal)
  } finally {
    await client.close()
  }
}

export async function loadUrl(
  url: string,
  options: { deadlineSeconds?: number; signal?: AbortSignal } = {},
): Promise<string> {
  return (await loadUrlDetailed(url, options)).content
}

export function availableLoaders(): string[] {
  return listLoaderNames()
}

export function explainPlan(url: string): Record<string, unknown> {
  return explainLoadChain(url).toObject()
}
