export const CHART_LIMIT = 12

// Identifier columns (id, customer_id, ...) are labels, never quantities.
export const isIdColumn = key => /(^|_)id$/i.test(key)

// A bar chart helps only when there is a label column with distinct values and a numeric measure;
// otherwise the table alone is clearer.
export function pickChart(rows) {
  if (rows.length < 2) return null
  const first = rows[0]
  const columns = Object.keys(first)
  const labelKey = columns.find(key => typeof first[key] === 'string' && !isIdColumn(key))
    || columns.find(key => !isIdColumn(key))
  const valueKey = columns.find(key => typeof first[key] === 'number' && key !== labelKey && !isIdColumn(key))
  if (!labelKey || !valueKey) return null
  const shown = rows.slice(0, CHART_LIMIT)
  if (new Set(shown.map(row => row[labelKey])).size !== shown.length) return null
  const max = Math.max(...shown.map(row => Number(row[valueKey]) || 0), 1)
  return { labelKey, valueKey, max, rows: shown }
}
