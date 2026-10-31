export const CHART_LIMIT = 12
export const LINE_LIMIT = 120

// Identifier columns (id, customer_id, ...) are labels, never quantities.
export const isIdColumn = key => /(^|_)id$/i.test(key)

// Years, months or days ("2025", "2025-03", "2025-03-14") in order, or a column named like a period.
const PERIOD_VALUE = /^\d{4}(-\d{2}(-\d{2})?)?$/
const PERIOD_KEY = /^(year|month|week|day|date|period)$|_(date|month|year)$/i
export function isTimeSeries(rows, labelKey) {
  if (rows.length < 3) return false
  const values = rows.map(row => String(row[labelKey] ?? ''))
  if (!values.every(value => PERIOD_VALUE.test(value)) && !PERIOD_KEY.test(labelKey)) return false
  const sorted = [...values].sort()
  return values.every((value, i) => value === sorted[i]) || values.every((value, i) => value === sorted[sorted.length - 1 - i])
}

// A chart helps only when there is a label column with distinct values and a numeric measure; otherwise the table
// alone is clearer. Values over time are drawn as a line with every point, other breakdowns as bars (first rows).
export function pickChart(rows) {
  if (rows.length < 2) return null
  const first = rows[0]
  const columns = Object.keys(first)
  const labelKey = columns.find(key => typeof first[key] === 'string' && !isIdColumn(key))
    || columns.find(key => !isIdColumn(key))
  const valueKey = columns.find(key => typeof first[key] === 'number' && key !== labelKey && !isIdColumn(key))
  if (!labelKey || !valueKey) return null
  const line = isTimeSeries(rows, labelKey) && rows.length <= LINE_LIMIT
  const shown = line ? rows : rows.slice(0, CHART_LIMIT)
  if (new Set(shown.map(row => row[labelKey])).size !== shown.length) return null
  const values = shown.map(row => Number(row[valueKey]) || 0)
  const max = Math.max(...values, 1)
  if (!line) return { kind: 'bar', labelKey, valueKey, max, rows: shown }
  // Time runs left to right even when the query sorted newest first.
  const ordered = String(shown[0][labelKey]) > String(shown[shown.length - 1][labelKey]) ? [...shown].reverse() : shown
  return { kind: 'line', labelKey, valueKey, max, min: Math.min(...values, 0), rows: ordered }
}

// Points for an SVG polyline in a width × height box, with room for labels around it.
export function linePoints(chart, width, height, pad = { top: 10, right: 12, bottom: 22, left: 12 }) {
  const n = chart.rows.length
  const span = chart.max - chart.min || 1
  return chart.rows.map((row, i) => ({
    x: pad.left + (n === 1 ? 0 : (i / (n - 1)) * (width - pad.left - pad.right)),
    y: pad.top + (1 - ((Number(row[chart.valueKey]) || 0) - chart.min) / span) * (height - pad.top - pad.bottom),
    row,
  }))
}
