// Pages of a table. Rows are cut in the browser: the tables here hold at most a few
// hundred rows, already returned by the API.

export const PAGE_SIZE = 5

export function pageCount(total: number, size = PAGE_SIZE): number {
  return Math.max(1, Math.ceil(total / size))
}

/** The page number kept inside 1..pages (a filter can leave fewer pages than before). */
export function clampPage(page: number, total: number, size = PAGE_SIZE): number {
  return Math.min(Math.max(1, Math.trunc(page) || 1), pageCount(total, size))
}

export function pageOf<T>(rows: T[], page: number, size = PAGE_SIZE): T[] {
  const current = clampPage(page, rows.length, size)
  return rows.slice((current - 1) * size, current * size)
}

/** "6–10 of 31": which rows the page shows. */
export function rangeText(total: number, page: number, size = PAGE_SIZE): string {
  if (total === 0) return '0 of 0'
  const current = clampPage(page, total, size)
  const first = (current - 1) * size + 1
  const last = Math.min(total, current * size)
  return `${first}–${last} of ${total}`
}
