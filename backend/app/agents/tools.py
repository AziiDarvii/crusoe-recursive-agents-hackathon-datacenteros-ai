"""Deterministic tools exposed to agents, gated by per-agent permissions."""
from typing import Any, Callable, Dict

from .. import config as C
from .. import optimizer
from ..metrics import compute_metrics
from ..models import DataCenterState, Plan
from ..safety import verify, verify_state
from ..simulator import simulate
from .prompts import TOOL_PERMISSIONS


class ToolPermissionError(PermissionError):
    pass


def analyze_thermal(state: DataCenterState) -> dict:
    sim = simulate(state)
    racks, at_risk = [], []
    for rack in state.racks:
        series = sim.racks[rack.id]
        peak_in = max(s.inlet_c for s in series)
        peak_out = max(s.outlet_c for s in series)
        peak_kw = max(s.it_kw for s in series)
        row = {
            "rack": rack.id, "peak_inlet_c": round(peak_in, 2), "peak_outlet_c": round(peak_out, 2),
            "headroom_c": round(min(C.INLET_LIMIT_C - peak_in, C.OUTLET_LIMIT_C - peak_out), 2),
            "cooling_load_pct": round(100 * peak_kw / rack.effective_cooling_kw, 1),
            "cooling_derate": rack.cooling_derate,
            "top_jobs": [j.id for j in sorted((j for j in state.jobs if j.rack_id == rack.id),
                                              key=lambda j: j.gpus * j.utilization, reverse=True)],
        }
        racks.append(row)
        if row["headroom_c"] < 0:
            at_risk.append(row)
    return {"racks": racks, "at_risk": at_risk,
            "model": "outlet = inlet + P/0.40 kW/°C; inlet rises 4 °C per kW above derated cooling capacity"}


def analyze_power(state: DataCenterState) -> dict:
    sim = simulate(state)
    metrics = compute_metrics(state, sim)
    racks = []
    for rack in state.racks:
        peak = max(s.it_kw for s in sim.racks[rack.id])
        racks.append({"rack": rack.id, "peak_kw": round(peak, 2), "limit_kw": rack.power_limit_kw,
                      "headroom_kw": round(rack.power_limit_kw - peak, 2), "power_cap": rack.power_cap})
    return {"racks": racks, "facility_peak_kw": metrics["facility_kw_peak"],
            "facility_limit_kw": state.facility.power_limit_kw, "cooling_kw_avg": metrics["cooling_kw_avg"],
            "pue": metrics["pue_avg"], "energy_kwh": metrics["energy_kwh"]}


def analyze_capacity(state: DataCenterState) -> dict:
    sim = simulate(state)
    racks = [{"rack": r.id, "free_gpus": r.usable_gpus - max(s.gpus_used for s in sim.racks[r.id]),
              "gpu_memory_gb": r.gpu_memory_gb} for r in state.racks]
    flexible = [{"job": j.id, "rack": j.rack_id, "gpus": j.gpus, "migratable": j.migratable,
                 "deferrable": j.deferrable, "priority": j.priority,
                 "slack_h": round(j.deadline_h - j.remaining_h - j.delay_h - j.start_h, 2)}
                for j in state.jobs if j.migratable or j.deferrable]
    queued_critical = [j.id for j in state.jobs if j.rack_id is None and j.priority == 1]
    return {"racks": racks, "flexible_jobs": flexible, "queued_critical": queued_critical}


def estimate_energy_impact(before: DataCenterState, after: DataCenterState) -> dict:
    b, a = compute_metrics(before), compute_metrics(after)
    keys = ("energy_kwh", "cooling_kw_avg", "facility_kw_peak", "pue_avg")
    return {k: {"before": b[k], "after": a[k], "delta": round(a[k] - b[k], 3)} for k in keys}


def plan_search(state: DataCenterState, strategy: str, active: frozenset, tabu: frozenset,
                weights: C.ObjectiveWeights) -> optimizer.SearchResult:
    return optimizer.search(state, strategy, active, tabu, weights)


def _fast_path(fn: Callable) -> Callable[[DataCenterState], Any]:
    return lambda state: fn(state, simulate(state))


REGISTRY: Dict[str, Callable[..., Any]] = {
    "read_state": lambda state: state,
    "delegate": lambda **kw: kw,
    "analyze_thermal": analyze_thermal,
    "analyze_power": analyze_power,
    "analyze_capacity": analyze_capacity,
    "thermal_fast_path": _fast_path(optimizer.thermal_fast_path),
    "power_fast_path": _fast_path(optimizer.power_fast_path),
    "scheduling_fast_path": _fast_path(optimizer.scheduling_fast_path),
    "estimate_energy_impact": estimate_energy_impact,
    "plan_search": plan_search,
    "verify_state": verify_state,
    "verify_plan": lambda state, plan: verify(state, plan),
}


def call_tool(agent: str, tool: str, *args: Any, **kwargs: Any) -> Any:
    """Invoke `tool` as `agent`; raises ToolPermissionError if the agent lacks permission."""
    if tool not in TOOL_PERMISSIONS.get(agent, frozenset()):
        raise ToolPermissionError(f"agent '{agent}' is not permitted to call '{tool}'")
    return REGISTRY[tool](*args, **kwargs)


def plan_or_none(value: Any) -> Plan | None:
    return value if isinstance(value, Plan) else None
