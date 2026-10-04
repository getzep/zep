import { useEffect, useMemo, useState } from 'react'
import { useChat } from '@ai-sdk/react'
import { DefaultChatTransport, type UIMessage } from 'ai'

type ToolPart = {
  type: string
  toolName?: string
  toolCallId: string
  state: string
  input?: unknown
  output?: unknown
  errorText?: string
}

type PlanStep = { tool: string; purpose: string; arguments_hint: string }
type RetrievalPlan = {
  subjects: string[]
  steps: PlanStep[]
  evidence_needed: string[]
  stop_when: string
}

type Orientation = {
  entity_types?: string
  edge_types?: string
  nodes?: { uuid: string; name: string; labels: string[] }[]
}

const SAMPLE_QUESTIONS = [
  'Is the Lyric 350 ready to launch on its planned date?',
  'Did firmware 2.3.1 fix the Aster 410 under-delivery issue?',
  'Which members of the Reliability Engineering team work on the Aster 410?',
  'What are the most important risks at Pemberline right now?',
]

function isToolPart(part: { type: string }): part is ToolPart {
  return part.type === 'dynamic-tool' || part.type.startsWith('tool-')
}

function toolName(part: ToolPart): string {
  return part.type === 'dynamic-tool' ? (part.toolName ?? 'tool') : part.type.slice('tool-'.length)
}

function asPlan(input: unknown): RetrievalPlan | null {
  if (!input || typeof input !== 'object') return null
  const value = 'plan' in input ? (input as { plan: unknown }).plan : input
  if (value && typeof value === 'object' && 'steps' in value) return value as RetrievalPlan
  return null
}

function format(value: unknown): string {
  if (value === undefined) return ''
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2)
}

function lastAssistant(messages: UIMessage[]): UIMessage | undefined {
  return [...messages].reverse().find((m) => m.role === 'assistant')
}

export default function App() {
  const [input, setInput] = useState('')
  const [useDomainKnowledge, setUseDomainKnowledge] = useState(true)
  const [usePlanning, setUsePlanning] = useState(true)
  const [tab, setTab] = useState<'plan' | 'tools'>('plan')
  const [contextTab, setContextTab] = useState<'graph' | 'domain'>('graph')
  const [orientation, setOrientation] = useState<Orientation | null>(null)
  const [domainKnowledge, setDomainKnowledge] = useState<string>('')

  const [runStart, setRunStart] = useState<number | null>(null)
  const [runMs, setRunMs] = useState<number | null>(null)
  const transport = useMemo(
    () =>
      new DefaultChatTransport({
        api: `/api/chat?domain_knowledge=${useDomainKnowledge}&planning=${usePlanning}`,
      }),
    [useDomainKnowledge, usePlanning],
  )
  const { messages, sendMessage, status, error } = useChat({
    transport,
    onFinish: () => setRunMs(runStart === null ? null : Date.now() - runStart),
  })
  const busy = status === 'submitted' || status === 'streaming'

  useEffect(() => {
    fetch('/api/orientation').then((r) => (r.ok ? r.json() : null)).then(setOrientation).catch(() => setOrientation(null))
    fetch('/api/domain-knowledge').then((r) => (r.ok ? r.json() : null)).then((d) => setDomainKnowledge(d?.domain_knowledge ?? '')).catch(() => setDomainKnowledge(''))
  }, [])

  const toolParts = useMemo(() => {
    const turn = lastAssistant(messages)
    return (turn?.parts ?? []).filter(isToolPart) as ToolPart[]
  }, [messages])

  const plans = toolParts.filter((p) => toolName(p) === 'submit_plan').map((p) => asPlan(p.input)).filter(Boolean) as RetrievalPlan[]
  const retrievalCalls = toolParts.filter((p) => toolName(p) !== 'submit_plan')

  function ask(text: string) {
    const question = text.trim()
    if (!question || busy) return
    // oxlint-disable-next-line react/purity -- ask() runs from event handlers, not during render.
    setRunStart(Date.now())
    setRunMs(null)
    sendMessage({ text: question })
    setInput('')
  }

  return (
    <div className="app">
      <header className="header">
        <div>
          <h1>Agent with Zep</h1>
          <p>A Pemberline Medical analyst agent. Zep is the context layer; the agent plans and calls retrieval tools.</p>
        </div>
        <div className="toggles">
          <label className="toggle">
            <input type="checkbox" checked={useDomainKnowledge} onChange={(e) => setUseDomainKnowledge(e.target.checked)} />
            Domain knowledge
          </label>
          <label className="toggle">
            <input type="checkbox" checked={usePlanning} onChange={(e) => setUsePlanning(e.target.checked)} />
            Planning step
          </label>
        </div>
      </header>

      <main className="layout">
        <section className="card chat">
          <div className="card-title">Conversation</div>
          <div className="messages">
            {messages.length === 0 && (
              <div className="empty">
                Ask a question about products, quality issues, regulatory status, suppliers, or owners.
                <div className="samples">
                  {SAMPLE_QUESTIONS.map((q) => (
                    <button key={q} onClick={() => ask(q)}>{q}</button>
                  ))}
                </div>
              </div>
            )}
            {messages.map((m) => (
              <div key={m.id} className={`msg ${m.role}`}>
                {m.role === 'assistant' &&
                  m.parts.filter(isToolPart).map((p) => (
                    <span key={(p as ToolPart).toolCallId} className="tool-chip">{toolName(p as ToolPart)}</span>
                  ))}
                {m.parts.filter((p) => p.type === 'text').map((p, i) => (
                  <div key={i}>{(p as { text: string }).text}</div>
                ))}
              </div>
            ))}
            {status === 'submitted' && <div className="empty">The agent is planning…</div>}
          </div>
          {error && <div className="error">{error.message}</div>}
          <form className="composer" onSubmit={(e) => { e.preventDefault(); ask(input) }}>
            <input value={input} onChange={(e) => setInput(e.target.value)} placeholder="Ask a question" aria-label="Question" />
            <button type="submit" disabled={busy || !input.trim()}>Send</button>
          </form>
        </section>

        <aside className="side">
          <section className="card">
            <div className="tabs">
              <button className={tab === 'plan' ? 'active' : ''} onClick={() => setTab('plan')}>
                Retrieval plan {plans.length > 0 && <span className="badge">{plans.length}</span>}
              </button>
              <button className={tab === 'tools' ? 'active' : ''} onClick={() => setTab('tools')}>
                Tool calls {retrievalCalls.length > 0 && <span className="badge">{retrievalCalls.length}</span>}
              </button>
            </div>
            <div className="panel-body">
              {tab === 'plan' && <PlanView plans={plans} planning={usePlanning} />}
              {tab === 'tools' && <ToolTimeline parts={retrievalCalls} runMs={runMs} />}
            </div>
          </section>

          <section className="card">
            <div className="tabs">
              <button className={contextTab === 'graph' ? 'active' : ''} onClick={() => setContextTab('graph')}>Graph orientation</button>
              <button className={contextTab === 'domain' ? 'active' : ''} onClick={() => setContextTab('domain')}>Domain knowledge</button>
            </div>
            <div className="panel-body">
              {contextTab === 'graph' && <OrientationView orientation={orientation} />}
              {contextTab === 'domain' && (
                <div className="domain">
                  <p className="note">The application writes this text. It goes in the system prompt when the toggle is on.</p>
                  <pre>{domainKnowledge || 'Not loaded.'}</pre>
                </div>
              )}
            </div>
          </section>
        </aside>
      </main>
    </div>
  )
}

