import { isIdColumn } from './chart.js'

// Starter questions built from the data's own columns (the demo questions don't apply to it).
export function suggestQuestions(columns, subject = 'this file') {
  const usable = columns.filter(column => !isIdColumn(column.name))
  const text = usable.find(column => column.type === 'text')
  const number = usable.find(column => column.type === 'integer' || column.type === 'decimal')
  const when = usable.find(column => column.type === 'date' || column.type === 'timestamp')
  const questions = []
  if (number && text) questions.push(`What is the total ${number.label} for each ${text.label}?`)
  if (number && when) questions.push(`Show the total ${number.label} by month of ${when.label}.`)
  if (text) questions.push(`How many rows are there for each ${text.label}?`)
  questions.push(`How many rows are in ${subject}?`)
  return questions.slice(0, 4)
}
