const currency = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 0, maximumFractionDigits: 2 })
const number = new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 })

// Column names decide the format; the API returns plain numbers. Money is only assumed to be dollars for the demo
// shop: an uploaded file or connected database may use any currency, so its numbers are shown plain.
export function formatValue(value, key = '', { money = true } = {}) {
  if (typeof value === 'number' && /(^|_)id$/i.test(key)) return String(value)
  if (typeof value === 'number') return money && /revenue|amount|price|sales|spend|value/i.test(key) ? currency.format(value) : number.format(value)
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  return String(value ?? '—')
}

export const formatCount = value => number.format(value)

export const prettyLabel = key => key.replaceAll('_', ' ')
