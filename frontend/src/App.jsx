import { useEffect, useMemo, useState } from 'react'

const prompts = [
  { icon: '↗', text: 'What was total revenue by month?', tag: 'Revenue' },
  { icon: '▤', text: 'Show the top 5 products by revenue', tag: 'Products' },
  { icon: '◷', text: 'How many orders are pending?', tag: 'Orders' },
  { icon: '◎', text: 'Show revenue by customer country', tag: 'Customers' },
]

function Icon({ name, size = 18 }) {
  const common = { width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.7, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true }
  const paths = {
    grid: <><rect x="3.5" y="3.5" width="7" height="7" rx="1.4"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.4"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.4"/><rect x="13.5" y="13.5" width="7" height="7" rx="1.4"/></>,
    chart: <><path d="M4 19V5M4 19h17"/><path d="m7 15 4-4 3 2 6-7"/></>,
    database: <><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v7c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12v7c0 1.7 3.6 3 8 3 2.1 0 4-.3 5.6-.9"/></>,
    clock: <><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,
    arrow: <><path d="M5 12h14m-6-6 6 6-6 6"/></>,
    spark: <><path d="m12 3 1.9 5.8L20 11l-6.1 2.2L12 19l-2-5.8L4 11l6-2.2L12 3Z"/><path d="m19 15 .9 2.1L22 18l-2.1.9L19 21l-.9-2.1L16 18l2.1-.9L19 15Z"/></>,
    play: <><path d="m8 5 11 7-11 7V5Z"/></>,
    check: <path d="m5 12 4 4L19 6"/>,
  }
  return <svg {...common}>{paths[name] || paths.grid}</svg>
}

async function api(path, options) {
  const response = await fetch(path, options)
  const data = await response.json()
  if (!response.ok) throw new Error(data.detail || 'Something went wrong')
  return data
}

function formatValue(value, key = '') {
  if (typeof value === 'number') {
    const currency = /revenue|amount|price|sales/i.test(key)
    return currency ? new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(value) : new Intl.NumberFormat('en-US').format(value)
  }
  return String(value ?? '—')
}

function Results({ result }) {
  const chartData = useMemo(() => {
    if (!result?.rows?.length) return null
    const columns = Object.keys(result.rows[0])
    const labelKey = columns.find(k => typeof result.rows[0][k] === 'string') || columns[0]
    const valueKey = columns.find(k => typeof result.rows[0][k] === 'number' && k !== labelKey)
    if (!valueKey) return null
    const max = Math.max(...result.rows.map(row => Number(row[valueKey]) || 0), 1)
    return { labelKey, valueKey, max, rows: result.rows.slice(0, 6) }
  }, [result])
  if (!result) return null
  return <section className="result-card">
    <div className="result-heading">
      <div><span className="eyebrow">QUERY RESULTS</span><h2>{result.row_count} {result.row_count === 1 ? 'row' : 'rows'} returned</h2></div>
      <div className="result-meta"><span className="live-dot" />{result.generator === 'llm' ? 'AI generated' : 'Rule based'}<span className="meta-divider"/>Read only</div>
    </div>
    <p className="explanation">{result.explanation}</p>
    <details className="sql-panel"><summary><span><span className="sql-mark">SQL</span> View generated query</span><span className="chevron">⌄</span></summary><pre><code>{result.sql}</code></pre></details>
    {chartData && <div className="chart-block"><div className="chart-title"><div><span className="eyebrow">VISUAL BREAKDOWN</span><h3>{chartData.valueKey.replaceAll('_', ' ')}</h3></div><span className="chart-unit">{chartData.rows.length} categories</span></div>
      <div className="bars">{chartData.rows.map((row, index) => <div className="bar-row" key={`${row[chartData.labelKey]}-${index}`}>
        <span className="bar-label" title={row[chartData.labelKey]}>{row[chartData.labelKey]}</span><div className="bar-track"><div className={`bar-fill tone-${index % 4}`} style={{ width: `${Math.max(3, (Number(row[chartData.valueKey]) / chartData.max) * 100)}%` }} /></div><span className="bar-value">{formatValue(row[chartData.valueKey], chartData.valueKey)}</span>
      </div>)}</div>
    </div>}
    <div className="table-wrap"><table><thead><tr>{Object.keys(result.rows[0] || {}).map(key => <th key={key}>{key.replaceAll('_', ' ')}</th>)}</tr></thead><tbody>{result.rows.map((row, i) => <tr key={i}>{Object.entries(row).map(([key, value]) => <td key={key}>{formatValue(value, key)}</td>)}</tr>)}</tbody></table>{result.truncated && <div className="table-foot">Showing up to the selected row limit.</div>}</div>
  </section>
}

