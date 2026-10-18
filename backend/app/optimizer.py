"""Deterministic optimization engine.

Two layers:
  * Specialist *fast-path* heuristics (locally reasonable, single-domain).
  * A bounded beam search over plan actions that minimises a configurable
    objective subject to the *active* constraint families. Families are added
    lazily when the independent verifier rejects a plan (lazy constraint
    generation), and rejected plan signatures are tabu.
"""
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Iterable, List, Optional, Tuple

from . import config as C
from .models import Action, DataCenterState, Plan
from .safety import find_violations
from .simulator import PlanApplicationError, SimResult, apply_action, simulate

STRATEGY_ACTIONS: Dict[str, Tuple[str, ...]] = {
    "constrained_search": ("migrate", "schedule"),
    "defer_flexible": ("migrate", "schedule", "defer"),
    "power_cap": ("migrate", "schedule", "power_cap"),
    "combined": ("migrate", "schedule", "defer", "power_cap"),
}
STRATEGY_ORDER = tuple(STRATEGY_ACTIONS)
BASE_FAMILIES: FrozenSet[str] = frozenset({"action_validity", "thermal", "gpu_capacity"})
DEFER_STARTS_H = (1.0, 2.0, 3.0)
CAP_LEVELS = (0.9, 0.8, 0.7)
VIOLATION_PENALTY = 1000.0
BEAM_WIDTH = 30
MAX_PLAN_ACTIONS = 3


@dataclass(frozen=True)
class SearchResult:
    plan: Optional[Plan]
    evaluated: int
    cost: Optional[float]
    breakdown: Dict[str, float] = field(default_factory=dict)
    reason: str = ""


def signature_of(actions: Iterable[Action]) -> str:
    return "|".join(sorted(a.signature() for a in actions)) or "noop"


# --- Objective -------------------------------------------------------------------

def objective(state: DataCenterState, sim: SimResult, actions: Tuple[Action, ...],
              weights: C.ObjectiveWeights) -> Tuple[float, Dict[str, float]]:
    energy = sum(sim.facility_kw) * state.facility.step_h
    thermal = 0.0
    for series in sim.racks.values():
        peak_out = max(s.outlet_c for s in series)
        peak_in = max(s.inlet_c for s in series)
        thermal += max(0.0, peak_out - (C.OUTLET_LIMIT_C - C.THERMAL_MARGIN_C)) ** 2
        thermal += max(0.0, peak_in - (C.INLET_LIMIT_C - 1.0)) ** 2
    usable = sum(r.usable_gpus for r in state.racks) * len(sim.times)
    used = sum(s.gpus_used for series in sim.racks.values() for s in series)
    idle_pct = 100.0 * (1 - used / usable)
    deadline = sum((5 - state.job(o.job_id).priority) * (1 + min(o.lateness_h, 24.0))
                   for o in sim.jobs.values() if o.missed_deadline)
    migration = 0.0
    for a in actions:
        if a.kind in ("migrate", "defer"):
            migration += state.job(a.target).gpus * (1.0 if a.kind == "migrate" else 0.75)
        elif a.kind == "power_cap":
            migration += (1 - a.cap) * 10 * state.rack(a.target).usable_gpus / 8
    parts = {
        "energy": weights.energy * energy,
        "thermal": weights.thermal * thermal,
        "utilization": weights.utilization * idle_pct,
        "deadline": weights.deadline * deadline,
        "migration": weights.migration * migration,
    }
    return sum(parts.values()), {k: round(v, 3) for k, v in parts.items()} | {"energy_kwh": round(energy, 2)}


# --- Candidate generation (owned by the GPU Scheduling agent) ---------------------

def problem_scope(state: DataCenterState, sim: SimResult) -> Tuple[set, set]:
    """Racks and jobs implicated by current hard-constraint violations."""
    racks, jobs = set(), set()
    for v in find_violations(state, sim):
        (racks if v.subject.startswith("rack-") else jobs).add(v.subject)
    return racks, jobs


def candidate_actions(state: DataCenterState, kinds: Tuple[str, ...]) -> List[Action]:
    sim = simulate(state)
    hot_racks, hot_jobs = problem_scope(state, sim)
    queued_problem = [j for j in state.jobs if j.id in hot_jobs and j.rack_id is None]
    out: List[Action] = []
    for job in state.jobs:
        if job.rack_id is None:
            continue
        movable_scope = job.rack_id in hot_racks or queued_problem
        if "migrate" in kinds and job.migratable and movable_scope:
            out += [Action("migrate", job.id, rack_id=r.id) for r in state.racks if r.id != job.rack_id]
        if "defer" in kinds and job.deferrable:
            out += [Action("defer", job.id, start_h=t) for t in DEFER_STARTS_H]
    if "power_cap" in kinds:
        out += [Action("power_cap", rid, cap=c) for rid in sorted(hot_racks) for c in CAP_LEVELS]
    if "schedule" in kinds:
        out += [Action("schedule", j.id, rack_id=r.id, start_h=0.0) for j in queued_problem for r in state.racks]
    return out


# --- Beam search -----------------------------------------------------------------------

