"""Deterministic time-stepped digital twin.

Pure functions: `apply_plan` returns a *new* state, `simulate` never mutates input.
"""
from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Tuple

from . import config as C
from .models import Action, DataCenterState, Job, Plan, Rack


@dataclass(frozen=True)
class RackStep:
    gpus_used: int
    it_kw: float
    cooling_kw: float
    inlet_c: float
    outlet_c: float
    utilization: float           # mean utilization of usable GPUs


@dataclass(frozen=True)
class JobOutcome:
    job_id: str
    completion_h: Optional[float]   # None = never completes (unplaced)
    completed_in_horizon: bool
    missed_deadline: bool
    lateness_h: float


@dataclass(frozen=True)
class SimResult:
    times: Tuple[float, ...]
    racks: Dict[str, Tuple[RackStep, ...]]
    facility_kw: Tuple[float, ...]
    jobs: Dict[str, JobOutcome]


class PlanApplicationError(ValueError):
    """Raised when an action is invalid for the current simulator state."""

    def __init__(self, action: Action, reason: str) -> None:
        super().__init__(f"{action.describe()}: {reason}")
        self.action = action
        self.reason = reason


# --- Physics -----------------------------------------------------------------

def gpu_power_kw(utilization: float, cap: float) -> float:
    return C.GPU_IDLE_KW + (C.GPU_MAX_KW - C.GPU_IDLE_KW) * utilization * cap


def cooling_cop(load_fraction: float) -> float:
    excess = max(0.0, load_fraction - C.COP_DEGRADE_KNEE)
    return max(C.COP_MIN, C.COP_NOMINAL * (1.0 - C.COP_DEGRADE_SLOPE * excess))


def rack_thermal(rack: Rack, it_kw: float) -> Tuple[float, float, float]:
    """Return (inlet_c, outlet_c, cooling_kw) for a rack drawing `it_kw`."""
    capacity = rack.effective_cooling_kw
    unremoved = max(0.0, it_kw - capacity)
    inlet = rack.supply_air_c + C.RECIRC_C_PER_KW * unremoved
    outlet = inlet + it_kw / C.AIRFLOW_KW_PER_C
    cooling_kw = min(it_kw, capacity) / cooling_cop(it_kw / capacity if capacity else 99.0)
    return inlet, outlet, cooling_kw


def throughput(cap: float) -> float:
    return cap ** C.POWER_CAP_PERF_EXPONENT


# --- Plan application ----------------------------------------------------------

def _require_job(state: DataCenterState, action: Action) -> Job:
    job = state.job(action.target)
    if job is None:
        raise PlanApplicationError(action, f"unknown job {action.target}")
    return job


def _require_rack(state: DataCenterState, action: Action, rack_id: Optional[str]) -> Rack:
    rack = state.rack(rack_id or "")
    if rack is None:
        raise PlanApplicationError(action, f"unknown rack {rack_id}")
    return rack


def apply_action(state: DataCenterState, action: Action) -> DataCenterState:
    if action.kind == "power_cap":
        rack = _require_rack(state, action, action.target)
        if action.cap is None or not C.MIN_POWER_CAP <= action.cap <= 1.0:
            raise PlanApplicationError(action, f"cap must be in [{C.MIN_POWER_CAP}, 1.0]")
        return state.with_rack(replace(rack, power_cap=action.cap))

    job = _require_job(state, action)
    if action.kind == "migrate":
        target = _require_rack(state, action, action.rack_id)
        if job.rack_id is None:
            raise PlanApplicationError(action, "job is queued, use schedule")
        if not job.migratable:
            raise PlanApplicationError(action, "job is not migratable")
        if job.rack_id == target.id:
            raise PlanApplicationError(action, "job already on target rack")
        return state.with_job(replace(job, rack_id=target.id, delay_h=job.delay_h + C.MIGRATION_RESTORE_H))
    if action.kind == "defer":
        if not job.deferrable:
            raise PlanApplicationError(action, "job is not deferrable")
        if action.start_h is None or action.start_h <= 0:
            raise PlanApplicationError(action, "defer requires start_h > 0")
        rack_id = action.rack_id or job.rack_id
        if rack_id is None:
            raise PlanApplicationError(action, "deferred job needs a rack")
        _require_rack(state, action, rack_id)
        return state.with_job(replace(job, rack_id=rack_id, start_h=action.start_h,
                                      delay_h=job.delay_h + C.RESUME_OVERHEAD_H))
    if action.kind == "schedule":
        target = _require_rack(state, action, action.rack_id)
        if job.rack_id is not None:
            raise PlanApplicationError(action, "job is already placed")
        return state.with_job(replace(job, rack_id=target.id, start_h=action.start_h or 0.0))
    raise PlanApplicationError(action, f"unknown action kind {action.kind}")


