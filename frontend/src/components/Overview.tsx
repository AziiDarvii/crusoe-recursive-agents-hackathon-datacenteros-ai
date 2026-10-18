import type { TwinState } from '../types'
import { Kpi, Panel, PRIORITY, Sparkline, fmt, priorityTone, tempColor } from '../ui'

export function Overview({ state }: { state: TwinState }) {
  const m = state.metrics
  const hasIncident = state.violations.length > 0
  return (
    <>
      <div className="kpis">
        <Kpi label="Status" value={hasIncident ? 'Incident' : state.incidents.length ? 'Mitigated' : 'Nominal'}
          tone={hasIncident ? 'crit' : 'ok'}
          sub={`${m.hard_violations} hard violation(s)${!hasIncident && state.incidents.length ? ' · fault still present' : ''}`} />
        <Kpi label="Facility power" value={fmt(m.facility_kw_avg)} unit="kW"
          sub={`peak ${fmt(m.facility_kw_peak)} / ${state.facility.power_limit_kw} kW`} />
        <Kpi label="Energy (horizon)" value={fmt(m.energy_kwh, 0)} unit="kWh" sub={`${m.horizon_h} h simulated`} />
        <Kpi label="Cooling power" value={fmt(m.cooling_kw_avg)} unit="kW" sub={`PUE ${fmt(m.pue_avg, 2)}`} />
        <Kpi label="Max outlet temp" value={fmt(m.temp_outlet_max_c)} unit="°C" tone={m.thermal_violations ? 'crit' : undefined}
          sub={`avg ${fmt(m.temp_outlet_avg_c)} °C · inlet max ${fmt(m.temp_inlet_max_c)} °C`} />
        <Kpi label="GPU allocation" value={fmt(m.gpu_allocation_pct)} unit="%" sub={`utilization ${fmt(m.gpu_utilization_pct)} %`} />
        <Kpi label="Jobs" value={`${m.jobs_completed}/${m.jobs_total}`} sub={`completed in horizon · ${m.jobs_missed_deadline} missed`}
          tone={m.jobs_missed_deadline ? 'crit' : undefined} />
      </div>

      <div className="grid cols-main">
        <Panel title="Rack temperature map" subtitle="Peak hot-aisle outlet over the simulated horizon (limit 45 °C, inlet limit 27 °C)">
          <div className="heatmap">
            {state.racks.map((r) => (
              <div key={r.id} className="heat-cell" style={{
                background: tempColor(r.peak_outlet_c),
                borderColor: r.peak_outlet_c > r.outlet_limit_c || r.peak_inlet_c > r.inlet_limit_c ? '#fecaca' : 'transparent',
              }}>
                <div className="rid">{r.id}</div>
                <div className="t">{fmt(r.peak_outlet_c)}°</div>
                <div className="s">in {fmt(r.peak_inlet_c)}° · {fmt(r.peak_it_kw)} kW</div>
              </div>
            ))}
          </div>
          <div className="legend"><span>cool</span><div className="bar" /><span>hot</span></div>
        </Panel>

        <Panel title="Active incidents" subtitle="From the deterministic safety checker">
          {state.incidents.length === 0 && state.violations.length === 0 && <div className="callout ok">No incidents. All hard constraints satisfied.</div>}
          <div className="grid" style={{ gap: 8 }}>
            {state.incidents.map((i) => <div key={i} className="callout">{i}</div>)}
            {state.violations.map((v) => (
              <div key={v.code + v.subject} className="callout crit">
                <span className="pill crit">{v.code}</span> <span className="mono">{v.message}</span>
              </div>
            ))}
          </div>
        </Panel>
      </div>

      <div className="grid cols-main">
        <Panel title="Workload queue" subtitle="Training and inference jobs with priorities, deadlines and placement">
          <div className="table-wrap">
            <table>
              <thead><tr><th>Job</th><th>Type</th><th>Priority</th><th className="num">GPUs</th><th className="num">Mem/GPU</th>
                <th className="num">Remaining</th><th className="num">Deadline</th><th>Rack</th><th>Status</th><th className="num">ETA</th></tr></thead>
              <tbody>
                {state.jobs.map((j) => (
                  <tr key={j.id}>
                    <td><span className="mono">{j.id}</span> <span className="muted">{j.name}</span></td>
                    <td>{j.kind}</td>
                    <td><span className={`pill ${priorityTone(j.priority)}`}>{PRIORITY[j.priority]}</span></td>
                    <td className="num">{j.gpus}</td>
                    <td className="num">{j.memory_gb_per_gpu} GB</td>
                    <td className="num">{fmt(j.remaining_h, 2)} h</td>
                    <td className="num">{j.deadline_h} h</td>
                    <td className="mono">{j.rack_id ?? '—'}</td>
                    <td>
                      <span className={`pill ${j.status === 'queued' ? '' : j.status === 'deferred' ? 'warn' : 'info'}`}>{j.status}</span>
                      {j.migratable && <span className="faint" title="migratable"> ⇄</span>}
                      {j.deferrable && <span className="faint" title="deferrable"> ⏸</span>}
                    </td>
                    <td className={`num ${j.missed_deadline ? 'bad' : ''}`}>
                      {j.completion_h === null ? 'unscheduled' : `${fmt(j.completion_h, 2)} h`}{j.missed_deadline && ' ⚠'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
        <Panel title="Facility power" subtitle={`Simulated ${state.facility.horizon_h} h horizon, ${state.facility.step_h * 60}-min steps`}>
          <Sparkline values={state.facility_series} height={120} />
          <div className="row muted" style={{ justifyContent: 'space-between', fontSize: 11.5 }}>
            <span>t = 0</span><span>IT + cooling + UPS losses + misc</span><span>t = {state.facility.horizon_h} h</span>
          </div>
        </Panel>
      </div>
    </>
  )
}
