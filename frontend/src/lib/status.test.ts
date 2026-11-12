import { describe, expect, it } from 'vitest'
import type { Ready } from '../api/health'
import { storeRows, summary } from './status'

const allUp: Ready = {
  status: 'ok',
  checks: {
    postgres: { ok: true, ms: 3 },
    mongo: { ok: true, ms: 5 },
    qdrant: { ok: true, ms: 2 },
  },
}

describe('storeRows', () => {
  it('lists the three stores in a fixed order with plain names', () => {
    expect(storeRows(allUp).map((row) => row.name)).toEqual(['PostgreSQL', 'MongoDB', 'Qdrant'])
    expect(storeRows(allUp)[0].detail).toBe('Answered in 3 ms')
  })

  it('explains why a store is down', () => {
    const ready: Ready = {
      status: 'degraded',
      checks: {
        ...allUp.checks,
        mongo: { ok: false, ms: 1500, error: 'unreachable' },
        qdrant: { ok: false, ms: 3000, error: 'timeout' },
      },
    }
    const rows = storeRows(ready)
    expect(rows[1]).toMatchObject({ ok: false, detail: 'Not reachable. Is it running?' })
    expect(rows[2]).toMatchObject({ ok: false, detail: 'Did not answer in time' })
  })

  it('treats a store the API did not report as down, never as up', () => {
    const rows = storeRows({ status: 'ok', checks: { postgres: { ok: true, ms: 1 } } })
    expect(rows[1]).toMatchObject({ ok: false, detail: 'Not reported' })
    expect(storeRows(null).every((row) => !row.ok)).toBe(true)
  })

  it('falls back to a general reason for an unknown error class', () => {
    const rows = storeRows({
      status: 'degraded',
      checks: { postgres: { ok: false, ms: 1, error: 'something_new' } },
    })
    expect(rows[0].detail).toBe('Answered with an error')
  })
})

describe('summary', () => {
  it('says everything is running only when it is', () => {
    expect(summary(true, storeRows(allUp))).toEqual({ ok: true, text: 'Everything is running.' })
  })

  it('names the stores that are down', () => {
    const rows = storeRows({ ...allUp, checks: { ...allUp.checks, mongo: { ok: false, ms: 1 } } })
    expect(summary(true, rows)).toEqual({
      ok: false,
      text: 'The API is running. Down: MongoDB.',
    })
  })

  it('reports the API itself being down first', () => {
    expect(summary(false, storeRows(null)).text).toBe('The API is not reachable.')
  })
})
