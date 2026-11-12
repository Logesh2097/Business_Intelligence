import { describe, expect, it } from 'vitest'
import { agentTitle, hasDetail, PERIODS, routeTitle, statusLook } from './answer'

describe('how an answer is worded', () => {
  it('has a look for every status the API can send', () => {
    const statuses = [
      'answered', 'partial', 'no_data', 'unanswerable', 'needs_clarification', 'unsupported',
      'failed', 'timeout', 'ok', 'refused', 'error', 'rejected', 'processing', 'ready',
    ]
    for (const status of statuses) {
      expect(statusLook(status).label).not.toBe(status)
      expect(statusLook(status).color).not.toBe('grey')
    }
  })

  it('shows an unknown status as it is, not as a success', () => {
    expect(statusLook('something_new')).toMatchObject({ label: 'something_new', color: 'grey' })
  })

  it('names the agents and the routes', () => {
    expect(agentTitle('sales_agent')).toBe('Sales agent')
    expect(agentTitle('document_agent')).toBe('Documents')
    expect(agentTitle('new_agent')).toBe('new_agent')
    for (const route of ['agents', 'general', 'documents', 'clarify', 'unsupported', 'undecided']) {
      expect(routeTitle(route)).not.toBe(route)
    }
  })

  it('knows when a part has more than its sentence', () => {
    expect(hasDetail({ data: [], sources: [], assumptions: [] })).toBe(false)
    expect(hasDetail({ data: [{}], sources: [], assumptions: [] })).toBe(true)
    expect(hasDetail({ data: [], sources: [{}], assumptions: [] })).toBe(true)
  })

  it('offers exactly the periods the tools accept', () => {
    // the list in backend/app/tools/registry.py (Period)
    expect(PERIODS.map((period) => period.value).sort()).toEqual(
      [
        'today', 'yesterday', 'this_week', 'last_week', 'this_month', 'last_month',
        'this_quarter', 'last_quarter', 'this_year', 'last_year',
        'last_7_days', 'last_30_days', 'last_90_days',
      ].sort(),
    )
  })
})
