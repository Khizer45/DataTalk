import { useMemo, useState } from 'react'
import { pickChart } from '../chart.js'
import { csvFilename, downloadCsv, toCsv } from '../csv.js'
import { formatValue, prettyLabel } from '../format.js'
import { isNumericColumn, nextDirection, sortRows } from '../sort.js'

const ARIA_SORT = { asc: 'ascending', desc: 'descending' }

export default function Results({ result }) {
  const [sort, setSort] = useState({ key: null, direction: null })
  const columns = useMemo(() => Object.keys(result.rows[0] || {}), [result])
  const numeric = useMemo(() => new Set(columns.filter(key => isNumericColumn(result.rows, key))), [result, columns])
  const tableRows = useMemo(() => sortRows(result.rows, sort.key, sort.direction), [result, sort])
  const chart = useMemo(() => pickChart(result.rows), [result])
  const singleValue = result.rows.length === 1 && columns.length === 1

  function toggleSort(key) {
    setSort(current => {
      const direction = nextDirection(current.key === key ? current.direction : null)
      return { key: direction ? key : null, direction }
    })
  }

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

    {!singleValue && result.rows.length > 0 && <>
      <div className="table-tools">
        <span>{sort.key ? `Sorted by ${prettyLabel(sort.key)}, ${ARIA_SORT[sort.direction]}` : 'Click a column name to sort'}</span>
        <button type="button" className="secondary-button" onClick={() => downloadCsv(csvFilename(result.question), toCsv(columns, tableRows))}>Download CSV</button>
      </div>
      <div className="table-wrap">
        <table>
          <thead><tr>{columns.map(key => <th key={key} scope="col" className={numeric.has(key) ? 'num' : undefined} aria-sort={sort.key === key ? ARIA_SORT[sort.direction] : 'none'}>
            <button type="button" className="sort-button" onClick={() => toggleSort(key)}>
              {prettyLabel(key)}<span className="sort-mark" aria-hidden="true">{sort.key === key ? (sort.direction === 'asc' ? '▲' : '▼') : '↕'}</span>
            </button>
          </th>)}</tr></thead>
          <tbody>{tableRows.map((row, i) => <tr key={i}>{columns.map(key => <td key={key} className={numeric.has(key) ? 'num' : undefined}>{formatValue(row[key], key)}</td>)}</tr>)}</tbody>
        </table>
        {result.truncated && <div className="table-foot">Showing the first {result.row_count} rows. Ask a narrower question to see fewer.</div>}
      </div>
    </>}
  </section>
}
