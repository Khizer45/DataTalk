import { useEffect, useState } from 'react'
import { api } from './api.js'
import DatasetView from './components/DatasetView.jsx'
import Icon from './components/Icon.jsx'
import Results from './components/Results.jsx'
import { formatCount } from './format.js'

const REPO_URL = 'https://github.com/Khizer45/DataTalk'
const prompts = [
  { icon: '↗', text: 'What was total revenue by month?', tag: 'Revenue' },
  { icon: '▤', text: 'Show the top 5 products by revenue', tag: 'Products' },
  { icon: '◷', text: 'How many orders are pending?', tag: 'Orders' },
  { icon: '◎', text: 'Show revenue by customer country', tag: 'Customers' },
]
const views = [{ id: 'ask', label: 'Ask data', icon: 'grid' }, { id: 'dataset', label: 'Dataset', icon: 'database' }]
const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform)

// 'checking' → first health check running; 'waking' → it is slow (free hosting sleeps); 'ok' / 'down'.
function useServerHealth() {
  const [health, setHealth] = useState('checking')
  useEffect(() => {
    let timer
    let stopped = false
    async function check() {
      const slow = window.setTimeout(() => !stopped && setHealth(current => (current === 'ok' ? current : 'waking')), 3000)
      let ok = false
      try { await api('/api/health'); ok = true } catch { /* asleep or unreachable; retried below */ }
      window.clearTimeout(slow)
      if (stopped) return
      setHealth(ok ? 'ok' : 'down')
      timer = window.setTimeout(check, ok ? 60000 : 5000)
    }
    check()
    return () => { stopped = true; window.clearTimeout(timer) }
  }, [])
  return health
}

function ServerNotice({ health }) {
  if (health === 'waking') {
    return <div className="notice info-notice" role="status"><span>i</span>Waking up the demo server. Free hosting puts it to sleep when nobody is using it, so the first visit can take up to a minute. This page updates by itself.</div>
  }
  if (health === 'down') {
    return <div className="notice error-notice" role="status"><span>!</span>Can't reach the DataTalk server right now. Retrying automatically…</div>
  }
  return null
}