function PlanView({ plans, planning }: { plans: RetrievalPlan[]; planning: boolean }) {
  if (plans.length === 0) {
    return <p className="note">{planning ? 'The plan will show here after the agent submits it.' : 'The planning step is off.'}</p>
  }
  return (
    <div className="plan">
      {plans.map((plan, i) => (
        <div key={i}>
          {plans.length > 1 && <span className="badge">{i === 0 ? 'First plan' : 'Revised plan'}</span>}
          <h4>Subjects</h4>
          <ul>{plan.subjects?.map((s) => <li key={s}>{s}</li>)}</ul>
          <h4>Steps</h4>
          <ol>
            {plan.steps?.map((s, j) => (
              <li key={j}><code>{s.tool}</code> — {s.purpose} <span className="note">({s.arguments_hint})</span></li>
            ))}
          </ol>
          <h4>Evidence needed</h4>
          <ul>{plan.evidence_needed?.map((e) => <li key={e}>{e}</li>)}</ul>
          <h4>Stop when</h4>
          <p>{plan.stop_when}</p>
        </div>
      ))}
    </div>
  )
}

function ToolTimeline({ parts, runMs }: { parts: ToolPart[]; runMs: number | null }) {
  if (parts.length === 0) return <p className="note">Retrieval tool calls will show here.</p>
  return (
    <div className="timeline">
      {runMs !== null && <p className="note">The run took {(runMs / 1000).toFixed(1)} s with {parts.length} retrieval calls.</p>}
      {parts.map((p, i) => {
        const done = p.state === 'output-available'
        return (
          <details key={p.toolCallId} className="call">
            <summary>
              <span className="mono">{i + 1}.</span>
              <span className="name">{toolName(p)}</span>
              <span className={done ? 'state-done' : 'state-run'}>{done ? 'done' : p.state === 'output-error' ? 'error' : 'running'}</span>
              <span className="meta">{p.output !== undefined ? `${format(p.output).length} chars` : ''}</span>
            </summary>
            <pre>{format(p.input)}</pre>
            {(p.output !== undefined || p.errorText) && <pre>{p.errorText ?? format(p.output)}</pre>}
          </details>
        )
      })}
    </div>
  )
}

function OrientationView({ orientation }: { orientation: Orientation | null }) {
  if (!orientation) return <p className="note">Run the server to load the graph orientation.</p>
  return (
    <div>
      <p className="note">The agent learns this once per graph. The schema goes in the system prompt. The node sample goes in the first user message as data.</p>
      <h4 className="note">Entity types</h4>
      <pre className="mono schema">{orientation.entity_types}</pre>
      <h4 className="note">Edge types</h4>
      <pre className="mono schema">{orientation.edge_types}</pre>
      <h4 className="note">Most connected nodes</h4>
      <ul className="kv">{orientation.nodes?.map((n) => <li key={n.uuid}>{n.name} <span className="note">[{n.labels.join(', ')}]</span></li>)}</ul>
    </div>
  )
}
