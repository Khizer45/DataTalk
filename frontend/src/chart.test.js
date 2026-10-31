import assert from 'node:assert/strict'
import { test } from 'node:test'
import { linePoints, pickChart } from './chart.js'
import { formatValue } from './format.js'

test('charts a measure against distinct labels', () => {
  const chart = pickChart([{ country: 'Canada', revenue: 10 }, { country: 'India', revenue: 30 }])
  assert.equal(chart.labelKey, 'country')
  assert.equal(chart.valueKey, 'revenue')
  assert.equal(chart.max, 30)
})

test('never charts identifiers as quantities', () => {
  assert.equal(pickChart([{ id: 1, name: 'A' }, { id: 2, name: 'B' }]), null)
  const chart = pickChart([{ id: 7, product: 'A', total: 5 }, { id: 8, product: 'B', total: 9 }])
  assert.equal(chart.valueKey, 'total')
})

test('skips the chart when labels repeat', () => {
  assert.equal(pickChart([{ order_date: '2025-12-31', total: 5 }, { order_date: '2025-12-31', total: 9 }]), null)
})

test('a numeric label works when there is no text column', () => {
  const chart = pickChart([{ year: 2024, revenue: 100 }, { year: 2025, revenue: 150 }])
  assert.equal(chart.labelKey, 'year')
  assert.equal(chart.valueKey, 'revenue')
})

test('long breakdowns chart the first rows only, as bars', () => {
  const rows = Array.from({ length: 24 }, (_, i) => ({ customer: `Customer ${i}`, revenue: 100 - i }))
  const chart = pickChart(rows)
  assert.equal(chart.kind, 'bar')
  assert.equal(chart.rows.length, 12)
})

test('values over time are a line with every point, oldest first', () => {
  const months = Array.from({ length: 24 }, (_, i) => `${2024 + Math.floor(i / 12)}-${String(i % 12 + 1).padStart(2, '0')}`)
  const chart = pickChart(months.map((month, i) => ({ month, revenue: i * 10 })))
  assert.equal(chart.kind, 'line')
  assert.equal(chart.rows.length, 24)
  const newestFirst = pickChart([...months].reverse().map(month => ({ month, revenue: 1 })))
  assert.equal(newestFirst.rows[0].month, '2024-01')
})

test('years charted as a line need at least three points', () => {
  assert.equal(pickChart([{ year: '2024', revenue: 1 }, { year: '2025', revenue: 2 }]).kind, 'bar')
  assert.equal(pickChart([{ year: '2023', revenue: 1 }, { year: '2024', revenue: 2 }, { year: '2025', revenue: 3 }]).kind, 'line')
})

test('unsorted periods are not drawn as a line', () => {
  const rows = [{ month: '2025-03', revenue: 9 }, { month: '2025-01', revenue: 8 }, { month: '2025-02', revenue: 1 }]
  assert.equal(pickChart(rows).kind, 'bar')
})

test('line points span the box and put larger values higher', () => {
  const chart = pickChart([{ month: '2025-01', revenue: 0 }, { month: '2025-02', revenue: 50 }, { month: '2025-03', revenue: 100 }])
  const [a, b, c] = linePoints(chart, 100, 100, { top: 0, right: 0, bottom: 0, left: 0 })
  assert.deepEqual([a.x, b.x, c.x], [0, 50, 100])
  assert.deepEqual([a.y, b.y, c.y], [100, 50, 0])
})

test('identifiers are shown without thousands separators', () => {
  assert.equal(formatValue(2425, 'id'), '2425')
  assert.equal(formatValue(2425, 'customer_id'), '2425')
  assert.equal(formatValue(2425, 'orders'), '2,425')
  assert.equal(formatValue(1245, 'total_amount'), '$1,245')
  assert.equal(formatValue(1245, 'total_amount', { money: false }), '1,245')  // uploads: currency unknown
})
