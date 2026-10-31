import { useMemo, useState } from 'react'
import { linePoints, pickChart } from '../chart.js'
import { csvFilename, downloadCsv, toCsv } from '../csv.js'
import { formatValue, prettyLabel, sourceLabel } from '../format.js'
import { isNumericColumn, nextDirection, sortRows } from '../sort.js'

const ARIA_SORT = { asc: 'ascending', desc: 'descending' }
const LINE_W = 640
const LINE_H = 190

function LineChart({ chart, show }) {
  const points = linePoints(chart, LINE_W, LINE_H)
  const peak = points.reduce((best, p) => (Number(p.row[chart.valueKey]) > Number(best.row[chart.valueKey]) ? p : best))
  const step = Math.max(1, Math.ceil(points.length / 5))
  // Evenly spaced labels, always ending on the last point, never crowding it.
  const ticks = points.filter((_, i) => (i % step === 0 && points.length - 1 - i >= step * 0.75) || i === points.length - 1)
  const baseline = LINE_H - 22
  const area = `M${points[0].x},${baseline} ` + points.map(p => `L${p.x},${p.y}`).join(' ') + ` L${points.at(-1).x},${baseline} Z`
  const describe = p => `${show(p.row[chart.labelKey], chart.labelKey)}: ${show(p.row[chart.valueKey], chart.valueKey)}`
  return <figure className="line-chart">
    <svg viewBox={`0 0 ${LINE_W} ${LINE_H}`} role="img" aria-label={`Line chart of ${chart.valueKey} over ${chart.labelKey}. Highest: ${describe(peak)}.`}>
      <line className="line-base" x1="12" x2={LINE_W - 12} y1={baseline} y2={baseline}/>
      <path className="line-area" d={area}/>
      <polyline className="line-path" points={points.map(p => `${p.x},${p.y}`).join(' ')}/>
      {points.map((p, i) => <g key={i} className="line-point">
        <circle className="line-hit" cx={p.x} cy={p.y} r="9"><title>{describe(p)}</title></circle>
        <circle className={p === peak ? 'line-dot peak' : 'line-dot'} cx={p.x} cy={p.y} r={p === peak ? 4 : 2.6}/>
      </g>)}
      {ticks.map((p, i) => <text key={i} className="line-tick" x={p.x} y={LINE_H - 5}
        textAnchor={i === 0 ? 'start' : i === ticks.length - 1 ? 'end' : 'middle'}>{show(p.row[chart.labelKey], chart.labelKey)}</text>)}
    </svg>
    <figcaption>Highest: <strong>{describe(peak)}</strong>. Hover a point to see its value; every row is in the table below.</figcaption>
  </figure>
}

function CopyButton({ text }) {
  const [state, setState] = useState('idle')
  async function copy(event) {
    event.preventDefault()  // inside <summary>: copying must not open or close the panel
    try {
      await navigator.clipboard.writeText(text)
      setState('copied')
    } catch {
      setState('failed')
    }
    window.setTimeout(() => setState('idle'), 1800)
  }
  return <button type="button" className="copy-button" onClick={copy} aria-live="polite">
    {state === 'copied' ? 'Copied' : state === 'failed' ? 'Copy failed' : 'Copy SQL'}
  </button>
}

export default function Results({ result, onRephrase, sectionRef }) {
  const [sort, setSort] = useState({ key: null, direction: null })
  const columns = useMemo(() => Object.keys(result.rows[0] || {}), [result])
  const numeric = useMemo(() => new Set(columns.filter(key => isNumericColumn(result.rows, key))), [result, columns])
  const tableRows = useMemo(() => sortRows(result.rows, sort.key, sort.direction), [result, sort])
  const chart = useMemo(() => pickChart(result.rows), [result])
  const singleValue = result.rows.length === 1 && columns.length === 1
  const show = (value, key) => formatValue(value, key, { money: !result.source || result.source === 'demo' })

  function toggleSort(key) {
    setSort(current => {
      const direction = nextDirection(current.key === key ? current.direction : null)
      return { key: direction ? key : null, direction }
    })
  }

  return <section className="result-card" aria-live="polite" ref={sectionRef}>
    <div className="result-heading">
      <div><span className="eyebrow">QUERY RESULTS</span><h2>{result.row_count} {result.row_count === 1 ? 'row' : 'rows'} returned</h2></div>
      <div className="result-meta"><span className="live-dot"/>{result.generator === 'llm' ? 'AI generated' : 'Demo mode (rules)'}<span className="meta-divider"/>{sourceLabel(result)}<span className="meta-divider"/>Read only</div>
    </div>
    <p className="explanation">{result.explanation}</p>
    <details className="sql-panel">
      <summary><span><span className="sql-mark">SQL</span> View the SQL that ran</span><span className="sql-actions"><CopyButton text={result.sql}/><span className="chevron">⌄</span></span></summary>
      <pre><code>{result.sql}</code></pre>
    </details>

    {result.rows.length === 0 && <p className="explanation">No rows matched this question.</p>}

    {singleValue && <div className="stat-block"><span>{prettyLabel(columns[0])}</span><strong>{show(result.rows[0][columns[0]], columns[0])}</strong></div>}

    {chart && <div className="chart-block">
      <div className="chart-title">
        <div><span className="eyebrow">VISUAL BREAKDOWN</span><h3>{prettyLabel(chart.valueKey)}</h3></div>
        <span className="chart-unit">{chart.rows.length < result.rows.length ? `First ${chart.rows.length} of ${result.rows.length} rows` : `${result.rows.length} ${chart.kind === 'line' ? 'points' : 'rows'}`}</span>
      </div>
      {chart.kind === 'line' ? <LineChart chart={chart} show={show}/> : <div className="bars">{chart.rows.map((row, index) => <div className="bar-row" key={`${row[chart.labelKey]}-${index}`}>
        <span className="bar-label" title={String(row[chart.labelKey])}>{show(row[chart.labelKey], chart.labelKey)}</span>
        <div className="bar-track"><div className="bar-fill" style={{ width: `${Math.max(3, (Number(row[chart.valueKey]) / chart.max) * 100)}%` }}/></div>
        <span className="bar-value">{show(row[chart.valueKey], chart.valueKey)}</span>
      </div>)}</div>}
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
          <tbody>{tableRows.map((row, i) => <tr key={i}>{columns.map(key => <td key={key} className={numeric.has(key) ? 'num' : undefined}>{show(row[key], key)}</td>)}</tr>)}</tbody>
        </table>
        {result.truncated && <div className="table-foot">Showing the first {result.row_count} rows. Ask a narrower question to see fewer.</div>}
      </div>
    </>}

    {result.assumptions?.length > 0 && <div className="assumptions">
      <span className="eyebrow">WHAT THE AI ASSUMED</span>
      <ul>{result.assumptions.map((text, i) => <li key={i}>{text}</li>)}</ul>
      <div className="assumptions-foot">
        <span>Not what you meant? Rephrase the question to say so, and ask again.</span>
        <button type="button" className="secondary-button" onClick={onRephrase}>Rephrase</button>
      </div>
    </div>}
  </section>
}
