// How values are shown. Figures come from the API already computed; nothing here
// calculates a business number, it only writes one out.
import type { Column, ColumnType, Row } from '../api/types'

export function formatNumber(value: number, digits = 0): string {
  return new Intl.NumberFormat(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value)
}

export function formatMoney(value: number, currency: string): string {
  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency',
      currency,
      maximumFractionDigits: 2,
    }).format(value)
  } catch {
    return `${currency} ${formatNumber(value, 2)}` // an unknown currency code
  }
}

export function formatDate(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const hasTime = value.length > 10
  return date.toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    ...(hasTime ? { hour: '2-digit', minute: '2-digit' } : {}),
    ...(hasTime ? {} : { timeZone: 'UTC' }), // a plain date has no time zone to shift it
  })
}

export function formatValue(value: unknown, type: ColumnType, currency: string): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (typeof value === 'number') {
    if (type === 'money') return formatMoney(value, currency)
    if (type === 'percent') {
      // the API sends a ratio (0.061) or a percentage (6.1); a ratio is at most 1
      const percent = Math.abs(value) <= 1 ? value * 100 : value
      return `${formatNumber(percent, 1)}%`
    }
    return formatNumber(value, Number.isInteger(value) ? 0 : 2)
  }
  if (type === 'date' && typeof value === 'string') return formatDate(value)
  return String(value)
}

/** "units_sold" -> "Units sold". */
export function titleOf(name: string): string {
  const words = name.replace(/_/g, ' ').trim()
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : name
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${formatNumber(bytes / 1024, 0)} KB`
  return `${formatNumber(bytes / (1024 * 1024), 1)} MB`
}

export function isNumeric(column: Column): boolean {
  return column.type === 'number' || column.type === 'money' || column.type === 'percent'
}

/** The value of one cell as a number, or null when it is not one. */
export function numberAt(row: Row, column: string): number | null {
  const value = row[column]
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/** A one-row result as tiles: one label and one written value per column. */
export function tilesOf(
  columns: Column[],
  row: Row | undefined,
  currency: string,
): { key: string; label: string; value: string }[] {
  if (!row) return []
  return columns.map((column) => ({
    key: column.name,
    label: titleOf(column.name),
    value: formatValue(row[column.name], column.type, currency),
  }))
}
