import type { ReactNode } from 'react'

export const fmt = (n: number | null | undefined, digits = 1) =>
  n === null || n === undefined || Number.isNaN(n) ? '—' : n.toFixed(digits)

/** Temperature (°C) to a restrained blue→teal→amber→red ramp. */
export function tempColor(t: number, limit = 45): string {
  const x = Math.max(0, Math.min(1, (t - 28) / (limit + 3 - 28)))
  const stops: [number, number, number][] = [[30, 58, 95], [15, 118, 110], [202, 138, 4], [220, 38, 38]]
  const seg = Math.min(stops.length - 2, Math.floor(x * (stops.length - 1)))
  const f = x * (stops.length - 1) - seg
  const [a, b] = [stops[seg], stops[seg + 1]]
  const c = a.map((v, i) => Math.round(v + (b[i] - v) * f))
  return `rgb(${c[0]}, ${c[1]}, ${c[2]})`
}

export function levelColor(ratio: number): string {
  if (ratio > 1) return 'var(--crit)'
  if (ratio > 0.9) return 'var(--warn)'
  return 'var(--accent)'
}

export function Kpi({ label, value, unit, sub, tone }: {
  label: string; value: ReactNode; unit?: string; sub?: ReactNode; tone?: 'crit' | 'ok'
}) {
  return (
    <div className={`kpi ${tone ?? ''}`}>
      <div className="label">{label}</div>
      <div className="value">{value}{unit && <small>{unit}</small>}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  )
}

export function Meter({ label, value, max, unit, digits = 1 }: {
  label: string; value: number; max: number; unit: string; digits?: number
}) {
  const ratio = max > 0 ? value / max : 0
  return (
    <div className="meter">
      <div className="meter-top">
        <span>{label}</span>
        <b>{fmt(value, digits)} / {fmt(max, digits)} {unit}</b>
      </div>
      <div className="track">
        <div className="fill" style={{ width: `${Math.min(100, ratio * 100)}%`, background: levelColor(ratio) }} />
      </div>
    </div>
  )
}

export function Sparkline({ values, limit, height = 36, color = 'var(--accent)' }: {
  values: number[]; limit?: number; height?: number; color?: string
}) {
  if (values.length < 2) return null
  const width = 200
  const lo = Math.min(...values, limit ?? Infinity) - 1
  const hi = Math.max(...values, limit ?? -Infinity) + 1
  const y = (v: number) => height - ((v - lo) / (hi - lo)) * height
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * width},${y(v).toFixed(1)}`).join(' ')
  return (
    <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" width="100%" height={height} role="img"
      aria-label="trend over simulated horizon">
      {limit !== undefined && (
        <line x1="0" x2={width} y1={y(limit)} y2={y(limit)} stroke="var(--crit)" strokeDasharray="3 3" strokeWidth="1" opacity="0.7" />
      )}
      <polyline points={pts} fill="none" stroke={color} strokeWidth="1.6" vectorEffect="non-scaling-stroke" />
    </svg>
  )
}

export function Panel({ title, subtitle, actions, children }: {
  title: ReactNode; subtitle?: ReactNode; actions?: ReactNode; children: ReactNode
}) {
  return (
    <section className="panel">
      <div className="panel-head">
        <div><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div>
        {actions && <div className="row">{actions}</div>}
      </div>
      {children}
    </section>
  )
}

export const PRIORITY = ['', 'critical', 'high', 'normal', 'low']
export const priorityTone = (p: number) => (p === 1 ? 'crit' : p === 2 ? 'warn' : '')

export const JOB_COLORS = ['#4cc9f0', '#a78bfa', '#34d399', '#fb923c', '#60a5fa', '#f472b6', '#facc15', '#2dd4bf',
  '#c084fc', '#f87171', '#86efac', '#93c5fd', '#fda4af', '#fcd34d', '#5eead4', '#e879f9']
export const jobColor = (id: string) => JOB_COLORS[(parseInt(id.replace(/\D/g, ''), 10) || 0) % JOB_COLORS.length]
