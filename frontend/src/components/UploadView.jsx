import { useRef, useState } from 'react'
import { api } from '../api.js'
import { CHOICE_LABELS, columnErrors, columnSettings, otherChoices, toColumnName, typingName } from '../columns.js'
import { formatCount } from '../format.js'
import { hoursLeft } from '../storage.js'

const TYPE_LABELS = { integer: 'whole number', decimal: 'decimal number', boolean: 'true / false', date: 'date',
  timestamp: 'date and time', text: 'text' }

function ReviewCard({ review, busy, error, onEdit, onImport, onCancel }) {
  const errors = columnErrors(review.columns)
  return <section className="dataset-card">
    <span className="eyebrow">REVIEW BEFORE IMPORT</span><h2 className="card-title">{review.name}</h2>
    <p className="explanation">{formatCount(review.row_count)} rows. Check how each column will be stored before importing. Clear names help the AI write the right SQL. If a column holds numbers or dates but was read as text, pick another type: only types that fit every value in the column are listed.</p>
    {review.warnings.length > 0 && <ul className="dataset-defs">{review.warnings.map(warning => <li key={warning}>{warning}</li>)}</ul>}
    <div className="table-wrap review-wrap"><table className="review-table">
      <thead><tr><th scope="col">Column in your file</th><th scope="col">Name in SQL</th><th scope="col">Type</th><th scope="col">Examples</th></tr></thead>
      <tbody>{review.columns.map((column, index) => {
        const hint = otherChoices(column)
        return <tr key={column.original}>
          <td data-label="Column in your file">{column.label}</td>
          <td data-label="Name in SQL">
            <input value={column.name} aria-label={`Name in SQL for ${column.label}`} aria-invalid={Boolean(errors[index])} spellCheck="false"
              onChange={event => onEdit(index, { name: typingName(event.target.value) })}
              onBlur={event => onEdit(index, { name: toColumnName(event.target.value) || column.original })}/>
            {errors[index] && <span className="field-error">{errors[index]}</span>}
          </td>
          <td data-label="Type">
            <select value={column.type} aria-label={`Type for ${column.label}`} onChange={event => onEdit(index, { type: event.target.value })}>
              {column.choices.map(choice => <option key={choice} value={choice}>{CHOICE_LABELS[choice] ?? choice}</option>)}
            </select>
            {hint.length > 0 && <span className="field-hint">Could also be: {hint.map(choice => CHOICE_LABELS[choice] ?? choice).join(', ')}</span>}
          </td>
          <td className="samples" data-label="Examples">{column.samples.length ? column.samples.join(' · ') : 'empty'}</td>
        </tr>
      })}</tbody>
    </table></div>
    <div className="review-actions">
      <button className="ask-button" disabled={busy || errors.some(Boolean)} onClick={onImport}>
        {busy ? <><span className="spinner" aria-hidden="true"/><span>Importing…</span></> : <span>Import {formatCount(review.row_count)} rows</span>}
      </button>
      <button className="link-button" disabled={busy} onClick={onCancel}>Cancel</button>
    </div>
    {error && <div className="notice error-notice" role="alert"><span>!</span>{error}</div>}
  </section>
}

export default function UploadView({ uploads, mode, datasets, activeId, onUploaded, onUse, onDelete }) {
  const [file, setFile] = useState(null)
  const [review, setReview] = useState(null)
  const [state, setState] = useState({ status: 'idle', message: '' })
  const input = useRef(null)
  const attempt = useRef(0)

  if (!uploads?.enabled) {
    return <section className="dataset-card">
      <span className="eyebrow">YOUR FILES</span><h2 className="card-title">Uploads are turned off on this server</h2>
      <p className="explanation">This deployment isn't configured for CSV uploads. A self-hosted install can turn them on by setting INGEST_DATABASE_URL (see the README).</p>
    </section>
  }

  const maxMb = uploads.max_bytes / 1024 / 1024
  const active = datasets.find(dataset => dataset.id === activeId)

  function reset() {
    attempt.current += 1
    setFile(null)
    setReview(null)
    setState({ status: 'idle', message: '' })
    if (input.current) input.current.value = ''
  }

  // Choosing a file checks it right away; nothing is stored until the review is confirmed.
  async function check(chosen) {
    const current = ++attempt.current
    setFile(chosen)
    setReview(null)
    if (!chosen) return setState({ status: 'idle', message: '' })
    if (chosen.size > uploads.max_bytes) return setState({ status: 'error', message: `The file is larger than ${maxMb} MB.` })
    setState({ status: 'checking', message: '' })
    try {
      const preview = await api(`/api/datasets/preview?name=${encodeURIComponent(chosen.name)}`,
        { method: 'POST', headers: { 'Content-Type': 'text/csv' }, body: chosen })
      if (current !== attempt.current) return
      setReview({ ...preview, columns: preview.columns.map(column => ({ ...column, original: column.name })) })
      setState({ status: 'idle', message: '' })
    } catch (err) {
      if (current === attempt.current) setState({ status: 'error', message: err.message })
    }
  }

  function edit(index, change) {
    setReview(current => ({ ...current, columns: current.columns.map((column, i) => (i === index ? { ...column, ...change } : column)) }))
  }

  async function importFile() {
    setState({ status: 'importing', message: '' })
    try {
      const dataset = await api(`/api/datasets?name=${encodeURIComponent(file.name)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'text/csv', 'X-Dataset-Columns': JSON.stringify(columnSettings(review.columns)) },
        body: file,
      })
      onUploaded(dataset)
      reset()
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
        <div><span className="eyebrow">YOUR FILES</span><h2>Upload a CSV file</h2>
          <p>DataTalk reads the file and shows you how each column will be stored. After you confirm, it goes into a private table that is deleted after {uploads.ttl_hours} hours, or sooner if you delete it. Only this browser holds the key to it.</p></div>
        <span className="badge">Private · temporary</span>
      </div>
      <ul className="dataset-defs">
        <li>A .csv file with column names in the first row (commas, semicolons or tabs).</li>
        <li>Up to {maxMb} MB, {formatCount(uploads.max_rows)} rows and {uploads.max_columns} columns.</li>
        <li>You can rename columns and change their types before importing, for example "$1,200" as a number or 03/04/2025 as day/month/year.</li>
        <li>Please don't upload personal or confidential data to a public demo. The original file is not kept, only the table.</li>
      </ul>
      {mode === 'demo' && <div className="notice info-notice" role="status"><span>i</span>AI mode is off on this server. You can upload and inspect a file, but questions about it will be declined.</div>}
      <div className="upload-form">
        <input ref={input} type="file" accept=".csv,text/csv" aria-label="CSV file" disabled={state.status === 'importing'}
          onChange={event => check(event.target.files[0] ?? null)}/>
        {state.status === 'checking' && <span className="upload-status"><span className="spinner dark-spinner" aria-hidden="true"/>Checking the file…</span>}
      </div>
      {state.status === 'error' && !review && <div className="notice error-notice" role="alert"><span>!</span>{state.message}</div>}
    </section>

    {review && <ReviewCard review={review} busy={state.status === 'importing'} error={state.status === 'error' ? state.message : ''}
      onEdit={edit} onImport={importFile} onCancel={reset}/>}

    {datasets.length > 0 && <section className="dataset-card">
      <span className="eyebrow">YOUR UPLOADED FILES</span>
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
      <span className="eyebrow">COLUMNS IN THIS FILE</span><h2 className="card-title">{active.name}</h2>
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
