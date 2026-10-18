export interface RackView {
  id: string
  name: string
  gpu_model: string
  gpu_count: number
  usable_gpus: number
  gpu_memory_gb: number
  gpus_used: number
  it_kw: number
  power_limit_kw: number
  cooling_kw: number
  cooling_capacity_kw: number
  cooling_nominal_kw: number
  cooling_derate: number
  power_cap: number
  inlet_c: number
  outlet_c: number
  utilization: number
  peak_outlet_c: number
  peak_inlet_c: number
  peak_it_kw: number
  outlet_series: number[]
  power_series: number[]
  inlet_limit_c: number
  outlet_limit_c: number
}

export interface JobView {
  id: string
  name: string
  kind: 'training' | 'inference'
  priority: number
  gpus: number
  memory_gb_per_gpu: number
  utilization: number
  remaining_h: number
  deadline_h: number
  rack_id: string | null
  start_h: number
  delay_h: number
  migratable: boolean
  deferrable: boolean
  status: 'running' | 'queued' | 'deferred'
  completion_h: number | null
  missed_deadline: boolean
  lateness_h: number
}

export type Metrics = Record<string, number>

export interface Violation {
  code: string
  family: string
  subject: string
  message: string
  value: number | null
  limit: number | null
  time_h: number | null
}

export interface TwinState {
  simulated: true
  scenario: string
  incidents: string[]
  facility: { power_limit_kw: number; horizon_h: number; step_h: number }
  racks: RackView[]
  jobs: JobView[]
  metrics: Metrics
  pre_incident_metrics: Metrics
  violations: Violation[]
  facility_series: number[]
  times: number[]
  active_run: string | null
}

export interface RunEvent {
  seq: number
  t_ms: number
  kind: 'state' | 'task' | 'delegate' | 'tool' | 'llm' | 'plan' | 'verdict' | 'memory' | 'commit' | 'escalate' | 'info' | 'error'
  agent: string
  state: string
  title: string
  detail: string
  data: Record<string, unknown>
  task_id: string | null
}

export interface PlanAction {
  kind: string
  target: string
  rack_id: string | null
  start_h: number | null
  cap: number | null
  description: string
}

export interface PlanView {
  strategy: string
  author: string
  rationale: string
  signature: string
  actions: PlanAction[]
}

export interface Attempt {
  attempt: number
  depth: number
  strategy: string
  author: string
  signature: string | null
  verdict: 'PASS' | 'REJECT' | 'NO_FEASIBLE_PLAN'
  families: string[]
  violations: Violation[]
  plan: PlanView | null
  energy_impact: Record<string, { before: number; after: number; delta: number }> | null
}

export interface TaskNode {
  id: string
  agent: string
  title: string
  parent: string | null
  depth: number
  status: string
  result: string | null
}

export interface RunResult {
  run_id: string
  scenario: string
  status: 'VERIFIED' | 'ESCALATED'
  summary: string
  escalation_reason: string | null
  incidents: string[]
  provider: { name: string; model: string; mock: boolean }
  llm: { calls: number; errors: number; tokens_in: number; tokens_out: number; avg_latency_ms: number }
  transitions: { from: string; to: string; reason: string; t_ms: number }[]
  tasks: TaskNode[]
  attempts: Attempt[]
  plan: PlanView | null
  learned_constraints: string[]
  metrics: { before: Metrics; after: Metrics }
  comparison: Record<string, { before: number; after: number; delta: number; pct: number | null }>
  rejections: number
  runtime_s: number
}

export interface RunStatus {
  run_id: string
  status: 'running' | 'finished' | 'error'
  events: RunEvent[]
  result: RunResult | null
  error: string | null
  replay: boolean
}

export interface ProviderInfo {
  name: string
  model: string
  mock: boolean
  base_url: string | null
  how_to_enable: string
}

export interface AgentConfig {
  max_attempts: number
  max_depth: number
  max_llm_calls: number
  pacing_s: number
  use_cross_run_memory: boolean
  weights: Record<'energy' | 'thermal' | 'utilization' | 'deadline' | 'migration', number>
}

export interface RunSummary {
  id: string
  scenario: string
  provider: string
  status: string
  started_at: string
  finished_at: string | null
  summary: string | null
}

export interface MemoryAttempt {
  run_id: string
  scenario: string
  attempt_no: number
  depth: number
  strategy: string
  author: string
  signature: string | null
  verdict: string
  families: string[]
  violations: Violation[]
  plan: PlanView | null
  created_at: string
}

export type BenchmarkRow = Record<string, string | number | boolean>
