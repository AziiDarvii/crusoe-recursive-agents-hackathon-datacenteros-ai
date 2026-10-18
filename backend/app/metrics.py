"""Metrics computed from simulator output. Every number is derived, never hard-coded."""
from typing import Dict, Optional

from . import config as C
from .models import DataCenterState
from .safety import find_violations
from .simulator import SimResult, simulate


def compute_metrics(state: DataCenterState, sim: Optional[SimResult] = None) -> Dict[str, float]:
    sim = sim or simulate(state)
    dt = state.facility.step_h
    steps = len(sim.times)
    violations = find_violations(state, sim)
    thermal_racks = {v.subject for v in violations if v.family == "thermal"}
    power_racks = {v.subject for v in violations if v.code == "RACK_POWER_LIMIT"}
    all_steps = [s for series in sim.racks.values() for s in series]
    usable = sum(r.usable_gpus for r in state.racks)
    cooling = [sum(sim.racks[r.id][i].cooling_kw for r in state.racks) for i in range(steps)]
    it = [sum(sim.racks[r.id][i].it_kw for r in state.racks) for i in range(steps)]
    outcomes = sim.jobs.values()
    return {
        "facility_kw_avg": round(sum(sim.facility_kw) / steps, 2),
        "facility_kw_peak": round(max(sim.facility_kw), 2),
        "energy_kwh": round(sum(sim.facility_kw) * dt, 2),
        "cooling_kw_avg": round(sum(cooling) / steps, 2),
        "it_kw_avg": round(sum(it) / steps, 2),
        "pue_avg": round(sum(sim.facility_kw) / sum(it), 3),
        "temp_outlet_avg_c": round(sum(s.outlet_c for s in all_steps) / len(all_steps), 2),
        "temp_outlet_max_c": round(max(s.outlet_c for s in all_steps), 2),
        "temp_inlet_max_c": round(max(s.inlet_c for s in all_steps), 2),
        "thermal_violations": len(thermal_racks),
        "power_violations": len(power_racks),
        "hard_violations": len(violations),
        "gpu_allocation_pct": round(100 * sum(s.gpus_used for s in all_steps) / (usable * steps), 1),
        "gpu_utilization_pct": round(100 * sum(s.utilization for s in all_steps) / len(all_steps), 1),
        "jobs_total": len(state.jobs),
        "jobs_completed": sum(1 for o in outcomes if o.completed_in_horizon),
        "jobs_missed_deadline": sum(1 for o in outcomes if o.missed_deadline),
        "horizon_h": state.facility.horizon_h,
    }


def rack_snapshot(state: DataCenterState, sim: Optional[SimResult] = None, step: int = 0) -> list:
    """Per-rack view at a given time step plus horizon peaks, for the dashboard."""
    sim = sim or simulate(state)
    rows = []
    for rack in state.racks:
        series = sim.racks[rack.id]
        now = series[step]
        rows.append({
            "id": rack.id, "name": rack.name, "gpu_model": rack.gpu_model,
            "gpu_count": rack.gpu_count, "usable_gpus": rack.usable_gpus,
            "gpu_memory_gb": rack.gpu_memory_gb, "gpus_used": now.gpus_used,
            "it_kw": round(now.it_kw, 2), "power_limit_kw": rack.power_limit_kw,
            "cooling_kw": round(now.cooling_kw, 2),
            "cooling_capacity_kw": round(rack.effective_cooling_kw, 2),
            "cooling_nominal_kw": rack.cooling_capacity_kw,
            "cooling_derate": rack.cooling_derate, "power_cap": rack.power_cap,
            "inlet_c": round(now.inlet_c, 2), "outlet_c": round(now.outlet_c, 2),
            "utilization": round(now.utilization, 3),
            "peak_outlet_c": round(max(s.outlet_c for s in series), 2),
            "peak_inlet_c": round(max(s.inlet_c for s in series), 2),
            "peak_it_kw": round(max(s.it_kw for s in series), 2),
            "outlet_series": [round(s.outlet_c, 2) for s in series],
            "power_series": [round(s.it_kw, 2) for s in series],
            "inlet_limit_c": C.INLET_LIMIT_C, "outlet_limit_c": C.OUTLET_LIMIT_C,
        })
    return rows


def compare(before: Dict[str, float], after: Dict[str, float]) -> Dict[str, dict]:
    """Absolute and percentage deltas computed from actual outputs (may be worse)."""
    out = {}
    for key, b in before.items():
        a = after.get(key)
        if not isinstance(b, (int, float)) or a is None:
            continue
        pct = None if b == 0 else round(100.0 * (a - b) / abs(b), 2)
        out[key] = {"before": b, "after": a, "delta": round(a - b, 3), "pct": pct}
    return out
