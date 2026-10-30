import { sourceLabel } from '../format.js'
import Icon from './Icon.jsx'

// The AI model asked which reading of the question was meant, so no SQL has run. Each option is a complete question;
// choosing one asks it (about the same data), through the same checks as any question.
export default function Clarification({ result, onChoose }) {
  const { question, options } = result.clarification
  return <section className="result-card clarification-card" aria-live="polite">
    <div className="result-heading">
      <div><span className="eyebrow">ONE QUESTION FIRST</span><h2>{question}</h2></div>
      <div className="result-meta"><span className="live-dot"/>AI generated<span className="meta-divider"/>{sourceLabel(result)}<span className="meta-divider"/>No query run</div>
    </div>
    <p className="explanation">DataTalk needs to know this before it writes any SQL. Choose one to ask it, or rephrase your question above.</p>
    <div className="clarification-options">{options.map(option => <button type="button" className="option-button" key={option} onClick={() => onChoose(option)}>
      <span>{option}</span><Icon name="arrow" size={15}/>
    </button>)}</div>
  </section>
}
