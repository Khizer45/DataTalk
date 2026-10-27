import assert from 'node:assert/strict'
import { test } from 'node:test'
import { csvFilename, toCsv } from './csv.js'

test('writes a header row and one line per row', () => {
  const csv = toCsv(['country', 'revenue'], [{ country: 'Canada', revenue: 120.5 }, { country: 'India', revenue: 99 }])
  assert.equal(csv, 'country,revenue\r\nCanada,120.5\r\nIndia,99\r\n')
})

test('quotes commas, quotes and line breaks', () => {
  const csv = toCsv(['note'], [{ note: 'a, b' }, { note: 'say "hi"' }, { note: 'two\nlines' }])
  assert.equal(csv, 'note\r\n"a, b"\r\n"say ""hi"""\r\n"two\nlines"\r\n')
})

test('text that a spreadsheet would run as a formula is neutralised', () => {
  const csv = toCsv(['value'], [{ value: '=1+1' }, { value: '@SUM(A1)' }, { value: '+44 20' }, { value: '-5' }])
  assert.equal(csv, "value\r\n'=1+1\r\n'@SUM(A1)\r\n'+44 20\r\n'-5\r\n")
})

test('real numbers keep their sign and empty values stay empty', () => {
  assert.equal(toCsv(['n', 'shipped'], [{ n: -5, shipped: null }]), 'n,shipped\r\n-5,\r\n')
})

test('file names are readable and dated', () => {
  assert.equal(csvFilename('Show revenue by customer country?', new Date('2026-10-27T10:00:00Z')),
    'datatalk-show-revenue-by-customer-country-2026-10-27.csv')
  assert.equal(csvFilename('???', new Date('2026-10-27T10:00:00Z')), 'datatalk-results-2026-10-27.csv')
})
