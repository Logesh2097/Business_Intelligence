import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useActivityStore } from './activity'
import { useAssistantStore } from './assistant'
import { problemWithFile, useDocumentsStore } from './documents'
import { usePanelsStore } from './panels'
import { useSessionStore } from './session'

type Handler = (url: string, init: RequestInit) => Response | Promise<Response>

const json = (status: number, body: unknown): Response => new Response(JSON.stringify(body), { status })
const denied = (): Response => json(403, { error: { code: 'permission_denied', message: 'You do not have access to this.' } })

function stub(handler: Handler): { url: string; init: RequestInit }[] {
  const seen: { url: string; init: RequestInit }[] = []
  vi.stubGlobal('fetch', (url: string, init: RequestInit) => {
    seen.push({ url, init })
    return Promise.resolve(handler(url, init))
  })
  return seen
}

const ME = {
  role: 'analyst', company: 'Brew & Bite', currency: 'INR', timezone: 'Asia/Kolkata', all_outlets: true,
  outlets: [], permissions: ['chat:ask', 'sql:view', 'documents:read'], scope_note: null, sign_in: 'development header',
}
const ANSWER = {
  question: 'q', status: 'answered', route: 'agents', reason: '', summary: 'Revenue was 5.', clarification: null,
  results: [], scope_note: null,
}
const TOOL = {
  tool: 'sales_summary', title: 'Sales summary', status: 'ok', period: null, columns: [], rows: [{ revenue: 5 }],
  row_count: 1, truncated: false, notes: [], scope_note: null, code: null, reason: null,
}
const DOC = {
  id: 'd1', filename: 'a.txt', title: 'A', kind: 'txt', size: 5, status: 'ready', error: null, chunk_count: 1,
  page_count: null, created_at: '2026-11-12T10:00:00Z', updated_at: '2026-11-12T10:00:00Z',
}

beforeEach(() => {
  localStorage.clear()
  setActivePinia(createPinia())
})
afterEach(() => vi.unstubAllGlobals())

describe('the session store', () => {
  it('signs in as a demo user and reads what they may do', async () => {
    const seen = stub(() => json(200, ME))
    const session = useSessionStore()
    expect(await session.signIn('  Analyst@Brewbite.example ')).toBe(true)
    expect(session.email).toBe('analyst@brewbite.example')
    expect(new Headers(seen[0].init.headers).get('X-Dev-User')).toBe('analyst@brewbite.example')
    expect(session.signedIn).toBe(true)
    expect(session.can('sql:view')).toBe(true)
    expect(session.can('documents:manage')).toBe(false)
    expect(session.currency).toBe('INR')
  })

  it('is not signed in when the API refuses, and says why', async () => {
    stub(() => json(401, { error: { code: 'not_authenticated', message: 'Unknown or inactive user.' } }))
    const session = useSessionStore()
    expect(await session.signIn('nobody@example.com')).toBe(false)
    expect(session.signedIn).toBe(false)
    expect(session.error).toBe('Unknown or inactive user.')
    expect(session.can('chat:ask')).toBe(false)
  })

  it('asks nothing when no user was chosen, and forgets the user on sign-out', async () => {
    const seen = stub(() => json(200, ME))
    const session = useSessionStore()
    expect(await session.restore()).toBe(false)
    expect(seen).toHaveLength(0)
    await session.signIn('analyst@brewbite.example')
    session.signOut()
    expect(session.signedIn).toBe(false)
    expect(localStorage.getItem('bi.devUser')).toBeNull()
  })
})

describe('the assistant store', () => {
  it('keeps the question and its answer together', async () => {
    const seen = stub(() => json(200, ANSWER))
    const assistant = useAssistantStore()
    await assistant.ask('  What was revenue?  ')
    expect(JSON.parse(String(seen[0].init.body))).toEqual({ question: 'What was revenue?' })
    expect(assistant.exchanges).toHaveLength(1)
    expect(assistant.exchanges[0]).toMatchObject({ question: 'What was revenue?', state: 'done' })
    expect(assistant.exchanges[0].answer?.summary).toBe('Revenue was 5.')
    expect(assistant.busy).toBe(false)
  })

  it('shows a failed question with the reason and can be asked again', async () => {
    stub(() => json(503, { error: { code: 'unavailable', message: 'The assistant is busy right now.' } }))
    const assistant = useAssistantStore()
    await assistant.ask('What was revenue?')
    expect(assistant.exchanges[0]).toMatchObject({ state: 'failed', error: 'The assistant is busy right now.' })
    expect(assistant.exchanges[0].answer).toBeNull()
  })

  it('refuses an empty or over-long question without calling the API', async () => {
    const seen = stub(() => json(200, ANSWER))
    const assistant = useAssistantStore()
    expect(assistant.problemWith('  ')).toBe('Type a question first.')
    expect(assistant.problemWith('x'.repeat(2001))).toContain('2000')
    expect(assistant.problemWith('What was revenue?')).toBe('')
    await assistant.ask(' ')
    expect(seen).toHaveLength(0)
    expect(assistant.exchanges).toHaveLength(0)
  })

  it('takes one question at a time', async () => {
    let release: (value: Response) => void = () => {}
    const seen = stub(() => new Promise<Response>((resolve) => (release = resolve)))
    const assistant = useAssistantStore()
    const first = assistant.ask('First question?')
    await assistant.ask('Second question?') // ignored while the first is waiting
    expect(assistant.busy).toBe(true)
    assistant.clear() // and the conversation cannot be cleared under it
    expect(assistant.exchanges).toHaveLength(1)
    release(json(200, ANSWER))
    await first
    expect(seen).toHaveLength(1)
    assistant.clear()
    expect(assistant.exchanges).toHaveLength(0)
  })
})

