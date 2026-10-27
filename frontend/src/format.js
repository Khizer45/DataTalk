const currency = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 0, maximumFractionDigits: 2 })
const number = new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 })

// Column names decide the format; the API returns plain numbers.
export function formatValue(value, key = '') {
  if (typeof value === 'number') return /revenue|amount|price|sales|spend|value/i.test(key) ? currency.format(value) : number.format(value)
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  return String(value ?? '—')
}

export const formatCount = value => number.format(value)

export const prettyLabel = key => key.replaceAll('_', ' ')
