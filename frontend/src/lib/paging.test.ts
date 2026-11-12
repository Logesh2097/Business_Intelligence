import { describe, expect, it } from 'vitest'
import { clampPage, pageCount, pageOf, PAGE_SIZE, rangeText } from './paging'

const rows = Array.from({ length: 12 }, (_, index) => index + 1)

describe('paging a table', () => {
  it('shows five rows a page', () => {
    expect(PAGE_SIZE).toBe(5)
    expect(pageOf(rows, 1)).toEqual([1, 2, 3, 4, 5])
    expect(pageOf(rows, 2)).toEqual([6, 7, 8, 9, 10])
    expect(pageOf(rows, 3)).toEqual([11, 12])
  })

  it('counts pages, with one page for an empty table', () => {
    expect(pageCount(12)).toBe(3)
    expect(pageCount(5)).toBe(1)
    expect(pageCount(6)).toBe(2)
    expect(pageCount(0)).toBe(1)
  })

  it('keeps the page inside what exists', () => {
    expect(clampPage(9, 12)).toBe(3)
    expect(clampPage(0, 12)).toBe(1)
    expect(clampPage(Number.NaN, 12)).toBe(1)
    expect(pageOf(rows, 99)).toEqual([11, 12]) // a filter left fewer pages: the last one shows
    expect(pageOf([], 3)).toEqual([])
  })

  it('says which rows are shown', () => {
    expect(rangeText(12, 1)).toBe('1–5 of 12')
    expect(rangeText(12, 3)).toBe('11–12 of 12')
    expect(rangeText(3, 1)).toBe('1–3 of 3')
    expect(rangeText(0, 1)).toBe('0 of 0')
  })
})
