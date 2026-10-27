import assert from 'node:assert/strict'
import { test } from 'node:test'
import { isNumericColumn, nextDirection, sortRows } from './sort.js'

const rows = [
  { name: 'Order 10', total: 5 },
  { name: 'order 2', total: null },
  { name: 'Order 1', total: 120 },
  { name: null, total: 5 },
]
const names = list => list.map(row => row.name)
const totals = list => list.map(row => row.total)

test('numbers sort numerically in both directions, empty values last', () => {
  assert.deepEqual(totals(sortRows(rows, 'total', 'asc')), [5, 5, 120, null])
  assert.deepEqual(totals(sortRows(rows, 'total', 'desc')), [120, 5, 5, null])
})

test('text sorts naturally and ignores case, empty values last', () => {
  assert.deepEqual(names(sortRows(rows, 'name', 'asc')), ['Order 1', 'order 2', 'Order 10', null])
  assert.deepEqual(names(sortRows(rows, 'name', 'desc')), ['Order 10', 'order 2', 'Order 1', null])
})

test('ties keep their original order', () => {
  assert.deepEqual(names(sortRows(rows, 'total', 'asc')).slice(0, 2), ['Order 10', null])
})

test('no direction returns the original rows untouched', () => {
  assert.equal(sortRows(rows, 'total', null), rows)
})

test('header clicks cycle through ascending, descending and original order', () => {
  assert.equal(nextDirection(null), 'asc')
  assert.equal(nextDirection('asc'), 'desc')
  assert.equal(nextDirection('desc'), null)
})

test('numeric columns are detected ignoring empty values', () => {
  assert.equal(isNumericColumn(rows, 'total'), true)
  assert.equal(isNumericColumn(rows, 'name'), false)
  assert.equal(isNumericColumn([{ n: null }], 'n'), false)
})
