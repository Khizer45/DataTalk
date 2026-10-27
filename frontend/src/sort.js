const collator = new Intl.Collator('en', { numeric: true, sensitivity: 'base' })
const isEmpty = value => value === null || value === undefined

// Numbers compare numerically, text naturally ("Order 2" before "Order 10"), and empty values always go
// last. Returns a new array and keeps the original order for ties; no direction means the original order.
export function sortRows(rows, key, direction) {
  if (!key || !direction) return rows
  const factor = direction === 'desc' ? -1 : 1
  return rows
    .map((row, index) => ({ row, index }))
    .sort((a, b) => {
      const x = a.row[key]
      const y = b.row[key]
      if (isEmpty(x) || isEmpty(y)) return isEmpty(x) === isEmpty(y) ? a.index - b.index : isEmpty(x) ? 1 : -1
      const order = typeof x === 'number' && typeof y === 'number' ? x - y : collator.compare(String(x), String(y))
      return order * factor || a.index - b.index
    })
    .map(item => item.row)
}

// Clicking a header cycles: ascending → descending → original order.
export const nextDirection = current => (current === 'asc' ? 'desc' : current === 'desc' ? null : 'asc')

export const isNumericColumn = (rows, key) => {
  const values = rows.map(row => row[key]).filter(value => !isEmpty(value))
  return values.length > 0 && values.every(value => typeof value === 'number')
}
