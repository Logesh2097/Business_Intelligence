import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useStatusStore } from './status'

function answer(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status })
}

const health = { status: 'ok', version: '0.1.0', env: 'dev' }
const up = { ok: true, ms: 2 }

describe('the status store', () => {
  beforeEach(() => setActivePinia(createPinia()))
  afterEach(() => vi.unstubAllGlobals())

  function stubFetch(routes: Record<string, () => Response | Promise<Response>>): void {
    vi.stubGlobal('fetch', (url: string) => Promise.resolve(routes[url]()))
  }

  it('reads a 503 from /readyz as a valid report, not as a failure', async () => {
    stubFetch({
      '/healthz': () => answer(200, health),
      '/readyz': () =>
        answer(503, {
          status: 'degraded',
          checks: { postgres: up, mongo: { ok: false, ms: 1500, error: 'unreachable' }, qdrant: up },
        }),
    })
    const store = useStatusStore()
    await store.refresh()

    expect(store.health?.version).toBe('0.1.0')
    expect(store.rows.map((row) => row.ok)).toEqual([true, false, true])
    expect(store.overall).toEqual({ ok: false, text: 'The API is running. Down: MongoDB.' })
    expect(store.loading).toBe(false)
    expect(store.checkedAt).toBeInstanceOf(Date)
  })

  it('reports everything running when it is', async () => {
    stubFetch({
      '/healthz': () => answer(200, health),
      '/readyz': () => answer(200, { status: 'ok', checks: { postgres: up, mongo: up, qdrant: up } }),
    })
    const store = useStatusStore()
    await store.refresh()
    expect(store.overall.ok).toBe(true)
  })

  it('shows the API as down when it cannot be reached, and every store with it', async () => {
    stubFetch({
      '/healthz': () => Promise.reject(new TypeError('Failed to fetch')),
      '/readyz': () => Promise.reject(new TypeError('Failed to fetch')),
    })
    const store = useStatusStore()
    await store.refresh()

    expect(store.health).toBeNull()
    expect(store.rows.every((row) => !row.ok)).toBe(true)
    expect(store.overall.text).toBe('The API is not reachable.')
    expect(store.loading).toBe(false)
  })
})
