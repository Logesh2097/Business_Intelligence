import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, devUser, get, messageOf, post, remove, setDevUser, upload } from './client'

type Seen = { url: string; init: RequestInit }

function stub(answer: () => Response | Promise<Response>): Seen[] {
  const seen: Seen[] = []
  vi.stubGlobal('fetch', (url: string, init: RequestInit) => {
    seen.push({ url, init })
    return Promise.resolve(answer())
  })
  return seen
}

const json = (status: number, body: unknown): Response => new Response(JSON.stringify(body), { status })

describe('the API client', () => {
  beforeEach(() => localStorage.clear())
  afterEach(() => vi.unstubAllGlobals())

  it('sends the chosen demo user with every call, and nothing when none is chosen', async () => {
    const seen = stub(() => json(200, { ok: true }))
    await get('/api/v1/me')
    expect(new Headers(seen[0].init.headers).has('X-Dev-User')).toBe(false)
    setDevUser('analyst@brewbite.example')
    expect(devUser()).toBe('analyst@brewbite.example')
    await post('/api/v1/tools/sales_summary', { arguments: {} })
    const headers = new Headers(seen[1].init.headers)
    expect(headers.get('X-Dev-User')).toBe('analyst@brewbite.example')
    expect(headers.get('Content-Type')).toBe('application/json')
    expect(seen[1].init.body).toBe('{"arguments":{}}')
    setDevUser('')
    expect(devUser()).toBe('')
  })

  it("turns the API's error shape into an error with its code and sentence", async () => {
    stub(() => json(403, { error: { code: 'permission_denied', message: 'You do not have access to this.' } }))
    const failure = await get('/api/v1/activity').catch((error: unknown) => error)
    expect(failure).toBeInstanceOf(ApiError)
    expect(failure).toMatchObject({ status: 403, code: 'permission_denied' })
    expect(messageOf(failure)).toBe('You do not have access to this.')
  })

  it('still gives a sentence when the error is not JSON', async () => {
    stub(() => new Response('<html>Bad gateway</html>', { status: 502 }))
    const failure = await get('/api/v1/me').catch((error: unknown) => error)
    expect(messageOf(failure)).toBe('The server answered 502.')
    expect(messageOf(failure)).not.toContain('<html>')
  })

  it('says the server could not be reached when the network fails', async () => {
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('Failed to fetch')))
    const failure = await get('/api/v1/me').catch((error: unknown) => error)
    expect(failure).toMatchObject({ status: 0, code: 'network' })
    expect(messageOf(failure)).toContain('could not be reached')
  })

  it('never shows the text of an unexpected error', () => {
    expect(messageOf(new Error('stack trace with secrets'))).toBe('Something went wrong. Please try again.')
  })

  it('uploads a file as the request body and deletes without reading a body', async () => {
    const seen = stub(() => json(201, { id: 'd1' }))
    const file = new Blob(['hello'])
    expect(await upload('/api/v1/documents?filename=a.txt', file)).toEqual({ id: 'd1' })
    expect(seen[0].init.method).toBe('POST')
    expect(seen[0].init.body).toBe(file)
    vi.unstubAllGlobals()
    const removed = stub(() => new Response(null, { status: 204 }))
    await remove('/api/v1/documents/d1')
    expect(removed[0].init.method).toBe('DELETE')
  })
})
