import type { Attempt, RunEvent, RunStatus, TaskNode } from '../types'
import { Panel } from '../ui'

const FSM = ['IDLE', 'INVESTIGATING', 'PLANNING', 'VALIDATING', 'REJECTED', 'RETRYING', 'SIMULATING', 'VERIFIED', 'ESCALATED']

function StateMachine({ events }: { events: RunEvent[] }) {
  const states = events.filter((e) => e.kind === 'state').map((e) => e.state)
  const current = states[states.length - 1] ?? 'IDLE'
  const counts = states.reduce<Record<string, number>>((acc, s) => ({ ...acc, [s]: (acc[s] ?? 0) + 1 }), {})
  return (
    <div>
      <div className="fsm">
        {FSM.map((s, i) => (
          <span key={s} className="row" style={{ gap: 6 }}>
            <span className={`fsm-node ${s} ${s === current ? 'current' : counts[s] ? 'visited' : ''}`}>
              {s}{counts[s] > 1 && <span className="n">×{counts[s]}</span>}
            </span>
            {i < FSM.length - 1 && <span className="fsm-arrow">›</span>}
          </span>
        ))}
      </div>
      <div className="trail">
        <span>IDLE</span>
        {states.map((s, i) => <span key={i} className={s}>→ {s}</span>)}
      </div>
    </div>
  )
}

function TaskTree({ events, tasks }: { events: RunEvent[]; tasks?: TaskNode[] }) {
  const nodes: TaskNode[] = tasks ?? events.filter((e) => e.kind === 'task').map((e) => e.data as unknown as TaskNode)
  const tone = (s: string) => (s === 'verified' || s === 'done' ? 'ok' : s === 'rejected' || s === 'failed' || s === 'escalated' ? 'crit' : 'info')
  return (
    <div className="tree">
      {nodes.map((n) => (
        <div key={n.id} className="node" style={{ marginLeft: n.depth * 16 }}>
          <span className={`ag who ${n.agent}`}>{n.agent}</span>
          <span className="ttl" title={n.result ?? n.title}>{n.title}</span>
          <span className={`pill ${tone(n.status)}`}>{n.status}</span>
        </div>
      ))}
      {nodes.length === 0 && <div className="muted">No tasks yet.</div>}
    </div>
  )
}

function AttemptCard({ a }: { a: Attempt }) {
  const tone = a.verdict === 'PASS' ? 'ok' : 'crit'
  return (
    <div className={`attempt ${a.verdict}`}>
      <div className="attempt-head">
        <b>Attempt {a.attempt}</b>
        <span className="pill">depth {a.depth}</span>
        <span className="pill agent">{a.strategy}</span>
        <span className="muted">by {a.author}</span>
        <span className="spacer" />
        <span className={`pill ${tone}`}>{a.verdict}</span>
      </div>
      {a.plan ? (
        <ul>{a.plan.actions.map((x) => <li key={x.description} className="mono">{x.description}</li>)}</ul>
      ) : <div className="muted">Optimizer found no plan satisfying all known constraints with this strategy.</div>}
      {a.violations.length > 0 && (
        <ul>{a.violations.map((v, i) => <li key={i} className="reason">{v.code}: {v.message}</li>)}</ul>
      )}
      {a.energy_impact && (
        <div className="muted" style={{ marginTop: 6, fontSize: 12 }}>
          Energy {a.energy_impact.energy_kwh.before} → {a.energy_impact.energy_kwh.after} kWh · cooling{' '}
          {a.energy_impact.cooling_kw_avg.before} → {a.energy_impact.cooling_kw_avg.after} kW
        </div>
      )}
    </div>
  )
}

function LlmOutput({ data }: { data: Record<string, unknown> }) {
  const out = data.output as Record<string, unknown> | undefined
  if (!out) return null
  const text = (out.rationale ?? out.summary ?? '') as string
  const strategy = out.strategy as string | undefined
  return (
    <div className="d">
      {strategy && <span className="pill agent" style={{ marginRight: 6 }}>{strategy}</span>}
      {text}
      {data.latency_ms != null && <span className="faint"> · {String(data.latency_ms)} ms</span>}
      {data.tokens_out != null && <span className="faint"> · {String(data.tokens_in)}/{String(data.tokens_out)} tok</span>}
    </div>
  )
}

