"""Independent deterministic verification. No LLM is involved in a PASS/REJECT."""
from dataclasses import asdict, dataclass
from typing import List, Optional, Tuple

from . import config as C
from .models import DataCenterState, Plan
from .simulator import PlanApplicationError, SimResult, apply_plan, simulate

# Violation code -> constraint family (used by the planner's lazy-constraint learning).
FAMILY = {
    "INVALID_ACTION": "action_validity",
    "GPU_OVERALLOCATION": "gpu_capacity",
    "MEMORY_INCOMPATIBLE": "memory",
    "RACK_POWER_LIMIT": "rack_power",
    "FACILITY_POWER_LIMIT": "facility_power",
    "INLET_TEMP_LIMIT": "thermal",
    "OUTLET_TEMP_LIMIT": "thermal",
    "CRITICAL_DEADLINE_MISS": "critical_deadline",
}
CHECKS = ("action_validity", "gpu_capacity", "memory", "rack_power", "facility_power",
          "thermal", "critical_deadline")


@dataclass(frozen=True)
class Violation:
    code: str
    subject: str
    message: str
    value: Optional[float] = None
    limit: Optional[float] = None
    time_h: Optional[float] = None

    @property
    def family(self) -> str:
        return FAMILY[self.code]

    def to_dict(self) -> dict:
        return {**asdict(self), "family": self.family}


@dataclass(frozen=True)
class Verdict:
    status: str                       # "PASS" | "REJECT"
    violations: Tuple[Violation, ...]
    checks: Tuple[Tuple[str, bool], ...]

    @property
    def passed(self) -> bool:
        return self.status == "PASS"

    def families(self) -> Tuple[str, ...]:
        return tuple(sorted({v.family for v in self.violations}))

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "violations": [v.to_dict() for v in self.violations],
            "checks": [{"check": name, "passed": ok} for name, ok in self.checks],
        }


def _first_exceed(series, limit: float):
    """Return (time_index, value) of the peak if it exceeds `limit`, else None."""
    idx = max(range(len(series)), key=lambda i: series[i])
    return (idx, series[idx]) if series[idx] > limit + 1e-9 else None


def find_violations(state: DataCenterState, sim: SimResult) -> List[Violation]:
    out: List[Violation] = []
    times = sim.times
    for rack in state.racks:
        steps = sim.racks[rack.id]
        hit = _first_exceed([s.gpus_used for s in steps], rack.usable_gpus)
        if hit:
            out.append(Violation("GPU_OVERALLOCATION", rack.id,
                                 f"{rack.id} needs {hit[1]} GPUs but has {rack.usable_gpus}",
                                 hit[1], rack.usable_gpus, times[hit[0]]))
        hit = _first_exceed([s.it_kw for s in steps], rack.power_limit_kw)
        if hit:
            out.append(Violation("RACK_POWER_LIMIT", rack.id,
                                 f"{rack.id} draws {hit[1]:.2f} kW > budget {rack.power_limit_kw:.2f} kW",
                                 round(hit[1], 3), rack.power_limit_kw, times[hit[0]]))
        hit = _first_exceed([s.inlet_c for s in steps], C.INLET_LIMIT_C)
        if hit:
            out.append(Violation("INLET_TEMP_LIMIT", rack.id,
                                 f"{rack.id} inlet {hit[1]:.1f}°C > {C.INLET_LIMIT_C}°C",
                                 round(hit[1], 2), C.INLET_LIMIT_C, times[hit[0]]))
        hit = _first_exceed([s.outlet_c for s in steps], C.OUTLET_LIMIT_C)
        if hit:
            out.append(Violation("OUTLET_TEMP_LIMIT", rack.id,
                                 f"{rack.id} outlet {hit[1]:.1f}°C > {C.OUTLET_LIMIT_C}°C",
                                 round(hit[1], 2), C.OUTLET_LIMIT_C, times[hit[0]]))
    hit = _first_exceed(list(sim.facility_kw), state.facility.power_limit_kw)
    if hit:
        out.append(Violation("FACILITY_POWER_LIMIT", "facility",
                             f"facility {hit[1]:.1f} kW > limit {state.facility.power_limit_kw} kW",
                             round(hit[1], 2), state.facility.power_limit_kw, times[hit[0]]))
    for job in state.jobs:
        rack = state.rack(job.rack_id) if job.rack_id else None
        if rack and job.memory_gb_per_gpu > rack.gpu_memory_gb:
            out.append(Violation("MEMORY_INCOMPATIBLE", job.id,
                                 f"{job.id} needs {job.memory_gb_per_gpu} GB/GPU; {rack.id} has {rack.gpu_memory_gb} GB",
                                 job.memory_gb_per_gpu, rack.gpu_memory_gb))
        outcome = sim.jobs[job.id]
        if job.priority == 1 and outcome.missed_deadline:
            out.append(Violation("CRITICAL_DEADLINE_MISS", job.id,
                                 f"critical {job.id} misses its {job.deadline_h:g} h deadline by "
                                 f"{outcome.lateness_h:.2f} h", round(outcome.lateness_h, 3), 0.0))
    return out


def _verdict(violations: List[Violation]) -> Verdict:
    failed = {v.family for v in violations}
    checks = tuple((name, name not in failed) for name in CHECKS)
    return Verdict("REJECT" if violations else "PASS", tuple(violations), checks)


def verify_state(state: DataCenterState) -> Verdict:
    return _verdict(find_violations(state, simulate(state)))


def verify(state: DataCenterState, plan: Plan) -> Verdict:
    """Apply `plan` to a copy of `state`, simulate the horizon, and check every hard constraint."""
    try:
        candidate = apply_plan(state, plan)
    except PlanApplicationError as exc:
        return _verdict([Violation("INVALID_ACTION", exc.action.target, str(exc))])
    return _verdict(find_violations(candidate, simulate(candidate)))
