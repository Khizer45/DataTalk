import { useEffect, useRef, useState } from 'react'
import { api } from './api.js'
import Clarification from './components/Clarification.jsx'
import DatasetView from './components/DatasetView.jsx'
import Icon from './components/Icon.jsx'
import Results from './components/Results.jsx'
import SourcesCard, { KeyForm } from './components/SourcesCard.jsx'
import UploadView from './components/UploadView.jsx'
import { formatCount } from './format.js'
import { sourceSuggestions, sourceValue } from './sources.js'
import { loadDatasets, loadSourceKeys, saveDatasets, saveSourceKeys } from './storage.js'
import { suggestQuestions } from './suggestions.js'

const REPO_URL = 'https://github.com/Khizer45/DataTalk'
const DEMO = 'demo'
const prompts = [
  { icon: '↗', text: 'What was total revenue by month?', tag: 'Revenue' },
  { icon: '▤', text: 'Show the top 5 products by revenue', tag: 'Products' },
  { icon: '◷', text: 'How many orders are pending?', tag: 'Orders' },
  { icon: '◎', text: 'Show revenue by customer country', tag: 'Customers' },
]
const views = [
  { id: 'ask', label: 'Ask data', icon: 'grid' },
  { id: 'dataset', label: 'Demo data', icon: 'database' },
  { id: 'uploads', label: 'Your data', icon: 'upload' },
]
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
  const [resultVersion, setResultVersion] = useState(0)
  const [schemaInfo, setSchemaInfo] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [view, setView] = useState('ask')
  const [datasets, setDatasets] = useState(() => loadDatasets())
  const [activeId, setActiveId] = useState(DEMO)
  const [sourceKeys, setSourceKeys] = useState(() => loadSourceKeys())
  const [unlocked, setUnlocked] = useState({})
  const questionBox = useRef(null)
  const health = useServerHealth()

  useEffect(() => {
    if (health !== 'ok' || schemaInfo) return
    api('/api/schema').then(setSchemaInfo).catch(() => {})
  }, [health, schemaInfo])

  useEffect(() => saveDatasets(datasets), [datasets])
  useEffect(() => saveSourceKeys(sourceKeys), [sourceKeys])

  const counts = Object.fromEntries((schemaInfo?.tables || []).map(table => [table.name, table.row_count]))
  const viewLabel = views.find(item => item.id === view).label
  const sources = schemaInfo?.sources ?? []
  const active = datasets.find(dataset => dataset.id === activeId)
  const activeSource = sources.find(source => sourceValue(source.id) === activeId)
  const sourceTables = activeSource && (activeSource.tables ?? unlocked[activeSource.id])
  const locked = Boolean(activeSource && !sourceTables)
  const suggestions = active ? suggestQuestions(active.columns) : activeSource ? sourceSuggestions(sourceTables) : []

  // A key saved earlier in this tab is checked again (and the tables fetched) when its database is chosen.
  useEffect(() => {
    if (!activeSource?.needs_key || unlocked[activeSource.id] || !sourceKeys[activeSource.id]) return
    unlock(activeSource, sourceKeys[activeSource.id]).catch(() => {})
  }, [activeSource, unlocked, sourceKeys])

  function forget(id) {
    setDatasets(list => list.filter(dataset => dataset.id !== id))
    setActiveId(current => (current === id ? DEMO : current))
    setResult(current => (current?.sourceId === id ? null : current))
  }

  function forgetKey(id) {
    setSourceKeys(({ [id]: _, ...rest }) => rest)
    setUnlocked(({ [id]: _, ...rest }) => rest)
  }

  async function unlock(source, key) {
    try {
      const info = await api(`/api/sources/${source.id}`, { headers: { 'X-Source-Key': key } })
      setUnlocked(current => ({ ...current, [source.id]: info.tables }))
      setSourceKeys(current => ({ ...current, [source.id]: key }))
    } catch (err) {
      if (err.status === 403) forgetKey(source.id)
      throw err
    }
  }

  async function ask(value = question, sourceId = activeId) {
    if (!value.trim()) return
    const dataset = datasets.find(item => item.id === sourceId)
    const source = sources.find(item => sourceValue(item.id) === sourceId)
    const headers = { 'Content-Type': 'application/json' }
    if (dataset) headers['X-Dataset-Token'] = dataset.access_token
    if (source && sourceKeys[source.id]) headers['X-Source-Key'] = sourceKeys[source.id]
    setView('ask'); setQuestion(value); setLoading(true); setError('')
    try {
      const data = await api('/api/query', {
        method: 'POST',
        headers,
        body: JSON.stringify({ question: value, max_rows: 100, ...(dataset ? { dataset_id: dataset.id } : {}),
          ...(source ? { source_id: source.id } : {}) }),
      })
      setResult({ ...data, sourceId }) // a clarification's options are asked about the same data
      setResultVersion(version => version + 1)
    } catch (err) {
      if (dataset && err.status === 404) {
        forget(dataset.id)
        setError('That file has expired or was deleted, so DataTalk switched back to the demo data.')
      } else {
        if (source && err.status === 403) forgetKey(source.id)
        setError(err.message)
      }
      setResult(null)
    } finally { setLoading(false) }
  }

  function askAbout(text, sourceId) {
    setActiveId(sourceId)
    ask(text, sourceId)
  }

  const askDemo = text => askAbout(text, DEMO)

  function rephrase() {
    const box = questionBox.current
    if (!box) return
    box.focus()
    box.setSelectionRange(box.value.length, box.value.length)
  }

  function uploaded(dataset) {
    setDatasets(list => [dataset, ...list.filter(item => item.id !== dataset.id)])
    setActiveId(dataset.id)
  }

  async function deleteDataset(dataset) {
    try {
      await api(`/api/datasets/${dataset.id}`, { method: 'DELETE', headers: { 'X-Dataset-Token': dataset.access_token } })
    } catch (err) {
      if (err.status !== 404) throw err
    }
    forget(dataset.id)
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
      <div className="side-section-head"><span>TRY THE DEMO</span><span className="spark-mini">✳</span></div>
      <div className="prompt-list">{prompts.map((item, i) => <button className="prompt-link" key={item.text} onClick={() => askDemo(item.text)}><span className={`prompt-symbol ps-${i}`}>{item.icon}</span><span>{item.text}</span></button>)}</div>
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
        {view === 'dataset' && <DatasetView schemaInfo={schemaInfo} health={health}/>}
        {view === 'uploads' && <>
          <UploadView uploads={schemaInfo?.uploads} mode={schemaInfo?.mode} datasets={datasets} activeId={activeId}
            onUploaded={uploaded} onUse={id => { setActiveId(id); setView('ask') }} onDelete={deleteDataset}/>
          <SourcesCard sources={sources} unlocked={unlocked} activeId={activeId} onUse={id => { setActiveId(id); setView('ask') }}/>
        </>}
        {view === 'ask' && <>
          <section className="welcome">
            <div className="welcome-copy">
              <div className="welcome-kicker"><span className="kicker-line"/> {active ? 'AI DATABASE ANALYST · YOUR DATA' : activeSource ? 'AI DATABASE ANALYST · CONNECTED DATABASE' : 'AI DATABASE ANALYST · DEMO'}</div>
              <h1>Ask questions about data<br/><span>in plain English.</span></h1>
              <p>{active
                ? `Asking about your file "${active.name}": ${formatCount(active.row_count)} rows and ${active.columns.length} columns, stored as a private table. DataTalk writes the SQL, checks it, and runs it read-only.`
                : activeSource
                  ? `Asking about the database "${activeSource.label}"${sourceTables ? ` (${sourceTables.length} ${sourceTables.length === 1 ? 'table' : 'tables'})` : ''}, connected by whoever runs this DataTalk with a read-only account. DataTalk writes the SQL, checks it, and runs it read-only.`
                  : schemaInfo
                  ? `This demo uses a synthetic online-shop dataset: ${formatCount(counts.customers ?? 0)} customers and ${formatCount(counts.orders ?? 0)} orders from 2024–2025. DataTalk writes the SQL, checks it, and runs it read-only.`
                  : 'DataTalk writes the SQL for your question, checks it, and runs it read-only.'}</p>
            </div>
            <div className="welcome-art" aria-hidden="true"><div className="art-orbit orbit-one"/><div className="art-orbit orbit-two"/><div className="art-core"><Icon name="spark" size={30}/></div><div className="art-chip chip-a">SQL <span>✓</span></div><div className="art-chip chip-b">↗ <span>Insights</span></div><div className="art-chip chip-c">✳ <span>AI</span></div></div>
          </section>
          {(datasets.length > 0 || sources.length > 0) && <div className="source-picker">
            <label htmlFor="source">Ask about</label>
            <select id="source" value={activeId} onChange={event => setActiveId(event.target.value)}>
              <option value={DEMO}>Demo online shop (synthetic)</option>
              {datasets.map(dataset => <option key={dataset.id} value={dataset.id}>{dataset.name} (your file)</option>)}
              {sources.map(source => <option key={source.id} value={sourceValue(source.id)}>{source.label} (database)</option>)}
            </select>
          </div>}
          {locked && <KeyForm key={activeSource.id} source={activeSource} onUnlock={unlock}/>}
          <section className="ask-card">
            <div className="ask-card-top"><div className="ask-icon"><Icon name="spark" size={19}/></div><div><strong>Ask your data</strong><span>Natural language · schema aware</span></div><span className="keyboard-hint">{isMac ? '⌘' : 'Ctrl'} ↵</span></div>
            <form onSubmit={event => { event.preventDefault(); ask() }}>
              <textarea ref={questionBox} aria-label="Your question" value={question} onChange={e => setQuestion(e.target.value)} onKeyDown={e => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') ask() }} placeholder={suggestions.length ? `e.g. ${suggestions[0]}` : activeSource ? 'Ask a question about this database' : 'e.g. Show revenue by customer country'} rows="2"/>
              <div className="ask-footer"><div className="privacy-note"><span className="lock">◈</span> Queries run in read-only mode</div><button className="ask-button" disabled={loading || !question.trim()}>{loading ? <span className="spinner" aria-label="Working"/> : <><span>Ask DataTalk</span><Icon name="arrow" size={17}/></>}</button></div>
            </form>
          </section>
          {active && schemaInfo?.mode === 'demo' && <p className="mode-note"><strong>Demo mode:</strong> questions about your own file need AI mode, which is off on this server, so they will be declined. Switch back to the demo data to try the example questions.</p>}
          {activeSource && schemaInfo?.mode === 'demo' && <p className="mode-note"><strong>Demo mode:</strong> questions about a connected database need AI mode, which is off on this server, so they will be declined.</p>}
          {!active && !activeSource && schemaInfo?.mode === 'demo' && <p className="mode-note"><strong>Demo mode:</strong> no AI model is connected, so DataTalk only answers question types like the examples below and says so when it can't.</p>}
          {schemaInfo?.mode === 'ai' && <p className="mode-note"><strong>AI mode:</strong> a language model writes the SQL. It may ask which reading of your question you mean, and lists what it assumed under the results. Open "View the SQL that ran" before relying on an answer.</p>}
          {active || activeSource ? suggestions.length > 0 && <>
            <div className="suggestion-head"><div><span className="eyebrow">GET STARTED</span><h2>{active ? 'Ask about your file' : 'Ask about this database'}</h2></div><span className="suggestion-caption">{active ? 'Built from your column names' : `Built from the ${sourceTables[0].name} table's columns`}</span></div>
            <section className="suggestion-grid">{suggestions.map((text, i) => <button className="suggestion-card" key={text} onClick={() => ask(text)}><div className={`suggestion-icon si-${i}`}>?</div><div className="suggestion-text"><span>{active ? 'Your data' : 'Database'}</span><strong>{text}</strong></div><span className="suggestion-arrow"><Icon name="arrow" size={16}/></span></button>)}</section>
          </> : <>
            <div className="suggestion-head"><div><span className="eyebrow">GET STARTED</span><h2>Explore the demo data</h2></div><span className="suggestion-caption">Questions that work in demo mode</span></div>
            <section className="suggestion-grid">{prompts.map((item, i) => <button className="suggestion-card" key={item.text} onClick={() => askDemo(item.text)}><div className={`suggestion-icon si-${i}`}>{item.icon}</div><div className="suggestion-text"><span>{item.tag}</span><strong>{item.text}</strong></div><span className="suggestion-arrow"><Icon name="arrow" size={16}/></span></button>)}</section>
          </>}
          {error && <div className="notice error-notice" role="alert"><span>!</span>{error}<button aria-label="Dismiss" onClick={() => setError('')}>×</button></div>}
          {result && (result.clarification
            ? <Clarification key={resultVersion} result={result} onChoose={option => askAbout(option, result.sourceId)}/>
            : <Results key={resultVersion} result={result} onRephrase={rephrase}/>)}
        </>}
        <footer className="page-footer"><span>Read-only · the demo data is synthetic</span><a href={REPO_URL} target="_blank" rel="noreferrer">Source code on GitHub</a></footer>
      </div>
    </main>
  </div>
}

export default App