def apply_plan(state: DataCenterState, plan: Plan) -> DataCenterState:
    targets = [a.target for a in plan.actions]
    if len(targets) != len(set(targets)):
        raise PlanApplicationError(plan.actions[0], "plan touches the same target twice")
    result = state
    for action in plan.actions:
        result = apply_action(result, action)
    return result


# --- Simulation ------------------------------------------------------------------

def _step_count(state: DataCenterState) -> int:
    return int(round(state.facility.horizon_h / state.facility.step_h))


def simulate(state: DataCenterState) -> SimResult:
    dt = state.facility.step_h
    steps = _step_count(state)
    racks = {r.id: r for r in state.racks}
    progress = {j.id: 0.0 for j in state.jobs}
    done_at: Dict[str, float] = {}
    series: Dict[str, List[RackStep]] = {r.id: [] for r in state.racks}
    facility: List[float] = []
    times = []

    for i in range(steps):
        t = i * dt
        times.append(t)
        used = {rid: 0 for rid in racks}
        load_kw = {rid: 0.0 for rid in racks}
        util_sum = {rid: 0.0 for rid in racks}
        for job in state.jobs:
            if job.rack_id is None or job.id in done_at or t < job.start_h:
                continue
            rack = racks[job.rack_id]
            used[rack.id] += job.gpus
            load_kw[rack.id] += job.gpus * (gpu_power_kw(job.utilization, rack.power_cap) - C.GPU_IDLE_KW)
            util_sum[rack.id] += job.gpus * job.utilization
            if t >= job.start_h + job.delay_h:
                progress[job.id] += dt * throughput(rack.power_cap)
                if progress[job.id] >= job.remaining_h - 1e-9:
                    done_at[job.id] = t + dt
        it_total = 0.0
        cooling_total = 0.0
        for rid, rack in racks.items():
            it_kw = C.RACK_BASE_KW + rack.usable_gpus * C.GPU_IDLE_KW + load_kw[rid]
            inlet, outlet, cooling_kw = rack_thermal(rack, it_kw)
            series[rid].append(RackStep(used[rid], it_kw, cooling_kw, inlet, outlet,
                                        util_sum[rid] / max(1, rack.usable_gpus)))
            it_total += it_kw
            cooling_total += cooling_kw
        facility.append(it_total * (1 + C.UPS_LOSS_FRACTION) + cooling_total + C.FACILITY_MISC_KW)

    horizon = steps * dt
    outcomes = {j.id: _outcome(j, done_at.get(j.id), progress[j.id], horizon, racks) for j in state.jobs}
    return SimResult(tuple(times), {k: tuple(v) for k, v in series.items()}, tuple(facility), outcomes)


def _outcome(job: Job, done: Optional[float], progress: float, horizon: float,
             racks: Dict[str, Rack]) -> JobOutcome:
    if done is None and job.rack_id is not None:
        rate = throughput(racks[job.rack_id].power_cap)
        done = horizon + max(0.0, job.remaining_h - progress) / rate
    if done is None:
        # Unplaced: best case it starts right after the horizon.
        lateness = horizon + job.remaining_h - job.deadline_h
        return JobOutcome(job.id, None, False, lateness > 1e-9, max(0.0, lateness))
    lateness = done - job.deadline_h
    return JobOutcome(job.id, done, done <= horizon + 1e-9, lateness > 1e-9, max(0.0, lateness))
