import { useEffect, useState } from 'react'
import { api } from '../api'
import type { AgentConfig, ProviderInfo } from '../types'
import { Panel } from '../ui'

export function Settings({ provider, onError }: { provider: ProviderInfo | null; onError: (msg: string) => void }) {
  const [cfg, setCfg] = useState<AgentConfig | null>(null)
  const [saved, setSaved] = useState(false)
  const [assumptions, setAssumptions] = useState<{ constants: Record<string, number>; tool_permissions: Record<string, string[]> } | null>(null)

  useEffect(() => {
    api.config().then(setCfg).catch((e: Error) => onError(e.message))
    api.assumptions().then(setAssumptions).catch((e: Error) => onError(e.message))
  }, [onError])

  if (!cfg) return <div className="empty">Loading…</div>
  const num = (key: keyof AgentConfig, step = 1) => (
    <label className="field">{key.replace(/_/g, ' ')}
      <input type="number" step={step} value={cfg[key] as number}
        onChange={(e) => { setSaved(false); setCfg({ ...cfg, [key]: Number(e.target.value) }) }} />
    </label>
  )
  const save = () => api.saveConfig(cfg).then((c) => { setCfg(c); setSaved(true) }).catch((e: Error) => onError(e.message))

  return (
    <>
      <Panel title="Agent budget & objective" subtitle="Hard safety constraints always override the objective"
        actions={<>{saved && <span className="good">Saved</span>}<button className="btn primary" onClick={save}>Save</button></>}>
        <div className="form">
          {num('max_attempts')}{num('max_depth')}{num('max_llm_calls')}{num('pacing_s', 0.05)}
          {(Object.keys(cfg.weights) as (keyof AgentConfig['weights'])[]).map((w) => (
            <label key={w} className="field">weight · {w}
              <input type="number" step={0.1} value={cfg.weights[w]}
                onChange={(e) => { setSaved(false); setCfg({ ...cfg, weights: { ...cfg.weights, [w]: Number(e.target.value) } }) }} />
            </label>
          ))}
          <label className="chk">
            <input type="checkbox" checked={cfg.use_cross_run_memory}
              onChange={(e) => { setSaved(false); setCfg({ ...cfg, use_cross_run_memory: e.target.checked }) }} />
            Cross-run memory (pre-load known-invalid plans from earlier runs)
          </label>
        </div>
      </Panel>
      <div className="grid cols-2">
        <Panel title="Inference provider">
          {provider?.mock ? (
            <div className="callout agent">
              <b>Mock mode active.</b> Agent decisions come from a deterministic, offline policy. No model inference occurs.
              <p style={{ marginBottom: 6 }}>To use Crusoe Managed Inference:</p>
              <pre className="code">{`export CRUSOE_API_KEY=<your key>
export CRUSOE_MODEL=nvidia/Nemotron-3-Nano-30B-A3B   # optional
cd backend && .venv/bin/uvicorn app.main:app --port 8000`}</pre>
            </div>
          ) : (
            <div className="callout ok">Connected to <b>{provider?.model}</b> via {provider?.base_url}.</div>
          )}
        </Panel>
        <Panel title="Tool permissions" subtitle="Enforced in code; an agent calling a tool outside its set raises an error">
          <table><tbody>
            {assumptions && Object.entries(assumptions.tool_permissions).map(([agent, tools]) => (
              <tr key={agent}><td className={`who ${agent}`} style={{ fontWeight: 600 }}>{agent}</td>
                <td className="mono" style={{ fontSize: 11.5 }}>{tools.join(', ')}</td></tr>
            ))}
          </tbody></table>
        </Panel>
      </div>
      <Panel title="Simulation assumptions" subtitle="Documented constants. These are estimates, not measurements, and are not real Crusoe data.">
        <div className="table-wrap"><table><tbody>
          {assumptions && Object.entries(assumptions.constants).map(([k, v]) => (
            <tr key={k}><td className="mono">{k}</td><td className="num mono">{String(v)}</td></tr>
          ))}
        </tbody></table></div>
      </Panel>
    </>
  )
}
