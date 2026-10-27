import assert from 'node:assert/strict'
import { test } from 'node:test'
import { hoursLeft, loadDatasets, saveDatasets } from './storage.js'
import { suggestQuestions } from './suggestions.js'

test('suggestions use the file\'s own columns and skip identifiers', () => {
  const columns = [
    { name: 'order_id', label: 'Order ID', type: 'integer' },
    { name: 'region', label: 'Region', type: 'text' },
    { name: 'units', label: 'Units', type: 'integer' },
    { name: 'closed_on', label: 'Closed On', type: 'date' },
  ]
  assert.deepEqual(suggestQuestions(columns), [
    'What is the total Units for each Region?',
    'Show the total Units by month of Closed On.',
    'How many rows are there for each Region?',
    'How many rows are in this file?',
  ])
  assert.deepEqual(suggestQuestions([{ name: 'n', label: 'N', type: 'integer' }]), ['How many rows are in this file?'])
})

test('saved datasets survive missing storage and drop expired entries', () => {
  assert.deepEqual(loadDatasets(), [])  // no localStorage in Node: nothing saved, no crash
  saveDatasets([{ id: 'x' }])           // must not throw either
  const memory = new Map()
  globalThis.localStorage = { getItem: key => memory.get(key) ?? null, setItem: (key, value) => memory.set(key, value) }
  try {
    const now = Date.parse('2026-10-27T12:00:00Z')
    saveDatasets([
      { id: 'live', access_token: 't1', expires_at: '2026-10-28T12:00:00Z' },
      { id: 'old', access_token: 't2', expires_at: '2026-10-26T12:00:00Z' },
      { id: 'broken' },
    ])
    assert.deepEqual(loadDatasets(now).map(item => item.id), ['live'])
    memory.set('datatalk.datasets', 'not json')
    assert.deepEqual(loadDatasets(now), [])
    assert.equal(hoursLeft('2026-10-28T12:00:00Z', now), 24)
  } finally {
    delete globalThis.localStorage
  }
})
