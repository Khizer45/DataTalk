import { suggestQuestions } from './suggestions.js'

// PostgreSQL column types → the kinds the starter questions understand.
export function columnKind(type) {
  const t = type.toLowerCase()
  if (/^(smallint|integer|bigint)\b/.test(t)) return 'integer'
  if (/^(numeric|decimal|real|double precision|money)\b/.test(t)) return 'decimal'
  if (t === 'date') return 'date'
  if (t.startsWith('timestamp')) return 'timestamp'
  if (/^(text|character|varchar|citext)\b/.test(t)) return 'text'
  return 'other'
}

// Starter questions for a connected database, from its first table's columns.
export function sourceSuggestions(tables) {
  const table = tables?.[0]
  if (!table) return []
  const columns = table.columns.map(column => ({ name: column.name, label: column.name.replaceAll('_', ' '),
    type: columnKind(column.type) }))
  return suggestQuestions(columns, `the ${table.name} table`)
}

export const sourceValue = id => `source:${id}`
