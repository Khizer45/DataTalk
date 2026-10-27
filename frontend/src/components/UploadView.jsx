import { useState } from 'react'
import { api } from '../api.js'
import { formatCount } from '../format.js'
import { hoursLeft } from '../storage.js'

const TYPE_LABELS = { integer: 'whole number', decimal: 'decimal number', boolean: 'true / false', date: 'date',
  timestamp: 'date and time', text: 'text' }

export default function UploadView({ uploads, mode, datasets, activeId, onUploaded, onUse, onDelete }) {
  const [file, setFile] = useState(null)
  const [state, setState] = useState({ status: 'idle', message: '' })

  if (!uploads?.enabled) {
    return <section className="dataset-card">
      <span className="eyebrow">YOUR DATA</span><h2 className="card-title">Uploads are turned off on this server</h2>
      <p className="explanation">This deployment isn't configured for CSV uploads. A self-hosted install can turn them on by setting INGEST_DATABASE_URL (see the README).</p>
    </section>
  }

  const maxMb = uploads.max_bytes / 1024 / 1024
  const active = datasets.find(dataset => dataset.id === activeId)

  async function upload(event) {
    event.preventDefault()
    if (!file) return
    if (file.size > uploads.max_bytes) {
      setState({ status: 'error', message: `The file is larger than ${maxMb} MB.` })
      return
    }
    setState({ status: 'uploading', message: '' })
    try {
      const dataset = await api(`/api/datasets?name=${encodeURIComponent(file.name)}`,
        { method: 'POST', headers: { 'Content-Type': 'text/csv' }, body: file })
      onUploaded(dataset)
      setState({ status: 'idle', message: '' })
      setFile(null)
      event.target.reset()
    } catch (err) {
      setState({ status: 'error', message: err.message })
    }
  }

  async function remove(dataset) {
    try {
      await onDelete(dataset)
    } catch (err) {
      setState({ status: 'error', message: err.message })
    }
  }

  return <>
    <section className="dataset-card">
      <div className="dataset-head">
        <div><span className="eyebrow">YOUR DATA</span><h2>Upload a CSV file</h2>
          <p>DataTalk reads the file, works out each column's type and stores it in a private table. The table is deleted after {uploads.ttl_hours} hours, or sooner if you delete it. Only this browser holds the key to it.</p></div>
        <span className="badge">Private · temporary</span>
      </div>
      <ul className="dataset-defs">
        <li>A .csv file with column names in the first row (commas, semicolons or tabs).</li>
        <li>Up to {maxMb} MB, {formatCount(uploads.max_rows)} rows and {uploads.max_columns} columns.</li>
        <li>Dates written as YYYY-MM-DD and plain numbers are recognised; anything else is kept as text.</li>
        <li>Please don't upload personal or confidential data to a public demo. The original file is not kept, only the table.</li>
      </ul>
      {mode === 'demo' && <div className="notice info-notice" role="status"><span>i</span>AI mode is off on this server. You can upload and inspect a file, but questions about it will be declined.</div>}
      <form className="upload-form" onSubmit={upload}>
        <input type="file" accept=".csv,text/csv" aria-label="CSV file"
          onChange={event => { setFile(event.target.files[0] ?? null); setState({ status: 'idle', message: '' }) }}/>
        <button className="ask-button" disabled={!file || state.status === 'uploading'}>
          {state.status === 'uploading' ? <><span className="spinner" aria-hidden="true"/><span>Checking the file…</span></> : <span>Upload</span>}
        </button>
      </form>
      {state.status === 'error' && <div className="notice error-notice" role="alert"><span>!</span>{state.message}</div>}
    </section>

    {datasets.length > 0 && <section className="dataset-card">
      <span className="eyebrow">YOUR FILES</span>
      <ul className="file-list">{datasets.map(dataset => <li key={dataset.id} className={dataset.id === activeId ? 'active' : ''}>
        <div><strong>{dataset.name}</strong>
          <span>{formatCount(dataset.row_count)} rows · {dataset.columns.length} columns · deleted in {hoursLeft(dataset.expires_at)} h</span></div>
        <div className="file-actions">
          <button className="secondary-button" disabled={dataset.id === activeId} onClick={() => onUse(dataset.id)}>
            {dataset.id === activeId ? 'Selected' : 'Ask about this file'}</button>
          <button className="link-button" onClick={() => remove(dataset)}>Delete</button>
        </div>
      </li>)}</ul>
    </section>}

    {active && <section className="dataset-card">
      <span className="eyebrow">COLUMNS DATATALK FOUND</span><h2 className="card-title">{active.name}</h2>
      {active.warnings?.length > 0 && <ul className="dataset-defs">{active.warnings.map(warning => <li key={warning}>{warning}</li>)}</ul>}
      <div className="table-wrap"><table>
        <thead><tr><th scope="col">Column in your file</th><th scope="col">Name in SQL</th><th scope="col">Type</th><th scope="col">Empty values</th></tr></thead>
        <tbody>{active.columns.map(column => <tr key={column.name}>
          <td>{column.label}</td><td><code>{column.name}</code></td><td>{TYPE_LABELS[column.type] ?? column.type}</td><td>{column.nullable ? 'some' : 'none'}</td>
        </tr>)}</tbody>
      </table></div>
    </section>}
  </>
}
