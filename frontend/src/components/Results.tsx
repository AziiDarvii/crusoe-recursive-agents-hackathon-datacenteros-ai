import type { RunResult, TwinState } from '../types'
import { Panel, fmt } from '../ui'

const ROWS: [string, string, 'lower' | 'higher' | 'neutral', number][] = [
  ['hard_violations', 'Hard-constraint violations', 'lower', 0],
  ['thermal_violations', 'Racks with thermal violations', 'lower', 0],
  ['power_violations', 'Racks over power budget', 'lower', 0],
  ['temp_outlet_max_c', 'Max outlet temperature (°C)', 'lower', 1],
  ['temp_inlet_max_c', 'Max inlet temperature (°C)', 'lower', 1],
  ['temp_outlet_avg_c', 'Avg outlet temperature (°C)', 'lower', 1],
  ['facility_kw_avg', 'Facility power avg (kW)', 'neutral', 1],
  ['facility_kw_peak', 'Facility power peak (kW)', 'neutral', 1],
  ['energy_kwh', 'Energy over horizon (kWh)', 'neutral', 1],
  ['cooling_kw_avg', 'Cooling power avg (kW)', 'lower', 2],
  ['pue_avg', 'PUE', 'lower', 3],
  ['gpu_allocation_pct', 'GPU allocation (%)', 'higher', 1],
  ['gpu_utilization_pct', 'GPU utilization (%)', 'higher', 1],
  ['jobs_completed', 'Jobs completed in horizon', 'higher', 0],
  ['jobs_missed_deadline', 'Jobs missing deadline', 'lower', 0],
]

function deltaClass(dir: string, d: number) {
  if (dir === 'neutral' || Math.abs(d) < 1e-9) return 'muted'
  return (dir === 'lower' ? d < 0 : d > 0) ? 'good' : 'bad'
}

export function Results({ result, state }: { result: RunResult | null; state: TwinState }) {
  if (!result) return <div className="empty">Results appear after an agent run completes.</div>
  const pre = state.pre_incident_metrics
  const { before, after } = result.metrics
  return (
    <>
      <Panel title="Before / after comparison"
        subtitle={`Same simulated workload and ${before.horizon_h} h horizon. Percentages are computed from simulator outputs; worse results are shown as worse.`}>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Metric</th><th className="num">Pre-incident</th><th className="num">Incident, no action</th>
              <th className="num">After agent plan</th><th className="num">Δ</th><th className="num">Δ %</th></tr></thead>
            <tbody>
              {ROWS.map(([key, label, dir, digits]) => {
                const c = result.comparison[key]
                if (!c) return null
                return (
                  <tr key={key}>
                    <td>{label}</td>
                    <td className="num muted">{fmt(pre?.[key], digits)}</td>
                    <td className="num">{fmt(before[key], digits)}</td>
                    <td className="num"><b>{fmt(after[key], digits)}</b></td>
                    <td className={`num ${deltaClass(dir, c.delta)}`}>{c.delta > 0 ? '+' : ''}{fmt(c.delta, digits)}</td>
                    <td className={`num ${deltaClass(dir, c.delta)}`}>{c.pct === null ? '—' : `${c.pct > 0 ? '+' : ''}${c.pct.toFixed(2)}%`}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </Panel>
      <div className="grid cols-3">
        <Panel title="Agent effort">
          <table><tbody>
            <tr><td>Outcome</td><td className="num"><span className={`pill ${result.status === 'VERIFIED' ? 'ok' : 'crit'}`}>{result.status}</span></td></tr>
            <tr><td>Recovery attempts</td><td className="num">{result.attempts.length}</td></tr>
            <tr><td>Plan rejections</td><td className="num">{result.rejections}</td></tr>
            <tr><td>Optimization runtime</td><td className="num">{result.runtime_s} s</td></tr>
            <tr><td>LLM calls</td><td className="num">{result.llm.calls}{result.provider.mock ? ' (mock)' : ''}</td></tr>
            <tr><td>LLM errors / fallbacks</td><td className="num">{result.llm.errors}</td></tr>
            <tr><td>Tokens in / out</td><td className="num">{result.llm.tokens_in} / {result.llm.tokens_out}</td></tr>
          </tbody></table>
        </Panel>
        <Panel title="Committed plan">
          {result.plan ? (
            <>
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {result.plan.actions.map((a) => <li key={a.description} className="mono">{a.description}</li>)}
              </ul>
              <p className="muted" style={{ fontSize: 12 }}>{result.plan.rationale}</p>
            </>
          ) : <div className="muted">{result.status === 'ESCALATED' ? `No plan applied. ${result.escalation_reason}` : 'No action required.'}</div>}
        </Panel>
        <Panel title="What the system learned">
          {result.learned_constraints.length ? (
            <>
              <div className="row">{result.learned_constraints.map((c) => <span key={c} className="pill agent">{c}</span>)}</div>
              <p className="muted" style={{ fontSize: 12 }}>Constraint families added to the planner after verifier rejections (lazy constraint generation).</p>
            </>
          ) : <div className="muted">No new constraint families were needed.</div>}
        </Panel>
      </div>
      <div className="callout" style={{ fontSize: 12 }}>
        These figures come from a simplified, documented simulation. They do not prove real-world safety or savings.
      </div>
    </>
  )
}
