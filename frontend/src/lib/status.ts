import type { Check, Ready } from '../api/health'

export interface StoreRow {
  key: string
  name: string
  purpose: string
  ok: boolean
  detail: string
}

// What each store is for, in plain words (docs/design/architecture.md §6).
const STORES: Record<string, { name: string; purpose: string }> = {
  postgres: { name: 'PostgreSQL', purpose: 'Business data, users and logs' },
  mongo: { name: 'MongoDB', purpose: 'Conversations and saved reports' },
  qdrant: { name: 'Qdrant', purpose: 'Document and example search' },
}

const REASONS: Record<string, string> = {
  unreachable: 'Not reachable. Is it running?',
  timeout: 'Did not answer in time',
  error: 'Answered with an error',
}

function describe(check: Check): string {
  if (check.ok) return `Answered in ${check.ms} ms`
  return REASONS[check.error ?? 'error'] ?? REASONS.error
}

/** One row per store, in a fixed order, including any store the API did not report. */
export function storeRows(ready: Ready | null): StoreRow[] {
  return Object.entries(STORES).map(([key, store]) => {
    const check = ready?.checks[key]
    return {
      key,
      ...store,
      ok: check?.ok ?? false,
      detail: check ? describe(check) : 'Not reported',
    }
  })
}

export function summary(apiUp: boolean, rows: StoreRow[]): { ok: boolean; text: string } {
  if (!apiUp) return { ok: false, text: 'The API is not reachable.' }
  const down = rows.filter((row) => !row.ok).map((row) => row.name)
  if (down.length === 0) return { ok: true, text: 'Everything is running.' }
  return { ok: false, text: `The API is running. Down: ${down.join(', ')}.` }
}
