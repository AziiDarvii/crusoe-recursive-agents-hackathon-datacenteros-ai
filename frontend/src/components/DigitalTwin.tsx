import type { CSSProperties } from 'react'
import type { JobView, RackView, TwinState } from '../types'
import { Meter, Panel, Sparkline, fmt, jobColor, tempColor } from '../ui'

function GpuGrid({ rack, jobs }: { rack: RackView; jobs: JobView[] }) {
  const slots: (JobView | null)[] = []
  jobs.filter((j) => j.status === 'running').forEach((j) => { for (let i = 0; i < j.gpus; i++) slots.push(j) })
  while (slots.length < rack.gpu_count) slots.push(null)
  return (
    <div className="gpus" aria-label={`${rack.gpus_used} of ${rack.gpu_count} GPUs allocated`}>
      {slots.map((j, i) => (
        <div key={i} className={`gpu ${j ? 'used' : ''}`}
          title={j ? `${j.id} ${j.name} · util ${Math.round(j.utilization * rack.power_cap * 100)}%` : 'idle'}
          style={j ? ({ '--c': jobColor(j.id), '--u': `${Math.round(j.utilization * 100)}%` } as CSSProperties) : undefined} />
      ))}
    </div>
  )
}

function RackCard({ rack, jobs }: { rack: RackView; jobs: JobView[] }) {
  const thermalAlert = rack.peak_outlet_c > rack.outlet_limit_c || rack.peak_inlet_c > rack.inlet_limit_c
  const powerAlert = rack.peak_it_kw > rack.power_limit_kw
  const degraded = rack.cooling_derate < 1
  return (
    <div className={`rack ${thermalAlert || powerAlert ? 'alert' : degraded ? 'degraded' : ''}`}>
      <div className="rack-head">
        <div>
          <div className="name">{rack.name}</div>
          <div className="model">{rack.gpu_model} · {rack.gpu_memory_gb} GB/GPU</div>
        </div>
        <div className="row" style={{ gap: 4 }}>
          {degraded && <span className="pill warn">cooling {Math.round(rack.cooling_derate * 100)}%</span>}
          {rack.power_cap < 1 && <span className="pill agent">cap {Math.round(rack.power_cap * 100)}%</span>}
          {thermalAlert && <span className="pill crit">thermal</span>}
          {powerAlert && <span className="pill crit">power</span>}
        </div>
      </div>
      <GpuGrid rack={rack} jobs={jobs} />
      <div className="temps">
        <div className="temp"><div className="l">Inlet</div>
          <div className="v" style={{ color: rack.inlet_c > rack.inlet_limit_c ? 'var(--crit)' : undefined }}>{fmt(rack.inlet_c)}°C</div></div>
        <div className="temp"><div className="l">Outlet</div>
          <div className="v" style={{ color: tempColor(rack.outlet_c) }}>{fmt(rack.outlet_c)}°C</div></div>
      </div>
      <Meter label="IT power" value={rack.it_kw} max={rack.power_limit_kw} unit="kW" />
      <Meter label="Cooling load" value={rack.it_kw} max={rack.cooling_capacity_kw} unit="kW" />
      <div>
        <div className="meter-top"><span>Outlet over horizon</span><b>peak {fmt(rack.peak_outlet_c)}°C</b></div>
        <Sparkline values={rack.outlet_series} limit={rack.outlet_limit_c} color={tempColor(rack.peak_outlet_c)} />
      </div>
      <div className="muted" style={{ fontSize: 11.5 }}>
        {jobs.length ? jobs.map((j) => (
          <span key={j.id} style={{ marginRight: 8 }}>
            <span style={{ color: jobColor(j.id) }}>■</span> {j.id}{j.status === 'deferred' ? ` (t+${j.start_h}h)` : ''}
          </span>
        )) : 'no jobs'}
      </div>
    </div>
  )
}

export function DigitalTwin({ state }: { state: TwinState }) {
  return (
    <Panel title="Digital twin — 8 racks · 64 GPUs"
      subtitle="Simulated values at t=0 with horizon peaks. Thermal model: outlet = inlet + P/0.40 kW·°C⁻¹; inlet rises 4 °C per kW of heat above derated CRAH capacity.">
      <div className="racks">
        {state.racks.map((r) => <RackCard key={r.id} rack={r} jobs={state.jobs.filter((j) => j.rack_id === r.id)} />)}
      </div>
    </Panel>
  )
}
