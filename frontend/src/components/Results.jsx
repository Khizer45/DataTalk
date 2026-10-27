import { useMemo } from 'react'
import { formatValue, prettyLabel } from '../format.js'

const CHART_LIMIT = 12

export default function Results({ result }) {
  const columns = useMemo(() => Object.keys(result.rows[0] || {}), [result])
  const chart = useMemo(() => {
    if (result.rows.length < 2) return null
    const first = result.rows[0]
    const labelKey = columns.find(key => typeof first[key] === 'string') || columns[0]
    const valueKey = columns.find(key => typeof first[key] === 'number' && key !== labelKey)
    if (!valueKey) return null
    const rows = result.rows.slice(0, CHART_LIMIT)
    const max = Math.max(...rows.map(row => Number(row[valueKey]) || 0), 1)
    return { labelKey, valueKey, max, rows }
  }, [result, columns])
  const singleValue = result.rows.length === 1 && columns.length === 1

  return <section className="result-card" aria-live="polite">
    <div className="result-heading">
      <div><span className="eyebrow">QUERY RESULTS</span><h2>{result.row_count} {result.row_count === 1 ? 'row' : 'rows'} returned</h2></div>
      <div className="result-meta"><span className="live-dot"/>{result.generator === 'llm' ? 'AI generated' : 'Demo mode (rules)'}<span className="meta-divider"/>Read only</div>
    </div>
    <p className="explanation">{result.explanation}</p>
    <details className="sql-panel">
      <summary><span><span className="sql-mark">SQL</span> View the SQL that ran</span><span className="chevron">⌄</span></summary>
      <pre><code>{result.sql}</code></pre>
    </details>

    {result.rows.length === 0 && <p className="explanation">No rows matched this question.</p>}

    {singleValue && <div className="stat-block"><span>{prettyLabel(columns[0])}</span><strong>{formatValue(result.rows[0][columns[0]], columns[0])}</strong></div>}

    {chart && <div className="chart-block">
      <div className="chart-title">
        <div><span className="eyebrow">VISUAL BREAKDOWN</span><h3>{prettyLabel(chart.valueKey)}</h3></div>
        <span className="chart-unit">{chart.rows.length < result.rows.length ? `First ${chart.rows.length} of ${result.rows.length} rows` : `${result.rows.length} rows`}</span>
      </div>
      <div className="bars">{chart.rows.map((row, index) => <div className="bar-row" key={`${row[chart.labelKey]}-${index}`}>
        <span className="bar-label" title={String(row[chart.labelKey])}>{formatValue(row[chart.labelKey], chart.labelKey)}</span>
        <div className="bar-track"><div className={`bar-fill tone-${index % 4}`} style={{ width: `${Math.max(3, (Number(row[chart.valueKey]) / chart.max) * 100)}%` }}/></div>
        <span className="bar-value">{formatValue(row[chart.valueKey], chart.valueKey)}</span>
      </div>)}</div>
    </div>}

    {!singleValue && result.rows.length > 0 && <div className="table-wrap">
      <table>
        <thead><tr>{columns.map(key => <th key={key} scope="col">{prettyLabel(key)}</th>)}</tr></thead>
        <tbody>{result.rows.map((row, i) => <tr key={i}>{columns.map(key => <td key={key}>{formatValue(row[key], key)}</td>)}</tr>)}</tbody>
      </table>
      {result.truncated && <div className="table-foot">Showing the first {result.row_count} rows. Ask a narrower question to see fewer.</div>}
    </div>}
  </section>
}