describe('the data panels', () => {
  it('loads each panel on its own: one failure does not hide the others', async () => {
    stub((url) => (url.endsWith('/top_products') ? denied() : json(200, TOOL)))
    const panels = usePanelsStore()
    await panels.loadAll([
      { key: 'a', tool: 'sales_summary', args: { period: 'last_month' } },
      { key: 'b', tool: 'top_products' },
    ])
    expect(panels.panel('a').result?.rows).toEqual([{ revenue: 5 }])
    expect(panels.panel('b')).toMatchObject({ result: null, error: 'You do not have access to this.', loading: false })
  })

  it('sends the arguments as the tool route takes them and forgets everything on reset', async () => {
    const seen = stub(() => json(200, TOOL))
    const panels = usePanelsStore()
    await panels.load('a', 'top_products', { period: 'last_week', limit: 5 })
    expect(seen[0].url).toBe('/api/v1/tools/top_products')
    expect(JSON.parse(String(seen[0].init.body))).toEqual({ arguments: { period: 'last_week', limit: 5 } })
    panels.reset()
    expect(panels.panel('a').result).toBeNull()
  })
})

describe('the documents store', () => {
  const file = (name: string, size = 5): File => new File(['x'.repeat(size)], name)

  it('checks a file before sending it', () => {
    expect(problemWithFile(null, 20)).toBe('Choose a file first.')
    expect(problemWithFile(file('sales.xlsx'), 20)).toContain('Only PDF')
    expect(problemWithFile(file('empty.txt', 0), 20)).toBe('The file is empty.')
    expect(problemWithFile(file('big.pdf', 2 * 1024 * 1024), 1)).toContain('at most 1 MB')
    expect(problemWithFile(file('Policy.PDF'), 20)).toBe('')
  })

  it('lists, uploads, re-indexes and deletes', async () => {
    const seen = stub((url, init) => {
      if (init.method === 'DELETE') return new Response(null, { status: 204 })
      if (url.endsWith('/reindex')) return json(200, { ...DOC, chunk_count: 3 })
      if (init.method === 'POST') return json(201, { ...DOC, id: 'd2', title: 'New' })
      return json(200, { documents: [DOC], can_manage: true, max_mb: 20, max_documents: 200 })
    })
    const store = useDocumentsStore()
    await store.refresh()
    expect(store.canManage).toBe(true)
    expect(store.documents.map((document) => document.id)).toEqual(['d1'])

    expect(await store.upload(file('refund policy.txt'), ' Refunds ')).toBe(true)
    const sent = seen[1]
    expect(sent.url).toBe('/api/v1/documents?filename=refund+policy.txt&title=Refunds')
    expect(sent.init.body).toBeInstanceOf(File)
    expect(store.documents.map((document) => document.id)).toEqual(['d2', 'd1'])

    await store.reindex('d1')
    expect(store.documents.find((document) => document.id === 'd1')?.chunk_count).toBe(3)
    await store.remove('d2')
    expect(store.documents.map((document) => document.id)).toEqual(['d1'])
    expect(store.working).toBe('')
  })

  it('keeps the list and says why when an upload is refused', async () => {
    stub((_url, init) =>
      init.method === 'POST'
        ? json(409, { error: { code: 'duplicate_document', message: 'This file was already uploaded as "A".' } })
        : json(200, { documents: [DOC], can_manage: true, max_mb: 20, max_documents: 200 }),
    )
    const store = useDocumentsStore()
    await store.refresh()
    expect(await store.upload(file('a.txt'), '')).toBe(false)
    expect(store.uploadError).toBe('This file was already uploaded as "A".')
    expect(store.documents).toHaveLength(1)
    expect(await store.upload(file('data.csv'), '')).toBe(false) // refused here, not sent
  })
})

describe('the activity store', () => {
  it('does not ask for the query log on behalf of a role that may not see it', async () => {
    const seen = stub(() => json(200, { tools: [], transport: 'direct' }))
    const store = useActivityStore()
    await store.refresh(false)
    expect(seen.map((call) => call.url)).toEqual(['/api/v1/tools'])
    expect(store.activity).toBeNull()
    expect(store.transport).toBe('direct')
  })

  it('shows the tools even when the log cannot be read', async () => {
    stub((url) => (url.startsWith('/api/v1/activity') ? denied() : json(200, { tools: [{ name: 't', title: 'T', description: 'd', group: 'business', input_schema: {} }], transport: 'mcp' })))
    const store = useActivityStore()
    await store.refresh(true)
    expect(store.tools).toHaveLength(1)
    expect(store.activityError).toBe('You do not have access to this.')
    expect(store.toolsError).toBe('')
  })
})