function App() {
  const [question, setQuestion] = useState('')
  const [result, setResult] = useState(null)
  const [schemaInfo, setSchemaInfo] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [view, setView] = useState('ask')
  const health = useServerHealth()

  useEffect(() => {
    if (health !== 'ok' || schemaInfo) return
    api('/api/schema').then(setSchemaInfo).catch(() => {})
  }, [health, schemaInfo])

  const counts = Object.fromEntries((schemaInfo?.tables || []).map(table => [table.name, table.row_count]))
  const viewLabel = views.find(item => item.id === view).label

  async function ask(value = question) {
    if (!value.trim()) return
    setView('ask'); setQuestion(value); setLoading(true); setError('')
    try {
      const data = await api('/api/query', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question: value, max_rows: 100 }) })
      setResult(data)
    } catch (err) { setError(err.message); setResult(null) }
    finally { setLoading(false) }
  }

  return <div className="app-shell">
    <aside className="sidebar">
      <a className="brand" href="#top" onClick={() => setView('ask')}><span className="brand-mark"><Icon name="spark" size={20}/></span><span>data<span className="brand-accent">talk</span><small>ANALYTICS, IN PLAIN ENGLISH</small></span></a>
      <div className="workspace-label">WORKSPACE</div>
      <nav className="side-nav" aria-label="Main">
        {views.map(item => <button key={item.id} className={`nav-item ${view === item.id ? 'selected' : ''}`} aria-current={view === item.id ? 'page' : undefined} aria-label={item.label} title={item.label} onClick={() => setView(item.id)}>
          <Icon name={item.icon}/>{item.label}{item.id === 'dataset' && <span className={`nav-status ${health === 'ok' ? 'online' : ''}`}/>}
        </button>)}
      </nav>
      <div className="side-section-head"><span>TRY A QUESTION</span><span className="spark-mini">✳</span></div>
      <div className="prompt-list">{prompts.map((item, i) => <button className="prompt-link" key={item.text} onClick={() => ask(item.text)}><span className={`prompt-symbol ps-${i}`}>{item.icon}</span><span>{item.text}</span></button>)}</div>
      <div className="sidebar-bottom">
        <button className="connection-card" onClick={() => setView('dataset')}>
          <span className={`connection-dot ${health === 'ok' ? 'online' : ''}`}/>
          <div><strong>{health === 'ok' ? 'Demo dataset connected' : health === 'waking' ? 'Waking up the server…' : 'Connecting…'}</strong><span>{schemaInfo ? `${schemaInfo.tables.length} tables · synthetic data` : 'Read-only access'}</span></div>
          <Icon name="arrow" size={15}/>
        </button>
      </div>
    </aside>
    <main className="main-content" id="top">
      <header className="topbar">
        <div className="breadcrumbs"><span>DataTalk</span><span className="crumb-slash">/</span><strong>{viewLabel}</strong></div>
        <div className="top-actions"><span className="safe-pill"><span className="safe-icon"><Icon name="check" size={12}/></span>Read-only mode</span></div>
      </header>
      <div className="page-content">
        <ServerNotice health={health}/>
        {view === 'dataset' ? <DatasetView schemaInfo={schemaInfo} health={health}/> : <>
          <section className="welcome">
            <div className="welcome-copy">
              <div className="welcome-kicker"><span className="kicker-line"/> AI DATABASE ANALYST · DEMO</div>
              <h1>Ask questions about data<br/><span>in plain English.</span></h1>
              <p>{schemaInfo
                ? `This demo uses a synthetic online-shop dataset: ${formatCount(counts.customers ?? 0)} customers and ${formatCount(counts.orders ?? 0)} orders from 2024–2025. DataTalk writes the SQL, checks it, and runs it read-only.`
                : 'DataTalk writes the SQL for your question, checks it, and runs it read-only.'}</p>
            </div>
            <div className="welcome-art" aria-hidden="true"><div className="art-orbit orbit-one"/><div className="art-orbit orbit-two"/><div className="art-core"><Icon name="spark" size={30}/></div><div className="art-chip chip-a">SQL <span>✓</span></div><div className="art-chip chip-b">↗ <span>Insights</span></div><div className="art-chip chip-c">✳ <span>AI</span></div></div>
          </section>
          <section className="ask-card">
            <div className="ask-card-top"><div className="ask-icon"><Icon name="spark" size={19}/></div><div><strong>Ask your data</strong><span>Natural language · schema aware</span></div><span className="keyboard-hint">{isMac ? '⌘' : 'Ctrl'} ↵</span></div>
            <form onSubmit={event => { event.preventDefault(); ask() }}>
              <textarea aria-label="Your question" value={question} onChange={e => setQuestion(e.target.value)} onKeyDown={e => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') ask() }} placeholder="e.g. Show revenue by customer country" rows="2"/>
              <div className="ask-footer"><div className="privacy-note"><span className="lock">◈</span> Queries run in read-only mode</div><button className="ask-button" disabled={loading || !question.trim()}>{loading ? <span className="spinner" aria-label="Working"/> : <><span>Ask DataTalk</span><Icon name="arrow" size={17}/></>}</button></div>
            </form>
          </section>
          {schemaInfo?.mode === 'demo' && <p className="mode-note"><strong>Demo mode:</strong> no AI model is connected, so DataTalk only answers question types like the examples below and says so when it can't.</p>}
          {schemaInfo?.mode === 'ai' && <p className="mode-note"><strong>AI mode:</strong> a language model writes the SQL. Open "View the SQL that ran" before relying on an answer.</p>}
          <div className="suggestion-head"><div><span className="eyebrow">GET STARTED</span><h2>Explore the demo data</h2></div><span className="suggestion-caption">Questions that work in demo mode</span></div>
          <section className="suggestion-grid">{prompts.map((item, i) => <button className="suggestion-card" key={item.text} onClick={() => ask(item.text)}><div className={`suggestion-icon si-${i}`}>{item.icon}</div><div className="suggestion-text"><span>{item.tag}</span><strong>{item.text}</strong></div><span className="suggestion-arrow"><Icon name="arrow" size={16}/></span></button>)}</section>
          {error && <div className="notice error-notice" role="alert"><span>!</span>{error}<button aria-label="Dismiss" onClick={() => setError('')}>×</button></div>}
          {result && <Results result={result}/>}
        </>}
        <footer className="page-footer"><span>Synthetic demo data · read-only</span><a href={REPO_URL} target="_blank" rel="noreferrer">Source code on GitHub</a></footer>
      </div>
    </main>
  </div>
}

export default App
