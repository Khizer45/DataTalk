import { formatCount } from '../format.js'

function ColumnNote({ column }) {
  const tags = [column.primary_key && 'primary key', column.references && `links to ${column.references}`, column.nullable && 'can be empty'].filter(Boolean)
  if (!column.description && tags.length === 0) return null
  return <small>{[column.description, tags.length > 0 && `(${tags.join(', ')})`].filter(Boolean).join(' ')}</small>
}

export default function DatasetView({ schemaInfo, health }) {
  if (!schemaInfo) {
    return <section className="dataset-card">
      <p className="explanation">{health === 'ok' ? 'Loading the dataset description…' : 'The dataset description will appear once the server is reachable.'}</p>
    </section>
  }
  const { dataset, tables } = schemaInfo
  return <section className="dataset-card">
    <div className="dataset-head">
      <div><span className="eyebrow">DATASET</span><h2>{dataset.name}</h2><p>{dataset.description}</p></div>
      {dataset.synthetic && <span className="badge">Synthetic data</span>}
    </div>
    {dataset.definitions?.length > 0 && <>
      <h3 className="dataset-subhead">Definitions</h3>
      <ul className="dataset-defs">{dataset.definitions.map(item => <li key={item}>{item}</li>)}</ul>
    </>}
    <div className="table-grid">{tables.map(table => <article className="table-card" key={table.name}>
      <h3>{table.name}<span>{formatCount(table.row_count)} rows</span></h3>
      {table.description && <p className="table-description">{table.description}</p>}
      <ul className="column-list">{table.columns.map(column => <li key={column.name}>
        <div className="column-row"><span>{column.name}</span><code>{column.type.toLowerCase()}</code></div>
        <ColumnNote column={column}/>
      </li>)}</ul>
    </article>)}</div>
    <p className="dataset-access">DataTalk's database user can only read these tables. It cannot change data, and every query has a time limit and a row limit.</p>
  </section>
}