def search(state: DataCenterState, strategy: str, active: FrozenSet[str], tabu: FrozenSet[str],
           weights: C.ObjectiveWeights) -> SearchResult:
    kinds = STRATEGY_ACTIONS[strategy]
    actions = candidate_actions(state, kinds)
    frontier: List[Tuple[Tuple[Action, ...], DataCenterState]] = [((), state)]
    evaluated = 0
    for _depth in range(MAX_PLAN_ACTIONS):
        scored = []
        for chosen, current in frontier:
            targets = {a.target for a in chosen}
            for action in actions:
                if action.target in targets or (chosen and action.signature() <= chosen[-1].signature()):
                    continue
                try:
                    candidate = apply_action(current, action)
                except PlanApplicationError:
                    continue
                sim = simulate(candidate)
                evaluated += 1
                plan_actions = chosen + (action,)
                violated = [v for v in find_violations(candidate, sim) if v.family in active]
                cost, parts = objective(candidate, sim, plan_actions, weights)
                scored.append((cost + VIOLATION_PENALTY * len(violated), len(violated), cost, parts,
                               plan_actions, candidate))
        scored.sort(key=lambda row: row[0])
        for _, n_violated, cost, parts, plan_actions, _ in scored:
            if n_violated == 0 and signature_of(plan_actions) not in tabu:
                plan = Plan(plan_actions, strategy, "optimizer",
                            f"Best of {evaluated} simulated candidates under constraints "
                            f"{sorted(active)} (objective {cost:.1f}).")
                return SearchResult(plan, evaluated, round(cost, 3), parts)
        frontier = [(row[4], row[5]) for row in scored[:BEAM_WIDTH]]
        if not frontier:
            break
    return SearchResult(None, evaluated, None, reason=f"no plan satisfies {sorted(active)} with "
                                                       f"actions {list(kinds)} within {MAX_PLAN_ACTIONS} steps")


# --- Specialist fast-path heuristics -----------------------------------------------------

def _free_gpus(state: DataCenterState, sim: SimResult, rack_id: str) -> int:
    rack = state.rack(rack_id)
    return rack.usable_gpus - max(s.gpus_used for s in sim.racks[rack_id])


def thermal_fast_path(state: DataCenterState, sim: SimResult) -> Optional[Plan]:
    """Move the biggest heat source off the hottest rack to the coolest rack with room."""
    hot = [r for r in state.racks if max(s.outlet_c for s in sim.racks[r.id]) > C.OUTLET_LIMIT_C
           or max(s.inlet_c for s in sim.racks[r.id]) > C.INLET_LIMIT_C]
    if not hot:
        return None
    rack = max(hot, key=lambda r: max(s.outlet_c for s in sim.racks[r.id]))
    jobs = sorted((j for j in state.jobs if j.rack_id == rack.id and j.migratable),
                  key=lambda j: j.gpus * j.utilization, reverse=True)
    for job in jobs:
        targets = [r for r in state.racks if r.id != rack.id and _free_gpus(state, sim, r.id) >= job.gpus]
        if targets:
            coolest = min(targets, key=lambda r: max(s.outlet_c for s in sim.racks[r.id]))
            return Plan((Action("migrate", job.id, rack_id=coolest.id),), "thermal_fast_path", "thermal",
                        f"Move highest heat load {job.id} off {rack.id} to coolest rack {coolest.id}.")
    return None


def power_fast_path(state: DataCenterState, sim: SimResult) -> Optional[Plan]:
    """Move the heaviest job off an over-budget rack to the rack with most power headroom."""
    over = [r for r in state.racks if max(s.it_kw for s in sim.racks[r.id]) > r.power_limit_kw]
    if not over:
        return None
    rack = over[0]
    jobs = sorted((j for j in state.jobs if j.rack_id == rack.id and j.migratable),
                  key=lambda j: j.gpus * j.utilization, reverse=True)
    for job in jobs:
        targets = [r for r in state.racks if r.id != rack.id and _free_gpus(state, sim, r.id) >= job.gpus]
        if targets:
            best = max(targets, key=lambda r: r.power_limit_kw - max(s.it_kw for s in sim.racks[r.id]))
            return Plan((Action("migrate", job.id, rack_id=best.id),), "power_fast_path", "power",
                        f"Move {job.id} off over-budget {rack.id} to {best.id} (most kW headroom).")
    return None


def scheduling_fast_path(state: DataCenterState, sim: SimResult) -> Optional[Plan]:
    """Place a critical queued job on the rack with the most free GPUs."""
    queued = [j for j in state.jobs if j.rack_id is None and j.priority == 1 and sim.jobs[j.id].missed_deadline]
    for job in queued:
        targets = [r for r in state.racks if _free_gpus(state, sim, r.id) >= job.gpus]
        if targets:
            best = max(targets, key=lambda r: _free_gpus(state, sim, r.id))
            return Plan((Action("schedule", job.id, rack_id=best.id, start_h=0.0),), "scheduling_fast_path",
                        "scheduling", f"Place critical {job.id} on {best.id} (most free GPUs).")
    return None
