import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { Column, Row } from '../api/types'
import ResultView from './ResultView.vue'

const columns: Column[] = [
  { name: 'outlet', type: 'text' },
  { name: 'revenue', type: 'money' },
]
const rows: Row[] = [
  { outlet: 'Indiranagar', revenue: 300 },
  { outlet: 'Koramangala', revenue: 200 },
]
const stubs = { BarChart: true, LineChart: true, DataTable: true }

function view(props: Record<string, unknown>) {
  return mount(ResultView, { props: { columns, rows, currency: 'INR', ...props }, global: { stubs } })
}

describe('a result on the page', () => {
  it('draws a chart by default when the shape allows one, and always shows the table', () => {
    // found on 2026-11-12: an absent boolean prop is false in Vue, so no chart was ever drawn
    const shown = view({})
    expect(shown.find('bar-chart-stub').exists()).toBe(true)
    expect(shown.find('data-table-stub').exists()).toBe(true)
    expect(shown.text()).toContain('Revenue by outlet')
  })

  it('draws a line for dates', () => {
    const shown = view({
      columns: [{ name: 'day', type: 'date' }, columns[1]],
      rows: [
        { day: '2026-10-01', revenue: 1 },
        { day: '2026-10-02', revenue: 2 },
      ],
    })
    expect(shown.find('line-chart-stub').exists()).toBe(true)
    expect(shown.find('bar-chart-stub').exists()).toBe(false)
  })

  it('draws no chart when asked not to, or when the shape does not allow one', () => {
    expect(view({ chart: false }).find('bar-chart-stub').exists()).toBe(false)
    const one = view({ rows: [rows[0]] })
    expect(one.find('bar-chart-stub').exists()).toBe(false)
    expect(one.find('data-table-stub').exists()).toBe(true)
  })
})
