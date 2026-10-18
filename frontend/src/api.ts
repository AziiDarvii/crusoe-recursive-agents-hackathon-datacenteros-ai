import type {
  AgentConfig, BenchmarkRow, MemoryAttempt, ProviderInfo, RunStatus, RunSummary, TwinState,
} from './types'

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...init })
  } catch {
    throw new ApiError(0, 'Backend unreachable — is the FastAPI server running on :8000?')
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    const detail = typeof body.detail === 'string' ? body.detail : `Request failed (${res.status})`
    throw new ApiError(res.status, detail)
  }
  return res.json() as Promise<T>
}

const post = <T>(path: string, body: unknown = {}) =>
  request<T>(path, { method: 'POST', body: JSON.stringify(body) })

export const api = {
  state: () => request<TwinState>('/api/state'),
  reset: (scenario: string) => post<TwinState>('/api/reset', { scenario }),
  injectCoolingFailure: (rack_id = 'rack-04', loss = 0.3) =>
    post<{ run_id: string; state: TwinState }>('/api/inject/cooling-failure', { rack_id, loss, auto_recover: true }),
  startRun: () => post<{ run_id: string }>('/api/runs'),
  run: (id: string) => request<RunStatus>(`/api/runs/${id}`),
  runs: () => request<{ runs: RunSummary[] }>('/api/runs'),
  memory: () => request<{ attempts: MemoryAttempt[] }>('/api/memory'),
  clearMemory: () => request<{ cleared: boolean }>('/api/memory', { method: 'DELETE' }),
  provider: () => request<ProviderInfo>('/api/provider'),
  config: () => request<AgentConfig>('/api/config'),
  saveConfig: (cfg: AgentConfig) => request<AgentConfig>('/api/config', { method: 'PUT', body: JSON.stringify(cfg) }),
  benchmarks: () => post<{ rows: BenchmarkRow[] }>('/api/benchmarks'),
  assumptions: () => request<{ constants: Record<string, number>; tool_permissions: Record<string, string[]> }>('/api/assumptions'),
}
