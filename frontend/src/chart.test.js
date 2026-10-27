import assert from 'node:assert/strict'
import { test } from 'node:test'
import { pickChart } from './chart.js'
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

test('long results chart the first rows only', () => {
  const rows = Array.from({ length: 24 }, (_, i) => ({ month: `2025-${i}`, revenue: i }))
  assert.equal(pickChart(rows).rows.length, 12)
})

test('identifiers are shown without thousands separators', () => {
  assert.equal(formatValue(2425, 'id'), '2425')
  assert.equal(formatValue(2425, 'customer_id'), '2425')
  assert.equal(formatValue(2425, 'orders'), '2,425')
  assert.equal(formatValue(1245, 'total_amount'), '$1,245')
})
