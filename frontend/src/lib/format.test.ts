import { describe, expect, it } from 'vitest'
import { formatBytes, formatDate, formatValue, isNumeric, numberAt, tilesOf, titleOf } from './format'

describe('writing values out', () => {
  it('writes money in the company currency and never changes the figure', () => {
    const written = formatValue(902538, 'money', 'INR')
    expect(written.replace(/[^0-9]/g, '')).toBe('90253800') // 902,538.00: the same figure
    expect(written).toMatch(/₹|INR/)
  })

  it('falls back to the code for a currency the browser does not know', () => {
    expect(formatValue(10, 'money', 'NOT-A-CODE')).toContain('NOT-A-CODE')
  })

  it('reads a ratio and a percentage as the same percent', () => {
    expect(formatValue(0.061, 'percent', 'INR')).toBe('6.1%')
    expect(formatValue(6.1, 'percent', 'INR')).toBe('6.1%')
  })

  it('keeps whole numbers whole and shows two decimals otherwise', () => {
    expect(formatValue(519, 'number', 'INR')).toBe('519')
    expect(formatValue(542.39, 'number', 'INR')).toBe('542.39')
  })

  it('shows a missing value as a dash and a boolean as a word', () => {
    expect(formatValue(null, 'money', 'INR')).toBe('—')
    expect(formatValue(undefined, 'text', 'INR')).toBe('—')
    expect(formatValue(true, 'text', 'INR')).toBe('Yes')
    expect(formatValue(false, 'text', 'INR')).toBe('No')
    expect(formatValue(0, 'number', 'INR')).toBe('0') // zero is a value, not a gap
  })

  it('writes a date without shifting its day, and leaves non-dates alone', () => {
    expect(formatDate('2026-10-01')).toMatch(/1/)
    expect(formatDate('2026-10-01')).toMatch(/2026/)
    expect(formatDate('not a date')).toBe('not a date')
  })

  it('turns a column name into a heading', () => {
    expect(titleOf('units_sold')).toBe('Units sold')
    expect(titleOf('revenue')).toBe('Revenue')
  })

  it('writes sizes in the nearest unit', () => {
    expect(formatBytes(900)).toBe('900 B')
    expect(formatBytes(2048)).toBe('2 KB')
    expect(formatBytes(5 * 1024 * 1024)).toBe('5.0 MB')
  })

  it('knows which columns are figures and reads a cell as a number only when it is one', () => {
    expect(isNumeric({ name: 'revenue', type: 'money' })).toBe(true)
    expect(isNumeric({ name: 'outlet', type: 'text' })).toBe(false)
    expect(numberAt({ a: 3 }, 'a')).toBe(3)
    expect(numberAt({ a: '3' }, 'a')).toBeNull()
    expect(numberAt({ a: null }, 'a')).toBeNull()
  })

  it('makes one tile per column of a one-row result and none without a row', () => {
    const columns = [
      { name: 'revenue', type: 'money' as const },
      { name: 'orders', type: 'number' as const },
    ]
    const tiles = tilesOf(columns, { revenue: 100, orders: 4 }, 'INR')
    expect(tiles.map((tile) => tile.label)).toEqual(['Revenue', 'Orders'])
    expect(tiles[1].value).toBe('4')
    expect(tilesOf(columns, undefined, 'INR')).toEqual([])
  })
})