function Timeline({ events }: { events: RunEvent[] }) {
  const shown = events.filter((e) => e.kind !== 'task')
  return (
    <div className="timeline">
      {shown.map((e) => {
        const status = e.kind === 'verdict' ? String(e.data.status) : ''
        const llm = e.kind === 'llm' ? e.data : null
        return (
          <div key={e.seq} className={`ev ${e.kind} ${status}`}>
            <div className="t">{(e.t_ms / 1000).toFixed(2)}s</div>
            <div className={`who ${e.agent}`}>{e.agent}</div>
            <div className="what">
              <div>
                {e.kind === 'tool' && <span className="pill info" style={{ marginRight: 6 }}>tool</span>}
                {e.kind === 'llm' && <span className={`pill ${llm?.mock ? 'agent' : 'ok'}`} style={{ marginRight: 6 }}>
                  {llm?.mock ? 'mock LLM' : 'LLM'}</span>}
                {e.kind === 'memory' && <span className="pill agent" style={{ marginRight: 6 }}>memory</span>}
                {e.title}
              </div>
              {e.detail && <div className="d">{e.detail}</div>}
              {llm && <LlmOutput data={llm} />}
            </div>
          </div>
        )
      })}
      {shown.length === 0 && <div className="muted">Waiting for events…</div>}
    </div>
  )
}

export function AgentRun({ run, events, onReplay }: { run: RunStatus | null; events: RunEvent[]; onReplay?: () => void }) {
  if (!run) {
    return <div className="empty">No agent run yet. Click <b>Inject Cooling Failure</b> to trigger the autonomous coordinator.</div>
  }
  const done = run.status !== 'running' && events.length >= run.events.length
  const result = done ? run.result : null
  const attempts: Attempt[] = result?.attempts
    ?? events.filter((e) => e.kind === 'memory' && e.data.attempt).map((e) => e.data.attempt as Attempt)
  return (
    <>
      <Panel title={<>Recursive agent run <span className="mono muted">#{run.run_id}</span></>}
        subtitle={run.replay ? 'Replaying from SQLite memory' : run.status === 'running' ? 'Live — every row is a real tool call, LLM call or state transition'
          : 'Completed'}
        actions={<>
          {run.status === 'running' && <span className="badge live"><span className="dot pulse" />running</span>}
          {result && <span className={`badge ${result.status === 'VERIFIED' ? 'live' : 'crit'}`}>{result.status}</span>}
          {done && onReplay && <button className="btn" onClick={onReplay}>Replay</button>}
        </>}>
        <StateMachine events={events} />
        {run.error && <div className="callout crit" style={{ marginTop: 12 }}>Run failed: {run.error}</div>}
        {result && (
          <div className={`callout ${result.status === 'VERIFIED' ? 'ok' : 'crit'}`} style={{ marginTop: 12 }}>
            {result.summary}
            <div className="muted" style={{ marginTop: 4, fontSize: 12 }}>
              {result.attempts.length} attempt(s) · {result.rejections} rejection(s) · {result.llm.calls} LLM call(s)
              {result.provider.mock ? ' (mock provider)' : ` (${result.provider.model})`} · {result.runtime_s}s runtime
              {result.learned_constraints.length > 0 && <> · learned: <span className="mono">{result.learned_constraints.join(', ')}</span></>}
            </div>
          </div>
        )}
      </Panel>
      <div className="grid cols-main">
        <Panel title="Execution timeline" subtitle={`${events.filter((e) => e.kind !== 'task').length} events`}>
          <Timeline events={events} />
        </Panel>
        <div className="grid" style={{ alignContent: 'start' }}>
          <Panel title="Plan attempts" subtitle="Rejected plans are stored and become tabu; violated constraint families are learned">
            <div className="attempts">
              {attempts.map((a) => <AttemptCard key={a.attempt} a={a} />)}
              {attempts.length === 0 && <div className="muted">No plan attempts yet.</div>}
            </div>
          </Panel>
          <Panel title="Delegation tree" subtitle="Coordinator → specialists → recursive attempts">
            <TaskTree events={events} tasks={result?.tasks} />
          </Panel>
        </div>
      </div>
    </>
  )
}
