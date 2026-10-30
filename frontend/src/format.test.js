import assert from 'node:assert/strict'
import { test } from 'node:test'
import { formatValue, sourceLabel } from './format.js'

test('results and clarifications say which data they are about', () => {
  assert.equal(sourceLabel({ source: 'demo', dataset_name: null }), 'Demo data')
  assert.equal(sourceLabel({ source: 'upload', dataset_name: 'sales.csv' }), 'Your file: sales.csv')
  assert.equal(sourceLabel({ source: 'database', dataset_name: 'Support desk' }), 'Database: Support desk')
})

test('money is only assumed to be dollars for the demo shop', () => {
  assert.equal(formatValue(1200.5, 'revenue'), '$1,200.5')
  assert.equal(formatValue(1200.5, 'revenue', { money: false }), '1,200.5')
  assert.equal(formatValue(42, 'customer_id'), '42')
})
