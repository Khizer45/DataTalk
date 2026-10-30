import assert from 'node:assert/strict'
import { test } from 'node:test'
import { columnErrors, columnSettings, otherChoices, toColumnName, typingName } from './columns.js'
import { columnKind, sourceSuggestions } from './sources.js'
import { loadSourceKeys, saveSourceKeys } from './storage.js'

test('column names are cleaned the way the server cleans headers', () => {
  assert.equal(typingName('Order_'), 'order_')  // a trailing _ survives typing
  assert.equal(typingName('Unit Price ($)'), 'unit_price_')
  assert.equal(toColumnName('  Unit Price ($) '), 'unit_price')
  assert.equal(toColumnName('2024 sales'), 'c_2024_sales')
  assert.equal(toColumnName('___'), '')
})

test('invalid and repeated names are flagged before sending', () => {
  const columns = [{ name: 'region' }, { name: 'units' }, { name: 'units' }, { name: '9lives' }, { name: '' }]
  assert.deepEqual(columnErrors(columns).map(Boolean), [false, true, true, true, true])
  assert.match(columnErrors(columns)[1], /Another column/)
})

test('review settings and type hints', () => {
  const columns = [{ name: 'price', label: 'Price', type: 'text', choices: ['text', 'decimal'], samples: ['$5'] },
    { name: 'day', label: 'Day', type: 'date_dmy', choices: ['text', 'date_dmy'], samples: [] }]
  assert.deepEqual(columnSettings(columns), [{ name: 'price', type: 'text' }, { name: 'day', type: 'date_dmy' }])
  assert.deepEqual(otherChoices(columns[0]), ['decimal'])
  assert.deepEqual(otherChoices(columns[1]), [])
})

test('database column types and starter questions', () => {
  assert.deepEqual(['integer', 'numeric(10,2)', 'character varying(40)', 'date', 'timestamp without time zone', 'uuid']
    .map(columnKind), ['integer', 'decimal', 'text', 'date', 'timestamp', 'other'])
  const tables = [{ name: 'tickets', columns: [{ name: 'id', type: 'integer' }, { name: 'status', type: 'text' },
    { name: 'hours_spent', type: 'numeric' }, { name: 'opened_on', type: 'date' }] }]
  assert.deepEqual(sourceSuggestions(tables), [
    'What is the total hours spent for each status?',
    'Show the total hours spent by month of opened on.',
    'How many rows are there for each status?',
    'How many rows are in the tickets table?',
  ])
  assert.deepEqual(sourceSuggestions(undefined), [])
})

test('access keys live in session storage and survive it being missing', () => {
  assert.deepEqual(loadSourceKeys(), {})
  saveSourceKeys({ sales: 'k' })  // no sessionStorage in Node: must not throw
  const memory = new Map()
  globalThis.sessionStorage = { getItem: key => memory.get(key) ?? null, setItem: (key, value) => memory.set(key, value) }
  try {
    saveSourceKeys({ sales: 'k' })
    assert.deepEqual(loadSourceKeys(), { sales: 'k' })
    memory.set('datatalk.sourceKeys', '[1]')
    assert.deepEqual(loadSourceKeys(), {})
  } finally {
    delete globalThis.sessionStorage
  }
})
