import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'
import { AgentRun } from './components/AgentRun'
import { Benchmarks } from './components/Benchmarks'
import { DigitalTwin } from './components/DigitalTwin'
import { MemoryView } from './components/MemoryView'
import { Overview } from './components/Overview'
import { Results } from './components/Results'
import { Settings } from './components/Settings'
import type { ProviderInfo, RunStatus, TwinState } from './types'

const TABS = ['Overview', 'Digital Twin', 'Agent Run', 'Results', 'Memory', 'Benchmarks', 'Settings'] as const
type Tab = (typeof TABS)[number]
const SCENARIOS = ['normal', 'cooling_failure', 'power_constraint', 'deadline', 'infeasible']
const STATE_POLL_MS = 1500
const RUN_POLL_MS = 300
const REPLAY_STEP_MS = 140
const slug = (t: string) => t.toLowerCase().replace(/\s+/g, '-')
const tabFromHash = (): Tab => TABS.find((t) => `#${slug(t)}` === window.location.hash) ?? 'Overview'

export default function App() {
  const [twin, setTwin] = useState<TwinState | null>(null)
  const [provider, setProvider] = useState<ProviderInfo | null>(null)
  const [tab, setTabState] = useState<Tab>(tabFromHash)
  const setTab = useCallback((t: Tab) => {
    setTabState(t)
    window.history.replaceState(null, '', `#${slug(t)}`)
  }, [])
  const [run, setRun] = useState<RunStatus | null>(null)
  const [visible, setVisible] = useState(0)
  const [replaying, setReplaying] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [scenario, setScenario] = useState('normal')
  const [memoryKey, setMemoryKey] = useState(0)
  const runIdRef = useRef<string | null>(null)

  const onError = useCallback((msg: string) => setError(msg), [])
  const refreshState = useCallback(() => api.state().then(setTwin).catch((e: Error) => setError(e.message)), [])

  useEffect(() => {
    refreshState()
    api.provider().then(setProvider).catch((e: Error) => setError(e.message))
    // Show the most recent stored run (e.g. after a page reload).
    api.runs().then(({ runs }) => {
      if (!runs.length || runIdRef.current) return
      runIdRef.current = runs[0].id
      api.run(runs[0].id).then((r) => { setRun(r); setVisible(r.events.length) })
    }).catch((e: Error) => setError(e.message))
    const id = setInterval(refreshState, STATE_POLL_MS)
    return () => clearInterval(id)
  }, [refreshState])

  // Poll the live run until it finishes.
  useEffect(() => {
    if (!run || run.status !== 'running' || run.replay) return
    const id = setInterval(() => {
      const runId = runIdRef.current
      if (!runId) return
      api.run(runId).then((r) => {
        setRun(r)
        setVisible(r.events.length)
        if (r.status !== 'running') { refreshState(); setMemoryKey((k) => k + 1) }
      }).catch((e: Error) => setError(e.message))
    }, RUN_POLL_MS)
    return () => clearInterval(id)
  }, [run, refreshState])

  // Replay animation: reveal stored events one by one.
  useEffect(() => {
    if (!replaying || !run) return
    if (visible >= run.events.length) { setReplaying(false); return }
    const id = setTimeout(() => setVisible((v) => v + 1), REPLAY_STEP_MS)
    return () => clearTimeout(id)
  }, [replaying, visible, run])

  useEffect(() => {
    if (!error) return
    const id = setTimeout(() => setError(null), 6000)
    return () => clearTimeout(id)
  }, [error])

  const startTracking = (runId: string) => {
    runIdRef.current = runId
    setRun({ run_id: runId, status: 'running', events: [], result: null, error: null, replay: false })
    setVisible(0)
    setReplaying(false)
    setTab('Agent Run')
  }

  const act = async (fn: () => Promise<void>) => {
    setBusy(true)
    try { await fn() } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  const inject = () => act(async () => {
    const res = await api.injectCoolingFailure()
    setTwin(res.state)
    startTracking(res.run_id)
  })
  const runAgents = () => act(async () => startTracking((await api.startRun()).run_id))
  const reset = () => act(async () => { setTwin(await api.reset(scenario)); setTab('Overview') })

  const replay = (runId: string) => act(async () => {
    const r = await api.run(runId)
    runIdRef.current = runId
    setRun({ ...r, replay: true })
    setVisible(0)
    setReplaying(true)
    setTab('Agent Run')
  })

  const running = run?.status === 'running' && !run.replay
  const incident = (twin?.violations.length ?? 0) > 0
  const events = run ? run.events.slice(0, visible) : []

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">◫</div>
          <div><h1>DataCenterOS AI</h1><small>Recursive agents for GPU data-center operations</small></div>
        </div>
        <div className="badges">
          <span className="badge sim"><span className="dot" />SIMULATION — synthetic data, no real controls</span>
          {provider && (provider.mock
            ? <span className="badge mock">Mock LLM mode</span>
            : <span className="badge live"><span className="dot" />Crusoe · {provider.model}</span>)}
          {running && <span className="badge live"><span className="dot pulse" />agents running</span>}
          {!running && incident && <span className="badge crit"><span className="dot pulse" />incident</span>}
        </div>
        <div className="spacer" />
        <div className="row">
          <select className="btn" value={scenario} onChange={(e) => setScenario(e.target.value)} aria-label="Scenario">
            {SCENARIOS.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <button className="btn" onClick={reset} disabled={busy || running}>Load scenario</button>
          <button className="btn" onClick={runAgents} disabled={busy || running || !incident}>Run agents</button>
          <button className="btn danger" onClick={inject} disabled={busy || running}>Inject Cooling Failure</button>
        </div>
      </header>

      <nav className="nav">
        {TABS.map((t) => (
          <button key={t} className={tab === t ? 'active' : ''} onClick={() => setTab(t)}>
            {t}
            {t === 'Agent Run' && run && <span className="count">{events.filter((e) => e.kind !== 'task').length}</span>}
          </button>
        ))}
      </nav>

      <main>
        {!twin ? <div className="empty">Connecting to backend…</div> : (
          <>
            {tab === 'Overview' && <Overview state={twin} />}
            {tab === 'Digital Twin' && <DigitalTwin state={twin} />}
            {tab === 'Agent Run' && <AgentRun run={run} events={events} onReplay={run ? () => replay(run.run_id) : undefined} />}
            {tab === 'Results' && <Results result={run && !replaying ? run.result : null} state={twin} />}
            {tab === 'Memory' && <MemoryView onReplay={replay} refreshKey={memoryKey} onError={onError} />}
            {tab === 'Benchmarks' && <Benchmarks onError={onError} />}
            {tab === 'Settings' && <Settings provider={provider} onError={onError} />}
          </>
        )}
      </main>
      {error && <div className="toast" role="alert">{error}</div>}
    </div>
  )
}
