// Text that spreadsheet apps would run as a formula ("CSV injection"); such cells get a leading apostrophe.
const FORMULA_START = /^[=+\-@\t\r]/

function cell(value) {
  if (value === null || value === undefined) return ''
  let text = String(value)
  if (typeof value === 'string' && FORMULA_START.test(text)) text = `'${text}`
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text
}

// RFC 4180 CSV: header row, then one line per row, CRLF line endings.
export function toCsv(columns, rows) {
  return [columns, ...rows.map(row => columns.map(key => row[key]))]
    .map(values => values.map(cell).join(','))
    .join('\r\n') + '\r\n'
}

export function csvFilename(question, now = new Date()) {
  const slug = question.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40) || 'results'
  return `datatalk-${slug}-${now.toISOString().slice(0, 10)}.csv`
}

export function downloadCsv(filename, text) {
  // The byte-order mark makes Excel read the file as UTF-8.
  const url = URL.createObjectURL(new Blob(['﻿', text], { type: 'text/csv;charset=utf-8' }))
  const link = Object.assign(document.createElement('a'), { href: url, download: filename })
  document.body.append(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 0)
}