function App() {
  const [question, setQuestion] = useState('')
  const [result, setResult] = useState(null)
  const [schema, setSchema] = useState([])
  const [health, setHealth] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [evaluation, setEvaluation] = useState(null)
  const [evaluating, setEvaluating] = useState(false)
  const [activeNav, setActiveNav] = useState('Ask data')

  useEffect(() => {
    api('/api/schema').then(data => setSchema(data.tables || [])).catch(() => {})
    const checkHealth = () => api('/api/health').then(() => setHealth(true)).catch(() => setHealth(false))
    checkHealth()
    const timer = window.setInterval(checkHealth, 5000)
    return () => window.clearInterval(timer)
  }, [])

  async function ask(value = question) {
    if (!value.trim()) return
    setQuestion(value); setLoading(true); setError(''); setEvaluation(null)
    try {
      const data = await api('/api/query', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question: value, max_rows: 100 }) })
      setResult(data)
    } catch (err) { setError(err.message); setResult(null) }
    finally { setLoading(false) }
  }

  async function runEvaluation() {
    setEvaluating(true); setError('')
    try { setEvaluation(await api('/api/evaluations/run', { method: 'POST' })) }
    catch (err) { setError(err.message) }
    finally { setEvaluating(false) }
  }

  return <div className="app-shell">
    <aside className="sidebar">
      <a className="brand" href="#top" onClick={() => setActiveNav('Ask data')}><span className="brand-mark"><Icon name="spark" size={20}/></span><span>data<span className="brand-accent">talk</span><small>ANALYTICS, IN PLAIN ENGLISH</small></span></a>
      <div className="workspace-label">WORKSPACE</div>
      <nav className="side-nav">
        {[['Ask data', 'grid'], ['Saved analyses', 'clock'], ['Data sources', 'database']].map(([label, icon]) => <button key={label} className={`nav-item ${activeNav === label ? 'selected' : ''}`} onClick={() => { setActiveNav(label); if (label === 'Saved analyses') setError('Your recent analysis will appear here after you run a query.'); if (label === 'Data sources') setError('Connected demo database · PostgreSQL ready') }}><Icon name={icon}/>{label}{label === 'Data sources' && <span className={`nav-status ${health ? 'online' : ''}`}/>}</button>)}
      </nav>
      <div className="side-section-head"><span>TRY A QUESTION</span><span className="spark-mini">✳</span></div>
      <div className="prompt-list">{prompts.map((item, i) => <button className="prompt-link" key={item.text} onClick={() => { setActiveNav('Ask data'); ask(item.text) }}><span className={`prompt-symbol ps-${i}`}>{item.icon}</span><span>{item.text}</span></button>)}</div>
      <div className="sidebar-bottom"><div className="connection-card"><span className={`connection-dot ${health ? 'online' : ''}`}/><div><strong>{health ? 'Database connected' : 'Connecting…'}</strong><span>{health ? `${schema.length} tables · demo workspace` : 'Checking connection'}</span></div><Icon name="arrow" size={15}/></div><div className="user-profile"><div className="avatar">DT</div><div><strong>DataTalk workspace</strong><span>Free plan</span></div><span className="more">···</span></div></div>
    </aside>
    <main className="main-content" id="top">
      <header className="topbar"><div className="breadcrumbs"><span>Workspace</span><span className="crumb-slash">/</span><strong>{activeNav}</strong></div><div className="top-actions"><span className="safe-pill"><span className="safe-icon"><Icon name="check" size={12}/></span>Read-only mode</span><button className="avatar small-avatar" title="Workspace profile">DT</button></div></header>
      <div className="page-content">
        <section className="welcome"><div className="welcome-copy"><div className="welcome-kicker"><span className="kicker-line"/> YOUR DATA, AT YOUR FINGERTIPS</div><h1>Good morning.<br/><span>What would you like to know?</span></h1><p>Ask a question in plain English. DataTalk will query your data and bring back the answer.</p></div><div className="welcome-art" aria-hidden="true"><div className="art-orbit orbit-one"/><div className="art-orbit orbit-two"/><div className="art-core"><Icon name="spark" size={30}/></div><div className="art-chip chip-a">SQL <span>✓</span></div><div className="art-chip chip-b">↗ <span>Insights</span></div><div className="art-chip chip-c">✳ <span>AI</span></div></div></section>
        <section className="ask-card"><div className="ask-card-top"><div className="ask-icon"><Icon name="spark" size={19}/></div><div><strong>Ask your data</strong><span>Natural language · schema aware</span></div><span className="keyboard-hint">⌘ ↵</span></div><form onSubmit={event => { event.preventDefault(); ask() }}><textarea value={question} onChange={e => setQuestion(e.target.value)} onKeyDown={e => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') ask() }} placeholder="e.g. Which products generated the most revenue last quarter?" rows="2"/><div className="ask-footer"><div className="privacy-note"><span className="lock">◈</span> Queries run in read-only mode</div><button className="ask-button" disabled={loading || !question.trim()}>{loading ? <span className="spinner"/> : <><span>Ask DataTalk</span><Icon name="arrow" size={17}/></>}</button></div></form></section>
        <div className="suggestion-head"><div><span className="eyebrow">GET STARTED</span><h2>Explore your data</h2></div><span className="suggestion-caption">A few questions to get you started</span></div>
        <section className="suggestion-grid">{prompts.map((item, i) => <button className="suggestion-card" key={item.text} onClick={() => ask(item.text)}><div className={`suggestion-icon si-${i}`}>{item.icon}</div><div className="suggestion-text"><span>{item.tag}</span><strong>{item.text}</strong></div><span className="suggestion-arrow"><Icon name="arrow" size={16}/></span></button>)}</section>
        {error && <div className="notice error-notice"><span>!</span>{error}<button onClick={() => setError('')}>×</button></div>}
        {result && <Results result={result}/>}
        {evaluation && <section className="evaluation-card"><div className="eval-header"><div><span className="eyebrow">QUALITY CHECK</span><h2>Evaluation results</h2></div><span className="eval-count">{evaluation.total_cases} benchmark questions</span></div><div className="metric-grid">{[['SQL correctness', evaluation.sql_correctness], ['Execution success', evaluation.execution_success], ['Response accuracy', evaluation.response_accuracy]].map(([label, value]) => <div className="metric" key={label}><span>{label}</span><strong>{Math.round(value * 100)}<small>%</small></strong><div className="metric-track"><div style={{ width: `${value * 100}%` }}/></div></div>)}</div><div className="eval-list">{evaluation.results.map((item, i) => <div className="eval-row" key={i}><span className={`eval-check ${item.execution_success && item.sql_correct ? 'pass' : 'fail'}`}>{item.execution_success && item.sql_correct ? '✓' : '!'}</span><span className="eval-question">{item.question}</span><span className="eval-status">{item.execution_success ? (item.sql_correct ? 'Passed' : 'Result differs') : 'Failed'}</span></div>)}</div></section>}
        <footer className="page-footer"><span>Built for curious minds <span className="footer-spark">✳</span></span><button onClick={runEvaluation} disabled={evaluating}>{evaluating ? <><span className="spinner dark-spinner"/> Running benchmark</> : <><Icon name="play" size={14}/> Run evaluation suite</>}</button></footer>
      </div>
    </main>
  </div>
}

export default App

