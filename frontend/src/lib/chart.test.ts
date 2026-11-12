import { describe, expect, it } from 'vitest'
import type { Column, Row } from '../api/types'
import { barWidths, chartFor, linePath, MAX_BAR_POINTS } from './chart'

const outlet: Column = { name: 'outlet', type: 'text' }
const day: Column = { name: 'day', type: 'date' }
const revenue: Column = { name: 'revenue', type: 'money' }
const orders: Column = { name: 'orders', type: 'number' }

const byOutlet: Row[] = [
  { outlet: 'Indiranagar', revenue: 300, orders: 3 },
  { outlet: 'Koramangala', revenue: 200, orders: 2 },
]

describe('when a result is drawn', () => {
  it('draws bars for one label column and a figure', () => {
    const spec = chartFor([outlet, revenue, orders], byOutlet)
    expect(spec?.kind).toBe('bar')
    expect(spec?.value.name).toBe('revenue') // the first figure
    expect(spec?.points).toEqual([
      { label: 'Indiranagar', value: 300 },
      { label: 'Koramangala', value: 200 },
    ])
  })

  it('draws a line when the label is a date', () => {
    const rows = [
      { day: '2026-10-01', revenue: 10 },
      { day: '2026-10-02', revenue: 12 },
    ]
    expect(chartFor([day, revenue], rows)?.kind).toBe('line')
  })

  it.each([
    ['a single figure', [revenue], [{ revenue: 5 }]],
    ['one row', [outlet, revenue], [byOutlet[0]]],
    ['two label columns', [outlet, { name: 'product', type: 'text' } as Column, revenue], byOutlet],
    ['no figure', [outlet], byOutlet],
    ['a missing value', [outlet, revenue], [byOutlet[0], { outlet: 'X', revenue: null }]],
    ['a negative value', [outlet, revenue], [byOutlet[0], { outlet: 'X', revenue: -5 }]],
    ['a figure sent as text', [outlet, revenue], [byOutlet[0], { outlet: 'X', revenue: '5' }]],
    ['all zeros', [outlet, revenue], [{ outlet: 'A', revenue: 0 }, { outlet: 'B', revenue: 0 }]],
  ])('draws nothing for %s', (_name, columns, rows) => {
    expect(chartFor(columns as Column[], rows as Row[])).toBeNull()
  })

  it('draws nothing when there are too many bars to read', () => {
    const many = Array.from({ length: MAX_BAR_POINTS + 1 }, (_, index) => ({ outlet: `O${index}`, revenue: 1 }))
    expect(chartFor([outlet, revenue], many)).toBeNull()
  })

  it('sizes bars against the largest value', () => {
    expect(barWidths([300, 150, 0])).toEqual([100, 50, 0])
    expect(barWidths([0, 0])).toEqual([0, 0])
    expect(barWidths([])).toEqual([])
  })

  it('draws a line from zero that stays inside its box', () => {
    const path = linePath([0, 50, 100], 100, 50, 0)
    expect(path).toBe('M0.0,50.0 L50.0,25.0 L100.0,0.0')
    expect(linePath([5], 100, 50)).toBe('')
  })
})
