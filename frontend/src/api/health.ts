// The shapes of /healthz and /readyz (docs/api.md §2).

export interface Health {
  status: string
  version: string
  env: string
}

export interface Check {
  ok: boolean
  ms: number
  error?: string
}

export interface Ready {
  status: 'ok' | 'degraded'
  checks: Record<string, Check>
}

export async function fetchHealth(): Promise<Health> {
  const response = await fetch('/healthz')
  if (!response.ok) throw new Error(`healthz answered ${response.status}`)
  return (await response.json()) as Health
}

/** /readyz answers 503 with the same body when a store is down; both are a valid reading. */
export async function fetchReady(): Promise<Ready> {
  const response = await fetch('/readyz')
  if (response.ok || response.status === 503) {
    return (await response.json()) as Ready
  }
  throw new Error(`readyz answered ${response.status}`)
}
