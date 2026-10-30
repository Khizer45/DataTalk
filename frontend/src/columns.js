// Reviewing an uploaded file's columns before import. The server checks everything again; this gives immediate
// feedback and builds the reviewed names and types it is sent.
export const CHOICE_LABELS = {
  text: 'Text',
  integer: 'Whole number',
  decimal: 'Decimal number',
  boolean: 'True / false',
  date: 'Date (YYYY-MM-DD)',
  date_dmy: 'Date (DD/MM/YYYY)',
  date_mdy: 'Date (MM/DD/YYYY)',
  timestamp: 'Date and time (YYYY-MM-DD)',
  timestamp_dmy: 'Date and time (DD/MM/YYYY)',
  timestamp_mdy: 'Date and time (MM/DD/YYYY)',
}
const NAME = /^[a-z][a-z0-9_]{0,62}$/

// While typing: lowercase, anything else becomes "_" (trailing "_" is kept so "order_" can become "order_id").
export function typingName(text) {
  return text.toLowerCase().replace(/[^0-9a-z_]+/g, '_').slice(0, 63)
}

// When leaving the field: the same clean-up the server applies to headers.
export function toColumnName(text) {
  const name = typingName(text.trim()).replace(/_+/g, '_').replace(/^_+|_+$/g, '').slice(0, 60)
  return /^\d/.test(name) ? `c_${name}` : name
}

export function columnErrors(columns) {
  const counts = new Map()
  for (const column of columns) counts.set(column.name, (counts.get(column.name) ?? 0) + 1)
  return columns.map(column => {
    if (!NAME.test(column.name)) return 'Use lowercase letters, digits and _, starting with a letter.'
    if (counts.get(column.name) > 1) return 'Another column already has this name.'
    return ''
  })
}

// Types worth pointing out for a column that is still stored as text.
export function otherChoices(column) {
  return column.type === 'text' ? column.choices.filter(choice => choice !== 'text') : []
}

export function columnSettings(columns) {
  return columns.map(({ name, type }) => ({ name, type }))
}
