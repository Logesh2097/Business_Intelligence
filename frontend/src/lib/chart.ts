// When a result can be drawn, and the geometry of the drawing. A chart is offered only
// for a shape that reads unambiguously: one label (or date) column and a numeric one.
import type { Column, Row } from '../api/types'
import { isNumeric, numberAt } from './format'

export type ChartKind = 'bar' | 'line'

export interface ChartSpec {
  kind: ChartKind
  label: Column
  value: Column
  points: { label: string; value: number }[]
}

export const MIN_POINTS = 2
export const MAX_BAR_POINTS = 25
export const MAX_LINE_POINTS = 400

/** A chart for this result, or null when a table alone is the honest way to show it. */
export function chartFor(columns: Column[], rows: Row[]): ChartSpec | null {
  const labels = columns.filter((column) => !isNumeric(column))
  const values = columns.filter(isNumeric)
  if (labels.length !== 1 || values.length < 1) return null
  const label = labels[0]
  const value = values[0]
  const kind: ChartKind = label.type === 'date' ? 'line' : 'bar'
  if (rows.length < MIN_POINTS) return null
  if (rows.length > (kind === 'line' ? MAX_LINE_POINTS : MAX_BAR_POINTS)) return null
  const points = []
  for (const row of rows) {
    const number = numberAt(row, value.name)
    const text = row[label.name]
    // one missing or negative value and the picture would mislead: no chart
    if (number === null || number < 0 || text === null || text === undefined) return null
    points.push({ label: String(text), value: number })
  }
  if (points.every((point) => point.value === 0)) return null
  return { kind, label, value, points }
}

/** Each bar's width as a share of the largest value, 0 to 100. */
export function barWidths(values: number[]): number[] {
  const largest = Math.max(...values, 0)
  if (largest <= 0) return values.map(() => 0)
  return values.map((value) => Math.round((Math.max(value, 0) / largest) * 1000) / 10)
}

/** The points of a line in a width x height box, y from zero, as an SVG path. */
export function linePath(values: number[], width: number, height: number, pad = 4): string {
  if (values.length < 2) return ''
  const largest = Math.max(...values, 0) || 1
  const step = (width - pad * 2) / (values.length - 1)
  return values
    .map((value, index) => {
      const x = pad + index * step
      const y = height - pad - (Math.max(value, 0) / largest) * (height - pad * 2)
      return `${index === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
}
