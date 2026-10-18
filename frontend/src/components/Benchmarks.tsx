import { useState } from 'react'
import { api } from '../api'
import type { BenchmarkRow } from '../types'
import { Panel } from '../ui'

const COLS: [string, string][] = [
  ['hard_violations', 'Violations'], ['temp_outlet_max_c', 'Max outlet °C'], ['energy_kwh', 'Energy kWh'],
  ['jobs_missed_deadline', 'Missed deadlines'], ['gpu_utilization_pct', 'GPU util %'],
]

export function Benchmarks({ onError }: { onError: (msg: string) => void }) {
  const [rows, setRows] = useState<BenchmarkRow[]>([])
  const [busy, setBusy] = useState(false)

  const run = () => {
    setBusy(true)
    api.benchmarks().then((r) => setRows(r.rows)).catch((e: Error) => onError(e.message)).finally(() => setBusy(false))
  }

  const download = () => {
    const blob = new Blob([JSON.stringify(rows, null, 2)], { type: 'application/json' })
    const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: 'datacenteros-benchmarks.json' })
    a.click()
    URL.revokeObjectURL(a.href)
  }

  return (
    <Panel title="Reproducible benchmark suite"
      subtitle="Five scenarios, same workload and 6 h horizon. CLI: python scripts/benchmark.py → benchmarks/results.{json,csv}"
      actions={<>
        <button className="btn primary" onClick={run} disabled={busy}>{busy ? 'Running…' : 'Run benchmarks'}</button>
        {rows.length > 0 && <button className="btn" onClick={download}>Download JSON</button>}
      </>}>
      {rows.length === 0 ? <div className="empty">Run the suite to compute results live.</div> : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Scenario</th><th>Outcome</th><th>Expected</th><th className="num">Attempts</th><th className="num">Rejections</th>
                <th className="num">LLM calls</th>
                {COLS.map(([, l]) => <th key={l} className="num">{l}</th>)}
                <th>Plan</th><th className="num">Runtime</th></tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={String(r.scenario)}>
                  <td className="mono">{String(r.scenario)}</td>
                  <td><span className={`pill ${r.status === 'VERIFIED' ? 'ok' : 'crit'}`}>{String(r.status)}</span></td>
                  <td>{r.as_expected ? <span className="good">✓ {String(r.expected)}</span> : <span className="bad">✗ {String(r.expected)}</span>}</td>
                  <td className="num">{String(r.attempts)}</td>
                  <td className="num">{String(r.rejections)}</td>
                  <td className="num">{String(r.llm_calls)}</td>
                  {COLS.map(([k]) => (
                    <td key={k} className="num">{String(r[`${k}_before`])} → <b>{String(r[`${k}_after`])}</b></td>
                  ))}
                  <td className="mono" style={{ fontSize: 11.5 }}>{String(r.plan) || '—'}</td>
                  <td className="num">{String(r.runtime_s)}s</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}
