import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { MemoryAttempt, RunSummary } from '../types'
import { Panel } from '../ui'

export function MemoryView({ onReplay, refreshKey, onError }: {
  onReplay: (runId: string) => void; refreshKey: number; onError: (msg: string) => void
}) {
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [attempts, setAttempts] = useState<MemoryAttempt[]>([])

  const load = useCallback(() => {
    Promise.all([api.runs(), api.memory()])
      .then(([r, m]) => { setRuns(r.runs); setAttempts(m.attempts) })
      .catch((e: Error) => onError(e.message))
  }, [onError])
  useEffect(() => { load() }, [load, refreshKey])

  const clear = () => api.clearMemory().then(load).catch((e: Error) => onError(e.message))

  return (
    <>
      <Panel title="Run history" subtitle="Persisted in SQLite — select a run to replay its execution history"
        actions={<button className="btn ghost" onClick={clear}>Clear memory</button>}>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Run</th><th>Scenario</th><th>Provider</th><th>Status</th><th>Started (UTC)</th><th>Summary</th><th /></tr></thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id}>
                  <td className="mono">{r.id}</td>
                  <td>{r.scenario}</td>
                  <td>{r.provider}</td>
                  <td><span className={`pill ${r.status === 'VERIFIED' ? 'ok' : r.status === 'ESCALATED' ? 'crit' : 'info'}`}>{r.status}</span></td>
                  <td className="mono muted">{r.started_at.replace('T', ' ').replace('+00:00', '')}</td>
                  <td className="muted" style={{ maxWidth: 420 }}>{r.summary}</td>
                  <td><button className="btn" onClick={() => onReplay(r.id)}>Replay</button></td>
                </tr>
              ))}
            </tbody>
          </table>
          {runs.length === 0 && <div className="empty">No runs stored yet.</div>}
        </div>
      </Panel>
      <Panel title="Plan memory" subtitle="Every attempted plan, its verdict and machine-readable rejection reasons">
        <div className="table-wrap">
          <table>
            <thead><tr><th>Run</th><th>#</th><th>Scenario</th><th>Strategy</th><th>Plan</th><th>Verdict</th><th>Reasons</th></tr></thead>
            <tbody>
              {attempts.map((a) => (
                <tr key={`${a.run_id}-${a.attempt_no}`}>
                  <td className="mono">{a.run_id}</td>
                  <td>{a.attempt_no}</td>
                  <td>{a.scenario}</td>
                  <td><span className="pill agent">{a.strategy}</span></td>
                  <td className="mono" style={{ fontSize: 11.5 }}>{a.plan?.actions.map((x) => x.description).join('; ') ?? '—'}</td>
                  <td><span className={`pill ${a.verdict === 'PASS' ? 'ok' : 'crit'}`}>{a.verdict}</span></td>
                  <td className="reason">{a.violations.map((v) => v.code).join(', ')}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {attempts.length === 0 && <div className="empty">No attempts stored yet.</div>}
        </div>
      </Panel>
    </>
  )
}
