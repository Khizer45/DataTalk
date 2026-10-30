import { useState } from 'react'
import { sourceValue } from '../sources.js'

// An access-key form for a database that needs one. The key is checked by the server before it is kept.
export function KeyForm({ source, onUnlock }) {
  const [key, setKey] = useState('')
  const [state, setState] = useState({ busy: false, error: '' })

  async function submit(event) {
    event.preventDefault()
    setState({ busy: true, error: '' })
    try {
      await onUnlock(source, key.trim())
    } catch (err) {
      setState({ busy: false, error: err.message })
      return
    }
    setState({ busy: false, error: '' })
  }

  return <form className="key-form" onSubmit={submit}>
    <label htmlFor={`key-${source.id}`}>"{source.label}" needs an access key from whoever runs this DataTalk.</label>
    <div>
      <input id={`key-${source.id}`} type="password" autoComplete="off" value={key} onChange={event => setKey(event.target.value)}/>
      <button className="secondary-button" disabled={state.busy || !key.trim()}>{state.busy ? 'Checking…' : 'Unlock'}</button>
    </div>
    {state.error && <span className="field-error" role="alert">{state.error}</span>}
  </form>
}

export default function SourcesCard({ sources, unlocked, activeId, onUse }) {
  if (!sources.length) return null
  return <section className="dataset-card">
    <span className="eyebrow">DATABASES ON THIS SERVER</span><h2 className="card-title">Connected databases</h2>
    <p className="explanation">Whoever runs this DataTalk has connected these databases with a read-only account. Questions about them can only read the tables listed, and every query runs read-only with a time limit.</p>
    <ul className="file-list">{sources.map(source => {
      const tables = source.tables ?? unlocked[source.id]
      const value = sourceValue(source.id)
      return <li key={source.id} className={value === activeId ? 'active' : ''}>
        <div><strong>{source.label}</strong>
          <span>{tables ? `${tables.length} ${tables.length === 1 ? 'table' : 'tables'}: ${tables.slice(0, 6).map(table => table.name).join(', ')}${tables.length > 6 ? ', …' : ''}` : 'Locked: needs an access key'}</span></div>
        <div className="file-actions">
          <button className="secondary-button" disabled={value === activeId} onClick={() => onUse(value)}>
            {value === activeId ? 'Selected' : 'Ask about this database'}</button>
        </div>
      </li>
    })}</ul>
  </section>
}
